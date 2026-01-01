#!/usr/bin/env python3
"""
Financial Analysis System - メインエントリーポイント
数値パイプラインとテキストパイプラインを統合
"""

import argparse
from pathlib import Path
from datetime import datetime

# ローカルインポート
import sys
sys.path.insert(0, str(Path(__file__).parent))

from config import Config, get_desktop_path


def run_numeric_pipeline(edinet_code: str, year: int, config: Config):
    """数値パイプライン実行"""
    
    from pipeline_numeric.numeric_pipeline import NumericPipeline, NumericValidator
    
    print("\n" + "="*60)
    print("📊 数値パイプライン")
    print("="*60)
    
    pipeline = NumericPipeline()
    
    # XBRLから抽出（既存のStockFlowデータを使用）
    # TODO: 既存のparallel_edinet_collector.pyと連携
    
    print("⚠️ 数値パイプラインは既存のStockFlowインフラと連携予定")
    print("   現在はGoogle Sheetsのデータを使用してください")


def run_text_pipeline(pdf_path: Path, config: Config, save_to_sheets: bool = False,
                      spreadsheet_id: str = None):
    """テキストパイプライン実行"""
    
    from pipeline_text.text_pipeline import TextPipeline, OllamaClient
    
    print("\n" + "="*60)
    print("📄 テキストパイプライン（RAPTOR）")
    print("="*60)
    print(f"  📁 入力: {pdf_path.name}")
    print(f"  🤖 モデル: {config.OLLAMA_MODEL}")
    print(f"  📦 チャンクサイズ: {config.CHUNK_SIZE} tokens")
    print(f"  👷 並列ワーカー: {config.MAX_WORKERS}")
    
    # Ollamaクライアント
    client = OllamaClient(
        base_url=config.OLLAMA_BASE_URL,
        model=config.OLLAMA_MODEL,
    )
    
    # パイプライン
    pipeline = TextPipeline(
        ollama_client=client,
        chunk_size=config.CHUNK_SIZE,
        chunk_overlap=config.CHUNK_OVERLAP,
        max_workers=config.MAX_WORKERS,
    )
    
    # 処理実行
    tree = pipeline.process_pdf(pdf_path)
    
    if tree:
        # JSON/Markdown保存
        output_dir = config.OUTPUT_DIR
        output_dir.mkdir(parents=True, exist_ok=True)
        json_path, md_path = pipeline.save_tree(tree, output_dir)
        
        # Google Sheets保存（オプション）
        if save_to_sheets and spreadsheet_id:
            try:
                from pipeline_numeric.sheets_db import GoogleSheetsDB
                
                db = GoogleSheetsDB(config.GOOGLE_SHEETS_CREDENTIALS, spreadsheet_id)
                
                # RAPTOR要約を保存
                from dataclasses import asdict
                root_dict = asdict(tree.level_2_root)
                db.save_raptor_summary(tree.metadata, root_dict)
                
                print(f"  ☁️ Google Sheetsに保存完了")
            except Exception as e:
                print(f"  ⚠️ Google Sheets保存エラー: {e}")
        
        return tree
    
    return None


def run_analysis(query: str, edinet_code: str, year: str, config: Config):
    """統合分析実行"""
    
    from analyzer.integrated_analyzer import IntegratedAnalyzer
    
    print("\n" + "="*60)
    print("🔍 統合分析")
    print("="*60)
    
    analyzer = IntegratedAnalyzer(
        numeric_db_path=config.OUTPUT_DIR,
        chromadb_path=config.CHROMADB_DIR,
        ollama_base_url=config.OLLAMA_BASE_URL,
        ollama_model=config.OLLAMA_MODEL,
    )
    
    result = analyzer.analyze(query, edinet_code, year)
    
    print(f"\n【質問】\n{result.query}")
    print(f"\n【回答】\n{result.answer}")
    
    return result


