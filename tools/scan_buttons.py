import re

html_path = r'd:\量化交易\ths-trading-ai\static\index.html'

with open(html_path, 'r', encoding='utf-8') as f:
    text = f.read()

buttons = re.findall(r'<button\b[^>]*>(.*?)</button>', text, re.DOTALL | re.IGNORECASE)
print(f"Total buttons: {len(buttons)}")

btn_tags = re.findall(r'<button\b[^>]*>', text, re.IGNORECASE)
for b in btn_tags:
    # Check style attribute
    m = re.search(r'style="([^"]*)"', b, re.IGNORECASE)
    txt = b[:80]
    if m:
        st = m.group(1)
        # Check if style has hardcoded dark/neon colors
        if any(c in st for c in ['#21262d', '#30363d', '#0d1117', '#161b22', '#c9d1d9', 'rgba(255, 24, 36', 'rgba(255,24,36', '#ff1824', '#00f59b', '#ffe600', '#238636', 'linear-gradient(135deg,#238636']):
            print(f"Mismatch Button: {b}")
