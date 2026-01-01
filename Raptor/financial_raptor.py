#!/usr/bin/env python3
"""
Financial RAPTOR - 有価証券報告書PDF階層化システム
- PDFからテキスト抽出
- チャンク分割
- LLMでセクション判定・要約
- 階層ツリー構築
"""

import os
import json
import re
import argparse
import requests
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, asdict
from typing import Optional

import pdfplumber
import tiktoken


# ============================================================
# 設定
# ============================================================
def get_desktop_path() -> Path:
    """Windows日本語環境対応のデスクトップパス取得"""
    home = Path.home()
    candidates = [
        home / "Desktop",
        home / "デスクトップ",
        home / "OneDrive" / "Desktop",
        home / "OneDrive" / "デスクトップ",
    ]
    for path in candidates:
        if path.exists():
            return path
    return home / "Desktop"


class Config:
    # PDFフォルダ
    PDF_DIR = get_desktop_path() / "PDF"
    
    # 出力ディレクトリ
    OUTPUT_DIR = get_desktop_path() / "financial_raptor_output"
    
    # Ollama設定
    OLLAMA_BASE_URL = "http://localhost:11434"
    OLLAMA_MODEL = "gemma2:27b"
    
    # チャンク設定
    CHUNK_SIZE = 1000  # トークン数
    CHUNK_OVERLAP = 100  # オーバーラップ
    
    # RAPTOR設定
    MAX_TREE_DEPTH = 3  # ツリーの最大深さ


# ============================================================
# トークンカウント
# ============================================================
def count_tokens(text: str) -> int:
    """テキストのトークン数をカウント"""
    try:
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except:
        # フォールバック: 文字数ベースの推定
        return len(text) // 3


# ============================================================
# PDF処理
# ============================================================
def extract_text_from_pdf(pdf_path: Path) -> dict:
    """PDFからテキストを抽出（ページ単位）"""
    print(f"  📄 PDF読み込み中: {pdf_path.name}")
    
    pages = []
    total_chars = 0
    
    try:
        with pdfplumber.open(pdf_path) as pdf:
            print(f"  📑 総ページ数: {len(pdf.pages)}")
            
            for i, page in enumerate(pdf.pages):
                text = page.extract_text() or ""
                text = clean_text(text)
                
                if text.strip():
                    pages.append({
                        'page_num': i + 1,
                        'text': text,
                        'char_count': len(text),
                    })
                    total_chars += len(text)
                
                # 進捗表示（50ページごと）
                if (i + 1) % 50 == 0:
                    print(f"    ... {i + 1}ページ処理済み")
    
    except Exception as e:
        print(f"  ❌ PDF読み込みエラー: {e}")
        return None
    
    print(f"  ✅ 抽出完了: {len(pages)}ページ, {total_chars:,}文字")
    
    return {
        'file_name': pdf_path.name,
        'file_path': str(pdf_path),
        'total_pages': len(pages),
        'total_chars': total_chars,
        'pages': pages,
    }


def clean_text(text: str) -> str:
    """テキストのクリーニング"""
    # 連続する空白を1つに
    text = re.sub(r'[ \t]+', ' ', text)
    # 連続する改行を2つまでに
    text = re.sub(r'\n{3,}', '\n\n', text)
    # 前後の空白除去
    text = text.strip()
    return text


