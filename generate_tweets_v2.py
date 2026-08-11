"""
含み資産ツイート v2: 1件1件コンセプトを設計して生成
7日 × 50件 = 350件、全て300字以上

コンセプトカテゴリ:
A. 比較系 (同業他社、業界間、有名企業同士)
B. 意外性 (帳簿と実態の乖離、含み益>時価総額)
C. ランキング (業種別TOP5、指標別)
D. 問題提起 (なぜ株価に反映されない？)
E. 解説・有益 (含み資産の読み方、使い方)
F. 有名企業深掘り
G. 業界俯瞰
"""
import json, random, sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

BASE = Path(r"C:\Users\shun nabeno\Desktop\Local LLM Project\StockFlow\frontend\public\data")
SITE = "https://kinmyakucode.com"

with open(BASE / "metrics_summary.json", encoding="utf-8") as f:
    ALL = json.load(f)["companies"]

JP = [c for c in ALL if str(c.get("t","")).isdigit() and c.get("haGain") and c.get("haMult") and c.get("haBook")]
for c in JP:
    for s in ["株式会社","（株）"]:
        c["n"] = c["n"].replace(s, "")
    c["n"] = c["n"].replace("　"," ").strip()

BY_S = defaultdict(list)
for c in JP: BY_S[c["s"]].append(c)
for s in BY_S: BY_S[s].sort(key=lambda c: -c["haGain"])

def oku(mil):
    o = mil / 100
    if abs(o) >= 10000: return f"{o/10000:.1f}兆円"
    if abs(o) >= 1: return f"{o:,.0f}億円"
    return f"{mil:,.0f}百万円"

def mult(v): return f"{v:.1f}倍"
def pct(v): return f"{v:.1f}%"

random.seed(42)
tweets = []

# ============================================================
# A. 比較系 — 同業種で倍率が大きく違う2社
# ============================================================
for s, comps in BY_S.items():
    if len(comps) < 5: continue
    hi = max(comps, key=lambda c: c["haMult"])
    lo_candidates = [c for c in comps if c["haMult"] > 0.5 and c["haBook"] > 500]
    if not lo_candidates: continue
    lo = min(lo_candidates, key=lambda c: c["haMult"])
    if hi["haMult"] / lo["haMult"] < 3: continue

    tweets.append({
        "concept": "同業比較_倍率格差",
        "text": (
            f"同じ{s}なのに、土地の含み益倍率がここまで違う\n\n"
            f"▶ {hi['n']}（{hi['t']}）\n"
            f"簿価{oku(hi['haBook'])} → 推定時価{oku(hi['haBook']*hi['haMult'])}\n"
            f"含み益{oku(hi['haGain'])}（{mult(hi['haMult'])}）\n\n"
            f"▶ {lo['n']}（{lo['t']}）\n"
            f"簿価{oku(lo['haBook'])} → 推定時価{oku(lo['haBook']*lo['haMult'])}\n"
            f"含み益{oku(lo['haGain'])}（{mult(lo['haMult'])}）\n\n"
            f"同じ業界でも、いつ・どこに土地を取得したかで含み益は何十倍も変わる。"
            f"取得時期が古い都心の土地ほど簿価と時価の差が開く。\n\n"
            f"業界比較はこちら\n{SITE}/ja/screening"
        ),
        "link": f"{SITE}/ja/screening",
    })

# 同業比較 — 含み益額トップ vs 2位
for s, comps in BY_S.items():
    if len(comps) < 3: continue
    c1, c2 = comps[0], comps[1]
    if c1["haGain"] < 10000: continue  # 100億以上

    tweets.append({
        "concept": "同業比較_1位vs2位",
        "text": (
            f"【{s}の含み益 1位 vs 2位】\n\n"
            f"1位 {c1['n']}（{c1['t']}）\n"
            f"含み益: {oku(c1['haGain'])}（簿価の{mult(c1['haMult'])}）\n\n"
            f"2位 {c2['n']}（{c2['t']}）\n"
            f"含み益: {oku(c2['haGain'])}（簿価の{mult(c2['haMult'])}）\n\n"
            f"1位は2位の{c1['haGain']/c2['haGain']:.1f}倍。"
            f"有報の「主要な設備の状況」に載っている土地の簿価を、"
            f"地価公示の市区町村×用途別データで時価推定しています。\n\n"
            f"{SITE}/ja/company/{c1['t']}"
        ),
        "link": f"{SITE}/ja/company/{c1['t']}",
    })

