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

---

# bus-optimizer — map data layer (2026-11-01)

- `naver.py`  NaverMap client (headless Playwright Chromium + Playwright APIRequestContext → Naver JSON APIs). Concurrent via asyncio semaphore.
  - `directions(start, goal, bus_only=, with_arrivals=)` start = (lat,lon) raw GPS | {'stop_id':N} | {'lat','lon','placeid'}
  - `arrivals(stop_id)`, `stop(stop_id)` (coords, ARS, routes), `route(route_id, after_stop_name=)` (full line stop list)
  - `nearby_transfer_options(stop_id, goal, max_walk_m=500)` router-based ≤500 m walk transfer candidates
  - `stops_within(center, stops, radius_m)` haversine filter
- `gmaps.py`  Google Maps transit probe (DOM scrape, ~1.2–2 s/query).
- `test_naver.py` → `results/test_log.txt`, `results/test_output.json`
- `probes/`  raw exploration scripts/samples (Kakao attempts, sample JSON).

Naver endpoints (all GET, no login, plain lon,lat accepted):
- /p/api/directions/transit?start=LON,LAT&goal=LON,LAT[,placeid=ID]&mode=TIME&departureTime=ISO+09:00
- /p/api/pubtrans/realtime/bus/arrivals?stopId=ID&base=realtime,schedule
- /p/api/pubtrans/realtime/bus/arrivals/multi?stations=STOP,ROUTE:STOP,ROUTE&base=realtime,schedule
- /p/api/pubtrans/bus/stops/ID
- /p/api/pubtrans/bus/routes/ROUTE_ID
Kakao: /route/pubtrans.json is gated by a proof-of-work header (x-kmap-captcha-token); headless page also fails to load the map SDK → not working yet.
