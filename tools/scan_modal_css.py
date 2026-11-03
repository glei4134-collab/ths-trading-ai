import re

html_path = r'd:\量化交易\ths-trading-ai\static\index.html'

with open(html_path, 'r', encoding='utf-8') as f:
    text = f.read()

# Find .modal CSS
modal_css = re.findall(r'\.modal\s*\{[^}]+\}', text)
for m in modal_css:
    print("Modal CSS:", m)

# Find .modal-header, .modal-body, .modal-footer
for cls in ['modal-header', 'modal-body', 'modal-footer', 'modal-overlay', 'connect-btn', 'form-input', 'form-select']:
    found = re.findall(rf'\.{cls}\s*\{{[^}}]+\}}', text)
    for f in found:
        print(f"{cls} CSS:", f)
