html_path = r'd:\量化交易\ths-trading-ai\static\index.html'

with open(html_path, 'r', encoding='utf-8') as f:
    text = f.read()

r_idx = text.find(':root')
next_star = text.find('* {', r_idx)

# Let's inspect where [data-theme="dark"] ends
dark_idx = text.find('[data-theme="dark"]', r_idx)
print("dark_idx:", dark_idx)
brace_count = 0
dark_end = dark_idx
for i in range(dark_idx, len(text)):
    if text[i] == '{':
        brace_count += 1
    elif text[i] == '}':
        brace_count -= 1
        if brace_count == 0:
            dark_end = i + 1
            break

print("dark_end:", dark_end)
print(text[dark_end:dark_end+300])
