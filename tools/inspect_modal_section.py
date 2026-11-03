import re

html_path = r'd:\量化交易\ths-trading-ai\static\index.html'

with open(html_path, 'r', encoding='utf-8') as f:
    text = f.read()

# Let's inspect CSS for modal and forms
modal_section = re.search(r'/\* Modal \*/(.*?)(/\*|\Z)', text, re.DOTALL)
if modal_section:
    print("Found Modal CSS section:")
    print(modal_section.group(1)[:1200])
else:
    print("Modal CSS section not matched directly, searching for .modal {")
    idx = text.find('.modal {')
    print(text[idx-50:idx+800])
