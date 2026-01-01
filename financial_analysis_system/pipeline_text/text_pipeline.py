#!/usr/bin/env python3
"""
テキストパイプライン - PDF → RAPTOR → ChromaDB
並列処理対応版
"""

import os
import json
import re
import hashlib
from pathlib import Path
from dataclasses import dataclass, asdict, field
from typing import Optional, List, Dict, Any
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

import pdfplumber
import requests


# ============================================================
# データ構造
# ============================================================
@dataclass
class TextChunk:
    """テキストチャンク"""
    chunk_id: str
    text: str
    token_count: int
    page_start: int
    page_end: int
    
    # RAPTOR処理後に追加
    section: str = ""
    summary: str = ""
    key_points: List[str] = field(default_factory=list)
    importance: str = "medium"  # high/medium/low


@dataclass 
class SectionSummary:
    """セクション要約（Level 1）"""
    section_name: str
    summary: str
    key_points: List[str]
    investment_insight: str
    chunk_ids: List[str]
    chunk_count: int


@dataclass
class DocumentSummary:
    """ドキュメント全体要約（Level 2 / Root）"""
    company_name: str
    executive_summary: str
    strengths: List[str]
    weaknesses: List[str]
    investment_conclusion: str


@dataclass
class RAPTORTree:
    """RAPTORツリー全体"""
    metadata: Dict[str, Any]
    level_0_chunks: List[TextChunk]
    level_1_sections: List[SectionSummary]
    level_2_root: DocumentSummary
    
    def to_dict(self) -> dict:
        return {
            'metadata': self.metadata,
            'level_0_chunks': [asdict(c) for c in self.level_0_chunks],
            'level_1_sections': [asdict(s) for s in self.level_1_sections],
            'level_2_root': asdict(self.level_2_root),
        }
    
    def generate_summary_header(self) -> str:
        """出力ファイル用のヘッダー情報を生成"""
        m = self.metadata
        return f"""# Financial RAPTOR Analysis Report

## 📋 実行情報

| 項目 | 値 |
|------|-----|
| 企業名 | {m.get('company_name', 'N/A')} |
| ソースファイル | {m.get('source_file', 'N/A')} |
| 実行開始 | {m.get('start_time', 'N/A')} |
| 実行終了 | {m.get('end_time', 'N/A')} |
| **総実行時間** | **{m.get('processing_time_formatted', 'N/A')}** |

## ⚙️ 処理設定

| 設定項目 | 値 |
|---------|-----|
| 使用モデル | {m.get('model', 'N/A')} |
| チャンクサイズ | {m.get('chunk_size', 'N/A')} tokens |
| チャンクオーバーラップ | {m.get('chunk_overlap', 'N/A')} tokens |
| 並列ワーカー数 | {m.get('max_workers', 'N/A')} |
| コンテキストウィンドウ | {m.get('context_window', 'N/A')} tokens |

## 📊 処理統計

| 項目 | 値 |
|------|-----|
| 総ページ数 | {m.get('total_pages', 0):,} |
| 総チャンク数 | {m.get('total_chunks', 0):,} |
| 総セクション数 | {m.get('total_sections', 0):,} |
| 入力トークン | {m.get('total_input_tokens', 0):,} |
| 出力トークン | {m.get('total_output_tokens', 0):,} |
| **合計トークン** | **{m.get('total_input_tokens', 0) + m.get('total_output_tokens', 0):,}** |
| 総文字数 | {m.get('total_chars', 0):,} |

## ⏱️ フェーズ別実行時間

| フェーズ | 時間 |
|---------|------|
| PDF読み込み | {m.get('phase1_duration', 'N/A')} |
| チャンク分割 | {m.get('phase2_duration', 'N/A')} |
| チャンク処理（並列） | {m.get('phase3_duration', 'N/A')} |
| セクション統合 | {m.get('phase4_duration', 'N/A')} |
| 全体要約 | {m.get('phase5_duration', 'N/A')} |

---
"""


