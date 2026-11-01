"""Not-running lines must be dropped (docs/requirements.md R4).
Run: python tests/test_filters.py   (offline unit checks + one live Naver check from 강남역 on today's date)"""
import asyncio, sys
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from naver import KST, GWANGHWAMUN, NaverMap, name_not_running, schedule_not_running

SUN_1330 = datetime(2026, 11, 1, 13, 30, tzinfo=KST)   # Sunday
MON_1330 = datetime(2026, 11, 2, 13, 30, tzinfo=KST)
A741 = "새벽A741(자율주행)(토,일 미운행)"


def test_names():
    assert name_not_running(A741, SUN_1330)                 # dropped on Sunday
    assert name_not_running(A741, MON_1330)                 # dawn-only: also dropped at 13:30 on Monday
    assert name_not_running("N13", SUN_1330)                # night bus in daytime
    assert not name_not_running("N13", SUN_1330.replace(hour=1))
    assert name_not_running("1234(공휴일 미운행)", SUN_1330)
    assert not name_not_running("1234(공휴일 미운행)", MON_1330)
    assert name_not_running("8146(평일만)", SUN_1330)
    for ok in ("470", "741", "401", "406", "9401", "143"):
        assert not name_not_running(ok, SUN_1330), ok


def test_schedule():
    weekday_only = {"schedule": {"startPointTimes": [
        {"calendar": {"dows": 62}, "firstTime": "05:00", "lastTime": "22:00"}]}}
    assert schedule_not_running(weekday_only, SUN_1330) == "calendar: no service today"
    assert schedule_not_running(weekday_only, MON_1330) is None
    allday = {"schedule": {"startPointTimes": [
        {"calendar": {"dows": 62}, "firstTime": "04:00", "lastTime": "22:40"},
        {"calendar": {"dows": 64}, "firstTime": "04:00", "lastTime": "22:40"},
        {"calendar": {"dows": 1}, "firstTime": "04:00", "lastTime": "22:40"}]}}
    assert schedule_not_running(allday, SUN_1330) is None
    assert schedule_not_running(allday, SUN_1330.replace(hour=3)) == "hours: 04:00~22:40"
    past_midnight = {"schedule": {"startPoint": {"firstTime": "05:30", "lastTime": "00:30"}}}
    assert schedule_not_running(past_midnight, SUN_1330.replace(hour=1, minute=0)) is None
    assert schedule_not_running(past_midnight, SUN_1330.replace(hour=4)) is not None


async def live_gangnam():
    """강남역12번출구 → 광화문 now: raw router output vs filtered output."""
    async with NaverMap() as nm:
        start = (37.49915, 127.02829)  # 강남역12번출구 부근
        raw = await nm.directions(start, GWANGHWAMUN, running_only=False, with_arrivals=False)
        kept = await nm.directions(start, GWANGHWAMUN, running_only=True, with_arrivals=False)
    names = lambda rs: sorted({l["name"] for r in rs for st in r["steps"] if st["mode"] == "BUS" for l in st["lines"]})
    raw_n, kept_n = names(raw), names(kept)
    bad = [n for n in kept_n if name_not_running(n, datetime.now(KST))]
    print("raw lines   :", raw_n)
    print("kept lines  :", kept_n)
    print("dropped log :", nm.dropped)
    assert not bad, bad
    assert not any("새벽" in n or "미운행" in n for n in kept_n)
    a741 = [d for d in nm.dropped if "A741" in d["line"]]
    if any("A741" in n for n in raw_n):
        assert a741 and a741[0]["why"].startswith("name"), a741  # must be caught by the Sunday/daytime rule
    return raw_n, kept_n, nm.dropped


if __name__ == "__main__":
    test_names(); test_schedule(); print("unit checks OK")
    raw_n, kept_n, dropped = asyncio.run(live_gangnam())
    a741_raw = [n for n in raw_n if "A741" in n]
    print("A741 in raw router output today:", a741_raw or "not returned this time")
    print("A741 in filtered output:", [n for n in kept_n if "A741" in n] or "none (dropped)")
    print("live check OK")
