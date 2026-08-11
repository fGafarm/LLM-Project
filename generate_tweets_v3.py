"""v3: ENEOS系 (業界1位vs2位 / 有名企業shock headline) を中心に練り直し。

v2の反省:
- 170件の「個別企業」generic templateは弱い → 削除
- 「有報を読んだことがありますか？」のhookは弱い → 削除
- 長い disclaimer / ハッシュタグスパムを削減
- ENEOSのような "【業界】1位 vs 2位" が効いた → 重点化
- 兆円級の shock headline を line1 に

構成:
- A. shock_headline: 有名/大企業の X兆円 (line1 で吊る)  ~40件
- B. ichi_vs_ni: 業界別 1位 vs 2位 (ENEOS-出光型)        ~25件
- C. top5_ranking: 業界別 TOP5                           ~20件
- D. cross_industry: 業界横断の meta TOP10              ~5件
- E. mcap_gt: 含み益 > 時価総額 の企業深掘り            ~10件

合計 ~100件 前後。1日分としては多すぎなので分配は別途。
"""
import json, random, re, sys
from collections import defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

BASE = Path(r"C:\Users\shun nabeno\Desktop\Local LLM Project\StockFlow\frontend\public\data")
SITE = "https://kinmyakucode.com"

with open(BASE / "metrics_summary.json", encoding="utf-8") as f:
    ALL = json.load(f)["companies"]

JP = [c for c in ALL if str(c.get("t", "")).isdigit() and c.get("haGain") and c.get("haMult") and c.get("haBook")]
for c in JP:
    for s in ["株式会社", "（株）", "㈱"]:
        c["n"] = c["n"].replace(s, "")
    c["n"] = c["n"].replace("　", " ").strip()

BY_S = defaultdict(list)
for c in JP:
    BY_S[c["s"]].append(c)
for s in BY_S:
    BY_S[s].sort(key=lambda c: -c["haGain"])


def oku(mil):
    o = mil / 100
    if abs(o) >= 10000:
        return f"{o/10000:.1f}兆円"
    if abs(o) >= 1:
        return f"{o:,.0f}億円"
    return f"{mil:,.0f}百万円"


def mult(v): return f"{v:.1f}倍"
def pct(v): return f"{v:.1f}%"


random.seed(42)
tweets = []

# ============================================================
# A. Shock headline: 1行目に衝撃数字, 有名/大企業
# ============================================================
# 含み益絶対額 Top 含み益がN兆円/N千億円の企業で、名前が知られているもの
JP_SORTED = sorted(JP, key=lambda c: -c["haGain"])
# famous: market cap top ~500 or name-recognized
FAMOUS_TICKERS = {
    "5411", "5401", "9502", "9504", "9506", "9508", "9501", "9503",  # 電力/鉄鋼大手
    "5020", "5019",  # 石油
    "7203", "7267", "7201", "7269", "7270",  # 自動車
    "7011", "7012",  # 重機
    "8830", "8802", "8801", "3289", "3003",  # 不動産
    "1812", "1801", "1802", "1803", "1808",  # 建設ゼネコン
    "9020", "9022", "9001", "9005", "9008", "9009",  # 鉄道
    "6701", "6503", "6752", "6502", "6758", "6981", "6861",  # 電機
    "9432", "9433", "9434",  # 通信
    "8058", "8031", "8001", "2768", "8002", "8053",  # 商社
    "6501",  # 日立
    "4661",  # オリエンタルランド
    "9983",  # ファーストリテイリング
    "4502", "4503", "4507", "4568", "4578",  # 医薬
    "8306", "8316", "8411",  # 銀行
    "8725", "8750", "8630",  # 保険
    "2503", "2914", "2502", "2802",  # 食品
    "4063", "4005", "4452", "4911",  # 化学
    "5108", "3402", "7974",  # その他大手
    "4021",  # 日産化学
}

hl_tmpl_variants = [
    # 型1: 衝撃数字 → 一文 → 補足
    lambda c, s, rank, n_s, avg_m: (
        f"{c['n']}、帳簿に載らない{oku(c['haGain'])}\n\n"
        f"有報の土地簿価{oku(c['haBook'])}が、"
        f"地価公示で時価推定すると{oku(c['haBook']*c['haMult'])}。\n"
        f"倍率{mult(c['haMult'])}、{s}{n_s}社中{rank}位の含み益。\n\n"
        f"{SITE}/ja/company/{c['t']}"
    ),
    # 型2: 業界で比較
    lambda c, s, rank, n_s, avg_m: (
        f"【{s}】{c['n']}の土地、簿価の{mult(c['haMult'])}\n\n"
        f"簿価: {oku(c['haBook'])}\n"
        f"推定時価: {oku(c['haBook']*c['haMult'])}\n"
        f"含み益: {oku(c['haGain'])}\n\n"
        f"業界平均倍率{mult(avg_m)}に対して{mult(c['haMult'])}。"
        + ("古い都市部土地を多く持つ証左。" if c["haMult"] > avg_m * 1.5
           else "業界平均並み。" if c["haMult"] > avg_m * 0.7
           else "比較的新しい取得が多い。")
        + f"\n\n{SITE}/ja/company/{c['t']}"
    ),
    # 型3: 時価総額比つき (富の隠れ具合)
    lambda c, s, rank, n_s, avg_m: (
        f"{c['n']}、時価総額の{pct(c['haGainToMcap'])}が「土地の含み益」\n\n"
        f"含み益: {oku(c['haGain'])}\n"
        f"簿価: {oku(c['haBook'])} → 推定時価: {oku(c['haBook']*c['haMult'])}\n"
        f"倍率: {mult(c['haMult'])}\n\n"
        f"日本の会計は取得原価主義。何十年前の価格がBSに残り続ける。\n\n"
        f"{SITE}/ja/company/{c['t']}"
    ),
]

