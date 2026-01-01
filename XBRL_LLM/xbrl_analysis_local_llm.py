#!/usr/bin/env python3
"""
XBRL決算解説生成スクリプト
- デスクトップのXBRL ZIPファイルから0104で始まるiXBRLファイルを抽出
- 企業ごとにグループ化して一括分析
- ローカルLLM（Ollama 9Bモデル）で決算解説を生成
"""

import os
import zipfile
import re
import json
import requests
from pathlib import Path
from bs4 import BeautifulSoup
from datetime import datetime
import argparse
from collections import defaultdict


# ============================================================
# 設定
# ============================================================
def get_desktop_path() -> Path:
    """Windows日本語環境対応のデスクトップパス取得"""
    home = Path.home()
    
    # 候補パス（優先順）
    candidates = [
        home / "Desktop",           # 英語Windows
        home / "デスクトップ",       # 日本語Windows
        home / "OneDrive" / "Desktop",          # OneDrive同期（英語）
        home / "OneDrive" / "デスクトップ",      # OneDrive同期（日本語）
    ]
    
    for path in candidates:
        if path.exists():
            return path
    
    # 見つからなければデフォルト
    return home / "Desktop"


class Config:
    # デスクトップのパス（Windows日本語環境対応）
    DESKTOP_PATH = get_desktop_path()
    
    # Ollama設定
    OLLAMA_BASE_URL = "http://localhost:11434"
    OLLAMA_MODEL = "gemma2:9b"  # Google製、日本語◎。代替: llama3.1:8b, elyza:jp8b
    
    # 出力ディレクトリ
    OUTPUT_DIR = get_desktop_path() / "xbrl_analysis_results"


# ============================================================
# ZIPファイル処理
# ============================================================
def find_company_folders(year_dir: Path) -> list[Path]:
    """年度ディレクトリ内の企業フォルダを検索"""
    company_folders = []
    
    for folder in year_dir.iterdir():
        if folder.is_dir() and folder.name.startswith('E'):
            company_folders.append(folder)
    
    return sorted(company_folders)


def extract_0104_from_company_folder(company_folder: Path) -> list[dict]:
    """企業フォルダ内の全ZIPから0104ファイルを抽出"""
    extracted = []
    
    # 企業フォルダ名からEDINETコードと企業名を取得
    folder_name = company_folder.name
    parts = folder_name.split('_', 1)
    edinet_code = parts[0] if parts else ''
    company_name = parts[1] if len(parts) > 1 else folder_name
    
    # フォルダ内のZIPを検索
    zip_files = list(company_folder.glob("*.zip"))
    
    for zip_path in zip_files:
        try:
            with zipfile.ZipFile(zip_path, 'r') as zf:
                for name in zf.namelist():
                    filename = os.path.basename(name)
                    if filename.startswith('0104') and filename.endswith('.htm'):
                        content = zf.read(name).decode('utf-8', errors='ignore')
                        
                        # 決算期情報を抽出
                        fiscal_info = extract_fiscal_info(content)
                        
                        extracted.append({
                            'zip_name': zip_path.name,
                            'file_name': filename,
                            'content': content,
                            'edinet_code': edinet_code,
                            'company_name': company_name,
                            'fiscal_year': fiscal_info.get('fiscal_year', ''),
                            'fiscal_period': fiscal_info.get('fiscal_period', ''),
                        })
        except Exception as e:
            print(f"    ⚠️ ZIP読み込みエラー: {zip_path.name} - {e}")
    
    return extracted


def extract_fiscal_info(html_content: str) -> dict:
    """iXBRLから決算期情報を抽出"""
    soup = BeautifulSoup(html_content, 'html.parser')
    
    info = {
        'fiscal_year': '',
        'fiscal_period': '',
    }
    
    for tag in soup.find_all(['ix:nonnumeric', 'ix:nonNumeric']):
        tag_name = tag.get('name', '').lower()
        value = tag.get_text(strip=True)
        
        if 'fiscalyear' in tag_name or 'currentfiscalyear' in tag_name:
            info['fiscal_year'] = value
        elif 'typeoffiscalperiod' in tag_name or 'fiscalperiod' in tag_name:
            info['fiscal_period'] = value
    
    return info


