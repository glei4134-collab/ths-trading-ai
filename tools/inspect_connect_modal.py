html_path = r'd:\量化交易\ths-trading-ai\static\index.html'

with open(html_path, 'r', encoding='utf-8') as f:
    text = f.read()

idx = text.find('id="connectModal"')
with open('d:\\量化交易\\ths-trading-ai\\connect_modal.txt', 'w', encoding='utf-8') as f:
    f.write(text[idx-50:idx+2500])

print("Wrote connect_modal.txt successfully")