# 含み益50億円以上 + famous は優先, その他 top200 含み益も含む
famous_big = [c for c in JP_SORTED
              if c["haGain"] >= 500
              and not (c["haMult"] > 300 and c["t"] not in FAMOUS_TICKERS)]  # 異常倍率は famous 以外除外
# famous を先に, その後 含み益順
famous_big.sort(key=lambda c: (0 if c["t"] in FAMOUS_TICKERS else 1, -c["haGain"]))

seen = set()
for c in famous_big[:200]:
    if c["t"] in seen: continue
    seen.add(c["t"])
    s_comps = BY_S.get(c["s"], [c])
    rank = next((i + 1 for i, x in enumerate(s_comps) if x["t"] == c["t"]), 0)
    avg_m = sum(x["haMult"] for x in s_comps) / len(s_comps)
    # 型3は haGainToMcap がある企業に限定
    if c.get("haGainToMcap") and c["haGainToMcap"] > 30 and random.random() < 0.35:
        tmpl = hl_tmpl_variants[2]
    else:
        tmpl = random.choice(hl_tmpl_variants[:2])
    tweets.append({
        "concept": "shock_headline",
        "text": tmpl(c, c["s"], rank, len(s_comps), avg_m),
        "link": f"{SITE}/ja/company/{c['t']}",
    })

# ============================================================
# B. ichi_vs_ni: 業界 1位 vs 2位 / 2位 vs 3位 / 3位 vs 5位
# ============================================================
for s, comps in BY_S.items():
    if len(comps) < 3: continue
    # Skip outlier-ridden sectors
    if any(c["haMult"] > 300 for c in comps[:5]):
        comps = [c for c in comps if c["haMult"] <= 300]
    if len(comps) < 3: continue

    pairs = [(0, 1, "1位 vs 2位")]
    if len(comps) >= 4:
        pairs.append((1, 2, "2位 vs 3位"))
    if len(comps) >= 6:
        pairs.append((2, 4, "3位 vs 5位"))

    for (i, j, label) in pairs:
        c1, c2 = comps[i], comps[j]
        if c1["haGain"] < 1000: continue
        ratio = c1["haGain"] / max(1, c2["haGain"])
        tweets.append({
            "concept": "ichi_vs_ni",
            "text": (
                f"【{s}の含み益 {label}】\n\n"
                f"▶ {c1['n']}（{c1['t']}）\n"
                f"含み益: {oku(c1['haGain'])}（{mult(c1['haMult'])}）\n\n"
                f"▶ {c2['n']}（{c2['t']}）\n"
                f"含み益: {oku(c2['haGain'])}（{mult(c2['haMult'])}）\n\n"
                f"差は{ratio:.1f}倍。有報の土地簿価を地価公示で時価推定した差額。\n\n"
                f"{SITE}/ja/company/{c1['t']}"
            ),
            "link": f"{SITE}/ja/company/{c1['t']}",
        })

# ============================================================
# C. top5_ranking: 業界別 TOP5
# ============================================================
for s, comps in BY_S.items():
    if len(comps) < 5: continue
    top5 = comps[:5]
    if top5[0]["haGain"] < 3000: continue
    lines = [f"【{s}】土地の含み益 TOP5\n"]
    for i, c in enumerate(top5, 1):
        lines.append(f"{i}. {c['n']}（{c['t']}）{oku(c['haGain'])}（{mult(c['haMult'])}）")
    avg_m = sum(c["haMult"] for c in comps) / len(comps)
    lines.append(f"\n業界{len(comps)}社平均{mult(avg_m)}。")
    lines.append(f"取得原価主義の会計では、簿価は何十年も前の価格のまま。")
    lines.append(f"\n全業界のランキング\n{SITE}/ja/screening")
    tweets.append({"concept": "top5_ranking", "text": "\n".join(lines), "link": f"{SITE}/ja/screening"})

# ============================================================
# D. cross_industry: 業界横断 meta TOP10
# ============================================================
lines = ["【全業種】含み益 TOP10（日本の上場企業）\n"]
for i, c in enumerate(JP_SORTED[:10], 1):
    lines.append(f"{i}. {c['n']}（{c['t']} / {c['s']}）{oku(c['haGain'])}")
