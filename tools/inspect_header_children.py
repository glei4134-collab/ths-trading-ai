import asyncio
from playwright.async_api import async_playwright

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel='msedge', headless=True)
        page = await browser.new_page(viewport={'width': 390, 'height': 844})
        await page.goto('http://127.0.0.1:8888/?token=GsXdjxQm3gqxv_l_Rl3mhjan46hQysYy')
        await page.wait_for_timeout(2000)
        
        children = await page.evaluate('''() => {
            const h = document.querySelector('.header');
            const res = [];
            for (let child of h.children) {
                res.push({
                    tag: child.tagName,
                    id: child.id,
                    className: child.className,
                    offsetWidth: child.offsetWidth,
                    innerHTML: child.innerHTML.slice(0, 50)
                });
            }
            return res;
        }''')
        
        for c in children:
            print("Header child:", c)
            
        await browser.close()

asyncio.run(run())
