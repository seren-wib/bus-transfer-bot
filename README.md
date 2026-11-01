# bus-transfer-bot

Finds the fastest bus transfer point by re-checking every intermediate stop with real-time arrivals, using a fleet of Grok Bots.

Map apps pick one transfer stop and show the average headway. The real wait there can be 20 minutes. This project treats every stop on the first bus as a transfer candidate, pulls real-time arrivals for each, and recommends the one that actually gets you there first — including moving between platforms inside a bus hub.

Built at the Grok Bot hackathon on 2026-11-01.

## How it works

1. An **orchestrator bot** searches A→B on Kakao Map, records the app's suggestion, and lists every stop on the first bus.
2. Three **search bots** each take a slice of those stops. For each stop they search stop→B, read the real-time arrival of the connecting bus, and write a JSON result.
3. The orchestrator merges the results, computes `ride + platform walk + real wait + ride`, and picks the minimum.

## Repository layout

| Path | Owner | Purpose |
| --- | --- | --- |
| `docs/requirements.md` | — | Requirements and bot contract (Korean) |
| `tasks/bot{n}.json` | orchestrator | Stops assigned to each search bot |
| `results/{stop}.json` | search bot | Candidates found for one stop |

## Scope

Bus only, first transfer only, platform moves inside a hub only. See `docs/requirements.md` for the full in/out list.