lines.append(f"\n土地を多く保有する鉄鋼・電力・石油・不動産が上位に。")
lines.append(f"\nスクリーニングで業界別・倍率別に絞り込み可能\n{SITE}/ja/screening")
tweets.append({"concept": "cross_industry", "text": "\n".join(lines), "link": f"{SITE}/ja/screening"})

# 全業種 倍率 TOP10 (簿価50億以上で絞る = まともな企業)
high_mult = [c for c in JP if c["haBook"] > 5000 and c["haMult"] > 30 and c["haGain"] > 3000]
high_mult.sort(key=lambda c: -c["haMult"])
if high_mult:
    lines = ["【倍率TOP10】簿価の数十倍に膨らむ土地を持つ企業\n"]
    for i, c in enumerate(high_mult[:10], 1):
        lines.append(f"{i}. {c['n']}（{c['t']} / {c['s']}）{mult(c['haMult'])}")
    lines.append(f"\n倍率高 = 古い都市部取得 or 再評価されない土地。")
    lines.append(f"\n{SITE}/ja/screening")
    tweets.append({"concept": "cross_industry", "text": "\n".join(lines), "link": f"{SITE}/ja/screening"})

# 時価総額比 TOP10
gt_sorted = sorted([c for c in JP if c.get("haGainToMcap") and c["haGainToMcap"] >= 200
                    and c["haGain"] > 500 and c["haBook"] > 500], key=lambda c: -c["haGainToMcap"])
if gt_sorted:
    lines = ["【時価総額 < 含み益】資産バリュー銘柄 TOP10\n"]
    for i, c in enumerate(gt_sorted[:10], 1):
        lines.append(f"{i}. {c['n']}（{c['t']}）含み益は時価総額の{pct(c['haGainToMcap'])}")
    lines.append(f"\n土地の含み益だけで、時価総額を大きく超える企業群。")
    lines.append(f"バリュー投資の観点では「資産の裏付け」として要注目。")
    lines.append(f"\n{SITE}/ja/screening")
    tweets.append({"concept": "cross_industry", "text": "\n".join(lines), "link": f"{SITE}/ja/screening"})

# ============================================================
# E. mcap_gt: 含み益 > 時価総額 の深掘り
# ============================================================
mcap_gt = sorted([c for c in JP if c.get("haGainToMcap") and c["haGainToMcap"] > 100
                  and c["haGain"] > 500 and c["haBook"] > 500
                  and c["haMult"] < 300], key=lambda c: -c["haGainToMcap"])
used_in_hl = seen.copy()
for c in mcap_gt[:40]:
    if c["t"] in used_in_hl: continue
    tweets.append({
        "concept": "mcap_gt",
        "text": (
            f"{c['n']}の土地だけで、時価総額の{pct(c['haGainToMcap'])}\n\n"
            f"簿価{oku(c['haBook'])} → 推定時価{oku(c['haBook']*c['haMult'])}\n"
            f"含み益: {oku(c['haGain'])}（{mult(c['haMult'])}）\n\n"
            f"理論上は土地を売却するだけで株を買い戻せる水準。"
            f"実際には事業用地なので簡単には動かせないが、"
            f"資産バリューの観点では見過ごせない。\n\n"
            f"{SITE}/ja/company/{c['t']}"
        ),
        "link": f"{SITE}/ja/company/{c['t']}",
    })

# ============================================================
# 仕上げ: ハッシュタグは最小限、文字数チェック
# ============================================================
TAGS = "\n\n#含み資産 #バリュー投資"

for tw in tweets:
    # 末尾のURLをそのままにして、tag は URL の後
    tw["text"] = tw["text"].rstrip() + TAGS
    tw["chars"] = len(tw["text"])

# 重複削除 (同じ ticker の shock_headline が先頭に複数来ないよう)
random.shuffle(tweets)
seen_t = defaultdict(int)
final = []
for tw in tweets:
    m = re.search(r"/company/(\d+)", tw["text"])
    t = m.group(1) if m else None
    if t:
        # 同じ ticker が3回以上出ないように
        if seen_t[t] >= 2: continue
        seen_t[t] += 1
    final.append(tw)
tweets = final

# Stats
print(f"Total: {len(tweets)}")
print(f"Avg chars: {sum(t['chars'] for t in tweets)/max(1,len(tweets)):.0f}")
print(f"Min chars: {min(t['chars'] for t in tweets)}")
print(f"Max chars: {max(t['chars'] for t in tweets)}")
concepts = defaultdict(int)
for t in tweets:
    concepts[t["concept"]] += 1
print("\nConcepts:")
for k, v in sorted(concepts.items(), key=lambda x: -x[1]):
    print(f"  {k}: {v}")

# Samples
print("\n===== SAMPLE 5 TWEETS =====")
for i, tw in enumerate(tweets[:5]):
    print(f"\n--- [{i+1}] concept={tw['concept']} chars={tw['chars']} ---")
    print(tw["text"])

# Save raw tweets (for review before scheduling)
out = Path(r"C:\Users\shun nabeno\Desktop\Local LLM Project\tweets_v3.json")
with open(out, "w", encoding="utf-8") as f:
    json.dump(tweets, f, ensure_ascii=False, indent=2)
print(f"\nSaved to {out}")
