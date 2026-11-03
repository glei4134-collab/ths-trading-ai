with open(r'd:\量化交易\ths-trading-ai\mq_0.txt', 'r', encoding='utf-8') as f:
    t0 = f.read()

import re
# Print all selectors and properties
lines = t0.splitlines()
for line in lines:
    if any(k in line for k in ['background', 'color', 'border', 'rgba', '#', 'fill', 'stroke']):
        print(line)
