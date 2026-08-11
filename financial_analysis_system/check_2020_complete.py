import json

# Load 2020 raw tags
with open('xbrl_store/7203_トヨタ自動車株式会社/2020_raw_tags.json', 'r', encoding='utf-8') as f:
    data = json.load(f)
    tags = data['tags']

# Get all numerical tags
all_nums = {k: v for k, v in tags.items() if isinstance(v.get('value'), (int, float))}

print(f"Total numerical tags: {len(all_nums)}\n")

# Categorize tags
print("=== Tag Categorization ===\n")

categories = {
    'Revenue/Sales': [],
    'Profit/Income': [],
    'Assets/Equity/BS': [],
    'Cash Flow': [],
    'Employee': [],
    'EPS/BPS/Ratios': [],
    'R&D/Capex': [],
    'Audit Fees': [],
    'Shareholder/Stock': [],
    'Investment Securities': [],
    'Other': []
}

for tag, tag_data in all_nums.items():
    value = tag_data['value']
    context = tag_data.get('context', '')

    if any(x in tag for x in ['Revenue', 'Sales']):
        categories['Revenue/Sales'].append((tag, value, context))
    elif any(x in tag for x in ['Profit', 'Income', 'Loss']):
        categories['Profit/Income'].append((tag, value, context))
    elif any(x in tag for x in ['Asset', 'Equity', 'Bps', 'BPS']):
        categories['Assets/Equity/BS'].append((tag, value, context))
    elif 'CashFlow' in tag or 'Cash' in tag:
        categories['Cash Flow'].append((tag, value, context))
    elif 'Employee' in tag or 'Worker' in tag:
        categories['Employee'].append((tag, value, context))
    elif any(x in tag for x in ['Eps', 'EPS', 'Diluted', 'Ratio', 'PriceEarnings']):
        categories['EPS/BPS/Ratios'].append((tag, value, context))
    elif 'Research' in tag or 'Development' in tag or 'Capex' in tag or 'CapitalExpenditure' in tag:
        categories['R&D/Capex'].append((tag, value, context))
    elif 'Audit' in tag:
        categories['Audit Fees'].append((tag, value, context))
    elif any(x in tag for x in ['Share', 'Stock', 'Voting', 'Treasury']):
        categories['Shareholder/Stock'].append((tag, value, context))
    elif 'Investment' in tag or 'Securities' in tag:
        categories['Investment Securities'].append((tag, value, context))
    else:
        categories['Other'].append((tag, value, context))

for category, items in categories.items():
    if items:
        print(f"[{category}] ({len(items)} tags)")
        for tag, value, context in items[:5]:  # Show first 5
            print(f"  - {tag[:60]}: {value}")
        if len(items) > 5:
            print(f"  ... and {len(items) - 5} more")
        print()
