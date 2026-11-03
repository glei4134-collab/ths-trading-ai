html_path = r'd:\量化交易\ths-trading-ai\static\index.html'

with open(html_path, 'r', encoding='utf-8') as f:
    text = f.read()

# Let's find all styles in @media (max-width: 768px)
import re

for m in re.finditer(r'@media\s*\(\s*max-width:\s*768px\s*\)\s*\{', text):
    start = m.start()
    # find matching brace
    count = 0
    end = start
    for i in range(start, len(text)):
        if text[i] == '{':
            count += 1
        elif text[i] == '}':
            count -= 1
            if count == 0:
                end = i + 1
                break
    print("="*60)
    print("MEDIA QUERY at", start, "to", end)
    print(text[start:end])
