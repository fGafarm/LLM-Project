#!/usr/bin/env python3
"""
PDF ファイルのソート順をテスト
"""
from pathlib import Path

section_dir = Path(r"E:\PDF\sections_test\1301_株式会社　極洋\2022_有報\02_経営戦略_リスク")

pdf_files = sorted(section_dir.glob("*.pdf"))

print(f"Found {len(pdf_files)} PDF files:")
print()

for i, pdf_file in enumerate(pdf_files, 1):
    print(f"{i:2d}. {pdf_file.name}")