# ============================================================
# B. 意外性 — 含み益が時価総額を超える企業
# ============================================================
gt_mcap = sorted([c for c in JP if c.get("haGainToMcap") and c["haGainToMcap"] > 100],
                 key=lambda c: -c["haGainToMcap"])

for c in gt_mcap[:30]:
    tweets.append({
        "concept": "意外性_含み益>時価総額",
        "text": (
            f"{c['n']}の含み益が時価総額を超えている件\n\n"
            f"時価総額に対する含み益の比率: {pct(c['haGainToMcap'])}\n\n"
            f"つまり、この会社の土地だけで時価総額以上の「帳簿に載らない価値」がある。"
            f"簿価{oku(c['haBook'])}の土地が、地価公示ベースで{oku(c['haBook']*c['haMult'])}。"
            f"含み益{oku(c['haGain'])}。\n\n"
            f"土地を適正価格で売却すれば、理論上は株を全部買い戻してもお釣りが来る計算。"
            f"もちろん実際にはそう単純ではないが、資産バリューの観点では注目に値する。\n\n"
            f"詳細\n{SITE}/ja/company/{c['t']}"
        ),
        "link": f"{SITE}/ja/company/{c['t']}",
    })

# 意外性 — 極端な倍率
extreme_mult = sorted(JP, key=lambda c: -c["haMult"])
for c in extreme_mult[:15]:
    if c["haMult"] < 50: continue
    tweets.append({
        "concept": "意外性_極端倍率",
        "text": (
            f"【簿価の{c['haMult']:.0f}倍】{c['n']}の土地\n\n"
            f"有報に載っている土地の簿価: {oku(c['haBook'])}\n"
            f"地価公示で推定した時価: {oku(c['haBook']*c['haMult'])}\n\n"
            f"なぜこんなに差がつくのか？\n"
            f"日本の会計基準では土地は取得原価で計上される。"
            f"つまり数十年前に買った土地は、当時の価格のままBSに載っている。"
            f"一方、地価は都市部を中心に大きく上昇した地域もある。\n\n"
            f"この差が「含み益」であり、{c['s']}平均の"
            f"{sum(x['haMult'] for x in BY_S[c['s']])/len(BY_S[c['s']]):.1f}倍と比べても"
            f"突出している。\n\n"
            f"{SITE}/ja/company/{c['t']}"
        ),
        "link": f"{SITE}/ja/company/{c['t']}",
    })

# ============================================================
# C. ランキング — 業種別 TOP5
# ============================================================
for s, comps in BY_S.items():
    if len(comps) < 5: continue
    top5 = comps[:5]
    if top5[0]["haGain"] < 5000: continue

    lines = [f"【{s}】含み益ランキング TOP5\n"]
    for i, c in enumerate(top5, 1):
        lines.append(f"{i}. {c['n']}（{c['t']}）{oku(c['haGain'])}（{mult(c['haMult'])}）")
    avg_m = sum(c["haMult"] for c in comps) / len(comps)
    lines.append(f"\n業界{len(comps)}社の平均倍率は{mult(avg_m)}。")
    lines.append(f"土地を多く持つ企業ほど含み益が大きいとは限らない。"
                 f"取得時期と所在地で倍率は大きく変わる。")
    lines.append(f"\n全企業のランキング・スクリーニング\n{SITE}/ja/screening")

    tweets.append({
        "concept": "ランキング_業種TOP5",
        "text": "\n".join(lines),
        "link": f"{SITE}/ja/screening",
    })

