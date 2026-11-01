"""Search bot (R3, R4, R5, R9) on top of naver.py.

For each task stop si: find platforms within 500 m walk (Naver router + same stop), single-bus (no further
transfer) routes to the goal, and the wait for line Y at the moment the user reaches that platform.

Rules: walk 5 km/h (83.3 m/min) recomputed from metres; transfer walk <= 500 m; only first transfer;
no safety margin. Lines not running at boarding time are dropped entirely (naver.filter_running: name notes,
service calendar, first/last bus; plus here: no live bus and no headway / non-RUNNING status) and never written.

CLI: python search_bot.py tasks/bot1.json --goal 126.9768428,37.5760259,placeid=13161322 [--out results]
"""
from __future__ import annotations
import argparse, asyncio, json, math, re, sys, time
from datetime import datetime
from pathlib import Path
from naver import NaverMap, KST, WALK_M_PER_MIN, GWANGHWAMUN, parse_arrival, name_not_running

MAX_WALK_M = 500
MAX_EXTRA_PLATFORMS = 4


def walk_min(m):
    return round((m or 0) / WALK_M_PER_MIN, 1)


def _parse_iso(s):
    d = datetime.fromisoformat(s)
    return d if d.tzinfo else d.replace(tzinfo=KST)


def _headway_min(s):
    nums = [int(x) for x in re.findall(r"\d+", s or "")]
    return sum(nums) / len(nums) if nums else None


def line_not_running(name: str, when: datetime) -> bool:
    return name_not_running(name, when) is not None


def compute_wait(arr: dict | None, offset_min: float):
    """offset_min = minutes from the arrival reading's referenceTime until the user stands at the platform.
    Returns (wait_min, source) or (None, reason)."""
    if not arr:
        return None, "no_arrival_data"
    buses = sorted([b["min"] for b in arr.get("buses", []) if b.get("min") is not None])
    head = _headway_min(arr.get("interval"))
    if not buses and arr.get("status") not in (None, "RUNNING"):
        return None, f"not_running:{arr.get('status')}"
    for i, m in enumerate(buses):
        if m >= offset_min:
            return round(m - offset_min, 1), "realtime" if i == 0 else "realtime_2nd"
    if buses and head:  # all tracked buses pass before we arrive -> extrapolate with headway
        last = buses[-1]
        k = math.ceil((offset_min - last) / head)
        return round(last + k * head - offset_min, 1), "realtime+headway"
    if head:
        return round(head / 2, 1), "schedule"  # no live position, expected wait = half headway
    return None, "no_realtime_no_headway"


def _single_bus_routes(routes):
    out = []
    for r in routes:
        transit = [s for s in r["steps"] if s["mode"] != "WALKING"]
        if len(transit) == 1 and transit[0]["mode"] == "BUS" and r["bus_only"]:
            out.append(r)
    return out


def _first_walk_m(r):
    s = r["steps"][0]
    return (s["distance_m"] or 0) if s["mode"] == "WALKING" else 0


def _final_walk_m(r):
    s = r["steps"][-1]
    return (s["distance_m"] or 0) if s["mode"] == "WALKING" else 0


