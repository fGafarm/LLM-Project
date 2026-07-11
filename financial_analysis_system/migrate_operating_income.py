# -*- coding: utf-8 -*-
"""
operating_income / ordinary_income 汚染の一括修復 (2026-07-12, 記事54 P0対応)

背景:
  旧 FALLBACK_TAGS['operating_income'] は銀行用フォールバックの経常利益タグ
  (jpcrp:OrdinaryIncomeLossSummaryOfBusinessResults / jppfs:OrdinaryIncome) が優先度1に
  居たため、J-GAAP企業の95-96%で operating_income に経常利益が混入していた。
  また ordinary_income は銀行/保険で経常「収益」(OrdinaryIncomeSummaryOfBusinessResults)
  が混入していた。extractor側は同日修正済み。本スクリプトは既存storeを raw_tags から
  新優先リストで再マッチして修復する (migrate_revenue_tags.py と同型)。

動作:
  - 対象: xbrl_store/*/{2019..2026}.json と {2019..2026}_Q*.json のうち
    同名 _raw_tags.json を持つもの (EDINET由来)。短信由来 (raw無し) はスキップ。
  - operating_income を新優先で再マッチ。タグ無しの場合は extractor と同じ
    「売上総利益 - 販管費」フォールバック。それも無ければ None (銀行/保険は None が正)。
  - ordinary_income も新優先 (経常利益本体 > 経常利益サマリ。経常収益タグは除外) で再マッチ。
  - 値が変わる場合のみ書込み、依存する派生指標
    (operating_margin_calc / ordinary_margin_calc / ebitda_calc / net_debt_ebitda_calc) を再計算。
  - 既定 dry-run。--apply で実際に書き込む。

実行:
  python migrate_operating_income.py            # dry-run (影響レポートのみ)
  python migrate_operating_income.py --apply    # 書込み
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

# extractorの新FALLBACK_TAGS (2026-07-12修正版) をそのまま使う
sys.path.insert(0, str(ROOT / "financial_analysis_system"))
from xbrl_batch_extractor import FALLBACK_TAGS  # noqa: E402

OI_LOCALS = [t.split(":")[-1] for t, _ in FALLBACK_TAGS["operating_income"]]
ORD_LOCALS = [t.split(":")[-1] for t, _ in FALLBACK_TAGS["ordinary_income"]]

# 経常利益タグが誤って残っていないかの自己防衛 (extractor改悪の早期検知)
assert "OrdinaryIncomeLossSummaryOfBusinessResults" not in OI_LOCALS, "経常利益タグがoperating_incomeに再混入している"
assert "OrdinaryIncome" not in OI_LOCALS, "経常利益タグがoperating_incomeに再混入している"
assert "OrdinaryIncomeSummaryOfBusinessResults" not in ORD_LOCALS, "経常収益タグがordinary_incomeに再混入している"


def match_tags(tags: dict, locals_pri: list) -> float | None:
    """FALLBACK_TAGSと同じ「優先度昇順・同率は先勝ち」でローカル名マッチ (extractorのループと同一挙動)"""
    best, best_pri = None, 999
    for local_name, pri in locals_pri:
        if best is not None and best_pri <= pri:
            continue
        td = tags.get(local_name)
        v = td.get("value") if isinstance(td, dict) else td
        if isinstance(v, (int, float)) and pri < best_pri:
            best, best_pri = float(v), pri
    return best


OI_PRI = [(t.split(":")[-1], p) for t, p in FALLBACK_TAGS["operating_income"]]
ORD_PRI = [(t.split(":")[-1], p) for t, p in FALLBACK_TAGS["ordinary_income"]]


def recompute_calcs(d: dict) -> None:
    """operating_income / ordinary_income に依存する派生指標を再計算 (extractorの式と同一)"""
    oi = d.get("operating_income")
    rev = d.get("revenue")
    # 営業利益率
    if oi is not None and rev:
        d["operating_margin_calc"] = round(oi / rev * 100, 2)
    else:
        d.pop("operating_margin_calc", None)
    # 経常利益率
    ordv = d.get("ordinary_income")
    if ordv is not None and rev:
        d["ordinary_margin_calc"] = round(ordv / rev * 100, 2)
    else:
        d.pop("ordinary_margin_calc", None)
    # EBITDA = 営業利益 + 減価償却
    dep = d.get("depreciation_cf")
    if oi and dep:
        d["ebitda_calc"] = oi + dep
    else:
        d.pop("ebitda_calc", None)
    # Net Debt / EBITDA
    ebitda = d.get("ebitda_calc")
    ibd = d.get("interest_bearing_debt_calc")
    if ebitda and ebitda > 0 and ibd:
        cash = d.get("cash_and_deposits", 0) or 0
        d["net_debt_ebitda_calc"] = round((ibd - cash) / ebitda, 2)
    else:
        d.pop("net_debt_ebitda_calc", None)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="operating_income/ordinary_income 一括修復 (既定dry-run)")
    ap.add_argument("--apply", action="store_true", help="実際に書き込む (省略時はdry-run)")
    ap.add_argument("--store", default=str(DEFAULT_STORE), help="xbrl_storeパス")
    ap.add_argument("--skip-ordinary", action="store_true", help="ordinary_incomeは触らない")
    args = ap.parse_args()

    store = Path(args.store)
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"=== migrate_operating_income [{mode}] store={store} 対象年={min(YEARS)}-{max(YEARS)} ===")

    stats = Counter()
    oi_by_year = defaultdict(Counter)
    samples = []          # 大きい変化のサンプル
    none_with_tags = Counter()  # 新OI=Noneだが raw にOIっぽきタグ名が残る件数 (取りこぼし監視)

    files = sorted(store.glob("*/[0-9][0-9][0-9][0-9]*.json"))
    for jf in files:
        m = re.match(r"^(\d{4})(_Q\d)?$", jf.stem)
        if not m or m.group(1) not in YEARS:
            continue
        if "_raw_tags" in jf.name or "_statements" in jf.name:
            continue
        rt_path = jf.with_name(jf.stem + "_raw_tags.json")
        if not rt_path.exists():
            stats["skip_no_raw (短信由来等)"] += 1
            continue
        try:
            data = json.loads(jf.read_text(encoding="utf-8"))
            tags = json.loads(rt_path.read_text(encoding="utf-8")).get("tags") or {}
        except Exception:
            stats["skip_broken_json"] += 1
            continue
        d = data.get("data")
        if not isinstance(d, dict):
            stats["skip_no_data"] += 1
            continue

        old_oi = d.get("operating_income")
        old_ord = d.get("ordinary_income")

        new_oi = match_tags(tags, OI_PRI)
        if new_oi is None:
            # extractorと同じフォールバック: 売上総利益 - 販管費
            gp, sga = d.get("gross_profit"), d.get("selling_general_admin")
            if gp and sga:
                new_oi = gp - sga
                stats["oi_from_gp_minus_sga"] += 1
        new_ord = old_ord if args.skip_ordinary else match_tags(tags, ORD_PRI)

        changed = False
        year = m.group(1)
        if new_oi != old_oi:
            changed = True
            if old_oi is not None and new_oi is None:
                stats["oi_changed_to_None (銀行/保険等)"] += 1
                oi_by_year[year]["to_None"] += 1
            elif old_oi is None and new_oi is not None:
                stats["oi_filled_from_None"] += 1
                oi_by_year[year]["filled"] += 1
            else:
                stats["oi_value_changed"] += 1
                oi_by_year[year]["changed"] += 1
            if len(samples) < 400:
                samples.append((jf.parent.name, jf.stem, "operating_income", old_oi, new_oi))
        else:
            stats["oi_unchanged"] += 1

        if not args.skip_ordinary and new_ord != old_ord:
            changed = True
            if old_ord is not None and new_ord is None:
                stats["ord_changed_to_None"] += 1
            elif old_ord is None and new_ord is not None:
                stats["ord_filled_from_None"] += 1
            else:
                stats["ord_value_changed"] += 1

        if new_oi is None and old_oi is not None:
            # 取りこぼし監視: OIっぽきタグ名が raw に残っているのに None になったケース
            for k in tags:
                if re.match(r"^Operating(Income|Profit)", k) and k not in dict(OI_PRI):
                    none_with_tags[k] += 1

        if not changed:
            continue

        stats["files_changed"] += 1
        if args.apply:
            # None はキー削除 (extractorは「見つかった項目のみ格納」なので出力形状を合わせる)
            if new_oi is None:
                d.pop("operating_income", None)
            else:
                d["operating_income"] = new_oi
            if not args.skip_ordinary:
                if new_ord is None:
                    d.pop("ordinary_income", None)
                else:
                    d["ordinary_income"] = new_ord
            recompute_calcs(d)
            data["data"] = d
            jf.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---- レポート ----
    print("\n--- 件数 ---")
    for k, c in stats.most_common():
        print(f"  {c:7,d}  {k}")
    print("\n--- operating_income 変更の年別内訳 ---")
    for y in sorted(oi_by_year):
        c = oi_by_year[y]
        print(f"  {y}: changed={c['changed']:5d}  to_None={c['to_None']:4d}  filled={c['filled']:4d}")
    if none_with_tags:
        print("\n--- 監視: OI→Noneだがrawに未知のOI系タグが残る (優先リスト追加候補) ---")
        for k, c in none_with_tags.most_common(15):
            print(f"  {c:5d}  {k}")
    print("\n--- サンプル (先頭30件) ---")
    for folder, stem, field, old, new in samples[:30]:
        fo = f"{old/1e8:,.1f}億" if isinstance(old, (int, float)) else str(old)
        fn = f"{new/1e8:,.1f}億" if isinstance(new, (int, float)) else str(new)
        print(f"  {folder:42s} {stem:8s} {field}: {fo} -> {fn}")
    if not args.apply:
        print("\n(dry-run: 書込みなし。--apply で実行)")


if __name__ == "__main__":
    main()
