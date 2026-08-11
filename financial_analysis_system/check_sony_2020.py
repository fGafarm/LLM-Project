import json

# Load Sony 2020 raw tags
with open('xbrl_store/6758_ソニーグループ株式会社/2020_raw_tags.json', 'r', encoding='utf-8') as f:
    data = json.load(f)
    tags = data['tags']

# Get all numerical tags
all_nums = {k: v for k, v in tags.items() if isinstance(v.get('value'), (int, float))}

print(f"Sony 2020 - Total numerical tags: {len(all_nums)}\n")

# Find tags that might be missing from current extraction
financial_tags = []
for tag, tag_data in all_nums.items():
    # Skip audit fees, shareholder metadata, etc.
    if any(x in tag for x in ['Audit', 'NonAudit', 'NumberOfShares', 'Shareholding',
                               'VotingRights', 'Treasury', 'AcquisitionCost', 'SaleAmount',
                               'NumberOfIssues', 'Carrying']):
        continue

    financial_tags.append((tag, tag_data['value'], tag_data.get('context')))

print("=== Sony 2020 Financial Tags (excluding audit/shareholder metadata) ===\n")
for tag, value, context in sorted(financial_tags):
    print(f"{tag}:")
    print(f"  Value: {value}, Context: {context}\n")
