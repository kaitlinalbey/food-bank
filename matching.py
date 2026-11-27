def find_matches(inventory_rows, bank_id):
    shortages = []
    surpluses_by_item = {}

    for row in inventory_rows:
        qty = row['quantity']
        min_lvl = row['min_level']
        max_lvl = row['max_level']

            if row['bank_id'] == bank_id:
                needed = min_lvl - qty
                if needed > 0:
                        shortages.append({'item_id': row['item_id'], 'item_name': row['item_name'],
                        'unit': row['unit'], 'category': row['category'], 'quantity': qty,
                        'min_level': min_lvl, 'needed': needed,})
                else:
                    excess = qty - max_lvl
                    if excess > 0:
                        surpluses_by_item.setdefault(row['item_id'], []).append({'supplier_bank_id': row['bank_id'],
                        'supplier_bank_name': row['bank_name'], 'excess': excess,})
    matches = []
        for shortage in sorted (shortages, key=lambda s: s['item_name'].lower()):
            suppliers = sorted(surpluses_by_item.get(shortage['item_id'], []),
            key =lambda sup: (-sup['excess'], sup['supplier_bank_id']),)
        for sup in suppliers:
            suggested = min(shortage['needed'], sup['excess'])
            matches.append({
                'item_id': shortage['item_id'],
                'item_name': shortage['item_name'],
                'unit': shortage['unit'],
                'category': shortage['category'],
                'requester_bank_id': bank_id,
                'supplier_bank_id': sup['supplier_bank_id'],
                'supplier_bank_name': sup['supplier_bank_name'],
                # Alias keys in case matching.html uses source_bank_* naming
                'source_bank_id': sup['supplier_bank_id'],
                'source_bank_name': sup['supplier_bank_name'],
                'needed': shortage['needed'],
                'available': sup['excess'],
                'suggested_quantity': suggested,
            })

    return matches
