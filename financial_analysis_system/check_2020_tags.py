import json

# Load 2020 raw tags
with open('xbrl_store/7203_トヨタ自動車株式会社/2020_raw_tags.json', 'r', encoding='utf-8') as f:
    data = json.load(f)
    tags = data['tags']

# Load extracted data
with open('xbrl_store/7203_トヨタ自動車株式会社/2020.json', 'r', encoding='utf-8') as f:
    extracted = json.load(f)['data']

# Get all numerical tags
all_nums = {k: v for k, v in tags.items() if isinstance(v.get('value'), (int, float))}

print(f"Total numerical tags in 2020 raw: {len(all_nums)}")
print(f"Total extracted items in 2020.json: {len(extracted)}")

# Check for potentially useful unextracted tags
print("\n=== Potentially useful unextracted tags ===\n")

useful_keywords = ['EquityToAssetRatio', 'PriceEarnings', 'NetIncomeToSales', 'RateOfReturnOnEquity']

for tag, tag_data in sorted(tags.items()):
    if not isinstance(tag_data.get('value'), (int, float)):
        continue
    if any(keyword in tag for keyword in useful_keywords):
        print(f"{tag}:")
        print(f"  Value: {tag_data['value']}")
        print(f"  Context: {tag_data.get('context')}")
        print()
