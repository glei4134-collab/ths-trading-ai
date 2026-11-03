with open(r'd:\量化交易\ths-trading-ai\static\index.html', 'r', encoding='utf-8') as f:
    text = f.read()

import re

for i, m in enumerate(re.finditer(r'@media\s*\(\s*max-width:\s*768px\s*\)\s*\{', text)):
    start = m.start()
    count = 0
    end = start
    for j in range(start, len(text)):
        if text[j] == '{':
            count += 1
        elif text[j] == '}':
            count -= 1
            if count == 0:
                end = j + 1
                break
    with open(f'd:\\量化交易\\ths-trading-ai\\mq_{i}.txt', 'w', encoding='utf-8') as out:
        out.write(text[start:end])
    print(f"Wrote mq_{i}.txt, length: {end - start}")
