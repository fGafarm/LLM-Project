# v10システム修正要件定義書

**バージョン**: v10 → v10.1
**作成日**: 2026-01-25
**承認**: 全修正許可済み

---

## 1. 修正概要

v10システムの品質問題を解決し、実用レベルの有価証券報告書分析レポートを生成できるようにする。

### 修正範囲
- `Run_integrated_v9.py`（実際にはv10として動作）
- XBRLパーサー関連モジュール
- RAGクエリ生成ロジック
- プロンプトテンプレート
- 品質検証ロジック

---

## 2. 優先度別修正要件

### 🔴 Priority 1: 緊急修正（必須）

#### REQ-001: XBRLタグマッピングの拡張

**現状の問題**:
- 売上高が取得できない企業: JT, トヨタ紡織, 日立, ファーストリテイリング, ソフトバンクG（6/10社）
- 営業利益が取得できない企業: 日立, ソフトバンクG

**要件**:

1. **売上高（Revenue）の複数タグ対応**
   ```python
   REVENUE_TAGS = [
       # IFRS
       "Revenue",
       "RevenueIFRS",
       "RevenuesIFRS",
       # 日本基準
       "NetSales",
       "NetSalesSummaryOfBusinessResults",
       "SalesJPCRPCOR",
       # 米国基準
       "SalesRevenueNet",
       "Revenues",
       # その他
       "OperatingRevenues",
       "OperatingRevenue1",
       "RevenueFromOperationsIFRS"
   ]
   ```

2. **営業利益の複数タグ対応**
   ```python
   OPERATING_PROFIT_TAGS = [
       # IFRS
       "ProfitLossFromOperatingActivitiesIFRS",
       "OperatingIncomeIFRS",
       # 日本基準
       "OperatingIncome",
       "OperatingIncomeLoss",
       "OperatingIncomeLossSummaryOfBusinessResults",
       # その他
       "IncomeFromOperations"
   ]
   ```

3. **フォールバックロジックの実装**
   ```python
   def get_financial_value(xbrl_data, tag_list, context_ref):
       """複数のタグを順番に試行し、最初に見つかった値を返す"""
       for tag in tag_list:
           value = xbrl_data.get(tag, context_ref)
           if value is not None and value != "":
               return value
       return None  # 全てのタグで見つからない場合
   ```

4. **会計基準の自動判定**
   ```python
   def detect_accounting_standard(xbrl_data):
       """XBRLデータから会計基準を判定"""
       if "IFRS" in xbrl_data.namespaces:
           return "IFRS"
       elif "US-GAAP" in xbrl_data.namespaces:
           return "US-GAAP"
       else:
           return "JGAAP"
   ```

**成功基準**:
- 10社中10社で売上高が取得できること
- 10社中10社で営業利益が取得できること

**実装ファイル**:
- `analyzer/xbrl_parser.py` または該当モジュール

---

#### REQ-002: RAGフィルタリングの厳格化

**現状の問題**:
- 極洋の「中国向けホタテ輸出」がJT、日立、ソニー、任天堂に混入
- 同一フレーズ「原材料価格の高騰 → 価格転嫁を推進 (P.15)」が5社で使用

**要件**:

1. **企業固有フィルタの必須化**
   ```python
   def generate_rag_query(company_code, company_name, section_name, question):
       """企業固有のフィルタを含むクエリ生成"""
       metadata_filter = {
           "company_code": company_code,  # 必須
           "company_name": company_name,  # 必須
           "fiscal_year": target_year     # 必須
       }

       query = f"""
       企業: {company_name} (コード: {company_code})
       対象年度: {target_year}

       以下の質問に、この企業の情報のみを使用して回答してください。
       他の企業の情報は絶対に使用しないでください。

       質問: {question}
       """

       return query, metadata_filter
   ```

2. **クロスコンタミネーション検出**
   ```python
   def detect_contamination(generated_text, current_company, all_companies):
       """他社名が混入していないかチェック"""
       other_companies = [c for c in all_companies if c != current_company]

       for other_company in other_companies:
           if other_company in generated_text:
               return True, f"他社名検出: {other_company}"

       return False, None
   ```

3. **プロンプトの強化**
   ```python
   SYSTEM_PROMPT = f"""
   あなたは{company_name}（証券コード: {company_code}）の有価証券報告書を分析しています。

   重要な制約:
   1. {company_name}以外の企業の情報は絶対に使用しないこと
   2. 他の企業の数値や事業内容を混同しないこと
   3. 不明な情報は「データなし」と明記すること
   4. すべての記述に必ずページ番号を付けること

   現在分析中の企業: {company_name}
   証券コード: {company_code}
   会計年度: {fiscal_year}
   """
   ```

