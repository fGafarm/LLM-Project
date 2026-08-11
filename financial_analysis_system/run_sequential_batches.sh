#!/bin/bash
# 2023完了待ち → 2022実行 → 2021実行（順次・並列なし）
# nohup で実行: VS Code落ちても継続

PYTHON="/c/Users/shun nabeno/AppData/Local/Python/bin/python.exe"
WORKDIR="/c/Users/shun nabeno/Desktop/Local LLM Project/financial_analysis_system"
BATCH_SCRIPT="run_nikkei225_batch.py"

cd "$WORKDIR"

echo "$(date '+%Y-%m-%d %H:%M:%S') [SEQ] Sequential batch runner started"
echo "$(date '+%Y-%m-%d %H:%M:%S') [SEQ] Waiting for 2023 batch to complete..."

# === 2023 完了待ち ===
while true; do
    COUNT=$("$PYTHON" -c "
import json
try:
    with open('batch_progress_2023.json', 'r', encoding='utf-8') as f:
        data = json.load(f)
    print(len(data.get('completed', {})))
except:
    print(0)
" 2>/dev/null)

    if [ "$COUNT" -ge 225 ]; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') [SEQ] 2023 batch complete: $COUNT/225"
        break
    fi
    echo "$(date '+%Y-%m-%d %H:%M:%S') [SEQ] 2023: $COUNT/225 - waiting 60s..."
    sleep 60
done

# === 2022 実行 ===
echo "$(date '+%Y-%m-%d %H:%M:%S') [SEQ] Starting 2022 batch..."
"$PYTHON" "$BATCH_SCRIPT" --year 2022 2>&1 | tee batch_2022_output.log

"$PYTHON" -c "
import json
with open('batch_progress_2022.json', 'r', encoding='utf-8') as f:
    data = json.load(f)
completed = len(data.get('completed', {}))
failed = len(data.get('failed', {}))
scores = [v.get('confidence', 0) for v in data['completed'].values()]
avg = sum(scores)/len(scores) if scores else 0
print(f'2022: {completed}/225 completed, {failed} failed, avg={avg:.1f}%')
"
echo "$(date '+%Y-%m-%d %H:%M:%S') [SEQ] 2022 batch complete"

# === 2021 実行 ===
echo "$(date '+%Y-%m-%d %H:%M:%S') [SEQ] Starting 2021 batch..."
"$PYTHON" "$BATCH_SCRIPT" --year 2021 2>&1 | tee batch_2021_output.log

"$PYTHON" -c "
import json
with open('batch_progress_2021.json', 'r', encoding='utf-8') as f:
    data = json.load(f)
completed = len(data.get('completed', {}))
failed = len(data.get('failed', {}))
scores = [v.get('confidence', 0) for v in data['completed'].values()]
avg = sum(scores)/len(scores) if scores else 0
print(f'2021: {completed}/225 completed, {failed} failed, avg={avg:.1f}%')
"
echo "$(date '+%Y-%m-%d %H:%M:%S') [SEQ] 2021 batch complete"
echo "$(date '+%Y-%m-%d %H:%M:%S') [SEQ] All sequential batches finished!"
