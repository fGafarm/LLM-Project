#!/usr/bin/env python3
"""
10社レポート一括再生成スクリプト
xbrl_batch_extractorの改善を反映させる
"""
import subprocess
from pathlib import Path

COMPANIES = [
    ("7203", "トヨタ自動車株式会社"),
    ("6758", "ソニーグループ株式会社"),
    ("1301", "株式会社　極洋"),
    ("9984", "ソフトバンクグループ株式会社"),
    ("7974", "任天堂株式会社"),
    ("9983", "株式会社ファーストリテイリング"),
    ("4063", "信越化学工業株式会社"),
    ("6501", "株式会社日立製作所"),
    ("2914", "日本たばこ産業株式会社"),
    ("8306", "株式会社三菱ＵＦＪフィナンシャル・グループ"),
]

def main():
    print("=" * 70)
    print("10社レポート一括再生成")
    print("=" * 70)
    print()
    print("目的: xbrl_batch_extractorの改善（dividend_per_share計算など）を反映")
    print()

    for i, (code, name) in enumerate(COMPANIES, 1):
        print(f"[{i}/10] {code} {name}")

        cmd = [
            "python", "Run_integrated_v10_1.py",
            code,
            "2022",
            "--doc_type", "有報"
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=600,  # 10分タイムアウト
                encoding='utf-8',
                errors='ignore'
            )

            if result.returncode == 0:
                print(f"  ✓ 成功")
            else:
                print(f"  ✗ 失敗 (exit code: {result.returncode})")
                if result.stderr:
                    print(f"    エラー: {result.stderr[:200]}")
        except subprocess.TimeoutExpired:
            print(f"  ✗ タイムアウト (10分超過)")
        except Exception as e:
            print(f"  ✗ エラー: {e}")

        print()

    print("=" * 70)
    print("完了！")
    print("=" * 70)

if __name__ == "__main__":
    main()