# ============================================================
# 有価証券報告書のセクション定義
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
    "研究開発活動",
    "その他",
]


# ============================================================
# 並列処理対応のOllamaクライアント
# ============================================================
class OllamaClient:
    """スレッドセーフなOllamaクライアント"""
    
    def __init__(self, base_url: str = "http://localhost:11434", 
                 model: str = "gemma2:27b"):
        self.base_url = base_url
        self.model = model
        self._lock = threading.Lock()
        self._request_count = 0
    
    def generate(self, prompt: str, temperature: float = 0.3,
                 max_tokens: int = 1024) -> dict:
        """LLM生成（スレッドセーフ）"""
        
        url = f"{self.base_url}/api/generate"
        
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
                "num_ctx": 8192,
            }
        }
        
        try:
            response = requests.post(url, json=payload, timeout=300)
            response.raise_for_status()
            result = response.json()
            
            with self._lock:
                self._request_count += 1
            
            return {
                'response': result.get('response', ''),
                'input_tokens': result.get('prompt_eval_count', 0),
                'output_tokens': result.get('eval_count', 0),
                'duration_ms': result.get('total_duration', 0) / 1_000_000,
            }
        
        except Exception as e:
            return {
                'response': '',
                'input_tokens': 0,
                'output_tokens': 0,
                'error': str(e),
            }
    
    @property
    def request_count(self) -> int:
        with self._lock:
            return self._request_count


