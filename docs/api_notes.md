# 지도 API 메모 (2026-11-01 조사)

직접 호출해 확인한 내용을 정리했다. 원본 응답과 탐색 스크립트는 저장소 밖(/workspace/probes-archive/)에 둔다.

## 네이버 지도 (계산용)

공통: 모두 `https://map.naver.com/p/api` 아래 GET 요청이다. 로그인은 필요 없다. 좌표는 `경도,위도` 순서로 넣는다. naver.py는 헤드리스 Chromium으로 map.naver.com/p를 한 번 연 뒤, 같은 컨텍스트의 요청 객체로 호출한다. 질의 1회는 0.6~0.8초, 동시 8개까지 돌린다.

| 용도 | 경로 | 주요 파라미터 | 쓰는 값 |
|---|---|---|---|
| 대중교통 경로 | `/directions/transit` | `start=LON,LAT[,placeid=ID]`, `goal=…`, `mode=TIME`, `departureTime=YYYY-MM-DDTHH:MM:SS+09:00` | paths[].legs[0].steps[] |
| 실시간 도착(정류장) | `/pubtrans/realtime/bus/arrivals` | `stopId`, `base=realtime,schedule` | 노선별 도착 |
| 실시간 도착(여러 쌍) | `/pubtrans/realtime/bus/arrivals/multi` | `stations=정류장,노선:정류장,노선…`(30쌍씩), `base=realtime,schedule` | 쌍별 도착 |
| 정류장 | `/pubtrans/bus/stops/{id}` | | 이름, ARS 번호(displayCode), 좌표(point), 정차 노선 |
| 노선 상세 | `/pubtrans/bus/routes/{id}` | | 정류장 순서(busStops), 회차 지점(turningPoint.stopIdx), 운행 달력·첫차/막차(schedule), 유형(type.name) |

### 경로 응답에서 쓰는 필드
- path: duration(ms), walkingDuration, waitingDuration, transferCount, pathLabels.
- step.type: WALKING / BUS / SUBWAY. distance(m), duration(ms), departureTime.
- BUS step의 routes[]: 같은 구간을 함께 도는 노선들(id, name, type.name = 간선/지선/광역/직행 등).
- BUS step의 stops[]: 첫 원소가 승차, 마지막이 하차. 정류장별 duration(ms)을 누적하면 정류장별 승차 시간이 된다. displayCodes[0]은 ARS 번호, pointIndex는 step.points 좌표 인덱스.
- 주의: 도보 분은 쓰지 않는다. distance(m)을 83.3m/분으로 직접 나눈다.
- 주의: 지금 운행하지 않는 노선도 경로에 나온다. 예를 들어 일요일 13:30 검색에 "새벽A741(자율주행)(토,일 미운행)"이 나왔는데, 탑승 시각은 다음 날 05:09로 잡혀 있었다. 그래서 naver.filter_running으로 걸러 낸다(requirements R4).

### 실시간 도착 응답
- arrival.buses[]: remainingTime(초), remainingStop, plateNo, congestion.desc. 보통 앞의 2대만 준다.
- arrival.referenceTime: 남은 시간의 기준 시각.
- arrival.status: 관측한 값은 RUNNING만 있었다.
- interval.message.intervalCount: 배차간격 문자열(예: "13분", "6-10분").

### 노선 상세 schedule
- startPointTimes[]: 달력별 firstTime/lastTime. calendar.dows는 요일 비트다(일=1, 월=2 … 토=64, 평일=62).
- intervals[]: 달력별 배차 min/max.

### 막힌 것
- 브라우저 밖에서 장소 검색(search/all)을 부르면 캡차가 뜬다. 근처 정류장 검색 API는 따로 없다. 그래서 500m 안 승강장은 정류장 좌표에서 경로 검색을 해 첫 도보 500m 이하인 탑승 정류장으로 찾는다.

## 카카오맵 (비교용)
- 경로 API(`/route/pubtrans.json`, 좌표는 WCONGNAMUL)는 요청 헤더에 작업증명 토큰(x-kmap-captcha-token)을 요구한다. 토큰이 없으면 404 페이지로 넘어간다. 헤드리스 브라우저에서는 지도 SDK도 로드되지 않았다. 그래서 실제 브라우저 화면에서 읽는다.
- 좌표 딥링크: `https://map.kakao.com/?map_type=TYPE_MAP&target=traffic&rt=X1,Y1,X2,Y2&rt1=출발이름&rt2=도착이름` (X/Y는 WCONGNAMUL).

## 구글맵 (비교용)
- `https://www.google.com/maps/dir/LAT,LON/LAT,LON/data=!4m2!4m1!3e3?hl=ko`(3e3 = 대중교통)를 열어 `[data-trip-index]` 카드의 텍스트를 읽는다(gmaps.py, 1.2~2초).