# ランキング — 含み益/時価総額 TOP10
gt_mcap_sorted = sorted([c for c in JP if c.get("haGainToMcap")], key=lambda c: -c["haGainToMcap"])
lines = ["含み益が時価総額に対して大きい企業 TOP10\n"]
for i, c in enumerate(gt_mcap_sorted[:10], 1):
    lines.append(f"{i}. {c['n']}（{c['t']}）含み益{oku(c['haGain'])} = 時価総額の{pct(c['haGainToMcap'])}")
lines.append(f"\n時価総額より含み益のほうが大きい = 土地だけで会社全体以上の隠れた価値がある。")
lines.append(f"バリュー投資の観点では「資産の裏付け」として重要な指標。")
lines.append(f"\nスクリーニングで絞り込み可能\n{SITE}/ja/screening")
tweets.append({"concept": "ランキング_時価総額比TOP10", "text": "\n".join(lines), "link": f"{SITE}/ja/screening"})

# ============================================================
# D. 問題提起
# ============================================================
famous = {"7203":"トヨタ自動車","6758":"ソニーグループ","9984":"ソフトバンクグループ",
          "8058":"三菱商事","9020":"JR東日本","1812":"鹿島建設","8830":"住友不動産",
          "9432":"NTT","6501":"日立製作所","7011":"三菱重工業"}
for t, name in famous.items():
    c = next((x for x in JP if x["t"] == t), None)
    if not c: continue
    tweets.append({
        "concept": "問題提起_有名企業",
        "text": (
            f"{c['n']}のBSに載っていない資産の話\n\n"
            f"有報によると土地の簿価は{oku(c['haBook'])}。\n"
            f"しかし地価公示データで現在の時価を推定すると{oku(c['haBook']*c['haMult'])}。\n"
            f"帳簿に載らない含み益: {oku(c['haGain'])}\n\n"
            f"日本の会計基準では土地は取得原価主義。\n"
            f"IFRSなら再評価モデルの選択肢もあるが、ほとんどの日本企業は原価モデルを採用。"
            f"結果、何十年も前の価格がBSに残り続ける。\n\n"
            f"この「見えない資産」をどう評価するかで、企業の見え方は変わる。\n\n"
            f"{SITE}/ja/company/{c['t']}"
        ),
        "link": f"{SITE}/ja/company/{c['t']}",
    })

# 問題提起 — なぜ含み益は実現しない？
tweets.append({
    "concept": "問題提起_実現しない理由",
    "text": (
        "「含み益があるなら売ればいいのに」と思うかもしれない。\n\n"
        "でも企業にとって土地は事業の基盤。工場、本社、営業所。\n"
        "売れば含み益は実現するが、事業を続けるには別の場所が必要。\n"
        "移転コストや従業員の生活を考えると、簡単には動けない。\n\n"
        "ただし、M&Aや事業再編の局面では含み益が顕在化する。\n"
        "買収者は「帳簿上の価値」ではなく「実際の資産価値」で企業を評価するから。\n\n"
        "全上場企業の含み資産データを無料公開中。\n"
        "有報の設備テーブル×地価公示で算出しています。\n\n"
        f"{SITE}/ja/screening"
    ),
    "link": f"{SITE}/ja/screening",
})

# ============================================================
# E. 解説・有益
# ============================================================
tweets.append({
    "concept": "解説_含み資産とは",
    "text": (
        "【含み資産の見方】3分でわかる解説\n\n"
        "1. 有報の「主要な設備の状況」を開く\n"
        "2. 土地の「簿価」と「面積」を確認\n"
        "3. 国土交通省の地価公示で同じ市区町村の地価を調べる\n"
        "4. 面積 × 地価 = 推定時価\n"
        "5. 推定時価 - 簿価 = 含み益\n\n"
        "この作業を全上場企業で自動化したのが金脈コード。\n"
        "2,000社超の含み資産データを無料で見られます。\n\n"
        "スクリーニングで「含み益」「倍率」「時価総額比」等で絞り込み可能。\n\n"
        f"{SITE}/ja/screening"
    ),
    "link": f"{SITE}/ja/screening",
})

