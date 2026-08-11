"""
含み資産データから1週間分のツイート(50本/日×7日=350本)を自動生成。

各ツイートの内容:
- 企業の含み益データ (簿価 vs 推定時価)
- 業界平均との比較
- 300文字以上
- サイトURLあり
"""
import json
import random
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

PROJECT_ROOT = Path(r"C:\Users\shun nabeno\Desktop\Local LLM Project")
METRICS = PROJECT_ROOT / "StockFlow" / "frontend" / "public" / "data" / "metrics_summary.json"
SITE_URL = "https://kinmyakucode.com/ja/company"
SCREENING_URL = "https://kinmyakucode.com/ja/screening"

with open(METRICS, encoding="utf-8") as f:
    data = json.load(f)
companies = data["companies"]

# Filter: hidden assets data available
ha_companies = [c for c in companies if c.get("haGain") and c.get("haBook") and c.get("haMult")]
print(f"Total with hidden assets: {len(ha_companies)}")

# Industry averages
by_sector = defaultdict(list)
for c in ha_companies:
    if c.get("s"):
        by_sector[c["s"]].append(c)

industry_avg = {}
for sector, comps in by_sector.items():
    gains = [c["haGain"] for c in comps]
    mults = [c["haMult"] for c in comps]
    books = [c["haBook"] for c in comps]
    mcap_ratios = [c["haGainToMcap"] for c in comps if c.get("haGainToMcap")]
    industry_avg[sector] = {
        "count": len(comps),
        "avg_gain": sum(gains) / len(gains),
        "avg_mult": sum(mults) / len(mults),
        "avg_book": sum(books) / len(books),
        "avg_mcap_ratio": sum(mcap_ratios) / len(mcap_ratios) if mcap_ratios else 0,
        "max_gain": max(gains),
        "max_mult": max(mults),
    }


def fmt_oku(mil: float) -> str:
    oku = mil / 100
    if oku >= 10000:
        return f"{oku/10000:.1f}兆円"
    if oku >= 100:
        return f"{oku:,.0f}億円"
    return f"{oku:.1f}億円"


def fmt_pct(v: float) -> str:
    return f"{v:.1f}%"


def fmt_mult(v: float) -> str:
    return f"{v:.1f}倍"


# Tweet templates
def sector_rank(c, comps_in_sector):
    sorted_s = sorted(comps_in_sector, key=lambda x: -x["haGain"])
    for i, x in enumerate(sorted_s):
        if x["t"] == c["t"]:
            return i + 1, len(sorted_s)
    return 0, len(sorted_s)


