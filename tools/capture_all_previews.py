import asyncio
from playwright.async_api import async_playwright

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel='msedge', headless=True)
        
        # 1. Desktop Test: Open Account Dropdown
        page_desktop = await browser.new_page(viewport={'width': 1600, 'height': 900})
        await page_desktop.goto('http://127.0.0.1:8888/?token=GsXdjxQm3gqxv_l_Rl3mhjan46hQysYy')
        await page_desktop.wait_for_timeout(3000)
        
        # Click .account-selector
        await page_desktop.click('.account-selector')
        await page_desktop.wait_for_timeout(600)
        
        shot_dropdown = r'C:\Users\Acer\.gemini\antigravity\brain\4ad78f66-67a7-4f15-b0af-1abbb7c0d6ed\preview_warm_account_dropdown.png'
        await page_desktop.screenshot(path=shot_dropdown, full_page=False)
        print("Desktop dropdown screenshot saved to", shot_dropdown)
        await page_desktop.close()
        
        # 2. Mobile Test (390 x 844)
        page_mobile = await browser.new_page(viewport={'width': 390, 'height': 844})
        await page_mobile.goto('http://127.0.0.1:8888/?token=GsXdjxQm3gqxv_l_Rl3mhjan46hQysYy')
        await page_mobile.wait_for_timeout(3000)
        
        shot_mobile_home = r'C:\Users\Acer\.gemini\antigravity\brain\4ad78f66-67a7-4f15-b0af-1abbb7c0d6ed\preview_mobile_home.png'
        await page_mobile.screenshot(path=shot_mobile_home, full_page=False)
        print("Mobile home screenshot saved to", shot_mobile_home)
        
        # Open dropdown on mobile
        await page_mobile.click('.account-selector')
        await page_mobile.wait_for_timeout(600)
        shot_mobile_dropdown = r'C:\Users\Acer\.gemini\antigravity\brain\4ad78f66-67a7-4f15-b0af-1abbb7c0d6ed\preview_mobile_account_dropdown.png'
        await page_mobile.screenshot(path=shot_mobile_dropdown, full_page=False)
        print("Mobile dropdown screenshot saved to", shot_mobile_dropdown)
        
        # Close dropdown, switch to quant tab
        await page_mobile.click('.account-selector')
        await page_mobile.wait_for_timeout(300)
        await page_mobile.click('.mobile-nav-item[data-tab="quant"]')
        await page_mobile.wait_for_timeout(1000)
        shot_mobile_quant = r'C:\Users\Acer\.gemini\antigravity\brain\4ad78f66-67a7-4f15-b0af-1abbb7c0d6ed\preview_mobile_quant_tab.png'
        await page_mobile.screenshot(path=shot_mobile_quant, full_page=False)
        print("Mobile quant tab screenshot saved to", shot_mobile_quant)
        
        await browser.close()

asyncio.run(run())
