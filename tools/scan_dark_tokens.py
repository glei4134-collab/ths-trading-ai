import re

html_path = r'd:\量化交易\ths-trading-ai\static\index.html'

with open(html_path, 'r', encoding='utf-8') as f:
    text = f.read()

dark_tokens = ['#161b22', '#0d1117', '#21262d', '#30363d', '#8b949e', '#c9d1d9', '#e6edf3', '#1e1115', '#100b0d', '#131b2e', '#0d1424', 'rgba(10, 14, 23', 'rgba(9, 13, 21']

for token in dark_tokens:
    count = text.count(token)
    print(f"Token '{token}': {count} occurrences")
