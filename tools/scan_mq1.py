with open(r'd:\量化交易\ths-trading-ai\mq_1.txt', 'r', encoding='utf-8') as f:
    t1 = f.read()

print("Length of mq_1:", len(t1))
lines = t1.splitlines()
for line in lines:
    if any(k in line for k in ['background', 'color', 'border', 'rgba', '#', 'fill', 'stroke']):
        print(line)
