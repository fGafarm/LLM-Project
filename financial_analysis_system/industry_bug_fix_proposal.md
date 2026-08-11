# industry=food バグ修正提案

## 問題

**`industry=food` でセグメントYoYが全て+1.8%になるエラーが100%発生**

| Industry | 成功率 | セグメントYoY |
|---|---|---|
| **food** | 0/4 (0%) | 全て+1.8% ❌ |
| **all** | 1/1 (100%) | 正確 ✅ |

## 根本原因（未確定）

1. ✅ QA抽出時点でエラー（最終レポート生成ではない）
2. ✅ 同じテキスト（10p, 6,691文字）でも結果が異なる
3. ✅ `analyze_section_qa`で`industry`パラメータは未使用
4. ❓ なぜ100%の相関があるのか不明

## 対策1: `industry=all` を推奨（即効性）

```python
def select_industry(default: str) -> str:
    """業種選択（allを強く推奨）"""
    print(f"\n業種（デフォルト: {default}）:")
    print("  1. all  2. food  3. manufacturing  4. retail  5. it  6. finance")
    print(f"\n  ⚠️  推奨: all （industry=foodでセグメント分析エラーが報告されています）")
    print(f"  現在のデフォルト: {default}")
    choice = input("\n選択 [1-6, Enter=all推奨]: ").strip()

    if not choice:
        return 'all'  # デフォルトをallに変更

    industries = {
        '1': 'all',
        '2': 'food',
        '3': 'manufacturing',
        '4': 'retail',
        '5': 'it',
        '6': 'finance'
    }
    return industries.get(choice, 'all')
```

## 対策2: Phase 1でセグメントYoY異常を検出（品質保証）

### 新規バリデーション追加

```python
def validate_segment_yoy_suspicious_pattern(section_extracts: List[Dict], xbrl: Dict) -> Dict:
    """
    セグメントYoYが全て同じ値（全社YoY）になっている異常を検出

    Returns:
        {
            'status': 'OK' | 'ERROR' | 'SKIP',
            'reason': str,
            'confidence_penalty': float  # エラー時の減点
        }
    """
    # セグメントセクションを取得
    segment_section = next(
        (s for s in section_extracts if s['section_key'] == '05_セグメント'),
        None
    )

    if not segment_section:
        return {'status': 'SKIP', 'reason': 'No segment section found'}

    # segment_revenue QA回答を取得
    segment_revenue_qa = next(
        (qa for qa in segment_section.get('qa_answers', [])
         if qa.get('question_id') == 'segment_revenue'),
        None
    )

    if not segment_revenue_qa:
        return {'status': 'SKIP', 'reason': 'No segment_revenue QA answer'}

    answer = segment_revenue_qa.get('answer', '')

    # YoY値を抽出
    import re
    yoy_pattern = r'\(前年比\s*([+-]?\d+\.?\d*)%\)'
    yoys = re.findall(yoy_pattern, answer)

    if len(yoys) < 3:
        return {'status': 'SKIP', 'reason': f'Too few YoY values ({len(yoys)})'}

    # 全セグメントが同じYoYか確認
    unique_yoys = set(yoys)

    if len(unique_yoys) == 1:
        # 全て同じ値 → 疑わしい
        suspicious_yoy = list(unique_yoys)[0]

        # 全社YoYと比較
        company_revenue_yoy = None
        if xbrl.get('revenue') and prev_xbrl := xbrl.get('_prev_xbrl'):
            prev_revenue = prev_xbrl.get('revenue')
            if prev_revenue and prev_revenue > 0:
                company_revenue_yoy = (
                    (xbrl['revenue'] - prev_revenue) / prev_revenue * 100
                )

        # 全社YoYと一致するか
        if company_revenue_yoy is not None and abs(float(suspicious_yoy) - company_revenue_yoy) < 0.2:
            return {
                'status': 'ERROR',
                'reason': (
                    f'All segments have identical YoY ({suspicious_yoy}%) '
                    f'matching company-wide YoY ({company_revenue_yoy:.1f}%) '
                    f'- LLM likely copied company YoY instead of calculating per-segment'
                ),
                'confidence_penalty': 20.0,  # 20%減点（重大エラー）
                'details': {
                    'segment_count': len(yoys),
                    'suspicious_yoy': suspicious_yoy,
                    'company_yoy': company_revenue_yoy,
                }
            }

    return {'status': 'OK'}
```

### Phase 1に組み込み

```python
def run_phase1_validation(
    section_extracts: List[Dict],
    xbrl: Dict,
    prev_xbrl: Dict,
    historical_xbrl: Dict
) -> Dict:
    """Phase 1品質検証"""

    # ... 既存のバリデーション ...

    # 🆕 セグメントYoY異常検出
    segment_yoy_check = validate_segment_yoy_suspicious_pattern(
        section_extracts,
        xbrl
    )

    if segment_yoy_check['status'] == 'ERROR':
        l5_warnings.append(segment_yoy_check['reason'])
        confidence_penalties.append(segment_yoy_check['confidence_penalty'])

    # ... 残りの処理 ...
```

## 対策3: QAプロンプトに明示的警告を追加

```python
def analyze_section_qa(section_key: str, section_text: str, xbrl: Dict,
                       prev_xbrl: Dict, industry: str) -> Dict:
    """セクションを読んで質問に答えさせる（v9.6.1方式）"""
    # ...

    for q in section_config["questions"]:
        # segment_revenue質問に特別な警告を追加
        question_text = q['question']

        if q['id'] == 'segment_revenue':
            question_text += """

⚠️ 重要: 各セグメントの前年比YoYを個別に計算してください。
全社の売上高YoYをそのまま全セグメントにコピーしないこと。
各セグメントの当期売上と前期売上から正確にYoYを計算すること。"""

        prompt = f\"\"\"以下のテキストを読んで質問に答えてください。

【テキスト】
{truncated_text}

【参考データ】
{xbrl_summary}

【質問】
{question_text}

【回答ルール】
- テキストに書いてある内容だけで答える
- 数値・金額・割合を含める
- ページ番号[P.X]を付ける

回答:\"\"\"

        # ... 残りの処理 ...
```

## 推奨実装順序

1. ✅ **対策1: `industry=all`を推奨**（即座に実装可能、1分）
   - デフォルトを`all`に変更
   - 選択画面で警告表示

2. ✅ **対策2: Phase 1検証追加**（実装時間: 10分）
   - セグメントYoY異常を自動検出
   - Confidence Scoreを20%減点

3. ⚠️ **対策3: QAプロンプト強化**（効果不確定、5分）
   - LLMへの明示的警告
   - 効果があるかは不明

## 検証方法

```bash
# 1301極洋で10回実行してエラー率を確認
for i in {1..10}; do
    python Run_integrated_v9.py --company 1301 --year 2022 --type 有報 --mode hybrid --industry all
done

# JSONからYoYパターンを抽出
python check_segment_yoy.py output_v10/1301_*/porta10_*.json
```

**期待結果**:
- `industry=all`: 90%以上成功
- Phase 1検証が異常を検出してConfidence低下
