#!/usr/bin/env python3
"""
Yuho_splitter_v4 の出力構造をチェック
"""
from pathlib import Path

test_dir = Path(r"E:\PDF\sections_test")
prod_dir = Path(r"E:\PDF\sections")

print("=" * 80)
print("sections_test structure")
print("=" * 80)

if test_dir.exists():
    # 1階層目: 会社フォルダ
    for company_folder in sorted(test_dir.iterdir()):
        if company_folder.is_dir():
            print(f"\n{company_folder.name}/")

            # 2階層目: 年度_種別フォルダ
            for year_folder in sorted(company_folder.iterdir()):
                if year_folder.is_dir():
                    print(f"  {year_folder.name}/")

                    # 3階層目: セクションフォルダ
                    for section_folder in sorted(year_folder.iterdir()):
                        if section_folder.is_dir():
                            pdf_count = len(list(section_folder.glob("*.pdf")))
                            print(f"    {section_folder.name}/ ({pdf_count} PDFs)")
else:
    print("sections_test does not exist")

print("\n" + "=" * 80)
print("sections structure")
print("=" * 80)

if prod_dir.exists():
    for company_folder in sorted(prod_dir.iterdir())[:3]:  # 最初の3社のみ
        if company_folder.is_dir():
            print(f"\n{company_folder.name}/")

            for year_folder in sorted(company_folder.iterdir())[:2]:  # 最初の2年度のみ
                if year_folder.is_dir():
                    print(f"  {year_folder.name}/")

                    for section_folder in sorted(year_folder.iterdir()):
                        if section_folder.is_dir():
                            pdf_count = len(list(section_folder.glob("*.pdf")))
                            print(f"    {section_folder.name}/ ({pdf_count} PDFs)")
else:
    print("sections does not exist")
