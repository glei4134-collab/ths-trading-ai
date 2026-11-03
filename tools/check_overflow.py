import asyncio
from playwright.async_api import async_playwright

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel='msedge', headless=True)
        page = await browser.new_page(viewport={'width': 390, 'height': 844})
        await page.goto('http://127.0.0.1:8888/?token=GsXdjxQm3gqxv_l_Rl3mhjan46hQysYy')
        await page.wait_for_timeout(3000)
        await page.click('.mobile-nav-item[data-tab="quant"]')
        await page.wait_for_timeout(1000)
        
        overflowing = await page.evaluate('''() => {
            const results = [];
            const docWidth = document.documentElement.clientWidth;
            document.querySelectorAll('*').forEach(el => {
                if (el.scrollWidth > docWidth + 10) {
                    results.push({
                        tag: el.tagName,
                        id: el.id,
                        className: el.className,
                        scrollWidth: el.scrollWidth,
                        clientWidth: el.clientWidth
                    });
                }
            });
            return results;
        }''')
        
        print(f'Total overflowing elements: {len(overflowing)}')
        for o in overflowing[:10]:
            print('  OVERFLOW:', o)
            
        await browser.close()

asyncio.run(run())