# ============================================================
# チャンク分割
# ============================================================
def split_into_chunks(pdf_data: dict, chunk_size: int = 1000, overlap: int = 100) -> list[dict]:
    """PDFデータをチャンクに分割"""
    print(f"  📦 チャンク分割中 (サイズ: {chunk_size}トークン)")
    
    # 全テキストを結合
    full_text = "\n\n".join([p['text'] for p in pdf_data['pages']])
    
    # 段落単位で分割
    paragraphs = re.split(r'\n{2,}', full_text)
    
    chunks = []
    current_chunk = ""
    current_tokens = 0
    chunk_id = 0
    
    for para in paragraphs:
        para_tokens = count_tokens(para)
        
        # 段落単体が大きすぎる場合は文単位で分割
        if para_tokens > chunk_size:
            sentences = re.split(r'(?<=[。．！？])', para)
            for sent in sentences:
                sent_tokens = count_tokens(sent)
                if current_tokens + sent_tokens > chunk_size and current_chunk:
                    # チャンク確定
                    chunks.append({
                        'chunk_id': chunk_id,
                        'text': current_chunk.strip(),
                        'token_count': current_tokens,
                    })
                    chunk_id += 1
                    # オーバーラップ処理
                    overlap_text = current_chunk[-overlap*3:] if len(current_chunk) > overlap*3 else ""
                    current_chunk = overlap_text + sent
                    current_tokens = count_tokens(current_chunk)
                else:
                    current_chunk += sent
                    current_tokens += sent_tokens
        else:
            if current_tokens + para_tokens > chunk_size and current_chunk:
                # チャンク確定
                chunks.append({
                    'chunk_id': chunk_id,
                    'text': current_chunk.strip(),
                    'token_count': current_tokens,
                })
                chunk_id += 1
                # オーバーラップ処理
                overlap_text = current_chunk[-overlap*3:] if len(current_chunk) > overlap*3 else ""
                current_chunk = overlap_text + "\n\n" + para
                current_tokens = count_tokens(current_chunk)
            else:
                current_chunk += "\n\n" + para
                current_tokens += para_tokens
    
    # 最後のチャンク
    if current_chunk.strip():
        chunks.append({
            'chunk_id': chunk_id,
            'text': current_chunk.strip(),
            'token_count': count_tokens(current_chunk),
        })
    
    print(f"  ✅ {len(chunks)}チャンクに分割完了")
    
    return chunks


# ============================================================
# セクション判定（有価証券報告書用）
# ============================================================
FINANCIAL_SECTIONS = [
    "表紙・目次",
    "企業の概況",
    "事業の状況",
    "経営成績の分析",
    "財政状態の分析",
    "キャッシュ・フローの状況",
    "設備の状況",
    "提出会社の状況",
    "経理の状況",
    "連結財務諸表",
    "個別財務諸表",
    "株式の状況",
    "配当政策",
    "コーポレートガバナンス",
    "事業等のリスク",
    "経営上の重要な契約",
    "研究開発活動",
    "その他",
]


def classify_and_summarize_chunk(chunk: dict, model: str = None) -> dict:
    """LLMでチャンクのセクション判定と要約を生成"""
    model = model or Config.OLLAMA_MODEL
    
    prompt = f"""あなたは日本の有価証券報告書の専門家です。
以下のテキストを分析し、JSONで回答してください。

【タスク】
1. このテキストが該当するセクションを判定
2. 50-100字程度の要約を作成
3. 重要な数値があれば抽出

【セクション選択肢】
{chr(10).join(f'- {s}' for s in FINANCIAL_SECTIONS)}

【テキスト】
{chunk['text'][:3000]}

【回答形式】必ずJSON形式で回答
{{
    "section": "セクション名",
    "summary": "要約文",
    "key_numbers": ["重要な数値1", "重要な数値2"],
    "importance": "high/medium/low"
}}

JSON回答:"""

    result = call_ollama(prompt, model)
    
    # JSONパース
    try:
        # JSON部分を抽出
        json_match = re.search(r'\{[^{}]*\}', result['response'], re.DOTALL)
        if json_match:
            parsed = json.loads(json_match.group())
            return {
                'section': parsed.get('section', 'その他'),
                'summary': parsed.get('summary', ''),
                'key_numbers': parsed.get('key_numbers', []),
                'importance': parsed.get('importance', 'medium'),
                'input_tokens': result['input_tokens'],
                'output_tokens': result['output_tokens'],
            }
    except json.JSONDecodeError:
        pass
    
    # パース失敗時のフォールバック
    return {
        'section': 'その他',
        'summary': chunk['text'][:100] + '...',
        'key_numbers': [],
        'importance': 'medium',
        'input_tokens': result.get('input_tokens', 0),
        'output_tokens': result.get('output_tokens', 0),
    }


