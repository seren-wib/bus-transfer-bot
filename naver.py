"""Naver Map public-transit client (headless Playwright, JSON APIs).

Endpoints (found by intercepting map.naver.com page traffic, 2026-11-01):
  GET /p/api/directions/transit?start=LON,LAT[,name=..][,placeid=..]&goal=LON,LAT[,placeid=..]&mode=TIME&departureTime=ISO
      -> {"paths":[...]}  every path has legs[0].steps[] (WALKING/BUS/SUBWAY...), each transit step has
         routes[] (alternative lines), stops[] (boarding..alighting with id, ARS displayCodes, per-stop
         duration ms, arrivalTime, pointIndex into step.points[] -> coords).
  GET /p/api/pubtrans/realtime/bus/arrivals?stopId=ID&base=realtime,schedule     -> arrivals at one stop
  GET /p/api/pubtrans/realtime/bus/arrivals/multi?stations=STOP,ROUTE:STOP,ROUTE -> arrivals for pairs
  GET /p/api/pubtrans/bus/stops/ID   -> stop info (name, ARS displayCode, point{x,y}, busRoutes[])
Plain lon/lat works for start/goal, so raw GPS can be used directly. A bus stop as origin = its coords.
"""
from __future__ import annotations
import asyncio, math, time, urllib.parse
from datetime import datetime, timezone, timedelta
from playwright.async_api import async_playwright

KST = timezone(timedelta(hours=9))
BASE = "https://map.naver.com/p/api"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")
WALK_M_PER_MIN = 5000 / 60  # 5 km/h
GWANGHWAMUN = {"lon": 126.9768428, "lat": 37.5760259, "placeid": 13161322, "name": "광화문"}


def haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _point_param(p) -> str:
    """p: (lat, lon) tuple | {'lat','lon',['placeid'],['name']} | raw 'lon,lat,...' string."""
    if isinstance(p, str):
        return p
    if isinstance(p, (tuple, list)):
        lat, lon = p
        return f"{lon},{lat}"
    s = f"{p['lon']},{p['lat']}"
    if p.get("placeid"):
        s += f",placeid={p['placeid']}"
    return s


def _ms_min(ms):
    return round((ms or 0) / 60000, 1)


def parse_path(p: dict) -> dict:
    steps = []
    for s in p["legs"][0]["steps"]:
        mode = s["type"]
        st = {"mode": mode, "distance_m": s.get("distance"), "minutes": _ms_min(s.get("duration")),
              "dep": s.get("departureTime"), "arr": s.get("arrivalTime")}
        if mode == "WALKING":
            st["walk_min_5kmh"] = round((s.get("distance") or 0) / WALK_M_PER_MIN, 1)
        else:
            pts = s.get("points") or []
            st["lines"] = [{"id": r["id"], "name": r["name"], "type": (r.get("type") or {}).get("name"),
                            "realtime": r.get("realtime")} for r in s.get("routes", [])]
            stops, cum = [], 0
            for x in s.get("stops", []):
                cum += x.get("duration") or 0
                pt = pts[x["pointIndex"]] if x.get("pointIndex") is not None and x["pointIndex"] < len(pts) else {}
                stops.append({"id": x["id"], "name": x["name"], "ars": (x.get("displayCodes") or [None])[0],
                              "ride_min_from_board": _ms_min(cum), "eta": x.get("arrivalTime"),
                              "lon": pt.get("x"), "lat": pt.get("y")})
            st["stops"] = stops
            st["board"] = stops[0] if stops else None
            st["alight"] = stops[-1] if stops else None
            st["arrivals"] = None  # filled by attach_arrivals
        steps.append(st)
    transit = [s for s in steps if s["mode"] != "WALKING"]
    return {"idx": p["idx"], "labels": [l["labelText"] for l in p.get("pathLabels", [])],
            "type": p["type"], "total_min": _ms_min(p["duration"]), "walk_min": _ms_min(p.get("walkingDuration")),
            "wait_min": _ms_min(p.get("waitingDuration")), "transfers": p.get("transferCount"),
            "dep": p.get("departureTime"), "arr": p.get("arrivalTime"),
            "bus_only": bool(transit) and all(s["mode"] == "BUS" for s in transit),
            # e.g. '새벽A741(자율주행)(토,일 미운행)' was returned on a Sunday -> flag, caller should drop it
            "suspect_service": any("미운행" in l["name"] or "새벽" in l["name"]
                                   for s in transit for l in s.get("lines", [])),
            "summary": " > ".join(
                (f"walk {s['distance_m']}m" if s["mode"] == "WALKING" else
                 f"{s['mode']} {'/'.join(l['name'] for l in s['lines'])} "
                 f"[{s['board']['name'] if s['board'] else ''}→{s['alight']['name'] if s['alight'] else ''}]")
                for s in steps),
            "steps": steps}


