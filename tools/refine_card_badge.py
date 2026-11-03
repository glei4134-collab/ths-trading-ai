html_path = r'd:\量化交易\ths-trading-ai\static\index.html'

with open(html_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Replace the active text badge with the clean green indicator dot in renderMultiAccountMatrix
target1 = """                                <span style="font-size: 11px; font-weight: 800; color: var(--text-primary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">
                                    ${dispName}
                                </span>
                                ${isActive ? '<span style="background: #16a34a; color: #fff; font-size: 8px; font-weight: 800; padding: 0 3px; border-radius: 2px;">活跃</span>' : ''}"""

repl1 = """                                ${isActive ? '<span style="color: #16a34a; font-size: 9px; font-weight: 900; line-height: 1;">●</span>' : ''}
                                <span style="font-size: 11px; font-weight: 800; color: var(--text-primary); white-space: nowrap;">
                                    ${dispName}
                                </span>"""

if target1 in content:
    content = content.replace(target1, repl1, 1)
    print("Replaced target1 in renderMultiAccountMatrix")
else:
    print("target1 not found")

# Replace in syncAndRenderMultiAccounts
target2 = """                                <span style="font-size: 11px; font-weight: 800; color: var(--text-primary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">${dispName}</span>
                                ${isActive ? '<span style="background: #16a34a; color: #fff; font-size: 8px; font-weight: 800; padding: 0 3px; border-radius: 2px;">活跃</span>' : ''}"""

repl2 = """                                ${isActive ? '<span style="color: #16a34a; font-size: 9px; font-weight: 900; line-height: 1;">●</span>' : ''}
                                <span style="font-size: 11px; font-weight: 800; color: var(--text-primary); white-space: nowrap;">${dispName}</span>"""

if target2 in content:
    content = content.replace(target2, repl2, 1)
    print("Replaced target2 in syncAndRenderMultiAccounts")
else:
    print("target2 not found")

with open(html_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("Refinement done.")
