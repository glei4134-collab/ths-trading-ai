from pathlib import Path

path = Path(__file__).resolve().parent.parent / "static" / "index.html"
txt = path.read_text(encoding="utf-8")

# 1. 给 table 加上 table-layout: fixed; width: 100%;
old_table_tag = '<table style="width:100%;border-collapse:collapse;font-size:12px;text-align:left;">'
new_table_tag = '<table style="width:100%;table-layout:fixed;border-collapse:collapse;font-size:12px;text-align:left;">'
if old_table_tag in txt:
    txt = txt.replace(old_table_tag, new_table_tag)
    print("Added table-layout: fixed")

# 2. 精确分配 9 列百分比宽度
old_th_block = """                                <tr style="color:var(--text-secondary);font-size:11.5px;font-weight:700;letter-spacing:0.2px;white-space:nowrap;">
                                    <th style="padding:7px 6px;text-align:left;white-space:nowrap;min-width:90px;">代码 / 名称</th>
                                    <th style="padding:7px 6px;text-align:left;white-space:nowrap;min-width:65px;">行业板块</th>
                                    <th style="padding:7px 6px;text-align:right;white-space:nowrap;min-width:55px;">最新价</th>
                                    <th style="padding:7px 6px;text-align:right;white-space:nowrap;min-width:55px;">涨跌幅</th>
                                    <th style="padding:7px 6px;text-align:right;white-space:nowrap;min-width:55px;">因子评分</th>
                                    <th style="padding:7px 6px;text-align:center;white-space:nowrap;min-width:65px;">策略表决</th>
                                    <th style="padding:7px 6px;text-align:center;white-space:nowrap;min-width:55px;">决策</th>
                                    <th style="padding:7px 6px;text-align:right;white-space:nowrap;min-width:85px;">建议仓位</th>
                                    <th style="padding:7px 6px;text-align:right;white-space:nowrap;min-width:65px;">合规止损</th>
                                </tr>"""

new_th_block = """                                <tr style="color:var(--text-secondary);font-size:11.5px;font-weight:700;letter-spacing:0.2px;white-space:nowrap;">
                                    <th style="padding:7px 6px;text-align:left;width:18%;">代码 / 名称</th>
                                    <th style="padding:7px 6px;text-align:left;width:11%;">行业板块</th>
                                    <th style="padding:7px 6px;text-align:right;width:9%;">最新价</th>
                                    <th style="padding:7px 6px;text-align:right;width:9%;">涨跌幅</th>
                                    <th style="padding:7px 6px;text-align:right;width:9%;">因子评分</th>
                                    <th style="padding:7px 6px;text-align:center;width:11%;">策略表决</th>
                                    <th style="padding:7px 6px;text-align:center;width:8%;">决策</th>
                                    <th style="padding:7px 6px;text-align:right;width:13%;">建议仓位</th>
                                    <th style="padding:7px 6px;text-align:right;width:12%;">合规止损</th>
                                </tr>"""

if old_th_block in txt:
    txt = txt.replace(old_th_block, new_th_block)
    print("Replaced th_block with fixed percentage widths")

# 3. 限制建议仓位副标题最大宽度，超长省略号截断（如茅台超长资金说明）
old_pos_subtitle = '<div style="font-size:9.5px;color:var(--text-muted);margin-top:1px;white-space:nowrap;" title="${item.reason || \'\'}">'
new_pos_subtitle = '<div style="font-size:9.5px;color:var(--text-muted);margin-top:1px;max-width:110px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;margin-left:auto;" title="${item.reason || \'\'}">'
if old_pos_subtitle in txt:
    txt = txt.replace(old_pos_subtitle, new_pos_subtitle)
    print("Constrained position subtitle max-width")

path.write_text(txt, encoding="utf-8")
print("Done")