tweets.append({
    "concept": "解説_倍率の読み方",
    "text": (
        "含み資産の「倍率」はどう読む？\n\n"
        "倍率 = 推定時価 ÷ 簿価\n\n"
        "倍率2倍: 土地の価値が簿価の2倍。堅実だが大きな含み益ではない。\n"
        "倍率10倍: 取得原価の10倍。数十年前の取得で、都市部の可能性。\n"
        "倍率50倍超: 非常に古い取得 or 都心一等地。帳簿と実態が大きく乖離。\n\n"
        "ただし倍率が高い = 良い企業、ではない。\n"
        "あくまで「帳簿に載っていない資産価値がどれだけあるか」の指標。\n\n"
        "業界平均と比較して初めて意味がある。\n\n"
        f"業界別の平均倍率はこちら\n{SITE}/ja/screening"
    ),
    "link": f"{SITE}/ja/screening",
})

# ============================================================
# F. 有名企業深掘り — 個別の数字で語る
# ============================================================
deep_dive_tickers = [
    "7203","9020","8830","1812","5411","9504","7011","6701",
    "9432","8058","8031","9433","6758","9983","4502","8035",
    "9401","2802","4901","2914","8801","9005","9024","3382",
    "6098","8306","8316","2503","4063","6902",
]
for t in deep_dive_tickers:
    c = next((x for x in JP if x["t"] == t), None)
    if not c: continue
    s_comps = BY_S.get(c["s"], [c])
    rank = next((i+1 for i, x in enumerate(s_comps) if x["t"] == t), 0)
    avg_m = sum(x["haMult"] for x in s_comps) / len(s_comps)

    tweets.append({
        "concept": "深掘り_有名企業",
        "text": (
            f"【{c['n']}の含み資産を分析】\n\n"
            f"簿価: {oku(c['haBook'])}\n"
            f"推定時価: {oku(c['haBook']*c['haMult'])}\n"
            f"含み益: {oku(c['haGain'])}\n"
            f"倍率: {mult(c['haMult'])}\n"
            + (f"時価総額比: {pct(c['haGainToMcap'])}\n" if c.get("haGainToMcap") else "")
            + f"\n{c['s']}{len(s_comps)}社中、含み益は{rank}位。"
            f"業界平均倍率{mult(avg_m)}に対して同社は{mult(c['haMult'])}。\n\n"
            + ("業界平均を大きく上回っており、古くからの都市部の土地を多く保有していると推測される。\n\n" if c["haMult"] > avg_m * 1.5
               else "業界平均並み。\n\n" if c["haMult"] > avg_m * 0.7
               else "業界平均を下回る。比較的新しい取得が多い可能性。\n\n")
            + f"{SITE}/ja/company/{c['t']}"
        ),
        "link": f"{SITE}/ja/company/{c['t']}",
    })

# ============================================================
# G. 業界俯瞰
# ============================================================
sector_avg = []
for s, comps in BY_S.items():
    if len(comps) < 3: continue
    sector_avg.append({
        "s": s, "n": len(comps),
        "avg_m": sum(c["haMult"] for c in comps)/len(comps),
        "avg_g": sum(c["haGain"] for c in comps)/len(comps),
        "total_g": sum(c["haGain"] for c in comps),
    })
sector_avg.sort(key=lambda x: -x["avg_m"])

# 業界倍率ランキング
lines = ["【業界別】含み資産の倍率ランキング\n"]
for i, sa in enumerate(sector_avg[:10], 1):
    lines.append(f"{i}. {sa['s']}（{sa['n']}社）平均{sa['avg_m']:.1f}倍")
lines.append(f"\n倍率が高い業界 = 古くからの土地を多く持つ傾向。")
lines.append(f"建設・サービス・情報通信が上位に来るのは、都市部に本社・営業所を構える企業が多いため。")
lines.append(f"\n{SITE}/ja/screening")
tweets.append({"concept": "業界俯瞰_倍率ランキング", "text": "\n".join(lines), "link": f"{SITE}/ja/screening"})

# 業界含み益合計ランキング
sector_avg_by_total = sorted(sector_avg, key=lambda x: -x["total_g"])
lines = ["【業界別】含み益の合計額ランキング\n"]
for i, sa in enumerate(sector_avg_by_total[:10], 1):
    lines.append(f"{i}. {sa['s']}（{sa['n']}社）合計{oku(sa['total_g'])}")
