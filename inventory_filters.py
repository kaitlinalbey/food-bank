"""Reusable, UI-independent search/filter rules for individual bank inventory tables.

The remote view deliberately exposes only the quantity shared for exchange,
not private quantities, targets, reservations, or lot details.
"""

OWN_STATUSES = ('all', 'in_stock', 'out_of_stock', 'shortage', 'surplus', 'expired', 'at_target')
SHARED_STATUSES = ('all', 'available', 'unavailable')


def parse_filters(query='', category='', status='all', *, shared=False):
    """Return bounded, normalized GET search parameters without SQL interpolation."""
    valid = SHARED_STATUSES if shared else OWN_STATUSES
    cleaned_status = str(status or 'all').strip().lower()
    return {
        'q': str(query or '').strip()[:100],
        'category': str(category or '').strip()[:60],
        'status': cleaned_status if cleaned_status in valid else 'all',
    }


def filter_inventory(rows, filters, *, shared=False):
    """Filter one bank's data; never combine inventory records across banks."""
    query = filters['q'].casefold()
    category = filters['category'].casefold()
    status = filters['status']
    filtered = []
    for item in rows:
        if query and not any(query in str(item.get(key, '')).casefold()
                             for key in ('item_name', 'category', 'unit')):
            continue
        if category and str(item.get('category', '')).casefold() != category:
            continue
        if shared:
            available = item.get('available', 0)
            if status == 'available' and available <= 0:
                continue
            if status == 'unavailable' and available > 0:
                continue
        else:
            quantity = item.get('quantity', 0)
            if status == 'in_stock' and quantity <= 0:
                continue
            if status == 'out_of_stock' and quantity != 0:
                continue
            if status == 'shortage' and item.get('shortage', 0) <= 0:
                continue
            if status == 'surplus' and item.get('surplus', 0) <= 0:
                continue
            if status == 'expired' and not item.get('expired', False):
                continue
            if status == 'at_target' and (item.get('shortage', 0) or item.get('surplus', 0) or item.get('expired', False)):
                continue
        filtered.append(item)
    return filtered


def categories_for(rows):
    """Only show categories actually represented by this particular bank."""
    return sorted({str(item['category']) for item in rows if item.get('category')},
                  key=str.casefold)


def shared_inventory_view(rows):
    """Publish each catalogued inventory item but only its exchangeable surplus."""
    return [{
        'item_id': item['item_id'], 'item_name': item['item_name'],
        'category': item['category'], 'unit': item['unit'],
        'available': item['surplus'],
    } for item in rows]