**成功基準**:
- 他社情報の混入が0件であること
- 各社固有の情報のみが記載されること

**実装ファイル**:
- `analyzer/rag_query_generator.py`
- `analyzer/llm_analyzer.py`

---

#### REQ-003: 品質検証基準の厳格化

**現状の問題**:
- 主要KPIがN/Aでも89.7% (HIGH) の評価
- データ混入があっても95.7% (HIGH) の評価

**要件**:

1. **主要KPI欠損のペナルティ強化**
   ```python
   def calculate_completeness_score(report_data):
       """完全性スコアの計算"""
       critical_fields = {
           "revenue": 30,        # 売上高（最重要）
           "operating_profit": 30, # 営業利益（最重要）
           "net_profit": 20,     # 純利益
           "total_assets": 10,   # 総資産
           "equity_ratio": 10    # 自己資本比率
       }

       score = 100
       for field, penalty in critical_fields.items():
           if report_data.get(field) in [None, "N/A", ""]:
               score -= penalty

       return max(score, 0)
   ```

2. **コンタミネーション検出の追加**
   ```python
   def validate_data_integrity(report_text, company_info):
       """L6_DATA_INTEGRITY追加検証"""
       issues = []

       # 他社名の検出
       contamination = detect_contamination(
           report_text,
           company_info['name'],
           ALL_COMPANY_NAMES
       )
       if contamination[0]:
           issues.append({
               "level": "CRITICAL",
               "message": f"データ混入検出: {contamination[1]}"
           })
           return False, issues

       return True, []
   ```

3. **Confidenceスコアの再計算**
   ```python
   def calculate_overall_confidence(scores, validation_results):
       """総合Confidenceスコアの計算"""
       base_score = (
           scores['completeness'] * 0.30 +
           scores['xbrl_coverage'] * 0.30 +
           scores['validation'] * 0.25 +
           scores['citation'] * 0.15
       )

       # 重大な問題がある場合は大幅減点
       if validation_results.get('L6_DATA_INTEGRITY', {}).get('passed') == False:
           base_score *= 0.5  # 50%減点

       # 主要KPI欠損の場合は減点
       critical_missing = scores.get('critical_fields_missing', 0)
       base_score -= (critical_missing * 10)  # 1項目につき10%減点

       return max(min(base_score, 100), 0)
   ```

**成功基準**:
- 売上高or営業利益がN/Aの場合、Confidence ≤ 60% (MEDIUM以下)
- データ混入がある場合、Confidence ≤ 50% (LOW)

**実装ファイル**:
- `analyzer/quality_validator.py`

---

### 🟡 Priority 2: 重要改善（推奨）

#### REQ-004: セグメントデータ検出の改善

**現状の問題**:
- 全10社で「No segment data found」警告
- 実際にはセグメント情報が記載されている

**要件**:

1. **マークダウンパーサーの改善**
   ```python
   def extract_segment_data(markdown_text):
       """セグメント別ハイライトセクションからデータ抽出"""
       import re

       segment_pattern = r"###\s*\*\*(.+?)\*\*.*?売上高:\s*(.+?)営業利益:\s*(.+?)評価:\s*(.+?)要因:\s*(.+?)(?=###|\Z)"

       segments = []
       for match in re.finditer(segment_pattern, markdown_text, re.DOTALL):
           segments.append({
               "name": match.group(1).strip(),
               "revenue": match.group(2).strip(),
               "operating_profit": match.group(3).strip(),
               "evaluation": match.group(4).strip(),
               "reason": match.group(5).strip()
           })

       return segments
   ```

2. **検証ロジックの修正**
   ```python
   def validate_L1_format(report_data):
       """L1フォーマット検証"""
       warnings = []

       # セグメントデータのチェック
       segment_section = report_data.get('segment_highlights', '')
       segments = extract_segment_data(segment_section)

       if len(segments) == 0:
           # 単一セグメント企業かチェック
           if "単一セグメント" in segment_section:
               pass  # 警告なし
           else:
               warnings.append("⚠️ No segment data found")

       return warnings
   ```

**成功基準**:
- セグメント情報がある企業で警告が出ないこと
- 単一セグメント企業は正しく識別されること

**実装ファイル**:
- `analyzer/markdown_parser.py`
- `analyzer/quality_validator.py`

---

#### REQ-005: 「今年起きたこと」の精度向上

**現状の問題**:
- ソニーで「純利益が1.8%増」など数値の羅列になっている
- 期待される「出来事」ではなく「KPI」が記載されている

**要件**:

