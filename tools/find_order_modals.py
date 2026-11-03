html_path = r'd:\量化交易\ths-trading-ai\static\index.html'

with open(html_path, 'r', encoding='utf-8') as f:
    text = f.read()

import re
matches = re.finditer(r'showBatchOrderFeedbackModal|openManualOrderModal|showOrderModal', text)
for m in matches:
    idx = m.start()
    print(f"Match at {idx}:")
    with open(f'd:\\量化交易\\ths-trading-ai\\order_modal_{idx}.txt', 'w', encoding='utf-8') as out:
        out.write(text[idx-50:idx+1500])
    print(f"Wrote order_modal_{idx}.txt")
