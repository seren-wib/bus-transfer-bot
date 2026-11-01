"""Orchestrator R2 test helper: tasks for the stops of line X after boarding stop s0.

a_to_si_min = walk(A->s0, metres from Naver router / 83.3 m/min) + live wait of X at s0 + ride s0->si
(per-stop durations from the directions API). Writes tasks/bot{n}.json split into --bots files.

python make_tasks.py --origin 37.504935,127.064147 --line 1273 --board-ars 23201 --n 15 --bots 3
"""
import argparse, asyncio, json, time
from datetime import datetime, timedelta
from pathlib import Path
from naver import NaverMap, KST, WALK_M_PER_MIN, parse_arrival


def find_ride(routes, line_id, board_id):
    """Return (first_walk_m, {stop_id: ride_min}) from a path whose BUS step carries line X boarding at s0."""
    for r in routes:
        walk = 0
        for s in r["steps"]:
            if s["mode"] == "WALKING":
                walk += s["distance_m"] or 0
                continue
            if s["mode"] == "BUS" and any(l["id"] == line_id for l in s["lines"]) and s["board"]["id"] == board_id:
                return walk, {x["id"]: x["ride_min_from_board"] for x in s["stops"]}
            break
    return None, {}


async def main(a):
    t0 = time.perf_counter()
    lat, lon = map(float, a.origin.split(","))
    async with NaverMap() as nm:
        route = await nm.route(a.line)
        stops = route["stops"]
        bi = next(i for i, s in enumerate(stops) if s["ars"] == a.board_ars)
        s0, nxt = stops[bi], stops[bi + 1: bi + 1 + a.n]
        now = datetime.now(KST)
        # ride times: one query s0 -> last stop, then per-stop queries for any stop not covered
        last = nxt[-1]
        rs = await nm.directions((s0["lat"], s0["lon"]), (last["lat"], last["lon"]), bus_only=True, with_arrivals=False)
        _, ride = find_ride(rs, a.line, s0["id"])
        missing = [s for s in nxt if s["id"] not in ride]
        more = await asyncio.gather(*[nm.directions((s0["lat"], s0["lon"]), (s["lat"], s["lon"]), bus_only=True,
                                                    with_arrivals=False) for s in missing], return_exceptions=True)
        for s, r in zip(missing, more):
            if not isinstance(r, Exception):
                ride.update({k: v for k, v in find_ride(r, a.line, s0["id"])[1].items() if k not in ride})
        # walk A -> s0 from a route A -> last that boards X at s0; fallback straight-line
        ra = await nm.directions((lat, lon), (last["lat"], last["lon"]), bus_only=True, with_arrivals=False)
        walk_m, _ = find_ride(ra, a.line, s0["id"])
        walk_src = "router"
        if walk_m is None:
            from naver import haversine_m
            walk_m, walk_src = round(haversine_m(lat, lon, s0["lat"], s0["lon"]) * 1.3), "haversine*1.3"
        walk = walk_m / WALK_M_PER_MIN
        res = await nm._get(f"/pubtrans/realtime/bus/arrivals/multi?stations={s0['id']},{a.line}&base=realtime,schedule", "arr")
        arr = parse_arrival(res[0]) if res else None
        ref = datetime.fromisoformat(arr["reference"]).replace(tzinfo=KST) if arr and arr["reference"] else now
        off = (now - ref).total_seconds() / 60 + walk
        bus = next((b["min"] for b in (arr or {}).get("buses", []) if b["min"] is not None and b["min"] >= off), None)
        wait = round(bus - off, 1) if bus is not None else None
        if wait is None:
            raise SystemExit(f"no live {route['name']} bus catchable at {s0['name']}: {arr}")
        tasks = []
        for s in nxt:
            if s["id"] not in ride:
                print("no ride time for", s["name"]); continue
            m = round(walk + wait + ride[s["id"]], 1)
            tasks.append({"si": s["ars"] or str(s["id"]), "si_name": s["name"], "stop_id": s["id"], "ars": s["ars"],
                          "a_to_si_min": m, "ride_from_s0_min": ride[s["id"]],
                          "arrive_at_si": (now + timedelta(minutes=m)).isoformat(timespec="seconds")})
    meta = {"generated_at": now.isoformat(timespec="seconds"), "origin": [lat, lon], "line": route["name"],
            "s0": s0["name"], "s0_ars": s0["ars"], "walk_m": walk_m, "walk_src": walk_src, "walk_min": round(walk, 1),
            "live_buses": arr["buses"], "wait_min": wait, "n_tasks": len(tasks), "sec": round(time.perf_counter() - t0, 2)}
    Path("tasks").mkdir(exist_ok=True)
    for b in range(a.bots):
        Path(f"tasks/bot{b + 1}.json").write_text(json.dumps(tasks[b::a.bots], ensure_ascii=False, indent=1))
    Path("tasks/meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    print(json.dumps(meta, ensure_ascii=False))
    for t in tasks:
        print(t["si"], t["si_name"], t["ride_from_s0_min"], t["a_to_si_min"], t["arrive_at_si"])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--origin", default="37.504935,127.064147")
    ap.add_argument("--line", type=int, default=1273)
    ap.add_argument("--board-ars", default="23201")
    ap.add_argument("--n", type=int, default=15)
    ap.add_argument("--bots", type=int, default=3)
    asyncio.run(main(ap.parse_args()))
