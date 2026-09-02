#!/usr/bin/env python3
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from espn_api.football import League

LEAGUE_ID = int(os.getenv("ESPN_LEAGUE_ID", "1749747321"))
SEASON = int(os.getenv("ESPN_SEASON", "2026"))
ESPN_S2 = os.environ["ESPN_S2"]
ESPN_SWID = os.environ["ESPN_SWID"]

OUTPUT_PATH = Path("docs/fantasy-state.json")
FREE_AGENT_LIMIT = int(os.getenv("FREE_AGENT_LIMIT", "250"))

# Fields we explicitly permit from ESPN objects. We never dump raw objects/responses.
PLAYER_FIELDS = (
    "playerId", "name", "position", "proTeam", "eligibleSlots", "lineupSlot",
    "injuryStatus", "injured", "posRank", "percent_owned", "percent_started",
    "total_points", "avg_points", "projected_total_points", "projected_avg_points",
    "acquisitionType", "onTeamId",
)

SENSITIVE_KEY_RE = re.compile(
    r"(cookie|authorization|token|secret|password|passwd|espn_s2|swid|session)",
    re.IGNORECASE,
)


def pick(obj: Any, field: str, default=None):
    try:
        value = getattr(obj, field)
    except Exception:
        return default
    return value if value is not None else default


def clean_scalar(value: Any):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [clean_scalar(v) for v in value]
    if isinstance(value, dict):
        return {str(k): clean_scalar(v) for k, v in value.items()}
    return str(value)


def serialize_player(player: Any) -> dict:
    out = {}
    for field in PLAYER_FIELDS:
        value = pick(player, field)
        if value is not None:
            out[field] = clean_scalar(value)

    # Current-week actual/projected points are useful for lineup analysis.
    stats = pick(player, "stats", {}) or {}
    current_week = CURRENT_WEEK
    week_stats = stats.get(current_week) or stats.get(str(current_week)) or {}
    if isinstance(week_stats, dict):
        if "points" in week_stats:
            out["week_points"] = clean_scalar(week_stats.get("points"))
        if "projected_points" in week_stats:
            out["week_projected_points"] = clean_scalar(week_stats.get("projected_points"))

    return out


def serialize_team(team: Any) -> dict:
    # Deliberately omit team.owners. ESPN private leagues can expose real names/profile data.
    return {
        "team_id": pick(team, "team_id"),
        "team_name": pick(team, "team_name"),
        "team_abbrev": pick(team, "team_abbrev"),
        "wins": pick(team, "wins", 0),
        "losses": pick(team, "losses", 0),
        "ties": pick(team, "ties", 0),
        "points_for": pick(team, "points_for", 0),
        "points_against": pick(team, "points_against", 0),
        "standing": pick(team, "standing"),
        "waiver_rank": pick(team, "waiver_rank"),
        "acquisitions": pick(team, "acquisitions"),
        "drops": pick(team, "drops"),
        "trades": pick(team, "trades"),
        "roster": [serialize_player(p) for p in (pick(team, "roster", []) or [])],
    }


def serialize_matchup(matchup: Any) -> dict:
    home = pick(matchup, "home_team")
    away = pick(matchup, "away_team")
    return {
        "home_team_id": pick(home, "team_id") if home else None,
        "home_team_name": pick(home, "team_name") if home else None,
        "home_score": pick(matchup, "home_score"),
        "away_team_id": pick(away, "team_id") if away else None,
        "away_team_name": pick(away, "team_name") if away else None,
        "away_score": pick(matchup, "away_score"),
    }


def previous_json() -> dict:
    if not OUTPUT_PATH.exists():
        return {}
    try:
        return json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def roster_map(data: dict) -> dict[int, set[int]]:
    result = {}
    for team in data.get("teams", []):
        tid = team.get("team_id")
        if tid is None:
            continue
        ids = {
            p.get("playerId") for p in team.get("roster", [])
            if p.get("playerId") is not None
        }
        result[int(tid)] = ids
    return result


def player_lookup(data: dict) -> dict[int, str]:
    result = {}
    for team in data.get("teams", []):
        for p in team.get("roster", []):
            if p.get("playerId") is not None:
                result[int(p["playerId"])] = p.get("name", str(p["playerId"]))
    for p in data.get("free_agents", []):
        if p.get("playerId") is not None:
            result[int(p["playerId"])] = p.get("name", str(p["playerId"]))
    return result


