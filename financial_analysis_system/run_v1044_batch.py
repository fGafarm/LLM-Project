#!/usr/bin/env python3
"""v10.4.4 バッチ実行スクリプト - 26社を一社ずつ処理"""
import sys
import os
import time
sys.stdout.reconfigure(encoding='utf-8')

# プロジェクトルートに移動
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# Run_integrated_v10_2から必要な関数をインポート
from Run_integrated_v10_2 import (
    process_single_company, Company, LocalRAGDB,
    infer_industry_from_name, Config
)
from pathlib import Path

# 26社リスト: 前回6社 + 新規20社
COMPANIES = [
    # 前回6社
    ("7203", "トヨタ自動車株式会社"),
    ("6861", "株式会社キーエンス"),
    ("6758", "ソニーグループ株式会社"),
    ("8306", "株式会社三菱ＵＦＪフィナンシャル・グループ"),
    ("2802", "味の素株式会社"),
    ("4502", "武田薬品工業株式会社"),
    # 新規20社（業種多様性を確保）
    ("9984", "ソフトバンクグループ株式会社"),      # IFRS, conglomerate
    ("9432", "ＮＴＴ株式会社"),                    # telecom
    ("6902", "株式会社デンソー"),                   # IFRS, auto parts
    ("8035", "東京エレクトロン株式会社"),           # semiconductor
    ("4063", "信越化学工業株式会社"),               # chemicals
    ("6501", "株式会社日立製作所"),                 # IFRS, conglomerate
    ("7741", "ＨＯＹＡ株式会社"),                  # IFRS, optics
    ("7267", "本田技研工業株式会社"),               # IFRS, auto
    ("6367", "ダイキン工業株式会社"),               # IFRS, AC
    ("3382", "株式会社セブン＆アイ・ホールディングス"),  # retail
    ("8058", "三菱商事株式会社"),                   # IFRS, trading
    ("4568", "第一三共株式会社"),                   # IFRS, pharma
    ("6098", "株式会社リクルートホールディングス"), # IFRS, services
    ("9433", "ＫＤＤＩ株式会社"),                  # telecom
    ("8001", "伊藤忠商事株式会社"),                 # IFRS, trading
    ("6594", "ニデック株式会社"),                   # IFRS, motors
    ("2914", "日本たばこ産業株式会社"),             # IFRS, tobacco
    ("8316", "株式会社三井住友フィナンシャルグループ"),  # banking
    ("5401", "日本製鉄株式会社"),                   # IFRS, steel
    ("6273", "ＳＭＣ株式会社"),                    # factory automation
]

def main():
    year = "2024"
    doc_type = "有報"
    output_base = Path("./output_v10.2")
    output_base.mkdir(parents=True, exist_ok=True)
    rag_db = LocalRAGDB("./rag_db")

    total_start = time.time()
    results = []

    print(f"\n{'='*70}")
    print(f"  v10.4.4 バッチ実行: {len(COMPANIES)}社")
    print(f"  年度: {year} / 種別: {doc_type}")
    print(f"{'='*70}\n")

    for i, (code, name) in enumerate(COMPANIES, 1):
        industry = infer_industry_from_name(name)
        print(f"\n{'='*70}")
        print(f"  [{i}/{len(COMPANIES)}] {code} {name} (industry={industry})")
        print(f"{'='*70}")

        start = time.time()
        try:
            result = process_single_company(
                company_code=code,
                company_name=name,
                year=year,
                doc_type=doc_type,
                industry="all",  # force_industry=all as in normal batch
                output_base=output_base,
                rag_db=rag_db
            )
            elapsed = time.time() - start
            status = result.get('status', 'unknown')
            xbrl_items = result.get('xbrl_items', 0)
            sections = result.get('sections', 0)
            print(f"  -> {status} | XBRL: {xbrl_items}項目 | sections: {sections} | {elapsed:.1f}秒")
            results.append((code, name, status, elapsed))
        except Exception as e:
            elapsed = time.time() - start
            print(f"  -> ERROR: {e} | {elapsed:.1f}秒")
            results.append((code, name, f"error: {e}", elapsed))

    total_elapsed = time.time() - total_start

    # サマリー
    print(f"\n\n{'='*70}")
    print(f"  バッチ完了サマリー ({total_elapsed/60:.1f}分)")
    print(f"{'='*70}")
    success = sum(1 for _, _, s, _ in results if s == 'success')
    print(f"  成功: {success}/{len(results)}社\n")
    for code, name, status, elapsed in results:
        mark = "✅" if status == "success" else "❌"
        print(f"  {mark} {code} {name}: {status} ({elapsed:.1f}秒)")

if __name__ == "__main__":
    main()
