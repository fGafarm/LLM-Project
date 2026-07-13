# -*- coding: utf-8 -*-
"""
inventories 系統的過小 / ordinary_income IFRS混入 の一括修復 (2026-07-13, 記事量産検算 P1対応)

背景 (2件のP1バグ、いずれも記事70/量産検算が発見):
  1. inventories: 合計タグ (Inventories/InventoriesCAIFRS) が無い提出者へのフォールバックが
     merchandise+work_in_progress+raw_materials+supplies の4フィールド合算のみで、
     販売用不動産 (RealEstateForSale系)・未成工事支出金 (CostsOnUncompletedConstructionContracts系)・
     商品/製品の単独表記等を含まず、不動産・建設・小売で系統的過小だった
     (FY2025実測673社。大和ハウス 345億→25,705億、良品計画 2億→1,700億)。
  2. ordinary_income: IFRS移行年の有報は経営指標サマリに「日本基準」参考表を併載し、その経常利益が
     jpcrp_cor:OrdinaryIncomeLossSummaryOfBusinessResults として素の CurrentYearDuration で
     打刻される (帝人3401 FY2025 = 123.7億)。IFRS/US-GAAPに経常利益概念は無く None が正。
     単体のみ再抽出を経たIFRS企業の単体 jppfs:OrdinaryIncome 混入 (2130等) も同型。
  extractor側は同日修正済み (INVENTORY_COMPONENT_TAGS / ordinary_income_is_foreign_gaap_artifact)。
  本スクリプトは既存storeを raw_tags から extractor と同一ロジックで再マッチして修復する
  (migrate_operating_income.py と同型)。

動作:
  - 対象: xbrl_store/*/{2019..2026}.json と {2019..2026}_Q*.json のうち
    同名 _raw_tags.json を持つもの (EDINET由来)。短信由来 (raw無し) はスキップ。
  - inventories: 合計タグ優先 (二重計上防止) → 無ければ構成タグ網羅合算 → 無ければ旧4フィールド。
    raw に根拠が無いのに旧値がある場合は保護のため触らない (件数は監視レポート)。
  - ordinary_income: 新優先リストで再マッチ後、IFRS/US-GAAP提出者ガードを適用 (該当は None=キー削除)。
  - 値が変わる場合のみ書込み、依存する派生指標
    (inventory_turnover_calc / inventory_days_calc / ccc_calc / ordinary_margin_calc) を再計算。
  - 既定 dry-run。--apply で実際に書き込む。

実行:
  python migrate_inventories_ordinary.py            # dry-run (影響レポートのみ)
  python migrate_inventories_ordinary.py --apply    # 書込み
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(r"C:/Users/shun nabeno/Desktop/Local LLM Project")
DEFAULT_STORE = ROOT / "financial_analysis_system" / "xbrl_store"
YEARS = {str(y) for y in range(2019, 2027)}

# extractorの修正版ロジックをそのまま使う (挙動一致の保証)
sys.path.insert(0, str(ROOT / "financial_analysis_system"))
from xbrl_batch_extractor import (  # noqa: E402
    FALLBACK_TAGS,
    INVENTORY_COMPONENT_TAGS,
    ordinary_income_is_foreign_gaap_artifact,
    sum_inventory_components,
)

# 自己防衛: extractor改悪の早期検知
assert 'RealEstateForSale' in INVENTORY_COMPONENT_TAGS, "販売用不動産タグが構成リストから消えている"
assert 'CostsOnUncompletedConstructionContractsCNS' in INVENTORY_COMPONENT_TAGS, "未成工事支出金タグが消えている"
assert 'Merchandise' in INVENTORY_COMPONENT_TAGS, "商品単独タグが消えている"
_ORD_LOCALS = [t.split(':')[-1] for t, _ in FALLBACK_TAGS['ordinary_income']]
assert 'OrdinaryIncomeSummaryOfBusinessResults' not in _ORD_LOCALS, "経常収益タグがordinary_incomeに再混入している"

INV_PRI = [(t.split(':')[-1], p) for t, p in FALLBACK_TAGS['inventories']]
ORD_PRI = [(t.split(':')[-1], p) for t, p in FALLBACK_TAGS['ordinary_income']]

LEGACY_COMPONENT_FIELDS = ('merchandise', 'work_in_progress', 'raw_materials', 'supplies')


def match_tags(tags: dict, locals_pri: list) -> float | None:
    """FALLBACK_TAGSと同じ「優先度昇順・同率は先勝ち」でローカル名マッチ (extractorのループと同一挙動)"""
    best, best_pri = None, 999
    for local_name, pri in locals_pri:
        if best is not None and best_pri <= pri:
            continue
        td = tags.get(local_name)
        v = td.get('value') if isinstance(td, dict) else td
        if isinstance(v, (int, float)) and pri < best_pri:
            best, best_pri = float(v), pri
    return best


def compute_inventories(tags: dict, d: dict) -> float | None:
    """extractor と同一の優先順: 合計タグ → 構成タグ網羅合算 → 旧4フィールド合算"""
    total = match_tags(tags, INV_PRI)
    if total:  # 合計タグ優先 (二重計上防止)
        return total
    comp = sum_inventory_components(tags)
    if comp is None:
        legacy = sum(d.get(k, 0) or 0 for k in LEGACY_COMPONENT_FIELDS)
        comp = legacy if legacy > 0 else None
    if comp and comp > 0:
        return comp
    return total  # 0.0 または None (extractorのマッピングは0も格納するため形状を合わせる)


def recompute_calcs(d: dict) -> None:
    """inventories / ordinary_income に依存する派生指標を再計算 (extractorの式と同一)"""
    rev = d.get('revenue')
    # 経常利益率
    ordv = d.get('ordinary_income')
    if ordv is not None and rev:
        d['ordinary_margin_calc'] = round(ordv / rev * 100, 2)
    else:
        d.pop('ordinary_margin_calc', None)
    # 棚卸資産回転率・回転日数
    inv = d.get('inventories')
    if (rev or 0) > 0 and inv:
        d['inventory_turnover_calc'] = round(rev / inv, 2)
        if d['inventory_turnover_calc'] > 0:
            d['inventory_days_calc'] = round(365 / d['inventory_turnover_calc'], 1)
        else:
            d.pop('inventory_days_calc', None)
    else:
        d.pop('inventory_turnover_calc', None)
        d.pop('inventory_days_calc', None)
    # CCC = 売上債権回転日数 + 棚卸資産回転日数 - 仕入債務回転日数
    if all(k in d for k in ('receivables_days_calc', 'inventory_days_calc', 'payables_days_calc')):
        d['ccc_calc'] = round(
            d['receivables_days_calc'] + d['inventory_days_calc'] - d['payables_days_calc'], 1)
    else:
        d.pop('ccc_calc', None)


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    ap = argparse.ArgumentParser(description='inventories/ordinary_income 一括修復 (既定dry-run)')
    ap.add_argument('--apply', action='store_true', help='実際に書き込む (省略時はdry-run)')
    ap.add_argument('--store', default=str(DEFAULT_STORE), help='xbrl_storeパス')
    ap.add_argument('--skip-ordinary', action='store_true', help='ordinary_incomeは触らない')
    ap.add_argument('--skip-inventories', action='store_true', help='inventoriesは触らない')
    args = ap.parse_args()

    store = Path(args.store)
    mode = 'APPLY' if args.apply else 'DRY-RUN'
    print(f"=== migrate_inventories_ordinary [{mode}] store={store} 対象年={min(YEARS)}-{max(YEARS)} ===")

    stats = Counter()
    inv_by_year = defaultdict(Counter)
    ord_by_year = defaultdict(Counter)
    inv_samples = []       # (folder, stem, old, new)
    inv_decrease_samples = []
    ord_samples = []
    monitor_inv_gt_ca = []       # 監視: 新inventories > current_assets (二重計上の兆候)
    monitor_kept_old = Counter()  # 監視: rawに根拠が無いのに旧値あり → 保護で不変

    files = sorted(store.glob('*/[0-9][0-9][0-9][0-9]*.json'))
    for jf in files:
        m = re.match(r'^(\d{4})(_Q\d)?$', jf.stem)
        if not m or m.group(1) not in YEARS:
            continue
        if '_raw_tags' in jf.name or '_statements' in jf.name:
            continue
        rt_path = jf.with_name(jf.stem + '_raw_tags.json')
        if not rt_path.exists():
            stats['skip_no_raw (短信由来等)'] += 1
            continue
        try:
            data = json.loads(jf.read_text(encoding='utf-8'))
            tags = json.loads(rt_path.read_text(encoding='utf-8')).get('tags') or {}
        except Exception:
            stats['skip_broken_json'] += 1
            continue
        d = data.get('data')
        if not isinstance(d, dict):
            stats['skip_no_data'] += 1
            continue

        year = m.group(1)
        changed = False

        # ---- inventories ----
        if not args.skip_inventories:
            old_inv = d.get('inventories')
            new_inv = compute_inventories(tags, d)
            if new_inv is None and old_inv is not None:
                # rawに根拠なし: 保護して触らない (patch由来等の可能性)
                monitor_kept_old[year] += 1
                new_inv = old_inv
            if new_inv != old_inv:
                changed = True
                if old_inv is None:
                    stats['inv_filled_from_None'] += 1
                    inv_by_year[year]['filled'] += 1
                elif new_inv > old_inv:
                    stats['inv_increased'] += 1
                    inv_by_year[year]['increased'] += 1
                else:
                    stats['inv_decreased'] += 1
                    inv_by_year[year]['decreased'] += 1
                    if len(inv_decrease_samples) < 30:
                        inv_decrease_samples.append((jf.parent.name, jf.stem, old_inv, new_inv))
                if len(inv_samples) < 400:
                    inv_samples.append((jf.parent.name, jf.stem, old_inv, new_inv))
                ca = d.get('current_assets')
                if ca and new_inv and new_inv > ca * 1.001:
                    monitor_inv_gt_ca.append((jf.parent.name, jf.stem, new_inv, ca))
                if args.apply:
                    if new_inv is None:
                        d.pop('inventories', None)
                    else:
                        d['inventories'] = new_inv
            else:
                stats['inv_unchanged'] += 1

        # ---- ordinary_income ----
        if not args.skip_ordinary:
            old_ord = d.get('ordinary_income')
            new_ord = match_tags(tags, ORD_PRI)
            if new_ord is not None and ordinary_income_is_foreign_gaap_artifact(tags):
                new_ord = None
                if old_ord is not None:
                    stats['ord_dropped_gaap_guard (IFRS/US-GAAP)'] += 1
                    ord_by_year[year]['guard_drop'] += 1
            if new_ord != old_ord:
                changed = True
                if old_ord is not None and new_ord is None:
                    stats['ord_changed_to_None'] += 1
                elif old_ord is None and new_ord is not None:
                    stats['ord_filled_from_None'] += 1
                    ord_by_year[year]['filled'] += 1
                else:
                    stats['ord_value_changed'] += 1
                    ord_by_year[year]['changed'] += 1
                if len(ord_samples) < 100:
                    ord_samples.append((jf.parent.name, jf.stem, old_ord, new_ord))
                if args.apply:
                    if new_ord is None:
                        d.pop('ordinary_income', None)
                    else:
                        d['ordinary_income'] = new_ord
            else:
                stats['ord_unchanged'] += 1

        if not changed:
            continue
        stats['files_changed'] += 1
        if args.apply:
            recompute_calcs(d)
            data['data'] = d
            jf.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

    # ---- レポート ----
    print('\n--- 件数 ---')
    for k, c in stats.most_common():
        print(f'  {c:7,d}  {k}')
    print('\n--- inventories 変更の年別内訳 ---')
    for y in sorted(inv_by_year):
        c = inv_by_year[y]
        print(f"  {y}: increased={c['increased']:5d}  decreased={c['decreased']:4d}  filled={c['filled']:4d}")
    print('\n--- ordinary_income 変更の年別内訳 ---')
    for y in sorted(ord_by_year):
        c = ord_by_year[y]
        print(f"  {y}: guard_drop={c['guard_drop']:4d}  changed={c['changed']:4d}  filled={c['filled']:4d}")
    if monitor_kept_old:
        print('\n--- 監視: rawに根拠なし・旧値保護 (触らず) ---')
        for y, c in sorted(monitor_kept_old.items()):
            print(f'  {y}: {c}')
    if monitor_inv_gt_ca:
        print(f'\n--- 監視: 新inventories > current_assets ({len(monitor_inv_gt_ca)}件, 二重計上の兆候) ---')
        for folder, stem, inv, ca in monitor_inv_gt_ca[:10]:
            print(f'  {folder:42s} {stem:8s} inv={inv/1e8:,.0f}億 > CA={ca/1e8:,.0f}億')
    if inv_decrease_samples:
        print('\n--- inventories 減少サンプル (要目視) ---')
        for folder, stem, old, new in inv_decrease_samples:
            fo = f'{old/1e8:,.1f}億' if isinstance(old, (int, float)) else str(old)
            fn = f'{new/1e8:,.1f}億' if isinstance(new, (int, float)) else str(new)
            print(f'  {folder:42s} {stem:8s} {fo} -> {fn}')
    print('\n--- inventories サンプル (変化量上位20件) ---')
    for folder, stem, old, new in sorted(
            inv_samples, key=lambda t: -abs((t[3] or 0) - (t[2] or 0)))[:20]:
        fo = f'{old/1e8:,.1f}億' if isinstance(old, (int, float)) else str(old)
        fn = f'{new/1e8:,.1f}億' if isinstance(new, (int, float)) else str(new)
        print(f'  {folder:42s} {stem:8s} {fo} -> {fn}')
    print('\n--- ordinary_income サンプル (先頭20件) ---')
    for folder, stem, old, new in ord_samples[:20]:
        fo = f'{old/1e8:,.1f}億' if isinstance(old, (int, float)) else str(old)
        fn = f'{new/1e8:,.1f}億' if isinstance(new, (int, float)) else str(new)
        print(f'  {folder:42s} {stem:8s} {fo} -> {fn}')
    if not args.apply:
        print('\n(dry-run: 書込みなし。--apply で実行)')


if __name__ == '__main__':
    main()