def calculate_changes(old: dict, new: dict) -> list[dict]:
    if not old:
        return [{"type": "initial_snapshot"}]

    changes = []
    old_rosters = roster_map(old)
    new_rosters = roster_map(new)
    names = player_lookup(old)
    names.update(player_lookup(new))

    team_names = {
        int(t["team_id"]): t.get("team_name")
        for t in new.get("teams", [])
        if t.get("team_id") is not None
    }

    for tid in sorted(set(old_rosters) | set(new_rosters)):
        added = new_rosters.get(tid, set()) - old_rosters.get(tid, set())
        dropped = old_rosters.get(tid, set()) - new_rosters.get(tid, set())
        for pid in sorted(added):
            changes.append({
                "type": "roster_add",
                "team_id": tid,
                "team_name": team_names.get(tid),
                "player_id": pid,
                "player_name": names.get(pid),
            })
        for pid in sorted(dropped):
            changes.append({
                "type": "roster_drop",
                "team_id": tid,
                "team_name": team_names.get(tid),
                "player_id": pid,
                "player_name": names.get(pid),
            })

    old_fa = {p.get("playerId") for p in old.get("free_agents", []) if p.get("playerId") is not None}
    new_fa = {p.get("playerId") for p in new.get("free_agents", []) if p.get("playerId") is not None}

    for pid in sorted((new_fa - old_fa))[:50]:
        changes.append({"type": "entered_available_pool", "player_id": pid, "player_name": names.get(pid)})
    for pid in sorted((old_fa - new_fa))[:50]:
        changes.append({"type": "left_available_pool", "player_id": pid, "player_name": names.get(pid)})

    return changes


def assert_safe(data: Any):
    """Fail closed if any sensitive-looking key or exact credential value appears."""
    serialized = json.dumps(data, ensure_ascii=False)

    # Exact-value checks catch accidental inclusion even under an unexpected key.
    for secret in (ESPN_S2, ESPN_SWID):
        if secret and secret in serialized:
            raise RuntimeError("SECURITY CHECK FAILED: an ESPN credential appeared in output")

    def walk(value: Any, path="root"):
        if isinstance(value, dict):
            for k, v in value.items():
                if SENSITIVE_KEY_RE.search(str(k)):
                    raise RuntimeError(f"SECURITY CHECK FAILED: sensitive-looking key at {path}.{k}")
                walk(v, f"{path}.{k}")
        elif isinstance(value, list):
            for i, item in enumerate(value):
                walk(item, f"{path}[{i}]")
    walk(data)


league = League(
    league_id=LEAGUE_ID,
    year=SEASON,
    espn_s2=ESPN_S2,
    swid=ESPN_SWID,
)

CURRENT_WEEK = int(pick(league, "current_week", 1) or 1)
NFL_WEEK = int(pick(league, "nfl_week", CURRENT_WEEK) or CURRENT_WEEK)

teams = [serialize_team(t) for t in league.teams]
free_agents = [serialize_player(p) for p in league.free_agents(
    week=CURRENT_WEEK,
    size=FREE_AGENT_LIMIT,
)]

try:
    matchups = [serialize_matchup(m) for m in league.scoreboard(week=CURRENT_WEEK)]
except Exception as exc:
    print(f"Warning: could not export scoreboard: {exc}", file=sys.stderr)
    matchups = []

settings = pick(league, "settings")
settings_out = {}
if settings is not None:
    # Only low-risk league configuration fields. No raw object dumping.
    for key in (
        "name", "reg_season_count", "team_count", "playoff_team_count",
        "trade_deadline", "faab", "acquisition_budget", "veto_votes_required",
    ):
        value = pick(settings, key)
        if value is not None:
            settings_out[key] = clean_scalar(value)

now = datetime.now(timezone.utc).isoformat()
snapshot = {
    "schema_version": 1,
    "generated_at_utc": now,
    "league": {
        "league_id": LEAGUE_ID,
        "season": SEASON,
        "current_week": CURRENT_WEEK,
        "nfl_week": NFL_WEEK,
        "settings": settings_out,
    },
    "teams": teams,
    "free_agents": free_agents,
    "matchups": matchups,
}

old = previous_json()
snapshot["changes_since_previous_refresh"] = calculate_changes(old, snapshot)

assert_safe(snapshot)

OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
tmp = OUTPUT_PATH.with_suffix(".tmp")
tmp.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
tmp.replace(OUTPUT_PATH)

print(
    f"Wrote {OUTPUT_PATH}: {len(teams)} teams, "
    f"{len(free_agents)} available players, "
    f"{len(snapshot['changes_since_previous_refresh'])} changes"
)