# ============================================================
# iXBRL解析
# ============================================================
def parse_ixbrl(html_content: str) -> dict:
    """iXBRLファイルから財務データを抽出"""
    soup = BeautifulSoup(html_content, 'html.parser')
    
    result = {
        'company_name': '',
        'fiscal_year_end': '',
        'document_type': '',
        'financial_data': {},
        'raw_text_sections': []
    }
    
    # タイトルから企業名を取得
    title = soup.find('title')
    if title:
        result['company_name'] = title.get_text(strip=True)
    
    # ix:nonNumeric と ix:nonFraction タグからデータ抽出
    # 主要な財務指標のタグ名パターン
    key_metrics = {
        'NetSales': '売上高',
        'Revenue': '売上収益',
        'OperatingIncome': '営業利益',
        'OperatingProfit': '営業利益',
        'OrdinaryIncome': '経常利益',
        'ProfitLoss': '当期純利益',
        'NetIncome': '当期純利益',
        'TotalAssets': '総資産',
        'NetAssets': '純資産',
        'Equity': '自己資本',
    }
    
    # ix:nonFraction（数値データ）の抽出
    for tag in soup.find_all(['ix:nonfraction', 'ix:nonFraction']):
        tag_name = tag.get('name', '')
        value = tag.get_text(strip=True)
        
        # 数値をパース
        try:
            # カンマ除去、単位考慮
            numeric_value = float(value.replace(',', '').replace('△', '-'))
            scale = int(tag.get('scale', 0))
            numeric_value *= (10 ** scale)
            
            # マッチするキー指標があれば保存
            for key, label in key_metrics.items():
                if key.lower() in tag_name.lower():
                    if label not in result['financial_data']:
                        result['financial_data'][label] = []
                    result['financial_data'][label].append({
                        'tag': tag_name,
                        'value': numeric_value,
                        'raw': value
                    })
                    break
        except:
            pass
    
    # テキストセクションの抽出（経営成績の概況など）
    text_sections = []
    
    # 主要な見出しパターン
    section_patterns = [
        r'経営成績',
        r'業績',
        r'財政状態',
        r'キャッシュ・フロー',
        r'今後の見通し',
        r'配当',
    ]
    
    # 本文テキストを抽出
    for p_tag in soup.find_all(['p', 'div']):
        text = p_tag.get_text(strip=True)
        if len(text) > 50:  # ある程度の長さがあるテキスト
            for pattern in section_patterns:
                if pattern in text:
                    text_sections.append(text[:2000])  # 長すぎる場合は切り詰め
                    break
    
    result['raw_text_sections'] = text_sections[:10]  # 最大10セクション
    
    return result


def create_financial_summary(parsed_data: dict) -> str:
    """財務データの要約テキストを生成"""
    lines = []
    
    lines.append(f"## 企業: {parsed_data['company_name']}")
    lines.append("")
    
    if parsed_data['financial_data']:
        lines.append("### 主要財務指標")
        for metric, values in parsed_data['financial_data'].items():
            if values:
                # 最初の値を使用（通常は当期）
                v = values[0]
                # 百万円単位で表示
                if v['value'] >= 1_000_000:
                    formatted = f"{v['value'] / 1_000_000:,.0f}百万円"
                else:
                    formatted = f"{v['value']:,.0f}円"
                lines.append(f"- {metric}: {formatted}")
        lines.append("")
    
    if parsed_data['raw_text_sections']:
        lines.append("### 開示文書からの抜粋")
        for i, section in enumerate(parsed_data['raw_text_sections'][:5], 1):
            lines.append(f"\n【セクション{i}】")
            lines.append(section[:500] + "..." if len(section) > 500 else section)
    
    return "\n".join(lines)