# ============================================================
# 階層要約（RAPTOR）
# ============================================================
def generate_section_summary(section_name: str, chunks: list[dict], model: str = None) -> dict:
    """セクション内のチャンクを統合して上位要約を生成"""
    model = model or Config.OLLAMA_MODEL
    
    # チャンクの要約を結合
    combined_summaries = "\n".join([
        f"- {c['summary']}" for c in chunks if c.get('summary')
    ])
    
    key_numbers = []
    for c in chunks:
        key_numbers.extend(c.get('key_numbers', []))
    key_numbers = list(set(key_numbers))[:10]  # 重複排除、最大10個
    
    prompt = f"""以下の「{section_name}」セクションの要約群を統合し、
より抽象度の高い要約を作成してください。

【個別要約】
{combined_summaries[:4000]}

【主要数値】
{', '.join(key_numbers) if key_numbers else 'なし'}

【タスク】
1. 100-200字の統合要約を作成
2. このセクションの重要ポイントを3つ挙げる
3. 投資判断に関わる注目点を1つ挙げる

【回答形式】JSON
{{
    "integrated_summary": "統合要約",
    "key_points": ["ポイント1", "ポイント2", "ポイント3"],
    "investment_insight": "投資判断への示唆"
}}

JSON回答:"""

    result = call_ollama(prompt, model)
    
    try:
        json_match = re.search(r'\{[^{}]*\}', result['response'], re.DOTALL)
        if json_match:
            parsed = json.loads(json_match.group())
            return {
                'section': section_name,
                'summary': parsed.get('integrated_summary', ''),
                'key_points': parsed.get('key_points', []),
                'investment_insight': parsed.get('investment_insight', ''),
                'chunk_count': len(chunks),
                'input_tokens': result['input_tokens'],
                'output_tokens': result['output_tokens'],
            }
    except:
        pass
    
    return {
        'section': section_name,
        'summary': combined_summaries[:200],
        'key_points': [],
        'investment_insight': '',
        'chunk_count': len(chunks),
        'input_tokens': result.get('input_tokens', 0),
        'output_tokens': result.get('output_tokens', 0),
    }


def generate_document_summary(section_summaries: list[dict], company_name: str, model: str = None) -> dict:
    """全セクションを統合してドキュメント全体の要約を生成（RAPTORのルートノード）"""
    model = model or Config.OLLAMA_MODEL
    
    sections_text = "\n\n".join([
        f"【{s['section']}】\n{s['summary']}\n重要点: {', '.join(s.get('key_points', []))}"
        for s in section_summaries
    ])
    
    prompt = f"""以下は「{company_name}」の有価証券報告書の各セクション要約です。
これらを統合し、投資家向けの総合分析を作成してください。

【セクション要約】
{sections_text[:6000]}

【タスク】
1. 300-500字の総合要約（企業の全体像）
2. 強み3つ、弱み/リスク3つ
3. 投資判断のための結論

【回答形式】JSON
{{
    "executive_summary": "総合要約",
    "strengths": ["強み1", "強み2", "強み3"],
    "weaknesses": ["弱み/リスク1", "弱み/リスク2", "弱み/リスク3"],
    "investment_conclusion": "投資判断への結論"
}}

JSON回答:"""

    result = call_ollama(prompt, model)
    
    try:
        json_match = re.search(r'\{[^{}]*\}', result['response'], re.DOTALL)
        if json_match:
            parsed = json.loads(json_match.group())
            return {
                'level': 'root',
                'company': company_name,
                'executive_summary': parsed.get('executive_summary', ''),
                'strengths': parsed.get('strengths', []),
                'weaknesses': parsed.get('weaknesses', []),
                'investment_conclusion': parsed.get('investment_conclusion', ''),
                'input_tokens': result['input_tokens'],
                'output_tokens': result['output_tokens'],
            }
    except:
        pass
    
    return {
        'level': 'root',
        'company': company_name,
        'executive_summary': '要約生成に失敗しました',
        'strengths': [],
        'weaknesses': [],
        'investment_conclusion': '',
        'input_tokens': result.get('input_tokens', 0),
        'output_tokens': result.get('output_tokens', 0),
    }