def main():
    parser = argparse.ArgumentParser(
        description='Financial Analysis System - 有価証券報告書分析'
    )
    
    subparsers = parser.add_subparsers(dest='command', help='コマンド')
    
    # ========================================
    # text: テキストパイプライン
    # ========================================
    text_parser = subparsers.add_parser('text', help='PDFからRAPTORツリー構築')
    text_parser.add_argument('--pdf', '-p', type=str, required=True,
                            help='PDFファイルパス')
    text_parser.add_argument('--model', '-m', type=str, default='gemma2:27b',
                            help='Ollamaモデル')
    text_parser.add_argument('--workers', '-w', type=int, default=3,
                            help='並列ワーカー数')
    text_parser.add_argument('--chunk-size', '-c', type=int, default=2000,
                            help='チャンクサイズ（トークン）')
    text_parser.add_argument('--sheets', '-s', type=str, default=None,
                            help='Google Sheets ID（保存先）')
    text_parser.add_argument('--output', '-o', type=str, default=None,
                            help='出力ディレクトリ')
    
    # ========================================
    # numeric: 数値パイプライン
    # ========================================
    numeric_parser = subparsers.add_parser('numeric', help='XBRL数値抽出')
    numeric_parser.add_argument('--code', '-c', type=str, required=True,
                               help='EDINETコード')
    numeric_parser.add_argument('--year', '-y', type=int, required=True,
                               help='年度')
    
    # ========================================
    # analyze: 統合分析
    # ========================================
    analyze_parser = subparsers.add_parser('analyze', help='統合分析実行')
    analyze_parser.add_argument('--query', '-q', type=str, required=True,
                               help='分析クエリ')
    analyze_parser.add_argument('--code', '-c', type=str, required=True,
                               help='EDINETコード')
    analyze_parser.add_argument('--year', '-y', type=str, required=True,
                               help='年度')
    
    # ========================================
    # report: レポート生成
    # ========================================
    report_parser = subparsers.add_parser('report', help='総合レポート生成')
    report_parser.add_argument('--code', '-c', type=str, required=True,
                              help='EDINETコード')
    report_parser.add_argument('--year', '-y', type=str, required=True,
                              help='年度')
    report_parser.add_argument('--output', '-o', type=str, default=None,
                              help='出力ファイルパス')
    
    args = parser.parse_args()
    
    # 設定
    config = Config()
    
    print("="*60)
    print("🏦 Financial Analysis System")
    print("="*60)
    print(f"📂 出力先: {config.OUTPUT_DIR}")
    print(f"🤖 モデル: {config.OLLAMA_MODEL}")
    
    if args.command == 'text':
        # テキストパイプライン
        config.OLLAMA_MODEL = args.model
        config.MAX_WORKERS = args.workers
        config.CHUNK_SIZE = args.chunk_size
        
        if args.output:
            config.OUTPUT_DIR = Path(args.output)
        
        pdf_path = Path(args.pdf)
        if not pdf_path.exists():
            # PDFディレクトリ内を検索
            pdf_path = config.PDF_DIR / args.pdf
        
        if pdf_path.exists():
            run_text_pipeline(
                pdf_path, 
                config, 
                save_to_sheets=bool(args.sheets),
                spreadsheet_id=args.sheets
            )
        else:
            print(f"❌ PDFが見つかりません: {args.pdf}")
    
    elif args.command == 'numeric':
        # 数値パイプライン
        run_numeric_pipeline(args.code, args.year, config)
    
    elif args.command == 'analyze':
        # 統合分析
        run_analysis(args.query, args.code, args.year, config)
    
    elif args.command == 'report':
        # レポート生成
        from analyzer.integrated_analyzer import IntegratedAnalyzer
        
        analyzer = IntegratedAnalyzer(
            numeric_db_path=config.OUTPUT_DIR,
            chromadb_path=config.CHROMADB_DIR,
        )
        
        report = analyzer.generate_report(args.code, args.year)
        
        if args.output:
            output_path = Path(args.output)
        else:
            output_path = config.OUTPUT_DIR / f"{args.code}_{args.year}_report.md"
        
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(report)
        
        print(f"📄 レポート保存: {output_path}")
    
    else:
        parser.print_help()


if __name__ == "__main__":
    main()