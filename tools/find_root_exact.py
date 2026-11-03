html_path = r'd:\量化交易\ths-trading-ai\static\index.html'

with open(html_path, 'r', encoding='utf-8') as f:
    text = f.read()

r_idx = text.find(':root')
print("Index of :root:", r_idx)
print(text[r_idx:r_idx+500])
