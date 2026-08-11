#!/usr/bin/env python3
"""
PIK / 信用ストレス指標のための軽量マイグレーションスクリプト。

既存の xbrl_store/{code}_{name}/{year}.json に対し、
- {year}_raw_tags.json から新規タグ（PIK関連）を追補
- _calculate_derived_metrics を再実行して派生指標を更新

ZIP 再抽出は不要なので高速。

Usage:
    python migrate_pik_fields.py                 # xbrl_store 全社対象
    python migrate_pik_fields.py --code 1301     # 単一社テスト
    python migrate_pik_fields.py --dry-run       # 書き込みせず効果のみ報告
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from xbrl_batch_extractor import FALLBACK_TAGS, _calculate_derived_metrics

# 本マイグレーションで新規に埋めたいフィールド
NEW_FIELDS = [
    'interest_expenses',
    'interest_expenses_lease',
    'interest_income_pl',
    'accrued_income_receivable',
    'allowance_for_doubtful_current',
    'allowance_for_doubtful_non_current',
    'allowance_for_doubtful_total',
]

# B/S は Instant 文脈、P/L と CF は Duration 文脈を優先
INSTANT_FIELDS = {
    'accrued_income_receivable',
    'allowance_for_doubtful_current',
    'allowance_for_doubtful_non_current',
    'allowance_for_doubtful_total',
}


def extract_from_raw_tags(raw_tags: dict, tag_entries: list, prefer_instant: bool) -> float | None:
    """優先度順にタグを走査して最適な値を返す。"""
    best_value = None
    best_priority = 99
    preferred_ctx = 'CurrentYearInstant' if prefer_instant else 'CurrentYearDuration'

    for full_tag, priority in tag_entries:
        if priority >= best_priority:
            continue  # 既に同等以上の優先度で確定済み
        local_name = full_tag.split(':', 1)[-1]
        entry = raw_tags.get(local_name)
        if entry is None:
            continue
        if not isinstance(entry, dict):
            continue
        ctx = entry.get('context', '')
        # CurrentYear 以外は除外（過去年度の誤採用を防ぐ）
        if 'CurrentYear' not in ctx:
            continue
        # フィールド種別と文脈が合致しない場合はスキップ
        if prefer_instant and 'Instant' not in ctx:
            continue
        if not prefer_instant and 'Duration' not in ctx:
            continue
        val = entry.get('value')
        if val is None:
            continue
        try:
            best_value = float(val)
            best_priority = priority
        except (TypeError, ValueError):
            continue
    return best_value


def migrate_year_file(year_json_path: Path, raw_tags_path: Path, dry_run: bool = False) -> dict:
    """1年度分の JSON をマイグレーション。"""
    result = {'added': 0, 'recalc': False, 'skipped_reason': None}

    if not raw_tags_path.exists():
        result['skipped_reason'] = 'no_raw_tags'
        return result

    with year_json_path.open('r', encoding='utf-8') as f:
        year_data = json.load(f)
    with raw_tags_path.open('r', encoding='utf-8') as f:
        raw_wrapper = json.load(f)

    raw_tags = raw_wrapper.get('tags', {})
    if not isinstance(raw_tags, dict) or not raw_tags:
        result['skipped_reason'] = 'empty_raw_tags'
        return result

    data = year_data.get('data')
    if not isinstance(data, dict):
        result['skipped_reason'] = 'no_data_key'
        return result

    added_any = False
    # interest_expenses は完全に再抽出する（旧データの broad FinanceCostsIFRS 混入を一掃）
    if 'interest_expenses' in data:
        data['interest_expenses'] = None  # クリアして新マッピングで再抽出
    for field in NEW_FIELDS:
        if field not in FALLBACK_TAGS:
            continue
        if data.get(field) is not None:
            continue  # 既に値あり（interest_expenses は上でNoneに戻されている）
        tag_entries = FALLBACK_TAGS[field]
        val = extract_from_raw_tags(
            raw_tags,
            tag_entries,
            prefer_instant=(field in INSTANT_FIELDS),
        )
        if val is not None:
            data[field] = val
            added_any = True
            result['added'] += 1

    # 派生指標の再計算（新規 source が入ったか、A1 等の未計算があれば更新）
    has_new_derivation_input = (
        added_any
        or data.get('interest_paid_cf') is not None
        or data.get('interest_expense_bank') is not None
        or data.get('finance_costs') is not None
    )
    if has_new_derivation_input:
        _calculate_derived_metrics(data)
        result['recalc'] = True

    if not dry_run and (added_any or result['recalc']):
        with year_json_path.open('w', encoding='utf-8') as f:
            json.dump(year_data, f, ensure_ascii=False, indent=2)

    return result


def iter_targets(xbrl_store: Path, company_code: str | None):
    for company_dir in sorted(xbrl_store.iterdir()):
        if not company_dir.is_dir():
            continue
        if company_code and not company_dir.name.startswith(f'{company_code}_'):
            continue
        for year_json in sorted(company_dir.glob('20*.json')):
            name = year_json.name
            if '_raw_tags' in name or '_statements' in name or '_Q' in name:
                continue
            year_stem = year_json.stem  # e.g. "2024"
            raw_tags_path = company_dir / f'{year_stem}_raw_tags.json'
            yield company_dir, year_json, raw_tags_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--code', help='Single company code, e.g. 1301')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--store', default='xbrl_store', help='xbrl_store directory')
    parser.add_argument('--verbose', action='store_true')
    args = parser.parse_args()

    xbrl_store = Path(args.store)
    if not xbrl_store.is_absolute():
        xbrl_store = Path(__file__).parent / xbrl_store
    if not xbrl_store.exists():
        print(f'ERROR: {xbrl_store} not found', file=sys.stderr)
        sys.exit(1)

    total_files = 0
    total_added_fields = 0
    total_recalc = 0
    skipped = {'no_raw_tags': 0, 'empty_raw_tags': 0, 'no_data_key': 0}
    companies_touched = set()
    errors = []

    for company_dir, year_json, raw_tags_path in iter_targets(xbrl_store, args.code):
        total_files += 1
        try:
            result = migrate_year_file(year_json, raw_tags_path, dry_run=args.dry_run)
        except Exception as exc:
            errors.append((str(year_json), str(exc)))
            continue
        if result['skipped_reason']:
            skipped[result['skipped_reason']] = skipped.get(result['skipped_reason'], 0) + 1
            continue
        total_added_fields += result['added']
        if result['recalc']:
            total_recalc += 1
        if result['added'] or result['recalc']:
            companies_touched.add(company_dir.name)
        if args.verbose and (result['added'] or result['recalc']):
            print(f'  [{company_dir.name}/{year_json.name}] added={result["added"]} recalc={result["recalc"]}')

    mode = 'DRY-RUN' if args.dry_run else 'APPLIED'
    print(f'--- {mode} SUMMARY ---')
    print(f'  files scanned:       {total_files}')
    print(f'  new fields added:    {total_added_fields}')
    print(f'  derived recalc done: {total_recalc}')
    print(f'  companies touched:   {len(companies_touched)}')
    print(f'  skipped: {skipped}')
    if errors:
        print(f'  errors: {len(errors)}')
        for path, msg in errors[:5]:
            print(f'    {path}: {msg}')


if __name__ == '__main__':
    main()