def create_company_summary(company_key: str, files_data: list[dict]) -> str:
    """企業の全決算ファイルを統合した要約を生成"""
    lines = []
    
    # 企業名を取得
    company_name = files_data[0].get('company_name', company_key)
    lines.append(f"# 企業: {company_name}")
    lines.append(f"**EDINET Code:** {files_data[0].get('edinet_code', '不明')}")
    lines.append(f"**分析対象ファイル数:** {len(files_data)}件")
    lines.append("")
    
    # 各期の財務データを時系列で整理
    all_periods = []
    
    for file_info in files_data:
        parsed = parse_ixbrl(file_info['content'])
        
        period_data = {
            'fiscal_year': file_info.get('fiscal_year', '不明'),
            'fiscal_period': file_info.get('fiscal_period', '不明'),
            'file_name': file_info['file_name'],
            'financial_data': parsed['financial_data'],
            'text_sections': parsed['raw_text_sections'],
        }
        all_periods.append(period_data)
    
    # 時系列で財務データを表示
    lines.append("## 📊 財務データ（時系列）")
    lines.append("")
    
    for period in all_periods:
        lines.append(f"### {period['fiscal_year']} {period['fiscal_period']}")
        lines.append(f"*ファイル: {period['file_name']}*")
        lines.append("")
        
        if period['financial_data']:
            for metric, values in period['financial_data'].items():
                if values:
                    v = values[0]
                    if v['value'] >= 1_000_000:
                        formatted = f"{v['value'] / 1_000_000:,.0f}百万円"
                    elif v['value'] >= 1_000:
                        formatted = f"{v['value'] / 1_000:,.0f}千円"
                    else:
                        formatted = f"{v['value']:,.0f}円"
                    lines.append(f"- {metric}: {formatted}")
        else:
            lines.append("- (財務データ抽出なし)")
        lines.append("")
    
    # 最新期のテキストセクションを追加
    if all_periods and all_periods[-1]['text_sections']:
        lines.append("## 📝 最新開示からの抜粋")
        for i, section in enumerate(all_periods[-1]['text_sections'][:3], 1):
            lines.append(f"\n【セクション{i}】")
            lines.append(section[:800] + "..." if len(section) > 800 else section)
    
    return "\n".join(lines)


# ============================================================
# ローカルLLM呼び出し
# ============================================================
def call_ollama(prompt: str, model: str = None) -> dict:
    """Ollamaを使ってローカルLLMを呼び出し（トークン情報付き）"""
    model = model or Config.OLLAMA_MODEL
    
    url = f"{Config.OLLAMA_BASE_URL}/api/generate"
    
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.3,
            "num_predict": 2048,
        }
    }
    
    try:
        print(f"  🤖 {model} で分析中...")
        response = requests.post(url, json=payload, timeout=300)
        response.raise_for_status()
        
        result = response.json()
        
        # トークン情報を抽出
        input_tokens = result.get('prompt_eval_count', 0)
        output_tokens = result.get('eval_count', 0)
        total_duration = result.get('total_duration', 0) / 1e9  # ナノ秒→秒
        
        print(f"  📊 トークン: 入力 {input_tokens:,} / 出力 {output_tokens:,} / 合計 {input_tokens + output_tokens:,}")
        print(f"  ⏱️ 処理時間: {total_duration:.1f}秒")
        
        return {
            'response': result.get('response', ''),
            'input_tokens': input_tokens,
            'output_tokens': output_tokens,
            'total_tokens': input_tokens + output_tokens,
            'duration_sec': total_duration,
        }
    
    except requests.exceptions.ConnectionError:
        return {
            'response': "❌ エラー: Ollamaに接続できません。",
            'input_tokens': 0,
            'output_tokens': 0,
            'total_tokens': 0,
            'duration_sec': 0,
        }
    except Exception as e:
        return {
            'response': f"❌ エラー: {str(e)}",
            'input_tokens': 0,
            'output_tokens': 0,
            'total_tokens': 0,
            'duration_sec': 0,
        }


def generate_analysis_prompt(financial_summary: str) -> str:
    """決算解説用のプロンプトを生成（複数期間対応）"""
    prompt = f"""あなたは日本の企業財務に詳しいアナリストです。
以下の企業の複数期間の決算データを分析し、包括的な決算解説を日本語で作成してください。

【分析のポイント】
1. 業績の推移と傾向（増収増益/減収減益のトレンド）
2. 各期の注目すべき数字とその変化
3. 前年同期比較や成長率の分析
4. 財務健全性の評価
5. 今後の展望や投資家への注目ポイント

【決算情報】
{financial_summary}

【回答形式】
- 見出しをつけて構造化
- 時系列での比較を重視
- 具体的な数字と変化率を含める
- 500-800字程度で詳しく

決算分析レポート:"""
    
    return prompt