# ============================================================
# LLM呼び出し
# ============================================================
def call_ollama(prompt: str, model: str = None) -> dict:
    """Ollamaを使ってローカルLLMを呼び出し"""
    model = model or Config.OLLAMA_MODEL
    
    url = f"{Config.OLLAMA_BASE_URL}/api/generate"
    
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.3,
            "num_predict": 1024,
            "num_ctx": 8192,
        }
    }
    
    try:
        response = requests.post(url, json=payload, timeout=300)
        response.raise_for_status()
        result = response.json()
        
        return {
            'response': result.get('response', ''),
            'input_tokens': result.get('prompt_eval_count', 0),
            'output_tokens': result.get('eval_count', 0),
        }
    
    except Exception as e:
        return {
            'response': f'エラー: {str(e)}',
            'input_tokens': 0,
            'output_tokens': 0,
        }


# ============================================================
# RAPTORツリー構築
# ============================================================
def build_raptor_tree(pdf_path: Path, model: str = None) -> dict:
    """PDFからRAPTORツリーを構築"""
    print(f"\n{'='*60}")
    print(f"🌳 RAPTOR Tree構築: {pdf_path.name}")
    print(f"{'='*60}")
    
    # 企業名をファイル名から推定
    company_name = pdf_path.stem.replace('_', ' ')
    
    # Phase 1: PDF読み込み
    print("\n📖 Phase 1: PDF読み込み")
    pdf_data = extract_text_from_pdf(pdf_path)
    if not pdf_data:
        return None
    
    # Phase 2: チャンク分割
    print("\n📦 Phase 2: チャンク分割")
    chunks = split_into_chunks(pdf_data, Config.CHUNK_SIZE, Config.CHUNK_OVERLAP)
    
    # Phase 3: セクション判定・要約（Level 0）
    print(f"\n🏷️ Phase 3: セクション判定・要約 ({len(chunks)}チャンク)")
    total_input_tokens = 0
    total_output_tokens = 0
    
    for i, chunk in enumerate(chunks):
        print(f"  [{i+1}/{len(chunks)}] チャンク処理中...", end="")
        result = classify_and_summarize_chunk(chunk, model)
        chunk.update(result)
        total_input_tokens += result.get('input_tokens', 0)
        total_output_tokens += result.get('output_tokens', 0)
        print(f" → {result['section'][:15]}...")
    
    print(f"  📊 Level 0 トークン: 入力 {total_input_tokens:,} / 出力 {total_output_tokens:,}")
    
    # Phase 4: セクション別グループ化・統合要約（Level 1）
    print("\n📊 Phase 4: セクション統合要約")
    section_groups = {}
    for chunk in chunks:
        section = chunk.get('section', 'その他')
        if section not in section_groups:
            section_groups[section] = []
        section_groups[section].append(chunk)
    
    section_summaries = []
    for section_name, section_chunks in section_groups.items():
        print(f"  📁 {section_name} ({len(section_chunks)}チャンク)")
        summary = generate_section_summary(section_name, section_chunks, model)
        section_summaries.append(summary)
        total_input_tokens += summary.get('input_tokens', 0)
        total_output_tokens += summary.get('output_tokens', 0)
    
    print(f"  📊 Level 1 累計トークン: 入力 {total_input_tokens:,} / 出力 {total_output_tokens:,}")
    
    # Phase 5: ドキュメント全体要約（Level 2 / Root）
    print("\n🎯 Phase 5: 全体要約（Root Node）")
    root_summary = generate_document_summary(section_summaries, company_name, model)
    total_input_tokens += root_summary.get('input_tokens', 0)
    total_output_tokens += root_summary.get('output_tokens', 0)
    
    # RAPTORツリー構築
    raptor_tree = {
        'metadata': {
            'company_name': company_name,
            'source_file': pdf_path.name,
            'total_pages': pdf_data['total_pages'],
            'total_chars': pdf_data['total_chars'],
            'total_chunks': len(chunks),
            'total_sections': len(section_summaries),
            'total_input_tokens': total_input_tokens,
            'total_output_tokens': total_output_tokens,
            'model': model or Config.OLLAMA_MODEL,
            'created_at': datetime.now().isoformat(),
        },
        'tree': {
            'level_2_root': root_summary,
            'level_1_sections': section_summaries,
            'level_0_chunks': chunks,
        }
    }
    
    print(f"\n{'='*60}")
    print(f"✅ RAPTOR Tree構築完了")
    print(f"📊 総トークン: 入力 {total_input_tokens:,} / 出力 {total_output_tokens:,}")
    print(f"🌳 構造: Root → {len(section_summaries)}セクション → {len(chunks)}チャンク")
    print(f"{'='*60}")
    
    return raptor_tree


