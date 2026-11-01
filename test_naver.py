import asyncio, json, time
from pathlib import Path
from naver import NaverMap, GWANGHWAMUN

def show(r):
    print(f"  #{r['idx']} {r['total_min']}min walk{r['walk_min']} transfers{r['transfers']} {r['labels']} | {r['summary']}")
    for s in r["steps"]:
        for a in (s.get("arrivals") or []):
            b = a["buses"][0] if a["buses"] else None
            print(f"      RT {a['route']}@{a['stop']}: " + (f"{b['min']}min/{b['stops_away']}stops" if b else a["status"]))

async def main():
    out = {}
    t0 = time.perf_counter()
    async with NaverMap(concurrency=8) as nm:
        t1 = time.perf_counter(); print(f"browser launch+warmup {t1-t0:.1f}s")
        routes = await nm.directions((37.504935, 127.064147), GWANGHWAMUN, bus_only=True)
        t2 = time.perf_counter(); print(f"A->광화문 directions+arrivals {t2-t1:.2f}s, bus-only routes={len(routes)}")
        for r in routes: show(r)
        out["A"] = routes
        r740 = await nm.route(1273, after_stop_name="한국무역센터.삼성역")
        cands = r740["next_stops"][:8]
        print("740 next 8:", [(s["name"], s["ars"]) for s in cands])
        t3 = time.perf_counter()
        res = await asyncio.gather(*[nm.directions({"stop_id": s["id"]}, GWANGHWAMUN, bus_only=True) for s in cands])
        t4 = time.perf_counter(); print(f"\nBATCH 8 stops stop->광화문 concurrent: {t4-t3:.2f}s")
        for s, rs in zip(cands, res):
            best = min(rs, key=lambda r: r["total_min"]) if rs else None
            print(f"- {s['name']}({s['ars']}): {len(rs)} bus routes; best " + (f"{best['total_min']}min | {best['summary']}" if best else "none"))
        out["batch8"] = {s["name"]: rs for s, rs in zip(cands, res)}
        # 500 m walking transfer options for the stops the parent flagged + whole 740 run to 반포대교남단
        full = r740["next_stops"][:15]
        t5 = time.perf_counter()
        res2 = await asyncio.gather(*[nm.nearby_transfer_options(s["id"], GWANGHWAMUN, 500) for s in full])
        t6 = time.perf_counter(); print(f"\nBATCH 15 stops nearby(<=500m) options concurrent: {t6-t5:.2f}s")
        for (st, rs) in res2:
            boards = {}
            for r in rs:
                b = next(s for s in r["steps"] if s["mode"] == "BUS")
                key = f"{b['board']['name']}({b['board']['ars']}) walk{r['first_walk_m']}m"
                boards.setdefault(key, []).append(f"{'/'.join(l['name'] for l in b['lines'])}:{r['total_min']}m")
            print(f"- {st['name']}({st['ars']}):")
            for k, v in boards.items(): print(f"     {k}: {', '.join(v[:4])}")
        out["nearby15"] = [{"stop": st, "routes": rs} for st, rs in res2]
        print(f"\narrivals(93596) sample:")
        t7 = time.perf_counter(); arr = await nm.arrivals(93596); print(f"  {time.perf_counter()-t7:.2f}s, {len(arr)} lines; e.g.", arr[0])
        import statistics as stt
        for tag in sorted({t for t, _ in nm.timings}):
            v = [d for t, d in nm.timings if t == tag]
            print(f"timing {tag}: n={len(v)} median {stt.median(v):.2f}s max {max(v):.2f}s")
    Path("results/raw").mkdir(parents=True, exist_ok=True); json.dump(out, open("results/raw/test_output.json", "w"), ensure_ascii=False, indent=1)

asyncio.run(main())