1. **プロンプトの明確化**
   ```python
   EVENTS_PROMPT = """
   ## 今年起きたこと（トップ3）

   以下の基準で、この企業にとって重要な「出来事」を3つ抽出してください:

   抽出対象の「出来事」の例:
   - M&A、買収、売却、子会社の設立・統廃合
   - 新製品・新サービスの発表
   - 大型設備投資、工場建設
   - 組織再編、事業分社化
   - 重要な提携・契約の締結
   - 規制対応、法令違反、訴訟
   - 重大な事故・災害とその対応

   抽出しないもの:
   ❌ 売上高や利益の増減（これは別セクションで扱う）
   ❌ 一般的な市場動向
   ❌ 継続的な取り組み（特定の出来事ではないもの）

   フォーマット:
   1. 【出来事の内容】 (P.XX)
   2. 【出来事の内容】 (P.XX)
   3. 【出来事の内容】 (P.XX)

   出来事が3つ見つからない場合は、2つまたは1つでも構いません。
   無理に数値を記載しないでください。
   """
   ```

2. **出来事の検証ロジック**
   ```python
   def validate_events(events_list):
       """出来事リストの妥当性検証"""
       invalid_patterns = [
           r"売上高.*増",
           r"営業利益.*減",
           r"純利益.*推移",
           r"\d+\.\d+%",  # パーセンテージのみの記述
           r"前年同期比"
       ]

       validated_events = []
       for event in events_list:
           is_valid = True
           for pattern in invalid_patterns:
               if re.search(pattern, event):
                   is_valid = False
                   break

           if is_valid:
               validated_events.append(event)

       return validated_events
   ```

**成功基準**:
- 数値のみの記述が含まれないこと
- 具体的な出来事が記載されていること

**実装ファイル**:
- `analyzer/prompt_templates.py`
- `analyzer/event_extractor.py`

---

#### REQ-006: 時系列データ取得の安定化

**現状の問題**:
- トヨタ紡織で「時系列データ不足」エラー

**要件**:

1. **XBRLからの時系列取得の改善**
   ```python
   def get_timeseries_data(xbrl_files, metric_tag, years=5):
       """過去N年分の時系列データを取得"""
       timeseries = {}

       for year in range(current_year, current_year - years, -1):
           try:
               xbrl_data = load_xbrl(year)
               value = get_financial_value(xbrl_data, [metric_tag])
               if value is not None:
                   timeseries[year] = value
           except FileNotFoundError:
               logger.warning(f"XBRL data not found for year {year}")
               continue
           except Exception as e:
               logger.error(f"Error loading XBRL for year {year}: {e}")
               continue

       return timeseries
   ```

2. **PDFからのバックアップ取得**
   ```python
   def fallback_to_pdf_timeseries(pdf_path, metric_name):
       """PDFから時系列表を抽出"""
       # 経営指標等の推移表を検出
       table_pattern = r"経営指標等.*推移"
       tables = extract_tables_near_pattern(pdf_path, table_pattern)

       # テーブルから該当指標を抽出
       for table in tables:
           if metric_name in table.columns:
               return table[metric_name]

       return None
   ```

**成功基準**:
- 過去4年分のデータが取得できること
- XBRLがない年度はPDFからフォールバック

**実装ファイル**:
- `analyzer/xbrl_parser.py`
- `analyzer/pdf_extractor.py`

---

### 🟢 Priority 3: 品質向上（任意）

#### REQ-007: 汎用フレーズの削減

**現状の問題**:
- 「原材料価格の高騰 → 価格転嫁を推進 (P.15)」が5社で同一

**要件**:

1. **企業固有の記述生成**
   ```python
   RISK_RESPONSE_PROMPT = """
   この企業のリスクと対応策を記述してください。

   重要:
   - この企業が実際に開示している対応策のみを記載
   - 汎用的な表現は避け、具体的な施策を記述
   - ページ番号を必ず付ける
   - 他の企業と同じ記述にならないよう注意

   例:
   ❌ 悪い例: 「原材料価格の高騰 → 価格転嫁を推進」
   ✅ 良い例: 「鋼材価格の高騰に対し、2023年4月より製品価格を平均8%引き上げ、
              主要顧客との価格改定交渉を実施 (P.45)」
   """
   ```

**成功基準**:
- 同一フレーズの使用が3社以下であること

**実装ファイル**:
- `analyzer/prompt_templates.py`

---

#### REQ-008: セグメント差異の詳細分析

**現状の問題**:
- JTで営業利益の差異が15.1%
- 説明が不十分

**要件**:

