with open(r'd:\量化交易\ths-trading-ai\mq_out.txt', 'r', encoding='utf-16') as f:
    text = f.read()

import re
# Find all color-like properties in the media queries
color_props = re.findall(r'([a-zA-Z\-]+color|[a-zA-Z\-]+background[a-zA-Z\-]*|box-shadow|border[a-zA-Z\-]*):\s*([^;]+);', text, re.IGNORECASE)

print(f"Total color properties in media queries: {len(color_props)}")
for prop, val in color_props:
    val_clean = val.strip()
    # Check if value has hardcoded dark colors
    if any(k in val_clean for k in ['rgba(10, 14, 23', 'rgba(9, 13, 21', '#0', '#1', '#2', '#3', 'rgba(255, 255, 255, 0.0', 'rgba(0,210,255', '#ffe600', '#00f59b', '#ff1824']):
        print(f"  {prop}: {val_clean}")
