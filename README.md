# ESPN Fantasy Sync

A small read-only data pipeline for ESPN Fantasy Football league `1749747321`.

It authenticates to ESPN inside GitHub Actions, extracts fantasy data into a deliberately limited schema, performs credential-leak checks, and publishes only the sanitized JSON file at:

`public/fantasy-state.json`

For the `main` branch, the raw read-only URL will be:

`https://raw.githubusercontent.com/Sleepingsouls/espn-fantasy-sync/main/public/fantasy-state.json`

## Security model

- `ESPN_S2` and `ESPN_SWID` are stored **only** as GitHub Actions repository secrets.
- The Python exporter never serializes raw ESPN API responses or raw Python objects.
- ESPN owner/profile information is deliberately omitted.
- The exporter fails if either exact ESPN credential appears in the output.
- A second independent workflow step scans the output for the exact credential values before committing.
- `.env` files are gitignored.
- This repository contains no code that performs waiver claims, lineup changes, trades, drops, or other ESPN write actions.

Treat `ESPN_S2` and `ESPN_SWID` as login/session credentials. Never paste them into ChatGPT, issues, commits, logs, screenshots, or README files.

## 1. Add the repository secrets

In this GitHub repository:

**Settings → Secrets and variables → Actions → New repository secret**

Create these two secrets:

- `ESPN_S2`
- `ESPN_SWID`

### Finding the values locally

Use a browser where you are already signed into ESPN.

In Firefox:
1. Open `fantasy.espn.com`.
2. Press **F12** and open **Storage**.
3. Expand **Cookies** and select the ESPN/fantasy domain.
4. Locate `espn_s2` and `SWID`.
5. Copy each value directly into the matching GitHub secret.

In Chrome/Edge:
1. Open `fantasy.espn.com`.
2. Press **F12** → **Application**.
3. Under **Storage**, expand **Cookies**.
4. Select the ESPN/fantasy domain.
5. Locate `espn_s2` and `SWID`.
6. Copy each value directly into the matching GitHub secret.

Do not send those values to anyone. GitHub will mask stored secrets in the normal UI after creation.

## 2. Run the first refresh

Open:

**Actions → Refresh ESPN fantasy data → Run workflow**

The action should:
1. authenticate to ESPN;
2. fetch the private league;
3. export all fantasy-team rosters;
4. export up to 250 available/waiver players;
5. export the current matchup scoreboard;
6. compare the snapshot with the previous one;
7. run two credential-leak checks;
8. commit `public/fantasy-state.json`.

## 3. Verify the public output

Open:

`https://raw.githubusercontent.com/Sleepingsouls/espn-fantasy-sync/main/public/fantasy-state.json`

You should see fantasy data but **no** cookies, login information, ESPN owner profile data, or authentication headers.

## Automatic refresh frequency

The GitHub Action is scheduled every four hours. GitHub scheduled workflows can be delayed during periods of heavy Actions load, which is fine for fantasy-football monitoring.

You can also trigger it manually at any time with **Run workflow**.

## What gets published

The sanitized feed includes:

- league ID, season, and current week;
- league settings useful for fantasy analysis;
- all fantasy teams and rosters;
- player position/team/injury/ownership/projection data exposed by `espn-api`;
- up to 250 players currently in ESPN's `FREEAGENT` or `WAIVERS` pool;
- current-week matchups;
- roster/free-agent changes since the previous successful refresh.

It deliberately excludes ESPN owner identity/profile information.

## Cookie expiration

ESPN does not provide an official OAuth integration for this undocumented fantasy API. If the job eventually begins returning an authentication/access error, obtain fresh `espn_s2` and `SWID` values from your browser and replace the two GitHub repository secrets.

## Dependency

This currently pins `espn-api` to `0.46.0` so an upstream package change cannot silently alter the collector. Upgrade intentionally after reviewing release notes.
