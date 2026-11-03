html_path = r'd:\量化交易\ths-trading-ai\static\index.html'

with open(html_path, 'r', encoding='utf-8') as f:
    content = f.read()

# In @media (max-width: 768px), hide #autoTradeHeader so header never exceeds 390px
target = """            #connStatusHeader,
            #autoTradeHeader {
                font-size: 10px !important;
                padding: 2px 7px !important;
                background: var(--bg-inset) !important;
                border-radius: 12px !important;
                border: 1px solid var(--border-default) !important;
                color: var(--text-secondary) !important;
                white-space: nowrap !important;
            }"""

repl = """            #connStatusHeader {
                font-size: 10px !important;
                padding: 2px 7px !important;
                background: var(--bg-inset) !important;
                border-radius: 12px !important;
                border: 1px solid var(--border-default) !important;
                color: var(--text-secondary) !important;
                white-space: nowrap !important;
            }
            #autoTradeHeader {
                display: none !important;
            }
            .header {
                max-width: 100vw !important;
                overflow: hidden !important;
                box-sizing: border-box !important;
            }"""

if target in content:
    content = content.replace(target, repl, 1)
    print("Replaced target in mobile header styles")
else:
    print("Notice: target not matched")

with open(html_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("Saved index.html")
