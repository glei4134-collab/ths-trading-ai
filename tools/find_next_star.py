html_path = r'd:\量化交易\ths-trading-ai\static\index.html'

with open(html_path, 'r', encoding='utf-8') as f:
    text = f.read()

r_idx = text.find(':root')
next_star = text.find('* {', r_idx)
print("r_idx:", r_idx, "next_star:", next_star)
print("Between r_idx and next_star:\n", text[next_star-100:next_star+20])