lines.append(f"\n不動産・建設・化学が上位。土地を大量に保有する業界ほど含み益の絶対額は大きくなる。")
lines.append(f"ただし倍率（質）と合計額（量）は必ずしも一致しない。")
lines.append(f"\n{SITE}/ja/screening")
tweets.append({"concept": "業界俯瞰_含み益合計", "text": "\n".join(lines), "link": f"{SITE}/ja/screening"})

# 個別業界深掘り
for sa in sector_avg[:8]:
    s = sa["s"]
    comps = BY_S[s]
    top = comps[0]
    tweets.append({
        "concept": "業界俯瞰_個別",
        "text": (
            f"【{s}の含み資産事情】\n\n"
            f"対象: {sa['n']}社\n"
            f"平均倍率: {sa['avg_m']:.1f}倍\n"
            f"平均含み益: {oku(sa['avg_g'])}\n"
            f"含み益1位: {top['n']}（{oku(top['haGain'])}）\n\n"
            f"この業界では、{'都市部の事業用地が多く倍率が高い傾向' if sa['avg_m'] > 15 else '工場・倉庫系の土地が中心で倍率はやや落ち着いている'}。"
            f"{'中小企業でも数十億の含み益を持つケースが目立つ' if sa['avg_g'] > 5000 else '大手と中小で含み益の差が顕著'}。\n\n"
            f"業界の全企業データはスクリーニングで\n{SITE}/ja/screening"
        ),
        "link": f"{SITE}/ja/screening",
    })

# ============================================================
# 追加: 個別企業ネタ（残り枠を埋める）
# ============================================================
used_tickers = set()
for tw in tweets:
    import re
    m = re.search(r"/company/(\d{4})", tw.get("text",""))
    if m: used_tickers.add(m.group(1))

# 含み益大きい順で未使用の企業
remaining = [c for c in sorted(JP, key=lambda c: -c["haGain"]) if c["t"] not in used_tickers]

concept_pool = [
    lambda c, avg_m, rank, n_s: (
        f"{c['n']}、帳簿には載らない{oku(c['haGain'])}の価値\n\n"
        f"有報ベースの土地簿価は{oku(c['haBook'])}だが、"
        f"地価公示で時価推定すると{oku(c['haBook']*c['haMult'])}。\n"
        f"倍率{mult(c['haMult'])}は{c['s']}平均{mult(avg_m)}の"
        f"{c['haMult']/avg_m:.1f}倍。\n\n"
        f"{c['s']}{n_s}社中{rank}位の含み益。"
        + (f"\n時価総額の{pct(c['haGainToMcap'])}に相当し、" if c.get("haGainToMcap") else "\n")
        + f"バリュー投資の観点では要注目の水準。\n\n"
        f"{SITE}/ja/company/{c['t']}"
    ),
    lambda c, avg_m, rank, n_s: (
        f"【{c['s']}】{c['n']}の土地、簿価と時価の差\n\n"
        f"簿価: {oku(c['haBook'])}\n"
        f"推定時価: {oku(c['haBook']*c['haMult'])}\n"
        f"差額: {oku(c['haGain'])}\n\n"
        f"この差が生まれる理由は、日本の会計基準が土地を取得原価で据え置くから。"
        f"地価が上がっても下がっても、BSの数字は変わらない。\n\n"
        f"業界{n_s}社の平均は{mult(avg_m)}で、{c['n']}は{mult(c['haMult'])}。"
        f"{'業界トップクラスの含み益を持つ' if rank <= 3 else '業界上位の含み益水準にある' if rank <= n_s//4 else '中堅だが見過ごせない含み益がある'}。\n\n"
        f"{SITE}/ja/company/{c['t']}"
    ),
    lambda c, avg_m, rank, n_s: (
        f"有報を読んだことがありますか？\n\n"
        f"{c['n']}の有報には「主要な設備の状況」というテーブルがある。"
        f"そこに載っている土地の簿価は{oku(c['haBook'])}。\n\n"
        f"でも国土交通省の地価公示データで同じ場所の地価を調べると、"
        f"推定時価は{oku(c['haBook']*c['haMult'])}。"
        f"含み益は{oku(c['haGain'])}で、簿価の{mult(c['haMult'])}。\n\n"
        f"こういうデータを全上場企業で自動計算して無料公開しています。\n\n"
        f"{SITE}/ja/company/{c['t']}"
    ),
]

