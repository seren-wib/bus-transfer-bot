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

# bus-optimizer (2026-11-01)

요구사항: docs/requirements.md · 진행: docs/progress.md · API 메모: docs/api_notes.md · 카카오 비교: docs/kakao_comparison.md

- `naver.py`: NaverMap 클라이언트. 헤드리스 Playwright Chromium의 요청 컨텍스트로 네이버 JSON API를 부르고, asyncio로 동시에 처리한다.
  - `directions(start, goal, departure=, bus_only=, with_arrivals=, running_only=True)`
    - start: (lat,lon) GPS | {'stop_id':N} | {'lat','lon','placeid'}
    - 지금 운행하지 않는 노선은 기본으로 완전히 뺀다(`filter_running`, `name_not_running`, `schedule_not_running`).
  - `arrivals(stop_id)`, `stop(stop_id)`, `route(route_id)`, `route_detail(route_id)`, `nearby_transfer_options(...)`
- `search_bot.py`: 검색 워커(R3/R4/R5/R9). 실행은 `python search_bot.py tasks/bot1.json --goal LON,LAT[,placeid=N]`이고 `results/{si}.json`을 쓴다.
- `make_tasks.py`: 테스트용 작업 파일 생성기. 노선 X의 s0 이후 정류장, A→si 분, arrive_at_si를 만든다.
- `summarize_results.py`: tasks와 results를 합쳐 A→B 총분 순위를 출력하고 `results/summary.json`을 쓴다.
- `gmaps.py`: 구글맵 대중교통 결과를 DOM에서 읽는다.
- `test_naver.py`: 1단계 측정. 원본 덤프는 `results/raw/`에 쓴다(git 제외).
- `tests/test_filters.py`: 운행하지 않는 노선 제외 테스트. 일요일 새벽A741 제외를 확인한다.
