import sqlite3
import json
import pathlib

db_path = '/opt/ths-trading-ai/data/trading.db'
conn = sqlite3.connect(db_path)
conn.execute("UPDATE scheduler_execution_ledger SET status = 'PENDING', executed_at = NULL, duration_ms = 0, output_summary = '' WHERE step_id = 'night_review' AND date = '2026-11-03'")
conn.commit()
conn.close()

json_path = pathlib.Path('/opt/ths-trading-ai/data/scheduler_ledger_2026-11-03.json')
if json_path.exists():
    data = json.loads(json_path.read_text(encoding='utf-8'))
    for item in data:
        if item.get('step_id') == 'night_review':
            item['status'] = 'PENDING'
            item['executed_at'] = None
            item['duration_ms'] = 0
            item['output_summary'] = ''
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

print('NIGHT_REVIEW_PENDING_RESET_SUCCESS')
