import re

html_path = r'd:\量化交易\ths-trading-ai\static\index.html'

with open(html_path, 'r', encoding='utf-8') as f:
    text = f.read()

# Find all divs with class="modal" or class="modal-overlay"
matches = re.findall(r'<div[^>]*class=["\'][^"\']*modal[^"\']*["\'][^>]*>', text, re.IGNORECASE)
for m in matches:
    print("Found modal element:", m)