# ============================================================
# 保存
# ============================================================
def save_raptor_tree(tree: dict, output_dir: Path):
    """RAPTORツリーをJSON/Markdownで保存"""
    output_dir.mkdir(parents=True, exist_ok=True)
    
    company_name = tree['metadata']['company_name']
    safe_name = re.sub(r'[\\/*?:"<>|]', '_', company_name)[:50]
    
    # JSON保存
    json_path = output_dir / f"{safe_name}_raptor_tree.json"
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(tree, f, ensure_ascii=False, indent=2)
    print(f"💾 JSON保存: {json_path.name}")
    
    # Markdown保存（人間向け）
    md_path = output_dir / f"{safe_name}_raptor_summary.md"
    md_content = generate_markdown_report(tree)
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(md_content)
    print(f"💾 Markdown保存: {md_path.name}")
    
    return json_path, md_path


def generate_markdown_report(tree: dict) -> str:
    """RAPTORツリーからMarkdownレポートを生成"""
    meta = tree['metadata']
    root = tree['tree']['level_2_root']
    sections = tree['tree']['level_1_sections']
    
    md = f"""# Financial RAPTOR Analysis Report

**企業名:** {meta['company_name']}  
**ソースファイル:** {meta['source_file']}  
**総ページ数:** {meta['total_pages']}  
**分析日時:** {meta['created_at']}  
**使用モデル:** {meta['model']}

---

## 📊 エグゼクティブサマリー（Level 2: Root）

{root.get('executive_summary', 'N/A')}

### 💪 強み
{chr(10).join(f'- {s}' for s in root.get('strengths', []))}

### ⚠️ リスク・課題
{chr(10).join(f'- {w}' for w in root.get('weaknesses', []))}

### 🎯 投資判断への示唆
{root.get('investment_conclusion', 'N/A')}

---

## 📁 セクション別分析（Level 1）

"""
    
    for section in sections:
        md += f"""### {section['section']}
**チャンク数:** {section.get('chunk_count', 0)}

{section.get('summary', 'N/A')}

**重要ポイント:**
{chr(10).join(f'- {p}' for p in section.get('key_points', []))}

**投資視点:** {section.get('investment_insight', 'N/A')}

---

"""
    
    md += f"""
## 📈 処理統計

| 項目 | 値 |
|------|-----|
| 総チャンク数 | {meta['total_chunks']} |
| 総セクション数 | {meta['total_sections']} |
| 入力トークン | {meta['total_input_tokens']:,} |
| 出力トークン | {meta['total_output_tokens']:,} |
| 総文字数 | {meta['total_chars']:,} |

---

*このレポートはFinancial RAPTOR（{meta['model']}）により自動生成されました。*
"""
    
    return md


