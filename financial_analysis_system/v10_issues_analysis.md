# PORTA v10 問題分析レポート

**作成日時**: 2026-01-24
**分析対象**: トヨタ自動車株式会社 2022年度 有価証券報告書
**比較バージョン**: v9 vs v10

---

## 📋 目次

1. [発見された問題の概要](#発見された問題の概要)
2. [問題1: 複数企業処理時のデータ混入](#問題1-複数企業処理時のデータ混入)
3. [問題2: セグメント情報が全てN/A](#問題2-セグメント情報が全てna)
4. [問題3: 営業利益がN/A](#問題3-営業利益がna)
5. [問題4: 業種パラメータによるYoY不一致](#問題4-業種パラメータによるyoy不一致)
6. [修正案](#修正案)
7. [推奨対応手順](#推奨対応手順)

---

## 発見された問題の概要

### 🔴 Critical（重大）
1. **セグメント情報が全てN/A** - PDFフォルダ欠損
2. **営業利益がN/A** - XBRLタグマッピング不足（IFRS未対応）
3. **複数企業のデータ混入** - 極洋の「中国向けホタテ輸出」がトヨタ・ソニーに混入

### ⚠️ Medium（中程度）
4. **業種パラメータによるYoY不一致** - `industry="all"` 以外でセグメントYoYが正しく計算されない
5. **PDFからセグメント情報抽出失敗** - MDAセクションから補完できていない

---

## 問題1: 複数企業処理時のデータ混入

### 症状

以下のファイルに、極洋の「中国向けホタテ輸出」データが混入:

```
トヨタ:
  C:\Users\shun nabeno\Desktop\Local LLM Project\financial_analysis_system\output_v10\7203_トヨタ自動車株式会社\porta10_7203_2023_有報_20260124_213301.json
  → Line 649: "国内外で自動車関連事業が好調に推移し、特に中国向けホタテ輸出が増加した"

ソニー:
  C:\Users\shun nabeno\Desktop\Local LLM Project\financial_analysis_system\output_v10\6758_ソニーグループ株式会社\porta10_6758_2023_有報_20260124_212752.json
  → Line 268: "中国向けホタテ輸出の増加"
```

### 根本原因（推定）

**最も可能性が高い**: セクション分割処理（Yuho_splitter_v4）で、極洋のPDFが他企業のフォルダに物理的に混入

**可能性のある原因**:
1. ✅ **PDFファイルの物理的混入** - 分割処理時にファイルコピーミス
2. ⚠️ **BM25キャッシュの汚染** - `@lru_cache`が複数企業で共有されている（可能性低）
3. ⚠️ **Ollama APIのコンテキスト汚染** - 前のリクエストの残留（可能性低）

### 確認が必要な事項

```bash
# トヨタのフォルダに極洋のPDFがないか確認
ls "E:\PDF\sections_test\7203_トヨタ自動車株式会社\2023_有報\03_MDA\"

# ソニーのフォルダに極洋のPDFがないか確認
ls "E:\PDF\sections_test\6758_ソニーグループ株式会社\2023_有報\03_MDA\"

# manifest.jsonの確認
cat "E:\PDF\sections_test\7203_トヨタ自動車株式会社\2023_有報\manifest.json"
```

### コードレベルの確認ポイント

#### 1. PDF抽出キャッシュ

**ファイル**: `Run_integrated_v10.py`
**行数**: 1062-1079

```python
if Config.ENABLE_PDF_CACHE:
    @lru_cache(maxsize=Config.PDF_CACHE_SIZE)
    def extract_text_from_pdf_with_pages(pdf_path: Path) -> List[Tuple[int, str]]:
        return _extract_text_from_pdf_with_pages_impl(pdf_path)
```

**問題**: `@lru_cache`はパス文字列でキャッシュするため、異なる企業で同じファイル名があると誤ってキャッシュを再利用する可能性がある

**対策**: キャッシュキーに企業コードを含める

#### 2. BM25インデックス

**ファイル**: `Run_integrated_v10.py`
**行数**: 736-820

```python
class BM25:
    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.doc_freqs = []
        self.idf = {}
        # ...
```

**問題**: BM25インスタンスが企業間で共有されていないか？（各セクション処理で新規作成されるので問題なし）

---

## 問題2: セグメント情報が全てN/A

### 症状

**出力ファイル**: `porta10_7203_2022_有報_20260124_231834.md`

```markdown
## セグメント別ハイライト
**自動車事業**:
- 売上高: N/A
- 営業利益: N/A
- **評価**: N/A
- **要因**: N/A

**金融事業**:
- 売上高: N/A
- 営業利益: N/A
- **評価**: N/A
- **要因**: N/A
```

### 根本原因

✅ **確認済**: `E:\PDF\sections_test\7203_トヨタ自動車株式会社\2022_有報\05_セグメント` フォルダが**存在しない**

```
存在するフォルダ:
- 01_会社概要
- 02_経営戦略_リスク
- 03_MDA
- 04_財務三表
- 06_ガバナンス
- 07_その他
- manifest.json

欠損:
- 05_セグメント ← ここが無い！
```

### 影響範囲

1. **セクション処理がスキップされる**
   - `process_section()` が `05_セグメント` をスキップ
   - extraction_logs に `05_セグメント_extraction.json` が作成されない

2. **レポートに反映されない**
   - セグメント別売上高・営業利益が全てN/A
   - セグメント別YoY（前年比）が計算不可
   - セグメント別評価（好調/不調）が出力不可

### PDF抽出ログからの確認

**ファイル**: `03_MDA_extraction.json`
**行数**: 130-134

```json
{
  "question_id": "segment_performance",
  "answer": "テキストには各事業部門（セグメント）の業績に関する詳細な情報は記載されていません。"
}
```

→ MDAセクションにもセグメント情報が含まれていないことを確認

---

## 問題3: 営業利益がN/A

### 症状

**出力ファイル**: `porta10_7203_2022_有報_20260124_231834.md`

```markdown
## 数字で見る2022年
- 売上高: 313,795.1億円 (+15.3%) (XBRL)
- 営業利益: N/A (N/A) (XBRL)  ← ここ！
- EBITDA: N/A / EBITDAマージン: N/A (XBRL)
- 純利益: 28,501.1億円 (+26.9%) (XBRL)
```

### 根本原因

✅ **確認済**: `xbrl_store/7203_トヨタ自動車株式会社/2022.json` に営業利益のキーが**存在しない**

```json
{
  "data": {
    "revenue": 31379507000000.0,
    "cost_of_sales": 24250784000000.0,
    "income_before_tax": 3990532000000.0,
    "net_income": 2850110000000.0,
    // ↓ 営業利益系のキーが無い！
    // "operating_profit": ???
    // "operating_income": ???
  }
}
```

### 原因の深堀り

**トヨタはIFRS（国際会計基準）採用企業**

- 日本基準: `jpcrp_cor:OperatingIncome`
- IFRS: `ifrs:ProfitLossFromOperatingActivities` など

**現在のtaxonomy_config2には、IFRS用の営業利益タグが設定されていない可能性が高い**

### 影響範囲

1. **営業利益率が計算不可**
   - `営業利益率 = 営業利益 ÷ 売上高 × 100`

2. **EBITDAが計算不可**
   - `EBITDA = 営業利益 + 減価償却費`

3. **ROICが計算不可**
   - `ROIC = 営業利益 × (1 - 税率) ÷ 投下資本`

4. **時系列推移表が空欄**
   ```markdown
   | 項目 | 2022 | 2021 | 2020 |
   |---|---|---|---|
   | 営業利益 | - | - | - |
   ```

### PDFからも取れていない

**ファイル**: `03_MDA_extraction.json`
**行数**: 54-56

```json
{
  "answer": "営業利益の主な増減要因は次のとおりです。 [P.2]\n\n* 営業面の努力 8,600億円\n* 為替変動の影響 6,100億円\n* 原価改善の努力 △3,600億円\n* 諸経費の増減・低減努力 △2,200億円\n* その他 △921億円"
}
```

→ PDFには**増減要因**は記載されているが、**営業利益の絶対額**が抽出できていない

---

## 問題4: 業種パラメータによるYoY不一致

### 症状

**ユーザー報告**:
> Allを選ばないと、セグメントのYoYがすべて一緒になるバグがある

### コードレベルの確認

**ファイル**: `Run_integrated_v10.py`
**行数**: 2230

```python
## 2. セグメント数値の扱い
- セグメント別の売上高・営業利益は、必ずセグメント情報から取得してください
- **会社全体のYoY（前年比）をセグメントに流用してはいけません**
- セグメント数値が取得できない場合は「N/A」と明記してください
```

→ プロンプトで警告しているが、LLMが守らないケースがある

### 推測される原因

`industry` パラメータによってプロンプトが変わり、LLMの挙動が変わる:

```python
# Run_integrated_v10.py: 1368-1370
industry_info = INDUSTRY_PROMPTS.get(industry, INDUSTRY_PROMPTS["all"])
industry_focus = "\n".join([f"- {p}" for p in industry_info["focus_points"]])
```

**industry="all"** の場合:
- フォーカスポイントが汎用的
- LLMがセグメント情報を正しく抽出

**industry="food"** の場合:
- フォーカスポイントが食品業界特化
- LLMが会社全体の数値をセグメントに流用してしまう（自動車業界には適用されないフォーカスポイントのため、混乱する）

---

## 修正案

### 修正1: XBRLタグマッピングの拡張（IFRS対応）

**対象ファイル**: Google Sheets `StockFlow企業データ` → `taxonomy_config2` タブ

**追加すべきIFRSタグ**:

```
タグ名: operating_profit

代替パス（優先順）:
1. jpcrp_cor:OperatingIncome                      # 日本基準
2. jppfs_cor:OperatingProfit                      # IFRS（日本版）
3. ifrs:ProfitLossFromOperatingActivities         # IFRS標準
4. ifrs-full:ProfitLossBeforeTax                  # 税引前利益（次善策）
```

**実装方法**:

1. Google Sheets の `taxonomy_config2` を開く
2. `operating_profit` 行を探す
3. `xpath` カラムに上記パスをパイプ区切りで追加

---

### 修正2: セグメント情報の代替抽出

**対象ファイル**: `Run_integrated_v10.py`

**実装箇所**: `process_section()` 関数（行数: 1848-1972）

#### 変更内容

```python
def process_section(
    section_folder: Path,
    section_key: str,
    xbrl: Dict,
    prev_xbrl: Dict,
    industry: str,
    extraction_mode: str
) -> Dict:
    """
    セクション処理のエントリーポイント（v10.1改良版）
    """
    # manifest.json読み込み
    manifest = load_manifest(section_folder)

    # セクション別ディレクトリ
    section_dir = section_folder / section_key

    # ★★★ 追加: セグメント情報の代替抽出 ★★★
    if section_key == "05_セグメント" and not section_dir.exists():
        logger.warning(f"  ⚠️ 05_セグメントフォルダが存在しません。代替抽出を試みます...")

        # 代替セクションから抽出
        alternative_sections = [
            ("04_財務三表", "財務諸表注記のセグメント情報"),
            ("03_MDA", "MDAのセグメント別業績分析")
        ]

        for alt_key, description in alternative_sections:
            alt_dir = section_folder / alt_key
            if alt_dir.exists() and alt_dir.is_dir():
                logger.info(f"  📖 {alt_key}から{description}を抽出...")

                # 代替セクションのPDF読み込み
                pdf_files = sorted(alt_dir.glob("*.pdf"))
                all_pages = []
                for pdf_file in pdf_files:
                    pages = extract_text_from_pdf_with_pages(pdf_file)
                    if pages:
                        offset = len(all_pages)
                        all_pages.extend([(p + offset, text) for p, text in pages])

                if all_pages:
                    # セグメント特化のQAを実行
                    return process_segment_from_alternative(
                        all_pages, xbrl, prev_xbrl, industry
                    )

        # 代替抽出も失敗
        logger.error(f"  ❌ セグメント情報を代替抽出できませんでした")
        return {"section_key": section_key, "extracted": {}, "qa_answers": []}

    # ★★★ 以下、既存コード ★★★
    if section_dir.exists() and section_dir.is_dir():
        # ... (既存の処理)
```

#### 新規関数: `process_segment_from_alternative()`

```python
def process_segment_from_alternative(
    pages: List[Tuple[int, str]],
    xbrl: Dict,
    prev_xbrl: Dict,
    industry: str
) -> Dict:
    """
    代替セクションからセグメント情報を抽出

    Args:
        pages: PDFページのリスト
        xbrl: 当期XBRLデータ
        prev_xbrl: 前期XBRLデータ
        industry: 業種コード

    Returns:
        セグメント抽出結果
    """
    full_text = "\n\n".join([f"[ページ {p}]\n{text}" for p, text in pages])

    # セグメント特化のプロンプト
    segment_prompt = f"""あなたは財務アナリストです。以下のテキストから、**セグメント情報**を抽出してください。

# 探すべき情報

1. **セグメント（事業）名** - すべてのセグメント名を列挙
2. **各セグメントの売上高** - 当期と前期の数値、前年比（%）
3. **各セグメントの営業利益** - 当期と前期の数値、前年比（%）
4. **増減要因** - 売上・利益が増減した理由

# 探す場所

以下のいずれかに記載されています：
- **連結財務諸表注記** → 「セグメント情報」の項目
- **経営成績の分析** → セグメント別の業績分析
- **事業別売上高・営業利益の表** → 表形式の数値データ

# 重要な注意事項

- **会社全体の数値をセグメントに流用してはいけません**
- セグメント固有の数値のみを記載してください
- 「地域別」「製品別」「顧客別」の開示も含めてください
- 数値がない場合のみ「N/A」と明記してください

# テキスト

{full_text[:15000]}

# 回答フォーマット

各セグメントについて:
- セグメント名: XXX
- 売上高: XX億円 (前期: XX億円, YoY: +/-X.X%)
- 営業利益: XX億円 (前期: XX億円, YoY: +/-X.X%)
- 増減要因: XXX
"""

    # Ollama呼び出し
    response = call_ollama(
        segment_prompt,
        model=Config.OLLAMA_MODEL_FINAL,
        num_ctx=Config.QA_NUM_CTX,
        num_predict=Config.QA_NUM_PREDICT,
        temperature=0.0
    )

    # 結果を構造化
    return {
        "section_key": "05_セグメント",
        "extracted": {},
        "qa_answers": [{
            "question_id": "segment_alternative",
            "question": "代替セクションからのセグメント抽出",
            "answer": response,
            "source": "alternative"
        }]
    }
```

---

### 修正3: PDFキャッシュのクリア処理

**対象ファイル**: `Run_integrated_v10.py`

**実装箇所**: `main()` 関数の先頭

```python
def main():
    parser = argparse.ArgumentParser(description="PORTA v10 - 完全統合版")
    # ... (既存のargparse設定)

    # ★★★ 追加: PDFキャッシュのクリア ★★★
    if Config.ENABLE_PDF_CACHE:
        logger.info("  🗑️ PDFキャッシュをクリアします...")
        extract_text_from_pdf_with_pages.cache_clear()

    # ★★★ 以下、既存コード ★★★
    # インタラクティブモード
    if not args.company or not args.year:
        # ...
```

---

### 修正4: 業種パラメータの処理改善

**対象ファイル**: `Run_integrated_v10.py`

**実装箇所**: `generate_final_report_v10()` 関数（行数: 1977-2314）

#### プロンプト強化

```python
# 行数: 2228-2232 あたりに追加

## 2. セグメント数値の扱い
- セグメント別の売上高・営業利益は、必ずセグメント情報から取得してください
- **会社全体のYoY（前年比）をセグメントに流用してはいけません**
- **各セグメント固有の増減率を使用してください**
- セグメント数値が取得できない場合は「N/A」と明記してください

【NG例】
- 会社全体: 売上高 +15.3%
- 自動車事業: 売上高 +15.3% ← これはNG！会社全体と同じ数値

【OK例】
- 会社全体: 売上高 +15.3%
- 自動車事業: 売上高 +18.2% ← セグメント固有の数値
- 金融事業: 売上高 +20.9% ← セグメント固有の数値
```

---

## 推奨対応手順

### Phase 1: 緊急対応（即座に実施）

#### 1-1. PDFファイルの物理確認

```bash
# トヨタ 2023年のMDAフォルダ確認
dir "E:\PDF\sections_test\7203_トヨタ自動車株式会社\2023_有報\03_MDA"

# ソニー 2023年のMDAフォルダ確認
dir "E:\PDF\sections_test\6758_ソニーグループ株式会社\2023_有報\03_MDA"

# 極洋のPDFがないか確認
# ファイル名に "1301" または "極洋" が含まれていないか
```

**もし極洋のPDFが混入していたら**:
- Yuho_splitter_v4 のバグを修正
- セクション分割を再実行

#### 1-2. PDFキャッシュのクリア

`Run_integrated_v10.py` に以下を追加:

```python
# main() 関数の先頭
if Config.ENABLE_PDF_CACHE:
    extract_text_from_pdf_with_pages.cache_clear()
```

#### 1-3. トヨタ 2022年の再実行

```bash
python Run_integrated_v10.py --company 7203 --year 2022 --industry all --mode hybrid
```

---

### Phase 2: 根本対策（1週間以内）

#### 2-1. XBRLタグマッピングの拡張

**対応者**: XBRLデータ担当者

**タスク**:
1. Google Sheets `taxonomy_config2` にIFRSタグを追加
2. トヨタ、ソニーなどIFRS企業で検証
3. 営業利益が正しく取得できることを確認

**検証コマンド**:
```bash
# トヨタのXBRL再抽出
python xbrl_batch_extractor.py --company 7203 --year 2022

# 結果確認
cat xbrl_store/7203_トヨタ自動車株式会社/2022.json | grep "operating_profit"
```

#### 2-2. セグメント代替抽出の実装

**対応者**: Python開発者

**タスク**:
1. `process_segment_from_alternative()` 関数を実装
2. `process_section()` に代替抽出ロジックを追加
3. トヨタ 2022年で検証（05_セグメントフォルダ無し）

**検証コマンド**:
```bash
python Run_integrated_v10.py --company 7203 --year 2022 --industry all --mode hybrid
```

**期待される結果**:
- セグメント情報が「N/A」でなく具体的数値が出力される
- extraction_logs に代替抽出のログが残る

---

### Phase 3: 品質向上（1ヶ月以内）

#### 3-1. Yuho_splitter_v4 の改善

**対応者**: PDF分割ツール担当者

**タスク**:
1. セグメント情報の検出ロジックを強化
2. IFRS企業、日本基準企業の両方に対応
3. manifest.json にセクション検出の信頼度を追加

#### 3-2. 自動テストの追加

**対応者**: QA担当者

**タスク**:
1. 複数企業の連続処理テスト
2. データ混入の検出テスト
3. XBRL/PDFの整合性チェック

**テストケース**:
```python
def test_no_data_contamination():
    """複数企業処理でデータ混入がないことを確認"""
    companies = ["1301", "7203", "6758"]

    for code in companies:
        result = analyze(code, 2022)

        # 極洋固有のキーワードが他社に混入していないか
        if code != "1301":
            assert "ホタテ" not in result
            assert "養殖" not in result
```

---

## 添付資料

### A. 問題のあるファイル一覧

```
データ混入:
- C:\Users\shun nabeno\Desktop\Local LLM Project\financial_analysis_system\output_v10\7203_トヨタ自動車株式会社\porta10_7203_2023_有報_20260124_213301.json
- C:\Users\shun nabeno\Desktop\Local LLM Project\financial_analysis_system\output_v10\6758_ソニーグループ株式会社\porta10_6758_2023_有報_20260124_212752.json

セグメントN/A:
- C:\Users\shun nabeno\Desktop\Local LLM Project\financial_analysis_system\output_v10\7203_トヨタ自動車株式会社\porta10_7203_2022_有報_20260124_231834.md

営業利益N/A:
- C:\Users\shun nabeno\Desktop\Local LLM Project\financial_analysis_system\xbrl_store\7203_トヨタ自動車株式会社\2022.json
```

### B. 重要な設定ファイル

```
XBRLタグマッピング:
- Google Sheets: StockFlow企業データ → taxonomy_config2

セクション分割設定:
- E:\PDF\sections_test\{company_code}_{company_name}\{year}_{doc_type}\manifest.json

PORTA v10設定:
- Run_integrated_v10.py → Config class (Line 144-207)
```

### C. 関連するGitHub Issue（例）

```markdown
# Issue #1: セグメント情報がN/Aになる
- Label: bug, critical
- Milestone: v10.2
- Assignee: @developer

# Issue #2: IFRS企業の営業利益が取得できない
- Label: enhancement, xbrl
- Milestone: v10.2
- Assignee: @xbrl-team

# Issue #3: 複数企業処理時のデータ混入
- Label: bug, data-integrity
- Milestone: v10.1
- Assignee: @qa-team
```

---

## まとめ

### 優先順位

1. **🔴 最優先**: PDFファイル混入の確認・修正（データ整合性）
2. **🟠 高**: セグメント代替抽出の実装（機能改善）
3. **🟡 中**: XBRLタグマッピングの拡張（IFRS対応）
4. **🟢 低**: 業種パラメータのプロンプト改善（品質向上）

### 期待される効果

**修正後**:
- ✅ セグメント情報が正しく出力される
- ✅ 営業利益・EBITDAが計算できる
- ✅ 複数企業のデータ混入がなくなる
- ✅ industry パラメータによる挙動差がなくなる

**KPI**:
- セグメントN/A率: 100% → 0%
- 営業利益N/A率（IFRS企業）: 100% → 0%
- データ混入検出数: 3件 → 0件

---

**次のアクション**:

修正コードの実装を開始しますか？
それとも、まずPDFファイルの物理確認から始めますか？