async def process_stop(si_task: dict, goal, nm: NaverMap) -> list[dict]:
    now = datetime.now(KST)
    arrive_at_si = _parse_iso(si_task["arrive_at_si"])
    si_id = si_task["stop_id"]
    si = await nm.stop(si_id)
    # 1) router from si: platforms within 500 m + single-bus routes
    routes = await nm.directions((si["lat"], si["lon"]), goal, departure=arrive_at_si, bus_only=True,
                                 with_arrivals=False)
    platform_walk = {si_id: 0}
    for r in routes:
        b = next((s for s in r["steps"] if s["mode"] == "BUS"), None)
        w = _first_walk_m(r)
        if b and w <= MAX_WALK_M:
            pid = b["board"]["id"]
            platform_walk[pid] = min(platform_walk.get(pid, 10 ** 9), w)
    # 2) also search from the other platforms (hub / across-the-road) to catch their own direct lines
    extra = sorted([p for p in platform_walk if p != si_id], key=platform_walk.get)[:MAX_EXTRA_PLATFORMS]
    extra_info = await asyncio.gather(*[nm.stop(p) for p in extra], return_exceptions=True)
    extra_routes = await asyncio.gather(*[
        nm.directions((x["lat"], x["lon"]), goal, departure=arrive_at_si, bus_only=True, with_arrivals=False)
        for x in extra_info if not isinstance(x, Exception)], return_exceptions=True)
    pool = [(r, None) for r in routes]
    for x, rs in zip([x for x in extra_info if not isinstance(x, Exception)], extra_routes):
        if isinstance(rs, Exception):
            continue
        pool += [(r, x["id"]) for r in rs]
    # 3) candidates = single-bus routes boarding at a known platform
    cands = []
    for r, origin_pid in pool:
        for rr in _single_bus_routes([r]):
            step = next(s for s in rr["steps"] if s["mode"] == "BUS")
            pid = step["board"]["id"]
            if pid not in platform_walk:
                continue
            if origin_pid is not None and pid != origin_pid:
                continue  # walk would be measured from another platform; skip
            wm = platform_walk[pid]
            for line in step["lines"]:
                if line_not_running(line["name"], arrive_at_si):
                    continue
                cands.append({"platform_stop_id": pid, "platform_name": step["board"]["name"],
                              "ars": step["board"]["ars"], "type": line["type"], "walk_m": wm,
                              "walk_min": walk_min(wm), "line_Y": line["name"], "line_Y_id": line["id"],
                              "si_to_B_ride_min": step["minutes"], "alight_name": step["alight"]["name"],
                              "final_walk_m": _final_walk_m(rr), "final_walk_min": walk_min(_final_walk_m(rr))})
    # dedupe (platform, line) keep shortest ride+final walk
    best = {}
    for c in cands:
        k = (c["platform_stop_id"], c["line_Y_id"])
        if k not in best or c["si_to_B_ride_min"] + c["final_walk_min"] < best[k]["si_to_B_ride_min"] + best[k]["final_walk_min"]:
            best[k] = c
    cands = list(best.values())
    if not cands:
        return []
    # 4) realtime arrivals for every (platform, Y) in one multi call (chunked)
    pairs = list({(c["platform_stop_id"], c["line_Y_id"]) for c in cands})
    arr_by = {}
    for i in range(0, len(pairs), 30):
        chunk = ":".join(f"{a},{b}" for a, b in pairs[i:i + 30])
        res = await nm._get(f"/pubtrans/realtime/bus/arrivals/multi?stations={chunk}&base=realtime,schedule",
                            "arrivals_multi")
        for a in res:
            arr_by[(a["stopId"], a["id"])] = parse_arrival(a)
    read_at = datetime.now(KST).isoformat(timespec="seconds")
    out = []
    for c in cands:
        a = arr_by.get((c["platform_stop_id"], c["line_Y_id"]))
        ref = _parse_iso(a["reference"]) if a and a.get("reference") else now
        at_platform = arrive_at_si.timestamp() + c["walk_min"] * 60
        offset = (at_platform - ref.timestamp()) / 60
        w, src = compute_wait(a, offset)
        c.update({"wait_min": w, "wait_source": src, "arrival_offset_min": round(offset, 1),
                  "live_buses_min": [b["min"] for b in a["buses"]] if a else [],
                  "headway": a.get("interval") if a else None, "read_at": read_at})
        if w is None:  # no live bus and no schedule (or status not RUNNING) -> treat as not running, drop
            nm.dropped.append({"line": c["line_Y"], "platform": c["platform_name"], "why": src})
            continue
        c["si_to_B_total_min"] = round(c["walk_min"] + w + c["si_to_B_ride_min"] + c["final_walk_min"], 1)
        out.append(c)
    out.sort(key=lambda c: c["si_to_B_total_min"])
    return out


def _parse_goal(s: str | None):
    if not s:
        return GWANGHWAMUN
    parts = s.split(",")
    g = {"lon": float(parts[0]), "lat": float(parts[1])}
    for p in parts[2:]:
        if p.startswith("placeid="):
            g["placeid"] = int(p.split("=")[1])
    return g


async def run(tasks_path: str, goal, out_dir: str = "results", concurrency: int = 8):
    tasks = json.load(open(tasks_path))
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    summary = []
    async with NaverMap(concurrency=concurrency) as nm:
        async def one(t):
            ts = time.perf_counter()
            try:
                res = await process_stop(t, goal, nm)
                Path(out_dir, f"{t['si']}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1))
                return {"si": t["si"], "ok": True, "n": len(res), "sec": round(time.perf_counter() - ts, 2)}
            except Exception as e:  # N3: one failure must not kill the run
                Path(out_dir, f"{t['si']}.error.json").write_text(
                    json.dumps({"si": t["si"], "error": repr(e)}, ensure_ascii=False))
                return {"si": t["si"], "ok": False, "error": repr(e)[:200], "sec": round(time.perf_counter() - ts, 2)}
        summary = await asyncio.gather(*[one(t) for t in tasks])
    total = time.perf_counter() - t0
    return summary, total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tasks")
    ap.add_argument("--goal", default=None, help="lon,lat[,placeid=N] (default 광화문)")
    ap.add_argument("--out", default="results")
    a = ap.parse_args()
    summary, total = asyncio.run(run(a.tasks, _parse_goal(a.goal), a.out))
    for s in summary:
        print(s)
    print(f"{a.tasks}: {len(summary)} stops in {total:.2f}s (incl. browser start)")


if __name__ == "__main__":
    main()
