html_path = r'd:\量化交易\ths-trading-ai\static\index.html'

with open(html_path, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. In lines 2021-2050, clean up .mobile-nav-item.active
target1 = """.mobile-nav-item.active svg {
            stroke: #ff1824;
            filter: drop-shadow(0 0 6px rgba(255, 24, 36, 0.6));
            transform: translateY(-1px);
        }
        .mobile-nav-item.active::after {
            content: '';
            position: absolute;
            bottom: -3px;
            width: 14px;
            height: 3px;
            background: #ff1824;
            border-radius: 3px;
            box-shadow: 0 0 8px #ff1824;
        }"""

repl1 = """.mobile-nav-item.active svg {
            stroke: var(--brand-blue) !important;
            filter: none !important;
            transform: translateY(-1px);
        }
        .mobile-nav-item.active::after {
            display: none !important;
        }"""

if target1 in content:
    content = content.replace(target1, repl1, 1)
    print("Replaced target1 in lines 2035-2050")
else:
    print("Notice: target1 not matched")

# 2. In lines 2570-2600, clean up .mobile-nav-item:active and .mobile-nav-item.active
target2 = """.mobile-nav-item:active {
                transform: scale(0.92);
                background: rgba(255, 24, 36, 0.12) !important;
            }

            .mobile-nav-item.active {
                background: linear-gradient(180deg, rgba(255, 24, 36, 0.25) 0%, rgba(255, 24, 36, 0.05) 100%) !important;
                color: #ff1824 !important;
                border: 1px solid rgba(255, 24, 36, 0.45) !important;
                box-shadow: 0 0 16px rgba(255, 24, 36, 0.35) !important;
            }

            .mobile-nav-item.active svg {
                stroke: #ff1824 !important;
                filter: drop-shadow(0 0 6px #ff1824);
                transform: translateY(-1px);
            }

            .mobile-nav-item.active::after {
                content: '';
                position: absolute;
                bottom: 2px;
                width: 16px;
                height: 2.5px;
                background: #ff1824;
                border-radius: 2px;
                box-shadow: 0 0 8px #ff1824;
            }"""

repl2 = """.mobile-nav-item:active {
                transform: scale(0.95);
                background: var(--bg-card-hover) !important;
            }

            .mobile-nav-item.active {
                background: var(--brand-blue-bg) !important;
                color: var(--brand-blue) !important;
                border: 1px solid var(--brand-blue-border) !important;
                box-shadow: none !important;
                border-radius: 8px !important;
            }

            .mobile-nav-item.active svg {
                stroke: var(--brand-blue) !important;
                filter: none !important;
                transform: translateY(-1px);
            }

            .mobile-nav-item.active::after {
                display: none !important;
            }"""

if target2 in content:
    content = content.replace(target2, repl2, 1)
    print("Replaced target2 in lines 2570-2600")
else:
    print("Notice: target2 not matched")

# 3. Add .theme-switch-pill { display: none !important; } in @media (max-width: 768px)
# and adjust .account-selector-panel on mobile: right: 8px; width: calc(100vw - 16px); max-width: 350px;
target3 = """.account-info {
                display: none !important;
            }"""

repl3 = """.account-info,
            .theme-switch-pill {
                display: none !important;
            }
            .account-selector-panel {
                right: 4px !important;
                width: calc(100vw - 16px) !important;
                max-width: 350px !important;
            }"""

if target3 in content:
    content = content.replace(target3, repl3, 1)
    print("Replaced target3 in mobile query")
else:
    print("Notice: target3 not matched")

with open(html_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("Saved index.html successfully.")
