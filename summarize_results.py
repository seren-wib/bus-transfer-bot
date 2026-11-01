"""Join tasks/*.json + results/{si}.json -> ranked A->B totals (A->si + si->B). Writes results/summary.json."""
import glob, json, sys
from pathlib import Path

tasks = {t["si"]: t for f in sorted(glob.glob("tasks/bot*.json")) for t in json.load(open(f))}
rows = []
for si, t in tasks.items():
    p = Path(f"results/{si}.json")
    if not p.exists():
        continue
    for c in json.load(open(p)):
        rows.append({"total_min": round(t["a_to_si_min"] + c["si_to_B_total_min"], 1), "si": si,
                     "si_name": t.get("si_name"), "a_to_si_min": t["a_to_si_min"], **{k: c[k] for k in (
                      "platform_name", "ars", "walk_m", "walk_min", "line_Y", "type", "wait_min", "wait_source",
                      "si_to_B_ride_min", "final_walk_m", "final_walk_min", "si_to_B_total_min", "read_at")}})
rows.sort(key=lambda r: r["total_min"])
meta = json.load(open("tasks/meta.json")) if Path("tasks/meta.json").exists() else {}
Path("results/summary.json").write_text(json.dumps({"meta": meta, "ranked": rows}, ensure_ascii=False, indent=1))
for r in rows[: int(sys.argv[1]) if len(sys.argv) > 1 else 10]:
    print(f'{r["total_min"]:5.1f}분 | {r["si_name"]}({r["si"]}) A→si {r["a_to_si_min"]} | 도보 {r["walk_m"]}m→'
          f'{r["platform_name"]}({r["ars"]}) | {r["line_Y"]}[{r["type"]}] 대기 {r["wait_min"]}({r["wait_source"]}) | '
          f'승차 {r["si_to_B_ride_min"]} + 마지막도보 {r["final_walk_m"]}m/{r["final_walk_min"]} | si→B {r["si_to_B_total_min"]}')