def parse_arrival(a: dict) -> dict:
    arr = a.get("arrival") or {}
    return {"stop_id": a.get("stopId"), "stop": a.get("stopDisplayName"), "route_id": a.get("id"),
            "route": a.get("name"), "direction": a.get("direction"), "status": arr.get("status"),
            "buses": [{"min": round(b["remainingTime"] / 60, 1) if b.get("remainingTime") is not None else None,
                       "stops_away": b.get("remainingStop"), "plate": b.get("plateNo"),
                       "congestion": (b.get("congestion") or {}).get("desc")} for b in arr.get("buses", [])],
            "interval": ((a.get("interval") or {}).get("message") or {}).get("intervalCount"),
            "reference": arr.get("referenceTime")}


class NaverMap:
    """async with NaverMap(concurrency=8) as nm: routes = await nm.directions((lat,lon), GWANGHWAMUN)"""

    def __init__(self, concurrency: int = 8, headless: bool = True):
        self.concurrency, self.headless = concurrency, headless
        self.timings: list[tuple[str, float]] = []

    async def __aenter__(self):
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=self.headless)
        self._ctx = await self._browser.new_context(locale="ko-KR", user_agent=UA,
                                                    extra_http_headers={"Referer": "https://map.naver.com/"})
        # warm-up: load the map shell once so the context carries normal cookies
        page = await self._ctx.new_page()
        try:
            await page.goto("https://map.naver.com/p", wait_until="domcontentloaded", timeout=20000)
        except Exception:
            pass
        await page.close()
        self._sem = asyncio.Semaphore(self.concurrency)
        return self

    async def __aexit__(self, *a):
        await self._browser.close()
        await self._pw.stop()

    async def _get(self, path: str, tag: str, retries: int = 2):
        url = BASE + path
        for attempt in range(retries + 1):
            async with self._sem:
                t = time.perf_counter()
                r = await self._ctx.request.get(url, timeout=20000)
                dt = time.perf_counter() - t
            self.timings.append((tag, dt))
            if r.ok:
                return await r.json()
            if attempt == retries:
                raise RuntimeError(f"{r.status} {url}: {(await r.text())[:200]}")
            await asyncio.sleep(0.5 * (attempt + 1))

    # ---- raw ----
    async def directions_raw(self, start, goal, departure: datetime | None = None) -> dict:
        dep = (departure or datetime.now(KST)).strftime("%Y-%m-%dT%H:%M:%S+09:00")
        q = (f"start={urllib.parse.quote(_point_param(start), safe=',=')}"
             f"&goal={urllib.parse.quote(_point_param(goal), safe=',=')}"
             f"&mode=TIME&departureTime={urllib.parse.quote(dep)}")
        return await self._get(f"/directions/transit?{q}", "directions")

    # ---- public ----
    async def directions(self, start, goal, departure=None, bus_only=False, with_arrivals=True) -> list[dict]:
        """start/goal: (lat,lon) | {'lat','lon','placeid'} | {'stop_id': N} (bus stop as origin)."""
        if isinstance(start, dict) and "stop_id" in start:
            s = await self.stop(start["stop_id"])
            start = (s["lat"], s["lon"])
        raw = await self.directions_raw(start, goal, departure)
        routes = [parse_path(p) for p in raw.get("paths", [])]
        if bus_only:
            routes = [r for r in routes if r["bus_only"]]
        if with_arrivals and routes:
            await self.attach_arrivals(routes)
        return routes

    async def attach_arrivals(self, routes: list[dict]):
        pairs = []
        for r in routes:
            for s in r["steps"]:
                if s["mode"] == "BUS" and s["board"]:
                    pairs += [(s["board"]["id"], l["id"]) for l in s["lines"]]
        pairs = list(dict.fromkeys(pairs))
        if not pairs:
            return
        res = []
        for i in range(0, len(pairs), 30):
            chunk = ":".join(f"{a},{b}" for a, b in pairs[i:i + 30])
            res += await self._get(f"/pubtrans/realtime/bus/arrivals/multi?stations={chunk}&base=realtime,schedule",
                                   "arrivals_multi")
        by = {(a["stopId"], a["id"]): parse_arrival(a) for a in res}
        for r in routes:
            for s in r["steps"]:
                if s["mode"] == "BUS" and s["board"]:
                    s["arrivals"] = [by[(s["board"]["id"], l["id"])] for l in s["lines"]
                                     if (s["board"]["id"], l["id"]) in by]

    async def arrivals(self, stop_id: int) -> list[dict]:
        return [parse_arrival(a) for a in
                await self._get(f"/pubtrans/realtime/bus/arrivals?stopId={stop_id}&base=realtime,schedule", "arrivals")]

    async def stop(self, stop_id: int) -> dict:
        d = await self._get(f"/pubtrans/bus/stops/{stop_id}", "stop")
        return {"id": d["id"], "name": d["name"], "ars": d.get("displayCode"), "lon": d["point"]["x"],
                "lat": d["point"]["y"], "direction": d.get("direction"),
                "routes": [{"id": r["id"], "name": r["name"]} for r in d.get("busRoutes", [])]}

    async def nearby_transfer_options(self, stop_id: int, goal, max_walk_m: int = 500, departure=None):
        """From stop `stop_id`, ask the router for goal and keep routes whose first boarding stop is within
        max_walk_m walking (the router's own walk distance). Covers the stop itself and nearby stops
        (e.g. across the road / other platforms). Returns (stop_info, [routes...])."""
        s = await self.stop(stop_id)
        routes = await self.directions((s["lat"], s["lon"]), goal, departure, bus_only=True)
        keep = []
        for r in routes:
            first_walk = r["steps"][0]["distance_m"] if r["steps"][0]["mode"] == "WALKING" else 0
            if (first_walk or 0) <= max_walk_m:
                r["first_walk_m"] = first_walk or 0
                keep.append(r)
        return s, keep

    def stops_within(self, center: dict, stops: list[dict], radius_m: float = 500):
        return [x for x in stops if x.get("lat") and
                haversine_m(center["lat"], center["lon"], x["lat"], x["lon"]) <= radius_m]

    async def gather(self, coros):
        return await asyncio.gather(*coros, return_exceptions=True)


async def _route(self, route_id: int, after_stop_name: str | None = None) -> dict:
    """Bus line detail: ordered stops (both directions, turningPoint marks the turnaround).
    If after_stop_name is given, `next_stops` = stops after the first match of that name."""
    d = await self._get(f"/pubtrans/bus/routes/{route_id}", "route")
    stops = [{"idx": i, "id": b["id"], "name": b["name"], "ars": b.get("displayCode"),
              "lon": b["point"]["x"], "lat": b["point"]["y"]} for i, b in enumerate(d["busStops"])]
    out = {"id": d["id"], "name": d["name"], "start": d.get("startPoint"), "end": d.get("endPoint"),
           "turning_idx": (d.get("turningPoint") or {}).get("stopIdx"), "stops": stops}
    if after_stop_name:
        i = next((s["idx"] for s in stops if s["name"] == after_stop_name), None)
        out["next_stops"] = stops[i + 1:] if i is not None else []
    return out

NaverMap.route = _route
