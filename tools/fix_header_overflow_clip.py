html_path = r'd:\量化交易\ths-trading-ai\static\index.html'

with open(html_path, 'r', encoding='utf-8') as f:
    content = f.read()

target = """            .header {
                max-width: 100vw !important;
                overflow: hidden !important;
                box-sizing: border-box !important;
            }"""

repl = """            .header {
                max-width: 100vw !important;
                overflow: visible !important;
                box-sizing: border-box !important;
            }"""

if target in content:
    content = content.replace(target, repl, 1)
    print("Replaced target in header styles")
else:
    print("Notice: target not matched")

with open(html_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("Saved")
