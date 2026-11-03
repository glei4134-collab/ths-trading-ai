import re

html_path = r'd:\量化交易\ths-trading-ai\static\index.html'

with open(html_path, 'r', encoding='utf-8') as f:
    text = f.read()

classes = set(re.findall(r'class=["\']([^"\']*mobile[^"\']*)["\']', text, re.IGNORECASE))
ids = set(re.findall(r'id=["\']([^"\']*mobile[^"\']*)["\']', text, re.IGNORECASE))

print("Mobile classes:", classes)
print("Mobile ids:", ids)

# Find bottom nav or mobile tab bar
bottom_nav = re.findall(r'<div[^>]*nav[^>]*>.*?(?=</div>)', text, re.DOTALL | re.IGNORECASE)
for bn in bottom_nav[:5]:
    print("Found nav:", bn[:200])

# Find all buttons in HTML
buttons = re.findall(r'<button[^>]*>.*?</button>', text, re.DOTALL | re.IGNORECASE)
print(f"Total buttons in HTML: {len(buttons)}")

# Check media query rules in CSS
mq_matches = re.findall(r'@media[^{]*\{.*?\}\s*\}', text, re.DOTALL)
print(f"Total media query blocks: {len(mq_matches)}")
for i, mq in enumerate(mq_matches):
    print(f"--- Media query {i} ---")
    print(mq[:300])