TEMPLATES = [
    # Type 1: 個別企業の含み益フォーカス + 業界比較
    lambda c, avg: (
        f"【{c['n']}（{c['t']}）の含み資産を徹底分析】\n\n"
        f"有価証券報告書に記載された土地の簿価と、国土交通省の地価公示データから推定した時価を比較しました。\n\n"
        f"■ 土地の簿価: {fmt_oku(c['haBook'])}\n"
        f"■ 推定時価: {fmt_oku(c['haBook'] * c['haMult'])}\n"
        f"■ 含み益: {fmt_oku(c['haGain'])}\n"
        f"■ 時価/簿価倍率: {fmt_mult(c['haMult'])}\n\n"
        f"【{c['s']}業界との比較】\n"
        f"業界平均の倍率は{fmt_mult(avg['avg_mult'])}（{avg['count']}社平均）ですが、"
        f"同社は{fmt_mult(c['haMult'])}と{'大きく上回って' if c['haMult'] > avg['avg_mult'] * 1.5 else '上回って' if c['haMult'] > avg['avg_mult'] else '下回って'}います。"
        f"帳簿に載らない「隠れた価値」が{fmt_oku(c['haGain'])}分存在しています。\n\n"
        f"詳細データはこちら（無料）\n{SITE_URL}/{c['t']}"
    ),
    # Type 2: 時価総額比での含み益
    lambda c, avg: (
        f"【含み資産で見る{c['n']}の企業価値】\n\n"
        f"{c['s']}の{c['n']}（{c['t']}）について、有報の「主要な設備の状況」から土地の含み益を算出しました。\n\n"
        f"簿価{fmt_oku(c['haBook'])}の土地が、地価公示ベースでは推定{fmt_oku(c['haBook'] * c['haMult'])}。"
        f"含み益は{fmt_oku(c['haGain'])}で、倍率は{fmt_mult(c['haMult'])}です。"
        + (f"\n\nこの含み益は時価総額の{fmt_pct(c['haGainToMcap'])}に相当し、株価に織り込まれていない隠れた資産価値と言えます。" if c.get('haGainToMcap') else "")
        + f"\n\n業界（{c['s']}）{avg['count']}社の平均含み益は{fmt_oku(avg['avg_gain'])}。"
        f"同社は業界平均の{c['haGain']/avg['avg_gain']:.1f}倍の含み益を保有しています。\n\n"
        f"全上場企業のデータを無料公開中\n{SITE_URL}/{c['t']}"
    ),
    # Type 3: 業界ランキング風
    lambda c, avg: (
        f"【{c['s']}の含み資産ランキング】\n\n"
        f"有報×地価公示データで{c['s']}全{avg['count']}社の含み資産を分析しました。\n\n"
        f"▶ {c['n']}（{c['t']}）\n"
        f"・含み益: {fmt_oku(c['haGain'])}\n"
        f"・簿価→推定時価: {fmt_oku(c['haBook'])} → {fmt_oku(c['haBook'] * c['haMult'])}\n"
        f"・倍率: {fmt_mult(c['haMult'])}（業界平均{fmt_mult(avg['avg_mult'])}）\n\n"
        f"{c['s']}全体では、含み益トップ企業は{fmt_oku(avg['max_gain'])}、最大倍率は{fmt_mult(avg['max_mult'])}。"
        f"業界全体の平均倍率{fmt_mult(avg['avg_mult'])}に対し、同社は{c['haMult']/avg['avg_mult']:.0%}の水準にあります。\n\n"
        f"スクリーニングで業界比較が可能です（無料）\n{SCREENING_URL}"
    ),
    # Type 4: 投資視点・解説
    lambda c, avg: (
        f"有価証券報告書から読み解く{c['n']}の「隠れた資産」\n\n"
        f"企業のBSに計上される土地は取得時の価格（簿価）です。{c['n']}の有報を見ると、土地の簿価は{fmt_oku(c['haBook'])}。\n\n"
        f"しかし国土交通省の地価公示データで現在の時価を推定すると{fmt_oku(c['haBook'] * c['haMult'])}になります。"
        f"差額の{fmt_oku(c['haGain'])}が帳簿に現れない「含み益」です。\n\n"
        f"【業界比較: {c['s']}】\n"
        f"・業界平均倍率: {fmt_mult(avg['avg_mult'])}\n"
        f"・{c['n']}: {fmt_mult(c['haMult'])}\n"
        + (f"・時価総額比: {fmt_pct(c['haGainToMcap'])}\n" if c.get('haGainToMcap') else "")
        + f"\nこの含み益は土地を売却しない限り実現しませんが、M&Aや資産活用の観点で重要な指標です。\n\n"
        f"2,000社超の含み資産データを無料公開中\n{SITE_URL}/{c['t']}"
    ),
    # Type 5: データ比較型
    lambda c, avg: (
        f"【データで見る含み資産】{c['n']}（{c['t']}）\n\n"
        f"《同社の土地評価》\n"
        f"[簿価] {fmt_oku(c['haBook'])}\n"
        f"[推定時価] {fmt_oku(c['haBook'] * c['haMult'])}\n"
        f"[含み益] {fmt_oku(c['haGain'])}\n"
        f"[倍率] {fmt_mult(c['haMult'])}\n"
        + (f"[時価総額比] {fmt_pct(c['haGainToMcap'])}\n" if c.get('haGainToMcap') else "")
        + f"\n《{c['s']}業界平均（{avg['count']}社）》\n"
        f"・平均倍率: {fmt_mult(avg['avg_mult'])}\n"
        f"・平均含み益: {fmt_oku(avg['avg_gain'])}\n"
        f"・最大含み益: {fmt_oku(avg['max_gain'])}\n\n"
        f"有報の設備テーブルと地価公示を突合し、全上場企業の含み資産を可視化しています。\n\n"
        f"全データ無料公開\n{SITE_URL}/{c['t']}"
    ),
    # Type 6: 驚き系
    lambda c, avg: (
        f"【知ってましたか？】{c['n']}の土地、帳簿上は{fmt_oku(c['haBook'])}ですが、現在の地価で評価すると{fmt_oku(c['haBook'] * c['haMult'])}の価値がある可能性があります。\n\n"
        f"含み益: {fmt_oku(c['haGain'])}（簿価の{fmt_mult(c['haMult'])}）\n\n"
        f"この倍率を{c['s']}の業界平均（{fmt_mult(avg['avg_mult'])}）と比べると、"
        f"{'かなり高い水準' if c['haMult'] > avg['avg_mult'] * 2 else '高い水準' if c['haMult'] > avg['avg_mult'] else '標準的な水準'}にあります。"
        f"有価証券報告書の設備テーブル（主要な設備の状況）と国土交通省の地価公示データを市区町村×用途別で突合して算出しました。\n\n"
        f"全企業の含み資産データ（無料）\n{SITE_URL}/{c['t']}"
    ),
    # Type 7: 解説 + 業界深掘り
    lambda c, avg: (
        f"含み資産とは？ {c['n']}（{c['t']}）の例で解説します。\n\n"
        f"日本の会計基準では、土地は取得時の価格（取得原価）でBSに計上されます。"
        f"そのため、数十年前に取得した土地は帳簿上は非常に安いままです。\n\n"
        f"{c['n']}の場合:\n"
        f"・土地の簿価: {fmt_oku(c['haBook'])}\n"
        f"・地価公示ベースの推定時価: {fmt_oku(c['haBook'] * c['haMult'])}\n"
        f"・差額（含み益）: {fmt_oku(c['haGain'])}\n"
        f"・倍率: {fmt_mult(c['haMult'])}（{c['s']}平均{fmt_mult(avg['avg_mult'])}）\n\n"
        f"この「帳簿に載らない価値」が含み資産です。特に都心部に古くからの土地を持つ企業は倍率が高くなる傾向があります。\n\n"
        f"2,000社超の含み資産データを無料公開しています\n{SITE_URL}/{c['t']}"
    ),
]