1. **差異分析の追加**
   ```python
   def analyze_segment_variance(segment_sum, consolidated, threshold=10.0):
       """セグメント合計と連結の差異分析"""
       variance_pct = abs(segment_sum - consolidated) / consolidated * 100

       if variance_pct > threshold:
           return {
               "status": "⚠️ 要確認",
               "message": f"差異が{variance_pct:.1f}%と大きいため、以下の項目を確認してください:\n"
                         f"- 全社費用（本社費用など）\n"
                         f"- セグメント間取引消去\n"
                         f"- 調整額の内訳"
           }
       else:
           return {
               "status": "✅",
               "message": "正常範囲内"
           }
   ```

**成功基準**:
- 10%以上の差異がある場合に詳細説明が追加されること

**実装ファイル**:
- `analyzer/segment_validator.py`

---

## 3. 実装計画

### フェーズ1: 緊急修正（今日中）

1. ✅ XBRLタグマッピング拡張 (REQ-001) - 2時間
2. ✅ RAGフィルタリング強化 (REQ-002) - 2時間
3. ✅ 品質検証厳格化 (REQ-003) - 1時間

**実装順序**:
1. Run_integrated_v9.pyを読む
2. XBRLパーサー部分を特定して修正
3. RAGクエリ生成部分を修正
4. 品質検証ロジックを修正
5. 1社でテスト実行
6. 10社で一括実行

---

### フェーズ2: 重要改善（明日）

4. ✅ セグメントデータ検出 (REQ-004) - 1時間
5. ✅ 「今年起きたこと」改善 (REQ-005) - 1時間
6. ✅ 時系列データ安定化 (REQ-006) - 1時間

---

### フェーズ3: 品質向上（来週）

7. 🔲 汎用フレーズ削減 (REQ-007) - 1時間
8. 🔲 セグメント差異分析 (REQ-008) - 0.5時間

---

## 4. テスト計画

### 単体テスト

各修正について以下をテスト:

1. **XBRLタグマッピング**
   - テストケース: JT, 日立, ソフトバンクG
   - 期待結果: 売上高と営業利益が取得できること

2. **RAGフィルタリング**
   - テストケース: 任天堂（ホタテ混入があった企業）
   - 期待結果: 他社情報が含まれないこと

3. **品質検証**
   - テストケース: JT（売上高N/A）
   - 期待結果: Confidence ≤ 60%

---

### 統合テスト

修正完了後、以下の10社で実行:

| 証券コード | 企業名 | 確認ポイント |
|-----------|--------|------------|
| 1301 | 極洋 | 基準データ（問題なし） |
| 2914 | JT | 売上高取得、データ混入なし |
| 3116 | トヨタ紡織 | 時系列データ取得 |
| 4063 | 信越化学 | 基準データ（問題なし） |
| 6501 | 日立 | 売上高・営業利益取得、データ混入なし |
| 6758 | ソニー | データ混入なし、「今年起きたこと」改善 |
| 7203 | トヨタ自動車 | 基準データ（問題なし） |
| 7974 | 任天堂 | データ混入なし |
| 9983 | ファーストリテイリング | 売上高取得 |
| 9984 | ソフトバンクG | 売上高・営業利益取得 |

---

### 成功基準

以下の条件を全て満たすこと:

- [ ] 10社中10社で売上高が取得できる
- [ ] 10社中10社で営業利益が取得できる
- [ ] 他社情報の混入が0件
- [ ] Confidence ≥ 80% が8社以上（80%以上）
- [ ] 重大なエラーが0件

---

## 5. リスクと対策

| リスク | 発生確率 | 影響度 | 対策 |
|--------|---------|-------|------|
| XBRLタグが想定外の構造 | 中 | 高 | ログ出力を詳細化、エラーハンドリング強化 |
| RAG修正で処理時間増加 | 低 | 中 | フィルタリングロジックの最適化 |
| 既存の正常動作が壊れる | 中 | 高 | 極洋・信越化学で回帰テスト必須 |

---

## 6. 修正後の期待品質

### 修正前 vs 修正後

| 項目 | 修正前 | 修正後目標 |
|------|--------|-----------|
| 主要KPI完全性 | 40% (4/10社) | 100% (10/10社) |
| データ混入率 | 40% (4/10社) | 0% (0/10社) |
| 平均Confidence | 88.7% | 92%+ |
| 実質的な品質 | 60点 | 90点+ |

---

## 7. 次のステップ

### 即座に実施

1. ✅ Run_integrated_v9.py の読み込み
2. ✅ 修正対象モジュールの特定
3. ✅ REQ-001からREQ-003を実装
4. ✅ 1社でテスト（極洋 - 正常系）
5. ✅ 1社でテスト（JT - 異常系）
6. ✅ 10社で一括実行
7. ✅ 結果の検証

---

**承認者**: ユーザー（全修正許可済み）
**実装者**: Claude Code
**レビュー**: 実装後にレビュー
