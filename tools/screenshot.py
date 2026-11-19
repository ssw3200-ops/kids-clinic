"""실제 운영 페이지 화면 캡처 (블로그용) — Actions 에서만 실행 (비밀번호는 Secrets)"""
import asyncio
import os
from pathlib import Path

from playwright.async_api import async_playwright

URL = "https://ssw3200-ops.github.io/kids-clinic/"
PW = os.environ["PAGE_PASSWORD"]
OUT = Path("shots")
OUT.mkdir(exist_ok=True)
GEO = {"latitude": 36.4801, "longitude": 127.2890}  # 세종시청 기준
NIGHT = os.environ.get("NIGHT_TIME", "2026-11-19T21:30:00+09:00")


async def ctx_page(b, night=False):
    ctx = await b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=3,
                              geolocation=GEO, permissions=["geolocation"], locale="ko-KR",
                              timezone_id="Asia/Seoul")
    pg = await ctx.new_page()
    if night:
        await pg.clock.install(time=NIGHT)
    return ctx, pg


async def unlock(pg):
    await pg.goto(URL + "?shot=" + str(os.getpid()))
    await pg.wait_for_selector("#pw", state="visible")
    await pg.fill("#pw", PW)
    await pg.click("#pwBtn")
    await pg.wait_for_selector(".card", timeout=60000)
    await pg.wait_for_timeout(1500)


async def shot(pg, name):
    await pg.evaluate("window.scrollTo(0,0)")
    await pg.wait_for_timeout(500)
    await pg.screenshot(path=str(OUT / f"{name}.png"))
    print("saved", name, await pg.inner_text("#count"))


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        # 01 잠금 화면
        ctx, pg = await ctx_page(b)
        await pg.goto(URL)
        await pg.wait_for_selector("#pw", state="visible")
        await pg.wait_for_timeout(1500)
        await pg.screenshot(path=str(OUT / "01_lock.png"))
        # 02~06 지금 시간
        await unlock(pg)
        await shot(pg, "02_now_region")
        await pg.click("#loc")
        await pg.wait_for_function("document.getElementById('count').innerText.includes('내 주변')", timeout=60000)
        await pg.wait_for_timeout(1000)
        await shot(pg, "03_now_near")
        await pg.click(".wk >> nth=0")
        await pg.wait_for_timeout(500)
        el = pg.locator(".card").first
        await el.screenshot(path=str(OUT / "04_week_card.png"))
        await pg.click("#tabs button[data-t=p]")
        await pg.wait_for_timeout(2500)
        await shot(pg, "05_now_pharmacy")
        await pg.click("#tabs button[data-t=e]")
        await pg.wait_for_timeout(2500)
        await shot(pg, "06_now_er")
        await ctx.close()
        # 07~09 밤 시간으로 맞춘 화면
        ctx, pg = await ctx_page(b, night=True)
        await unlock(pg)
        await pg.click("#loc")
        await pg.wait_for_function("document.getElementById('count').innerText.includes('내 주변')", timeout=60000)
        await pg.wait_for_timeout(1000)
        await shot(pg, "07_night_clinic")
        await pg.click("#tabs button[data-t=p]")
        await pg.wait_for_timeout(2500)
        await shot(pg, "08_night_pharmacy")
        await pg.click("#tabs button[data-t=h]")
        await pg.wait_for_timeout(1500)
        await pg.click("#chips [data-k=open]")
        await pg.wait_for_timeout(1500)
        await shot(pg, "09_night_all")
        await ctx.close()
        await b.close()


asyncio.run(main())
