#!/usr/bin/env python3
"""
Financial RAPTOR - シンプル実行スクリプト
デスクトップのPDFフォルダから自動でPDFを読み込み、27B並列3で処理
"""

import sys
from pathlib import Path
from datetime import datetime


def get_desktop_path() -> Path:
    """デスクトップパス取得"""
    home = Path.home()
    for path in [home / "Desktop", home / "デスクトップ", 
                 home / "OneDrive" / "Desktop", home / "OneDrive" / "デスクトップ"]:
        if path.exists():
            return path
    return home / "Desktop"


def main():
    # ============================================================
    # 設定（ここを変更）
    # ============================================================
    MODEL = "gemma2:27b"      # 使用モデル
    CHUNK_SIZE = 2000         # チャンクサイズ
    MAX_WORKERS = 3           # 並列ワーカー数
    
    DESKTOP = get_desktop_path()
    PDF_DIR = DESKTOP / "PDF"
    OUTPUT_DIR = DESKTOP / "financial_raptor_output"
    
    # ============================================================
    # PDF選択
    # ============================================================
    print("=" * 60)
    print("🌳 Financial RAPTOR - シンプル実行")
    print("=" * 60)
    
    if not PDF_DIR.exists():
        print(f"❌ PDFフォルダが見つかりません: {PDF_DIR}")
        return
    
    pdf_files = list(PDF_DIR.glob("*.pdf"))
    
    if not pdf_files:
        print(f"❌ PDFファイルがありません: {PDF_DIR}")
        return
    
    # コマンドライン引数でPDF指定
    if len(sys.argv) > 1:
        pdf_name = sys.argv[1]
        # 部分一致で検索
        matches = [p for p in pdf_files if pdf_name.lower() in p.name.lower()]
        if matches:
            pdf_path = matches[0]
        else:
            print(f"❌ '{pdf_name}' に一致するPDFがありません")
            print("\n利用可能なPDF:")
            for i, p in enumerate(pdf_files, 1):
                print(f"  {i}. {p.name}")
            return
    else:
        # PDF一覧表示して選択
        print(f"\n📁 PDFフォルダ: {PDF_DIR}")
        print("\n利用可能なPDF:")
        for i, p in enumerate(pdf_files, 1):
            size_mb = p.stat().st_size / (1024 * 1024)
            print(f"  {i}. {p.name} ({size_mb:.1f}MB)")
        
        print("\n" + "-" * 40)
        choice = input("処理するPDFの番号を入力 (Enter=1): ").strip()
        
        if choice == "":
            choice = "1"
        
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(pdf_files):
                pdf_path = pdf_files[idx]
            else:
                print("❌ 無効な番号です")
                return
        except ValueError:
            print("❌ 数字を入力してください")
            return
    
    # ============================================================
    # 実行確認
    # ============================================================
    print("\n" + "=" * 60)
    print("⚙️ 実行設定")
    print("=" * 60)
    print(f"  📄 PDF: {pdf_path.name}")
    print(f"  🤖 モデル: {MODEL}")
    print(f"  📦 チャンクサイズ: {CHUNK_SIZE} tokens")
    print(f"  👷 並列ワーカー: {MAX_WORKERS}")
    print(f"  📁 出力先: {OUTPUT_DIR}")
    print("=" * 60)
    
    confirm = input("\n実行しますか？ (Enter=実行 / n=キャンセル): ").strip().lower()
    if confirm == 'n':
        print("キャンセルしました")
        return
    
    # ============================================================
    # パイプライン実行
    # ============================================================
    from pipeline_text.text_pipeline import TextPipeline, OllamaClient
    
    # Ollama接続テスト
    import requests
    try:
        response = requests.get("http://localhost:11434/api/tags", timeout=5)
        models = [m['name'] for m in response.json().get('models', [])]
        print(f"\n✅ Ollama接続OK - 利用可能: {', '.join(models[:3])}...")
    except:
        print("\n❌ Ollamaに接続できません。Ollamaを起動してください。")
        return
    
    # クライアント初期化
    client = OllamaClient(
        base_url="http://localhost:11434",
        model=MODEL,
    )
    
    # パイプライン初期化
    pipeline = TextPipeline(
        ollama_client=client,
        chunk_size=CHUNK_SIZE,
        chunk_overlap=200,
        max_workers=MAX_WORKERS,
    )
    
    # 処理実行
    tree = pipeline.process_pdf(pdf_path)
    
    if tree:
        # 保存
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        json_path, md_path = pipeline.save_tree(tree, OUTPUT_DIR)
        
        print("\n" + "=" * 60)
        print("🎉 完了！")
        print("=" * 60)
        print(f"  📄 JSON: {json_path.name}")
        print(f"  📝 Markdown: {md_path.name}")
        print(f"  📁 保存先: {OUTPUT_DIR}")
        print("=" * 60)
    else:
        print("\n❌ 処理に失敗しました")


if __name__ == "__main__":
    main()