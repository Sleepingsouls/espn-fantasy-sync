import json
import os
import subprocess
from pathlib import Path

from espn_api.football import League


path = Path("docs/fantasy-state.json")
state = json.loads(path.read_text(encoding="utf-8"))

league = League(
    league_id=int(os.environ["ESPN_LEAGUE_ID"]),
    year=int(os.environ["ESPN_SEASON"]),
    espn_s2=os.environ["ESPN_S2"],
    swid=os.environ["ESPN_SWID"],
)

# Player ID -> name lookup using only information we already publish.
player_names = {}

for team in state.get("teams", []):
    for player in team.get("roster", []):
        player_id = player.get("playerId")
        if player_id is not None:
            player_names[str(player_id)] = player.get("name")

for player in state.get("free_agents", []):
    player_id = player.get("playerId")
    if player_id is not None:
        player_names[str(player_id)] = player.get("name")


def team_fields(team):
    if not team:
        return None, None

    return (
        getattr(team, "team_id", None),
        getattr(team, "team_name", None),
    )


def player_fields(player, fallback_id=None):
    player_id = getattr(player, "playerId", fallback_id)
    player_name = getattr(player, "name", None)

    if player_name is None and player_id is not None:
        player_name = player_names.get(str(player_id))

    if player_name is None and isinstance(player, str):
        player_name = player

    if player_name is None:
        player_name = "Unknown"

    return player_id, player_name


executed = []

current_week = max(
    1,
    int(state.get("league", {}).get("current_week", 1)),
)

# ---------------------------------------------------------
# FREE AGENT + WAIVER MOVES
#
# mTransactions2 gives us an explicit transaction status,
# so we publish EXECUTED only. Pending/failed waiver claims
# never enter the sanitized JSON.
# ---------------------------------------------------------

for scoring_period in range(1, current_week + 1):
    try:
        transactions = league.transactions(
            scoring_period=scoring_period,
            types={"FREEAGENT", "WAIVER"},
        )
    except Exception as exc:
        # espn-api raises when a scoring period has no transactions.
        print(
            f"No add/drop transactions for scoring period "
            f"{scoring_period}: {exc}"
        )
        continue

    for tx in transactions:
        if str(getattr(tx, "status", "")).upper() != "EXECUTED":
            continue

        team_id, team_name = team_fields(
            getattr(tx, "team", None)
        )

        actions = []

        for item in getattr(tx, "items", []):
            player_id = getattr(item, "playerId", None)

            player_id, player_name = player_fields(
                getattr(item, "player", None),
                fallback_id=player_id,
            )

            actions.append(
                {
                    "team_id": team_id,
                    "team_name": team_name,
                    "action": getattr(
                        item,
                        "type",
                        "UNKNOWN",
                    ),
                    "player_id": player_id,
                    "player_name": player_name,
                }
            )

        executed.append(
            {
                "type": getattr(
                    tx,
                    "type",
                    "UNKNOWN",
                ),
                "status": "EXECUTED",
                "scoring_period": getattr(
                    tx,
                    "scoring_period",
                    scoring_period,
                ),
                "date_epoch_ms": getattr(
                    tx,
                    "date",
                    None,
                ),
                "bid_amount": getattr(
                    tx,
                    "bid_amount",
                    None,
                ),
                "actions": actions,
            }
        )


# ---------------------------------------------------------
# TRADES
#
# ESPN's mTransactions2 trade records can omit the actual
# players involved. recent_activity preserves completed
# trade activity and both teams, so use that for trades.
# ---------------------------------------------------------

offset = 0
page_size = 25

for _ in range(20):  # maximum 500 trade activity records
    batch = league.recent_activity(
        size=page_size,
        msg_type="TRADED",
        offset=offset,
    )

    if not batch:
        break

    for activity in batch:
        actions = []

        for action_tuple in getattr(
            activity,
            "actions",
            [],
        ):
            team = (
                action_tuple[0]
                if len(action_tuple) > 0
                else None
            )

            action = (
                action_tuple[1]
                if len(action_tuple) > 1
                else "TRADED"
            )

            player = (
                action_tuple[2]
                if len(action_tuple) > 2
                else None
            )

            team_id, team_name = team_fields(team)
            player_id, player_name = player_fields(
                player
            )

            actions.append(
                {
                    "team_id": team_id,
                    "team_name": team_name,
                    "action": action,
                    "player_id": player_id,
                    "player_name": player_name,
                }
            )

        executed.append(
            {
                "type": "TRADE",
                "status": "EXECUTED",
                "scoring_period": None,
                "date_epoch_ms": getattr(
                    activity,
                    "date",
                    None,
                ),
                "bid_amount": None,
                "actions": actions,
            }
        )

    if len(batch) < page_size:
        break

    offset += page_size


# ---------------------------------------------------------
# DEDUPE + SORT
# ---------------------------------------------------------

def fingerprint(transaction):
    return json.dumps(
        transaction,
        sort_keys=True,
        separators=(",", ":"),
    )


unique = {}

for transaction in executed:
    unique[fingerprint(transaction)] = transaction

executed = list(unique.values())

executed.sort(
    key=lambda transaction: (
        transaction.get("date_epoch_ms") or 0
    ),
    reverse=True,
)


# ---------------------------------------------------------
# LOAD PREVIOUS COMMITTED STATE
#
# This lets us explicitly expose only the transactions
# that appeared since the previous GitHub refresh.
# ---------------------------------------------------------

previous = None

try:
    previous_text = subprocess.check_output(
        [
            "git",
            "show",
            "HEAD:docs/fantasy-state.json",
        ],
        text=True,
        stderr=subprocess.DEVNULL,
    )

    previous = json.loads(previous_text)

except Exception:
    previous = None


state["transactions"] = {
    "count": len(executed),
    "executed": executed,
}


changes = state.get(
    "changes_since_previous_refresh"
)

if not isinstance(changes, dict):
    changes = {}


# First transaction-enabled run establishes a baseline.
# We don't want the entire preseason history reported as
# "new" the first time we turn this on.
if (
    previous
    and isinstance(
        previous.get("transactions"),
        dict,
    )
):
    previous_keys = {
        fingerprint(transaction)
        for transaction
        in previous["transactions"].get(
            "executed",
            [],
        )
    }

    changes["transactions_added"] = [
        transaction
        for transaction in executed
        if fingerprint(transaction)
        not in previous_keys
    ]

else:
    changes["transactions_added"] = []


state["changes_since_previous_refresh"] = changes

path.write_text(
    json.dumps(
        state,
        indent=2,
        sort_keys=False,
    )
    + "\n",
    encoding="utf-8",
)


print(
    f"Added {len(executed)} "
    f"executed transaction records."
)

print(
    "New since previous refresh: "
    f"{len(changes['transactions_added'])}"
)
