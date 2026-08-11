"""Verify US 業種内ポジション section on AAPL company page."""
import sys
from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8")

URL = "http://localhost:3456/ja/company/AAPL"
SHOT = r"C:\Users\shun nabeno\Desktop\Local LLM Project\aapl_sector_verify.png"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(viewport={"width": 1280, "height": 2000})
    page = ctx.new_page()

    console_msgs = []
    page.on("console", lambda m: console_msgs.append(f"[{m.type}] {m.text}"))

    page.goto(URL, wait_until="networkidle", timeout=30000)
    # wait a bit for fetches
    page.wait_for_timeout(2500)

    # locate sector position section
    headings = page.locator("h2:has-text('業種内ポジション')").count()
    print(f"業種内ポジション h2 count: {headings}")

    # GICS marker
    gics_text = page.locator("text=Information Technology").count()
    print(f"Information Technology marker count: {gics_text}")
    gics_label = page.locator("text=(GICS)").count()
    print(f"(GICS) label count: {gics_label}")

    # SectorMetricBar 内の自社ドット (青) - 業種内ポジション section 全体を screenshot
    section = page.locator("h2:has-text('業種内ポジション')").locator("xpath=ancestor::div[contains(@class,'bg-white')][1]").first
    visible = section.is_visible() if section.count() > 0 else False
    print(f"section visible: {visible}")

    # NVDA リンク or 業種上位 link
    nvda_links = page.locator('a[href*="/company/NVDA"]').count()
    print(f"NVDA company link count: {nvda_links}")

    # 業種上位 label
    sector_top_label = page.locator("text=業種上位").count()
    print(f"業種上位 label count: {sector_top_label}")

    # 業種内の強み / 業種内の課題
    strengths = page.locator("text=業種内の強み").count()
    weaknesses = page.locator("text=業種内の課題").count()
    print(f"強み: {strengths}, 課題: {weaknesses}")

    # screenshot section (if visible) else full page
    if visible:
        section.screenshot(path=SHOT)
        print(f"Saved section screenshot to {SHOT}")
    else:
        page.screenshot(path=SHOT, full_page=True)
        print(f"Saved full page screenshot to {SHOT}")

    print("--- Console messages ---")
    for m in console_msgs[:20]:
        print(m)

    browser.close()