# ============================================================
# メイン処理
# ============================================================
def process_company(company_key: str, files_data: list[dict], output_dir: Path) -> dict:
    """1企業の全ファイルを一括処理"""
    company_name = files_data[0].get('company_name', company_key)[:30]
    print(f"\n🏢 処理中: {company_name} ({len(files_data)}ファイル)")
    
    # 企業の統合サマリーを生成
    summary = create_company_summary(company_key, files_data)
    
    # LLM呼び出し
    prompt = generate_analysis_prompt(summary)
    llm_result = call_ollama(prompt)
    
    result = {
        'company_key': company_key,
        'company_name': files_data[0].get('company_name', ''),
        'edinet_code': files_data[0].get('edinet_code', ''),
        'file_count': len(files_data),
        'files': [f['file_name'] for f in files_data],
        'financial_summary': summary,
        'llm_analysis': llm_result['response'],
        'input_tokens': llm_result['input_tokens'],
        'output_tokens': llm_result['output_tokens'],
        'total_tokens': llm_result['total_tokens'],
        'duration_sec': llm_result['duration_sec'],
        'processed_at': datetime.now().isoformat()
    }
    
    # 結果を保存
    safe_name = re.sub(r'[\\/*?:"<>|]', '_', company_key)[:50]
    output_file = output_dir / f"{safe_name}_analysis.md"
    save_company_analysis_markdown(result, output_file)
    
    return result


def save_company_analysis_markdown(result: dict, output_path: Path):
    """企業分析結果をMarkdownで保存"""
    content = f"""# 決算分析レポート

**企業名:** {result['company_name']}  
**EDINETコード:** {result['edinet_code']}  
**分析ファイル数:** {result['file_count']}件  
**分析日時:** {result['processed_at']}

**📊 トークン情報:** 入力 {result.get('input_tokens', 0):,} / 出力 {result.get('output_tokens', 0):,} / 処理時間 {result.get('duration_sec', 0):.1f}秒

---

## 📊 財務データ要約

{result['financial_summary']}

---

## 🤖 AI決算分析

{result['llm_analysis']}

---

### 分析対象ファイル
{chr(10).join('- ' + f for f in result['files'])}

---

*このレポートはローカルLLM（{Config.OLLAMA_MODEL}）により自動生成されました。*
"""
    
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding='utf-8')
    print(f"  💾 保存: {output_path.name}")