# ============================================================
# メイン処理
# ============================================================
def main():
    parser = argparse.ArgumentParser(description='Financial RAPTOR - 有価証券報告書階層化システム')
    parser.add_argument('--dir', '-d', type=str, default=str(Config.PDF_DIR),
                        help='PDFファイルのディレクトリ')
    parser.add_argument('--output', '-o', type=str, default=str(Config.OUTPUT_DIR),
                        help='出力ディレクトリ')
    parser.add_argument('--model', '-m', type=str, default=Config.OLLAMA_MODEL,
                        help='使用するOllamaモデル')
    parser.add_argument('--test', '-t', type=int, nargs='?', const=1, default=None,
                        help='テストモード：指定した数のPDFのみ処理')
    parser.add_argument('--list', '-l', action='store_true',
                        help='PDFファイル一覧を表示')
    parser.add_argument('--file', '-f', type=str, default=None,
                        help='特定のPDFファイルのみ処理')
    
    args = parser.parse_args()
    
    Config.OLLAMA_MODEL = args.model
    Config.OUTPUT_DIR = Path(args.output)
    pdf_dir = Path(args.dir)
    
    print("=" * 60)
    print("🌳 Financial RAPTOR - 有価証券報告書階層化システム")
    print("=" * 60)
    print(f"📂 PDFディレクトリ: {pdf_dir}")
    print(f"📁 出力先: {Config.OUTPUT_DIR}")
    print(f"🤖 使用モデル: {Config.OLLAMA_MODEL}")
    
    # PDFファイル検索
    if args.file:
        pdf_files = [Path(args.file)]
        if not pdf_files[0].exists():
            pdf_files = [pdf_dir / args.file]
    else:
        pdf_files = list(pdf_dir.glob("*.pdf"))
    
    if not pdf_files:
        print(f"\n⚠️ PDFファイルが見つかりません: {pdf_dir}")
        return
    
    print(f"\n📄 {len(pdf_files)}個のPDFファイルを発見")
    
    # 一覧表示
    if args.list:
        print("\nPDFファイル一覧:")
        for i, pdf in enumerate(pdf_files, 1):
            size_mb = pdf.stat().st_size / (1024 * 1024)
            print(f"  {i:3}. {pdf.name} ({size_mb:.1f}MB)")
        return
    
    # テストモード
    if args.test:
        pdf_files = pdf_files[:args.test]
        print(f"\n🧪 テストモード: {len(pdf_files)}ファイルのみ処理")
    
    # Ollama接続テスト
    try:
        response = requests.get(f"{Config.OLLAMA_BASE_URL}/api/tags", timeout=5)
        models = [m['name'] for m in response.json().get('models', [])]
        print(f"\n✅ Ollama接続OK - 利用可能モデル: {', '.join(models[:5])}...")
    except:
        print("\n❌ Ollamaに接続できません")
        return
    
    # 処理実行
    all_results = []
    for i, pdf_path in enumerate(pdf_files, 1):
        print(f"\n[{i}/{len(pdf_files)}]")
        
        tree = build_raptor_tree(pdf_path, args.model)
        
        if tree:
            json_path, md_path = save_raptor_tree(tree, Config.OUTPUT_DIR)
            all_results.append({
                'file': pdf_path.name,
                'json': str(json_path),
                'md': str(md_path),
                'stats': tree['metadata'],
            })
    
    # 全体サマリー
    print("\n" + "=" * 60)
    print(f"✅ 完了: {len(all_results)}ファイルのRAPTORツリーを構築")
    print(f"📁 出力先: {Config.OUTPUT_DIR}")
    
    if all_results:
        total_input = sum(r['stats']['total_input_tokens'] for r in all_results)
        total_output = sum(r['stats']['total_output_tokens'] for r in all_results)
        print(f"\n📊 総トークン統計:")
        print(f"  入力: {total_input:,} tokens")
        print(f"  出力: {total_output:,} tokens")
    print("=" * 60)


if __name__ == "__main__":
    main()