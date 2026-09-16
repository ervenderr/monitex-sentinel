"""Screenshot the running dashboard. Used to review the UI while building it.

Playwright rather than `chrome --screenshot`: the dashboard holds an open SSE
connection, so headless Chrome never reaches network-idle and never fires its
one-shot screenshot. Playwright captures on command instead.
"""

from __future__ import annotations

import argparse
import asyncio

from playwright.async_api import async_playwright


async def shoot(url: str, out: str, width: int, height: int, settle_s: float) -> None:
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome")
        page = await browser.new_page(viewport={"width": width, "height": height})
        await page.goto(url, wait_until="domcontentloaded")
        await page.wait_for_timeout(settle_s * 1000)
        await page.screenshot(path=out, full_page=False)
        await browser.close()
    print(f"wrote {out}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8000/")
    parser.add_argument("--out", default="/tmp/shots/board.png")
    parser.add_argument("--width", type=int, default=1600)
    parser.add_argument("--height", type=int, default=1150)
    parser.add_argument("--settle", type=float, default=4.0)
    args = parser.parse_args()
    asyncio.run(shoot(args.url, args.out, args.width, args.height, args.settle))


if __name__ == "__main__":
    main()
