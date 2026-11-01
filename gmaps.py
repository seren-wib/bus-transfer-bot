"""Google Maps transit probe (headless Playwright, DOM scrape of the directions panel).
URL: https://www.google.com/maps/dir/LAT,LON/LAT,LON/data=!4m2!4m1!3e3?hl=ko  (3e3 = transit)"""
import asyncio, re, time
from playwright.async_api import async_playwright
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")

def _minutes(s):
    h = re.search(r"(\d+)\s*시간", s); m = re.search(r"(\d+)\s*분", s)
    return (int(h.group(1)) * 60 if h else 0) + (int(m.group(1)) if m else 0)

async def google_transit(ctx, start_latlon, dest_latlon):
    pg = await ctx.new_page()
    try:
        u = (f"https://www.google.com/maps/dir/{start_latlon[0]},{start_latlon[1]}/"
             f"{dest_latlon[0]},{dest_latlon[1]}/data=!4m2!4m1!3e3?hl=ko")
        await pg.goto(u, wait_until="domcontentloaded")
        await pg.wait_for_selector("[data-trip-index]", timeout=20000)
        out = []
        for el in await pg.locator("[data-trip-index]").all():
            lines = [x.strip() for x in (await el.inner_text()).split("\n") if x.strip()]
            dur = next((x for x in lines if re.fullmatch(r"(\d+시간\s*)?\d+분|\d+시간", x)), "")
            times = next((x for x in lines if "~" in x), "")
            # line badges: spans with aria-label or short text
            badges = await el.evaluate("""e=>[...e.querySelectorAll('[aria-label]')].map(x=>x.getAttribute('aria-label')).filter(Boolean)""")
            out.append({"total_min": _minutes(dur), "times": times, "lines_text": " ".join(lines[2:4]),
                        "aria": badges[:8], "subway": any("호선" in x for x in lines)})
        return out
    finally:
        await pg.close()

async def _demo():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(locale="ko-KR", user_agent=UA, viewport={"width": 1400, "height": 900})
        t = time.perf_counter()
        r = await google_transit(ctx, (37.504935, 127.064147), (37.5760259, 126.9768428))
        print(f"google: {time.perf_counter()-t:.1f}s")
        for x in r: print(x)
        await b.close()

if __name__ == "__main__":
    asyncio.run(_demo())
