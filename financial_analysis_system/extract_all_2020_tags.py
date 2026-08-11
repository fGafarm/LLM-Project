import json

# Load Toyota 2020 and Sony 2020 raw tags
with open('xbrl_store/7203_トヨタ自動車株式会社/2020_raw_tags.json', 'r', encoding='utf-8') as f:
    toyota_data = json.load(f)
    toyota_tags = toyota_data['tags']

with open('xbrl_store/6758_ソニーグループ株式会社/2020_raw_tags.json', 'r', encoding='utf-8') as f:
    sony_data = json.load(f)
    sony_tags = sony_data['tags']

# Get all numerical tags
toyota_nums = [(k, v) for k, v in toyota_tags.items() if isinstance(v.get('value'), (int, float))]
sony_nums = [(k, v) for k, v in sony_tags.items() if isinstance(v.get('value'), (int, float))]

print(f"Toyota 2020: {len(toyota_nums)} numerical tags")
print(f"Sony 2020: {len(sony_nums)} numerical tags\n")

# Combine and deduplicate
all_tags = {}
for tag, data in toyota_nums + sony_nums:
    if tag not in all_tags:
        all_tags[tag] = data

print(f"Total unique numerical tags: {len(all_tags)}\n")
print("=" * 80)
print("ALL NUMERICAL TAGS TO BE EXTRACTED")
print("=" * 80)

# Group by category for easier FALLBACK_TAGS creation
categories = {}

for tag, data in sorted(all_tags.items()):
    context = data.get('context', '')
    value = data['value']

    # Categorize
    if 'Audit' in tag:
        cat = 'AUDIT_FEES'
    elif 'Remuneration' in tag or 'Directors' in tag or 'Officers' in tag:
        cat = 'DIRECTOR_REMUNERATION'
    elif any(x in tag for x in ['Share', 'Stock', 'Voting', 'Treasury', 'Shareholder']):
        cat = 'SHAREHOLDER_STOCK'
    elif 'Investment' in tag and 'Securities' in tag:
        cat = 'INVESTMENT_SECURITIES'
    elif any(x in tag for x in ['Revenue', 'Sales', 'Income', 'Loss', 'Profit', 'Comprehensive']):
        cat = 'PL_ITEMS'
    elif any(x in tag for x in ['Asset', 'Equity', 'Liability']):
        cat = 'BS_ITEMS'
    elif 'CashFlow' in tag or 'Cash' in tag:
        cat = 'CF_ITEMS'
    elif 'Employee' in tag or 'Worker' in tag:
        cat = 'EMPLOYEE'
    elif any(x in tag for x in ['Eps', 'EPS', 'Bps', 'BPS', 'Diluted']):
        cat = 'PER_SHARE'
    elif 'Research' in tag or 'Development' in tag or 'Capex' in tag or 'CapitalExpenditure' in tag:
        cat = 'RD_CAPEX'
    elif 'Ratio' in tag or 'PriceEarnings' in tag or 'RateOfReturn' in tag:
        cat = 'RATIOS'
    elif 'Book' in tag or 'BalanceSheet' in tag:
        cat = 'BOOK_VALUE'
    else:
        cat = 'OTHER'

    if cat not in categories:
        categories[cat] = []
    categories[cat].append((tag, value, context))

# Print by category
for cat, tags_list in sorted(categories.items()):
    print(f"\n{'='*80}")
    print(f"{cat} ({len(tags_list)} tags)")
    print('='*80)
    for tag, value, context in tags_list:
        print(f"\nTag: {tag}")
        print(f"  Value: {value}")
        print(f"  Context: {context}")
        # Suggest field name
        field_name = tag.replace('USGAAP', '').replace('IFRS', '').replace('SummaryOfBusinessResults', '')
        field_name = field_name.replace('ResearchAndDevelopmentActivities', '')
        field_name = field_name.replace('OverviewOfCapitalExpendituresEtc', '')
        # Convert to snake_case
        import re
        field_name = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', field_name)
        field_name = re.sub('([a-z0-9])([A-Z])', r'\1_\2', field_name).lower()
        field_name = field_name.strip('_')
        print(f"  Suggested field: {field_name}")