def main():
    # デフォルトのEDINETダウンロードディレクトリ
    default_edinet_dir = get_desktop_path() / "EDINET_DL"
    
    parser = argparse.ArgumentParser(description='XBRL決算解説生成ツール（企業単位分析）')
    parser.add_argument('--dir', '-d', type=str, default=str(default_edinet_dir),
                        help='EDINETダウンロードディレクトリ')
    parser.add_argument('--year', '-y', type=str, default=None,
                        help='処理する年度（例: 2020）複数指定はカンマ区切り（例: 2020,2021）')
    parser.add_argument('--model', '-m', type=str, default=Config.OLLAMA_MODEL,
                        help='使用するOllamaモデル')
    parser.add_argument('--output', '-o', type=str, default=str(Config.OUTPUT_DIR),
                        help='出力ディレクトリ')
    parser.add_argument('--test', '-t', type=int, nargs='?', const=3, default=None,
                        help='テストモード：指定した企業数だけ処理（デフォルト3社）')
    parser.add_argument('--list', '-l', action='store_true',
                        help='企業一覧を表示して終了（処理しない）')
    
    args = parser.parse_args()
    
    # 設定更新
    Config.OLLAMA_MODEL = args.model
    Config.OUTPUT_DIR = Path(args.output)
    
    base_dir = Path(args.dir)
    
    # 年度ディレクトリを決定
    year_dirs = []
    if args.year:
        years = [y.strip() for y in args.year.split(',')]
        for year in years:
            year_dir = base_dir / year
            if year_dir.exists():
                year_dirs.append(year_dir)
            else:
                print(f"⚠️ 年度フォルダが見つかりません: {year_dir}")
    else:
        # 年度指定なし：サブフォルダを自動検出
        subdirs = [d for d in base_dir.iterdir() if d.is_dir() and d.name.isdigit()]
        if subdirs:
            print(f"📅 利用可能な年度: {', '.join(sorted(d.name for d in subdirs))}")
            print("💡 --year オプションで年度を指定してください")
            return
        else:
            print("⚠️ 年度フォルダが見つかりません")
            return
    
    print("=" * 60)
    print("🚀 XBRL決算解説生成ツール（企業単位分析）")
    print("=" * 60)
    print(f"📂 ベースディレクトリ: {args.dir}")
    print(f"📅 対象年度: {args.year}")
    if args.test:
        print(f"🧪 テストモード: {args.test}社のみ処理")
    if args.list:
        print(f"📋 一覧表示モード")
    print(f"🤖 使用モデル: {Config.OLLAMA_MODEL}")
    print(f"📁 出力先: {Config.OUTPUT_DIR}")
    print("=" * 60)
    
    # 企業フォルダを収集
    all_company_folders = []
    for year_dir in year_dirs:
        company_folders = find_company_folders(year_dir)
        for cf in company_folders:
            all_company_folders.append((year_dir.name, cf))
    
    print(f"\n📊 {len(all_company_folders)}社を検出")
    
    # 企業一覧を表示
    if args.list or args.test:
        print("\n企業一覧:")
        for i, (year, folder) in enumerate(all_company_folders[:50], 1):  # 最大50社表示
            print(f"  {i:3}. [{year}] {folder.name}")
        if len(all_company_folders) > 50:
            print(f"  ... 他 {len(all_company_folders) - 50}社")
    
    # 一覧表示モードなら終了
    if args.list:
        return
    
    # Ollamaの接続テスト
    try:
        response = requests.get(f"{Config.OLLAMA_BASE_URL}/api/tags", timeout=5)
        models = [m['name'] for m in response.json().get('models', [])]
        print(f"\n✅ Ollama接続OK - 利用可能モデル: {', '.join(models[:5])}...")
    except:
        print("\n❌ Ollamaに接続できません。以下を確認してください:")
        print("   1. Ollamaがインストールされているか")
        print("   2. タスクトレイにOllamaアイコンがあるか")
        print(f"   3. モデルがダウンロード済みか (`ollama pull {Config.OLLAMA_MODEL}`)")
        return
    
    # テストモードなら企業数を制限
    if args.test:
        all_company_folders = all_company_folders[:args.test]
        print(f"\n🧪 テストモード: {len(all_company_folders)}社を処理します")
    
    # 出力ディレクトリ作成
    Config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    # 企業ごとに処理
    print("\n" + "=" * 60)
    print("🤖 企業単位でLLM分析開始")
    print("=" * 60)
    
    all_results = []
    for i, (year, company_folder) in enumerate(all_company_folders, 1):
        print(f"\n[{i}/{len(all_company_folders)}] 🏢 {company_folder.name}")
        
        # 企業フォルダから0104ファイルを抽出
        files_data = extract_0104_from_company_folder(company_folder)
        
        if not files_data:
            print(f"  ⚠️ 0104ファイルなし、スキップ")
            continue
        
        print(f"  📄 {len(files_data)}ファイル発見")
        
        # 分析実行
        result = process_company(company_folder.name, files_data, Config.OUTPUT_DIR)
        result['year'] = year
        all_results.append(result)
    
    # 全体サマリー
    print("\n" + "=" * 60)
    print(f"✅ 完了: {len(all_results)}社の決算分析を生成")
    print(f"📁 出力先: {Config.OUTPUT_DIR}")
    
    # トークン統計
    if all_results:
        total_input = sum(r.get('input_tokens', 0) for r in all_results)
        total_output = sum(r.get('output_tokens', 0) for r in all_results)
        total_duration = sum(r.get('duration_sec', 0) for r in all_results)
        avg_input = total_input / len(all_results)
        avg_output = total_output / len(all_results)
        
        print("\n📊 トークン統計:")
        print(f"  合計入力: {total_input:,} tokens")
        print(f"  合計出力: {total_output:,} tokens")
        print(f"  合計: {total_input + total_output:,} tokens")
        print(f"  平均/企業: 入力 {avg_input:,.0f} / 出力 {avg_output:,.0f}")
        print(f"  総処理時間: {total_duration:.1f}秒 ({total_duration/60:.1f}分)")
    
    print("=" * 60)
    
    # 全結果をJSONでも保存
    if all_results:
        summary_file = Config.OUTPUT_DIR / "all_company_analyses.json"
        with open(summary_file, 'w', encoding='utf-8') as f:
            json_results = []
            for r in all_results:
                json_results.append({
                    'year': r.get('year', ''),
                    'company_key': r['company_key'],
                    'company_name': r['company_name'],
                    'edinet_code': r['edinet_code'],
                    'file_count': r['file_count'],
                    'files': r['files'],
                    'input_tokens': r.get('input_tokens', 0),
                    'output_tokens': r.get('output_tokens', 0),
                    'total_tokens': r.get('total_tokens', 0),
                    'duration_sec': r.get('duration_sec', 0),
                    'processed_at': r['processed_at'],
                })
            json.dump(json_results, f, ensure_ascii=False, indent=2)
        print(f"📋 全結果JSON: {summary_file}")


if __name__ == "__main__":
    main()