HASHTAGS = "\n\n#含み資産 #有報分析 #株式投資 #金脈コード #バリュー投資"

def generate_tweet(company: dict) -> str:
    sector = company.get("s", "")
    avg = industry_avg.get(sector, {
        "count": 1, "avg_gain": company["haGain"], "avg_mult": company["haMult"],
        "avg_book": company["haBook"], "avg_mcap_ratio": 0, "max_gain": company["haGain"],
        "max_mult": company["haMult"],
    })
    template = random.choice(TEMPLATES)
    text = template(company, avg) + HASHTAGS
    # 300字未満なら補足を追加
    if len(text) < 300:
        extra = (
            f"\n\n※有価証券報告書「主要な設備の状況」の土地簿価と"
            f"国土交通省「地価公示」（令和6年）の市区町村×用途別平均値で算出。"
            f"実際の売却価格とは異なる場合があります。"
        )
        text = text.replace(HASHTAGS, extra + HASHTAGS)
    return text


# Sort by different criteria for variety
sorted_by_gain = sorted(ha_companies, key=lambda c: -c["haGain"])
sorted_by_mult = sorted(ha_companies, key=lambda c: -c["haMult"])
sorted_by_mcap = sorted([c for c in ha_companies if c.get("haGainToMcap")], key=lambda c: -c["haGainToMcap"])

# Build 7 days × 50 tweets
random.seed(42)
start_date = date.today() + timedelta(days=1)

all_used = set()
output = {}

for day in range(7):
    d = start_date + timedelta(days=day)
    day_key = d.strftime("%Y-%m-%d")
    tweets = []

    # Mix different rankings for variety
    candidates = []
    # Top gain companies
    candidates.extend(sorted_by_gain[:100])
    # Top multiplier companies
    candidates.extend(sorted_by_mult[:100])
    # Top mcap ratio companies
    candidates.extend(sorted_by_mcap[:100])
    # Random from all
    candidates.extend(random.sample(ha_companies, min(200, len(ha_companies))))

    # Deduplicate while preserving some order
    seen_today = set()
    day_pool = []
    for c in candidates:
        key = c["t"]
        if key not in seen_today and key not in all_used:
            day_pool.append(c)
            seen_today.add(key)

    # Take 50
    day_pool = day_pool[:50]
    for c in day_pool:
        tweet = generate_tweet(c)
        tweets.append({"ticker": c["t"], "name": c["n"], "text": tweet, "chars": len(tweet)})
        all_used.add(c["t"])

    output[day_key] = tweets
    avg_chars = sum(t["chars"] for t in tweets) / len(tweets) if tweets else 0
    print(f"{day_key}: {len(tweets)} tweets (avg {avg_chars:.0f} chars)")

# Save
out_file = PROJECT_ROOT / "tweets_weekly.json"
with open(out_file, "w", encoding="utf-8") as f:
    json.dump(output, f, ensure_ascii=False, indent=2)
print(f"\nSaved to {out_file}")

# Also save as readable text
txt_file = PROJECT_ROOT / "tweets_weekly.txt"
with open(txt_file, "w", encoding="utf-8") as f:
    for day_key, tweets in output.items():
        f.write(f"\n{'='*60}\n")
        f.write(f" {day_key} ({len(tweets)} tweets)\n")
        f.write(f"{'='*60}\n\n")
        for i, t in enumerate(tweets, 1):
            f.write(f"--- #{i} [{t['ticker']}] {t['name']} ({t['chars']}字) ---\n")
            f.write(t["text"])
            f.write("\n\n")
print(f"Saved to {txt_file}")