# ============================================================
# テキストパイプライン
# ============================================================
class TextPipeline:
    """テキストパイプライン - PDF → RAPTOR → ChromaDB"""
    
    def __init__(self, ollama_client: OllamaClient = None,
                 chunk_size: int = 2000,
                 chunk_overlap: int = 200,
                 max_workers: int = 3):
        
        self.client = ollama_client or OllamaClient()
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.max_workers = max_workers
        
        # 統計
        self.stats = {
            'total_input_tokens': 0,
            'total_output_tokens': 0,
            'total_duration_ms': 0,
            'chunks_processed': 0,
        }
        self._stats_lock = threading.Lock()
    
    def _update_stats(self, result: dict):
        """統計を更新（スレッドセーフ）"""
        with self._stats_lock:
            self.stats['total_input_tokens'] += result.get('input_tokens', 0)
            self.stats['total_output_tokens'] += result.get('output_tokens', 0)
            self.stats['total_duration_ms'] += result.get('duration_ms', 0)
            self.stats['chunks_processed'] += 1
    
    # ========================================
    # Phase 1: PDF読み込み
    # ========================================
    def extract_text_from_pdf(self, pdf_path: Path) -> List[dict]:
        """PDFからテキストを抽出（ページ単位）"""
        
        print(f"  📄 PDF読み込み中: {pdf_path.name}")
        pages = []
        
        try:
            with pdfplumber.open(pdf_path) as pdf:
                print(f"  📑 総ページ数: {len(pdf.pages)}")
                
                for i, page in enumerate(pdf.pages):
                    text = page.extract_text() or ""
                    text = self._clean_text(text)
                    
                    if text.strip():
                        pages.append({
                            'page_num': i + 1,
                            'text': text,
                        })
                    
                    if (i + 1) % 50 == 0:
                        print(f"    ... {i + 1}ページ処理済み")
        
        except Exception as e:
            print(f"  ❌ PDF読み込みエラー: {e}")
            return []
        
        print(f"  ✅ 抽出完了: {len(pages)}ページ")
        return pages
    
    def _clean_text(self, text: str) -> str:
        """テキストクリーニング"""
        text = re.sub(r'[ \t]+', ' ', text)
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()
    
    # ========================================
    # Phase 2: チャンク分割
    # ========================================
    def split_into_chunks(self, pages: List[dict]) -> List[TextChunk]:
        """ページをチャンクに分割"""
        
        print(f"  📦 チャンク分割中 (サイズ: {self.chunk_size}トークン)")
        
        chunks = []
        current_text = ""
        current_pages = []
        chunk_id = 0
        
        for page in pages:
            page_text = page['text']
            page_num = page['page_num']
            
            # 現在のチャンクにページを追加
            test_text = current_text + "\n\n" + page_text if current_text else page_text
            test_tokens = self._count_tokens(test_text)
            
            if test_tokens > self.chunk_size and current_text:
                # 現在のチャンクを確定
                chunks.append(TextChunk(
                    chunk_id=f"chunk_{chunk_id:04d}",
                    text=current_text.strip(),
                    token_count=self._count_tokens(current_text),
                    page_start=current_pages[0] if current_pages else page_num,
                    page_end=current_pages[-1] if current_pages else page_num,
                ))
                chunk_id += 1
                
                # 新しいチャンク開始（オーバーラップ付き）
                overlap_text = current_text[-self.chunk_overlap*3:] if len(current_text) > self.chunk_overlap*3 else ""
                current_text = overlap_text + "\n\n" + page_text
                current_pages = [page_num]
            else:
                current_text = test_text
                current_pages.append(page_num)
        
        # 最後のチャンク
        if current_text.strip():
            chunks.append(TextChunk(
                chunk_id=f"chunk_{chunk_id:04d}",
                text=current_text.strip(),
                token_count=self._count_tokens(current_text),
                page_start=current_pages[0] if current_pages else 0,
                page_end=current_pages[-1] if current_pages else 0,
            ))
        
        print(f"  ✅ {len(chunks)}チャンクに分割完了")
        return chunks
    
    def _count_tokens(self, text: str) -> int:
        """トークン数カウント（簡易版）"""
        # 日本語: 約1.5文字/トークン、英語: 約4文字/トークン
        # 混合文書なので約2文字/トークンで推定
        return len(text) // 2
    
    # ========================================
    # Phase 3: チャンク処理（並列対応）
    # ========================================
    def classify_chunk(self, chunk: TextChunk) -> TextChunk:
        """チャンクのセクション判定と要約（単一）"""
        
        prompt = f"""あなたは日本の有価証券報告書の専門家です。
以下のテキストを分析し、JSONで回答してください。

【タスク】
1. このテキストが該当するセクションを判定
2. 50-100字程度の要約を作成（数値は含めない、概念のみ）
3. 重要度を判定

【セクション選択肢】
{chr(10).join(f'- {s}' for s in FINANCIAL_SECTIONS)}

【重要】数値の抽出は不要です。概念・論点・構造のみ要約してください。

【テキスト】
{chunk.text[:3000]}

【回答形式】JSON
{{"section": "セクション名", "summary": "要約文（数値なし）", "importance": "high/medium/low"}}

JSON:"""

        result = self.client.generate(prompt)
        self._update_stats(result)
        
        # JSONパース
        try:
            json_match = re.search(r'\{[^{}]*\}', result['response'], re.DOTALL)
            if json_match:
                parsed = json.loads(json_match.group())
                chunk.section = parsed.get('section', 'その他')
                chunk.summary = parsed.get('summary', '')
                chunk.importance = parsed.get('importance', 'medium')
        except:
            chunk.section = 'その他'
            chunk.summary = ''
            chunk.importance = 'medium'
        
        return chunk
    
    def process_chunks_parallel(self, chunks: List[TextChunk], 
                                progress_callback=None) -> List[TextChunk]:
        """チャンクを並列処理"""
        
        print(f"  🔄 並列処理開始 (ワーカー数: {self.max_workers})")
        
        processed = []
        total = len(chunks)
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # タスク投入
            future_to_chunk = {
                executor.submit(self.classify_chunk, chunk): chunk.chunk_id
                for chunk in chunks
            }
            
            # 結果収集
            for future in as_completed(future_to_chunk):
                chunk_id = future_to_chunk[future]
                try:
                    result = future.result()
                    processed.append(result)
                    
                    # 進捗表示
                    done = len(processed)
                    print(f"    [{done}/{total}] {chunk_id} → {result.section[:15]}...")
                    
                    if progress_callback:
                        progress_callback(done, total)
                        
                except Exception as e:
                    print(f"    ❌ {chunk_id} エラー: {e}")
        
        # chunk_idでソート
        processed.sort(key=lambda c: c.chunk_id)
        
        print(f"  ✅ {len(processed)}チャンクの処理完了")
        return processed
    
    # ========================================
    # Phase 4: セクション統合（Level 1）
    # ========================================
    def generate_section_summaries(self, chunks: List[TextChunk]) -> List[SectionSummary]:
        """セクション別にチャンクを統合して要約"""
        
        print(f"  📊 セクション統合中...")
        
        # セクション別にグループ化
        section_groups: Dict[str, List[TextChunk]] = {}
        for chunk in chunks:
            section = chunk.section
            if section not in section_groups:
                section_groups[section] = []
            section_groups[section].append(chunk)
        
        summaries = []
        
        for section_name, section_chunks in section_groups.items():
            print(f"    📁 {section_name} ({len(section_chunks)}チャンク)")
            
            # チャンクの要約を結合
            combined = "\n".join([f"- {c.summary}" for c in section_chunks if c.summary])
            
            prompt = f"""以下の「{section_name}」セクションの要約群を統合してください。

【個別要約】
{combined[:4000]}

【タスク】
1. 100-150字の統合要約（数値は含めない）
2. 重要ポイントを3つ
3. 投資判断への示唆を1つ

【回答形式】JSON
{{"summary": "統合要約", "key_points": ["ポイント1", "ポイント2", "ポイント3"], "insight": "投資示唆"}}

JSON:"""

            result = self.client.generate(prompt)
            self._update_stats(result)
            
            try:
                json_match = re.search(r'\{[^{}]*\}', result['response'], re.DOTALL)
                if json_match:
                    parsed = json.loads(json_match.group())
                    summaries.append(SectionSummary(
                        section_name=section_name,
                        summary=parsed.get('summary', ''),
                        key_points=parsed.get('key_points', []),
                        investment_insight=parsed.get('insight', ''),
                        chunk_ids=[c.chunk_id for c in section_chunks],
                        chunk_count=len(section_chunks),
                    ))
            except:
                summaries.append(SectionSummary(
                    section_name=section_name,
                    summary=combined[:200],
                    key_points=[],
                    investment_insight='',
                    chunk_ids=[c.chunk_id for c in section_chunks],
                    chunk_count=len(section_chunks),
                ))
        
        print(f"  ✅ {len(summaries)}セクションの統合完了")
        return summaries
    
    # ========================================
    # Phase 5: 全体要約（Level 2 / Root）
    # ========================================
    def generate_document_summary(self, sections: List[SectionSummary],
                                  company_name: str) -> DocumentSummary:
        """ドキュメント全体の要約を生成"""
        
        print(f"  🎯 全体要約生成中...")
        
        sections_text = "\n\n".join([
            f"【{s.section_name}】\n{s.summary}\n重要点: {', '.join(s.key_points)}"
            for s in sections
        ])
        
        prompt = f"""以下は「{company_name}」の有価証券報告書の各セクション要約です。

【セクション要約】
{sections_text[:6000]}

【タスク】
1. 300-400字の総合要約（概念のみ、具体的数値は不要）
2. 強み3つ
3. リスク・課題3つ
4. 投資判断への結論

【回答形式】JSON
{{"summary": "総合要約", "strengths": ["強み1", "強み2", "強み3"], "weaknesses": ["リスク1", "リスク2", "リスク3"], "conclusion": "結論"}}

JSON:"""

        result = self.client.generate(prompt, max_tokens=1500)
        self._update_stats(result)
        
        try:
            json_match = re.search(r'\{[^{}]*\}', result['response'], re.DOTALL)
            if json_match:
                parsed = json.loads(json_match.group())
                return DocumentSummary(
                    company_name=company_name,
                    executive_summary=parsed.get('summary', ''),
                    strengths=parsed.get('strengths', []),
                    weaknesses=parsed.get('weaknesses', []),
                    investment_conclusion=parsed.get('conclusion', ''),
                )
        except:
            pass
        
        return DocumentSummary(
            company_name=company_name,
            executive_summary='要約生成に失敗しました',
            strengths=[],
            weaknesses=[],
            investment_conclusion='',
        )
    
    # ========================================
    # メインパイプライン
    # ========================================
    def process_pdf(self, pdf_path: Path) -> Optional[RAPTORTree]:
        """PDFを処理してRAPTORツリーを構築"""
        
        print(f"\n{'='*60}")
        print(f"🌳 RAPTOR Tree構築: {pdf_path.name}")
        print(f"{'='*60}")
        
        company_name = pdf_path.stem.replace('_', ' ')
        start_time = datetime.now()
        phase_times = {}
        
        # Phase 1: PDF読み込み
        print("\n📖 Phase 1: PDF読み込み")
        phase1_start = datetime.now()
        pages = self.extract_text_from_pdf(pdf_path)
        phase_times['phase1'] = (datetime.now() - phase1_start).total_seconds()
        
        if not pages:
            return None
        
        total_chars = sum(len(p['text']) for p in pages)
        
        # Phase 2: チャンク分割
        print("\n📦 Phase 2: チャンク分割")
        phase2_start = datetime.now()
        chunks = self.split_into_chunks(pages)
        phase_times['phase2'] = (datetime.now() - phase2_start).total_seconds()
        
        # Phase 3: チャンク処理（並列）
        print(f"\n🏷️ Phase 3: チャンク処理 ({len(chunks)}チャンク)")
        phase3_start = datetime.now()
        processed_chunks = self.process_chunks_parallel(chunks)
        phase_times['phase3'] = (datetime.now() - phase3_start).total_seconds()
        
        # Phase 4: セクション統合
        print("\n📊 Phase 4: セクション統合")
        phase4_start = datetime.now()
        sections = self.generate_section_summaries(processed_chunks)
        phase_times['phase4'] = (datetime.now() - phase4_start).total_seconds()
        
        # Phase 5: 全体要約
        print("\n🎯 Phase 5: 全体要約")
        phase5_start = datetime.now()
        root = self.generate_document_summary(sections, company_name)
        phase_times['phase5'] = (datetime.now() - phase5_start).total_seconds()
        
        # 終了時間
        end_time = datetime.now()
        total_duration = (end_time - start_time).total_seconds()
        
        # 時間フォーマット
        def format_duration(seconds):
            if seconds < 60:
                return f"{seconds:.1f}秒"
            elif seconds < 3600:
                minutes = int(seconds // 60)
                secs = seconds % 60
                return f"{minutes}分{secs:.1f}秒"
            else:
                hours = int(seconds // 3600)
                minutes = int((seconds % 3600) // 60)
                return f"{hours}時間{minutes}分"
        
        # メタデータ
        metadata = {
            # 基本情報
            'company_name': company_name,
            'source_file': pdf_path.name,
            'total_pages': len(pages),
            'total_chars': total_chars,
            'total_chunks': len(chunks),
            'total_sections': len(sections),
            
            # トークン統計
            'total_input_tokens': self.stats['total_input_tokens'],
            'total_output_tokens': self.stats['total_output_tokens'],
            
            # 時間情報
            'start_time': start_time.strftime('%Y-%m-%d %H:%M:%S'),
            'end_time': end_time.strftime('%Y-%m-%d %H:%M:%S'),
            'processing_time_sec': total_duration,
            'processing_time_formatted': format_duration(total_duration),
            
            # フェーズ別時間
            'phase1_duration': format_duration(phase_times['phase1']),
            'phase2_duration': format_duration(phase_times['phase2']),
            'phase3_duration': format_duration(phase_times['phase3']),
            'phase4_duration': format_duration(phase_times['phase4']),
            'phase5_duration': format_duration(phase_times['phase5']),
            
            # 設定情報
            'model': self.client.model,
            'chunk_size': self.chunk_size,
            'chunk_overlap': self.chunk_overlap,
            'max_workers': self.max_workers,
            'context_window': 8192,  # OllamaClientのnum_ctx
            
            # メタ
            'created_at': datetime.now().isoformat(),
            'version': '2.0.0',
        }
        
        tree = RAPTORTree(
            metadata=metadata,
            level_0_chunks=processed_chunks,
            level_1_sections=sections,
            level_2_root=root,
        )
        
        print(f"\n{'='*60}")
        print(f"✅ RAPTOR Tree構築完了")
        print(f"{'='*60}")
        print(f"📊 統計:")
        print(f"  入力トークン: {self.stats['total_input_tokens']:,}")
        print(f"  出力トークン: {self.stats['total_output_tokens']:,}")
        print(f"  合計トークン: {self.stats['total_input_tokens'] + self.stats['total_output_tokens']:,}")
        print(f"{'='*60}")
        print(f"⏱️ 実行時間:")
        print(f"  Phase 1 (PDF読込): {format_duration(phase_times['phase1'])}")
        print(f"  Phase 2 (分割): {format_duration(phase_times['phase2'])}")
        print(f"  Phase 3 (チャンク処理): {format_duration(phase_times['phase3'])}")
        print(f"  Phase 4 (セクション統合): {format_duration(phase_times['phase4'])}")
        print(f"  Phase 5 (全体要約): {format_duration(phase_times['phase5'])}")
        print(f"  ───────────────────")
        print(f"  **総実行時間: {format_duration(total_duration)}**")
        print(f"{'='*60}")
        
        return tree
    
    def save_tree(self, tree: RAPTORTree, output_dir: Path) -> tuple[Path, Path]:
        """RAPTORツリーをJSON/Markdownで保存"""
        
        output_dir.mkdir(parents=True, exist_ok=True)
        
        company_name = tree.metadata['company_name']
        safe_name = re.sub(r'[\\/*?:"<>|]', '_', company_name)[:50]
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        
        # JSON保存
        json_filename = f"{safe_name}_{timestamp}_raptor_tree.json"
        json_path = output_dir / json_filename
        
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(tree.to_dict(), f, ensure_ascii=False, indent=2)
        
        print(f"💾 JSON保存: {json_path.name}")
        
        # Markdown保存（詳細情報付き）
        md_filename = f"{safe_name}_{timestamp}_raptor_summary.md"
        md_path = output_dir / md_filename
        
        md_content = tree.generate_summary_header()
        md_content += self._generate_analysis_content(tree)
        
        with open(md_path, 'w', encoding='utf-8') as f:
            f.write(md_content)
        
        print(f"💾 Markdown保存: {md_path.name}")
        
        return json_path, md_path
    
    def _generate_analysis_content(self, tree: RAPTORTree) -> str:
        """分析内容のMarkdownを生成"""
        
        root = tree.level_2_root
        sections = tree.level_1_sections
        
        content = f"""
## 📊 エグゼクティブサマリー（Level 2: Root）

{root.executive_summary or 'N/A'}

### 💪 強み
{chr(10).join(f'- {s}' for s in root.strengths) if root.strengths else '- データなし'}

### ⚠️ リスク・課題
{chr(10).join(f'- {w}' for w in root.weaknesses) if root.weaknesses else '- データなし'}

### 🎯 投資判断への示唆
{root.investment_conclusion or 'N/A'}

---

## 📁 セクション別分析（Level 1）

"""
        
        for section in sections:
            content += f"""### {section.section_name}
**チャンク数:** {section.chunk_count}

{section.summary or 'N/A'}

**重要ポイント:**
{chr(10).join(f'- {p}' for p in section.key_points) if section.key_points else '- データなし'}

**投資視点:** {section.investment_insight or 'N/A'}

---

"""
        
        content += """
---

*このレポートはFinancial RAPTOR Systemにより自動生成されました。*
*数値情報は別途検証が必要です。テキスト要約は概念・論点の整理を目的としています。*
"""
        
        return content


# ============================================================
# ChromaDB統合
# ============================================================
class ChromaDBWriter:
    """RAPTORツリーをChromaDBに保存"""
    
    def __init__(self, persist_dir: Path):
        self.persist_dir = persist_dir
        self._client = None
        self._collection = None
    
    def _init_client(self):
        """ChromaDBクライアント初期化"""
        if self._client is None:
            try:
                import chromadb
                from chromadb.config import Settings
                
                self._client = chromadb.Client(Settings(
                    chroma_db_impl="duckdb+parquet",
                    persist_directory=str(self.persist_dir),
                ))
                self._collection = self._client.get_or_create_collection(
                    name="financial_raptor",
                    metadata={"description": "Financial RAPTOR text embeddings"}
                )
            except ImportError:
                print("⚠️ chromadb未インストール: pip install chromadb")
                raise
    
    def add_tree(self, tree: RAPTORTree):
        """RAPTORツリーをChromaDBに追加"""
        
        self._init_client()
        
        company = tree.metadata['company_name']
        
        # Level 0: チャンク
        for chunk in tree.level_0_chunks:
            self._collection.add(
                documents=[chunk.text],
                metadatas=[{
                    'company': company,
                    'level': 0,
                    'section': chunk.section,
                    'summary': chunk.summary,
                    'page_start': chunk.page_start,
                    'page_end': chunk.page_end,
                }],
                ids=[f"{company}_{chunk.chunk_id}"],
            )
        
        # Level 1: セクション
        for section in tree.level_1_sections:
            self._collection.add(
                documents=[section.summary],
                metadatas=[{
                    'company': company,
                    'level': 1,
                    'section': section.section_name,
                    'key_points': json.dumps(section.key_points, ensure_ascii=False),
                    'insight': section.investment_insight,
                }],
                ids=[f"{company}_section_{section.section_name}"],
            )
        
        # Level 2: Root
        self._collection.add(
            documents=[tree.level_2_root.executive_summary],
            metadatas=[{
                'company': company,
                'level': 2,
                'strengths': json.dumps(tree.level_2_root.strengths, ensure_ascii=False),
                'weaknesses': json.dumps(tree.level_2_root.weaknesses, ensure_ascii=False),
                'conclusion': tree.level_2_root.investment_conclusion,
            }],
            ids=[f"{company}_root"],
        )
        
        print(f"✅ ChromaDBに追加: {company}")
    
    def search(self, query: str, n_results: int = 5, 
               company: str = None, level: int = None) -> List[dict]:
        """類似テキスト検索"""
        
        self._init_client()
        
        where = {}
        if company:
            where['company'] = company
        if level is not None:
            where['level'] = level
        
        results = self._collection.query(
            query_texts=[query],
            n_results=n_results,
            where=where if where else None,
        )
        
        return [
            {
                'id': results['ids'][0][i],
                'document': results['documents'][0][i],
                'metadata': results['metadatas'][0][i],
                'distance': results['distances'][0][i] if 'distances' in results else None,
            }
            for i in range(len(results['ids'][0]))
        ]


# ============================================================
# 使用例
# ============================================================
if __name__ == "__main__":
    from pathlib import Path
    
    # テスト
    client = OllamaClient(model="gemma2:9b")  # テスト用に軽量モデル
    pipeline = TextPipeline(
        ollama_client=client,
        chunk_size=2000,
        max_workers=2,
    )
    
    # PDFパス（テスト用）
    desktop = Path.home() / "Desktop"
    pdf_dir = desktop / "PDF"
    
    if pdf_dir.exists():
        pdfs = list(pdf_dir.glob("*.pdf"))
        if pdfs:
            print(f"テスト対象: {pdfs[0].name}")
            # tree = pipeline.process_pdf(pdfs[0])