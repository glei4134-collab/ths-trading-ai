with open(r'd:\量化交易\ths-trading-ai\mq_0.txt', 'r', encoding='utf-8') as f:
    t0 = f.read()

import re
idx = t0.find('#131b2e')
if idx != -1:
    print(t0[idx-250:idx+250])
