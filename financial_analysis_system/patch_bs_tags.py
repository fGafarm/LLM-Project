#!/usr/bin/env python3
"""
xbrl_storeのB/Sタグを修正するパッチスクリプト。
- buildings: BuildingsAndStructures(取得価額) → BuildingsAndStructuresNet(帳簿価額)
- notes_receivable: NotesAndAccountsReceivableTrade(結合) → NotesReceivableTrade(受取手形のみ)
- notes_payable: NotesAndAccountsPayableTrade(結合) → NotesPayableTrade(支払手形のみ)
- inventories: コンポーネントから合計を補完
- 回転日数の再計算
"""

import json
import os
import sys
import glob
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

STORE_DIR = Path(r'c:\Users\shun nabeno\Desktop\Local LLM Project\financial_analysis_system\xbrl_store')

# 修正対象のタグマッピング
FIXES = {
    'buildings': {
        'old_tag': 'BuildingsAndStructures',
        'new_tag': 'BuildingsAndStructuresNet',
    },
    'notes_receivable': {
        'old_tag': 'NotesAndAccountsReceivableTrade',  # 結合タグ（trade_receivablesと重複）
        'new_tag': 'NotesReceivableTrade',  # 受取手形のみ
    },
    'notes_payable': {
        'old_tag': 'NotesAndAccountsPayableTrade',  # 結合タグ（trade_payablesと重複）
        'new_tag': 'NotesPayableTrade',  # 支払手形のみ
    },
}


def patch_store_file(store_file: Path, raw_tags_file: Path) -> bool:
    """1年分のデータを修正"""
    with open(store_file, 'r', encoding='utf-8') as f:
        store = json.load(f)
    data = store.get('data', {})

    with open(raw_tags_file, 'r', encoding='utf-8') as f:
        raw = json.load(f)
    tags = raw.get('tags', {})

    changed = False

    for field, fix in FIXES.items():
        old_tag = fix['old_tag']
        new_tag = fix['new_tag']

        # 新しいタグがraw_tagsに存在するか確認
        if new_tag in tags:
            td = tags[new_tag]
            val = td.get('value') if isinstance(td, dict) else None
            if isinstance(val, (int, float)):
                old_val = data.get(field)
                data[field] = val
                if old_val != val:
                    changed = True
        else:
            # 新しいタグが存在しない → フィールドをクリア（重複防止）
            if field in data:
                del data[field]
                changed = True

    # inventories: コンポーネントから合計を補完
    if not data.get('inventories'):
        inv_sum = sum(data.get(k, 0) or 0 for k in ['merchandise', 'work_in_progress', 'raw_materials', 'supplies'])
        if inv_sum > 0:
            data['inventories'] = inv_sum
            changed = True

    # 回転日数の再計算
    if changed:
        revenue = data.get('revenue', 0)
        cost_of_sales = data.get('cost_of_sales', 0)

        # 売上債権回転日数
        receivables = 0
        if data.get('trade_receivables'):
            receivables = data['trade_receivables']
        else:
            receivables = (data.get('notes_receivable') or 0) + (data.get('accounts_receivable') or 0)
        if receivables > 0 and revenue:
            turnover = round(revenue / receivables, 2)
            data['receivables_turnover_calc'] = turnover
            data['receivables_days_calc'] = round(365 / turnover, 1)

        # 仕入債務回転日数
        payables = 0
        if data.get('trade_payables'):
            payables = data['trade_payables']
        else:
            payables = (data.get('notes_payable') or 0) + (data.get('accounts_payable') or 0)
        if payables > 0 and cost_of_sales:
            turnover = round(cost_of_sales / payables, 2)
            data['payables_turnover_calc'] = turnover
            data['payables_days_calc'] = round(365 / turnover, 1)

        # 棚卸資産回転日数
        if data.get('inventories') and revenue:
            turnover = round(revenue / data['inventories'], 2)
            data['inventory_turnover_calc'] = turnover
            data['inventory_days_calc'] = round(365 / turnover, 1)

        # CCC
        if all(k in data for k in ['receivables_days_calc', 'inventory_days_calc', 'payables_days_calc']):
            data['ccc_calc'] = round(
                data['receivables_days_calc'] + data['inventory_days_calc'] - data['payables_days_calc'], 1
            )

    if changed:
        store['data'] = data
        with open(store_file, 'w', encoding='utf-8') as f:
            json.dump(store, f, ensure_ascii=False, indent=2)

    return changed


def main():
    raw_files = sorted(STORE_DIR.glob('*/*_raw_tags.json'))
    print(f"B/Sパッチ: {len(raw_files)} ファイルを処理")

    updated = 0
    errors = 0

    for rf in raw_files:
        year = rf.stem.split('_')[0]
        store_file = rf.parent / f'{year}.json'
        if not store_file.exists():
            continue

        try:
            if patch_store_file(store_file, rf):
                updated += 1
        except Exception as e:
            print(f"  ERROR {rf}: {e}")
            errors += 1

    print(f"\nDone! Updated: {updated}, Errors: {errors}, Total: {len(raw_files)}")


if __name__ == '__main__':
    main()