for c in remaining:
    if len(tweets) >= 360: break
    s_comps = BY_S.get(c["s"], [c])
    rank = next((i+1 for i, x in enumerate(s_comps) if x["t"] == c["t"]), 0)
    avg_m = sum(x["haMult"] for x in s_comps) / len(s_comps)
    fn = random.choice(concept_pool)
    text = fn(c, avg_m, rank, len(s_comps))
    # 300字未満なら補足
    if len(text) < 300:
        text += (
            f"\n\n※有報「主要な設備の状況」の土地簿価と"
            f"国土交通省「地価公示」（令和6年）の市区町村別データで算出"
        )
    tweets.append({
        "concept": "個別企業",
        "text": text,
        "link": f"{SITE}/ja/company/{c['t']}",
    })

# ============================================================
# 350件に整形して7日分に分配
# ============================================================
random.shuffle(tweets)
tweets = tweets[:350]

# 300字チェック + ハッシュタグ追加
TAGS = "\n\n#含み資産 #有報分析 #バリュー投資 #金脈コード"
FILLER_A = "\n\n※有価証券報告書「主要な設備の状況」の土地簿価と国土交通省「地価公示」（令和6年）の市区町村×用途別平均値で算出。実際の売却価格とは異なる場合があります。"
FILLER_B = "\n\n金脈コードでは2,000社超の含み資産データを無料公開。スクリーニングで倍率・含み益・時価総額比など複数条件で絞り込みできます。"
FILLER_C = "\n\n含み資産 = 有報に記載された土地の「簿価」と、地価公示データから推定した「時価」の差額。取得原価主義の日本会計基準では、この差が帳簿に反映されない。"

fillers = [FILLER_A, FILLER_B, FILLER_C]
for i, tw in enumerate(tweets):
    tw["text"] += TAGS
    attempt = 0
    while len(tw["text"]) < 300 and attempt < len(fillers):
        tw["text"] = tw["text"].replace(TAGS, fillers[(i + attempt) % len(fillers)] + TAGS)
        attempt += 1
    tw["chars"] = len(tw["text"])

# 7日分に分割
start = date.today() + timedelta(days=1)
weekly = {}
for day in range(7):
    d = (start + timedelta(days=day)).strftime("%Y-%m-%d")
    day_tweets = tweets[day*50:(day+1)*50]
    weekly[d] = [{"ticker": re.search(r'/company/(\d{4})', tw["text"]).group(1) if re.search(r'/company/(\d{4})', tw["text"]) else "0000",
                  "name": "", "text": tw["text"], "chars": tw["chars"], "concept": tw["concept"]}
                 for tw in day_tweets]

# Stats
total = sum(len(v) for v in weekly.values())
under300 = sum(1 for v in weekly.values() for t in v if t["chars"] < 300)
concepts = defaultdict(int)
for v in weekly.values():
    for t in v: concepts[t["concept"]] += 1

print(f"Total: {total}")
print(f"Under 300: {under300}")
print(f"Avg chars: {sum(t['chars'] for v in weekly.values() for t in v)/total:.0f}")
print(f"Min chars: {min(t['chars'] for v in weekly.values() for t in v)}")
print(f"\nConcept distribution:")
for k, v in sorted(concepts.items(), key=lambda x: -x[1]):
    print(f"  {k}: {v}")

for d, ts in weekly.items():
    print(f"\n{d}: {len(ts)} tweets (avg {sum(t['chars'] for t in ts)/len(ts):.0f} chars)")

# Save
out = Path(r"C:\Users\shun nabeno\Desktop\Local LLM Project\tweets_weekly.json")
with open(out, "w", encoding="utf-8") as f:
    json.dump(weekly, f, ensure_ascii=False, indent=2)
print(f"\nSaved to {out}")
