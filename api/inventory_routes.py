from flask import Blueprint, g, jsonify, request

from auth import login_required
from db import get_db, transaction
from errors import APIError
from matching import find_matches

bp = Blueprint('inventory', __name__)

INVENTORY_SQL = """
    SELECT inv.bank_id, b.name AS bank_name,
           inv.item_id, it.name AS item_name, it.unit, it.category,
           inv.quantity, inv.min_level, inv.max_level
    FROM inventory inv
    JOIN banks b ON b.id = inv.bank_id
    JOIN items it ON it.id = inv.item_id
"""


def get_inventory(where='', params=()):
    rows = get_db().execute(INVENTORY_SQL + where + ' ORDER BY inv.bank_id, it.name', params).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item['is_shortage'] = item['quantity'] < item['min_level']
        item['is_surplus'] = item['quantity'] > item['max_level']
        result.append(item)
    return result


def get_own_row(item_id):
    return get_inventory('WHERE inv.bank_id = ? AND inv.item_id = ?', (g.bank_id, item_id))[0]


def json_body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise APIError('request body must be a JSON object', 400)
    return data


def non_negative_int(data, name):
    value = data.get(name)
    if type(value) is not int or value < 0:
        raise APIError(f'{name} must be a non-negative integer', 400)
    return value


@bp.get('/api/banks')
@login_required
def list_banks():
    rows = get_db().execute('SELECT id, name, city FROM banks ORDER BY id').fetchall()
    return jsonify(banks=[dict(row) for row in rows])


@bp.get('/api/items')
@login_required
def list_items():
    rows = get_db().execute('SELECT id, name, unit, category FROM items ORDER BY name').fetchall()
    return jsonify(items=[dict(row) for row in rows])


@bp.get('/api/banks/<int:bank_id>/inventory')
@login_required
def bank_inventory(bank_id):
    if get_db().execute('SELECT 1 FROM banks WHERE id = ?', (bank_id,)).fetchone() is None:
        raise APIError('bank not found', 404)
    return jsonify(bank_id=bank_id, inventory=get_inventory('WHERE inv.bank_id = ?', (bank_id,)))


@bp.post('/api/inventory')
@login_required
def add_inventory_item():
    data = json_body()
    item_id = data.get('item_id')
    if type(item_id) is not int:
        raise APIError('item_id must be an integer', 400)
    quantity = non_negative_int(data, 'quantity')
    min_level = non_negative_int(data, 'min_level')
    max_level = non_negative_int(data, 'max_level')
    if min_level > max_level:
        raise APIError('min_level cannot be greater than max_level', 400)

    with transaction() as db:
        if db.execute('SELECT 1 FROM items WHERE id = ?', (item_id,)).fetchone() is None:
            raise APIError('item not found', 404)
        existing = db.execute(
            'SELECT 1 FROM inventory WHERE bank_id = ? AND item_id = ?', (g.bank_id, item_id)
        ).fetchone()
        if existing is not None:
            raise APIError('item is already in your inventory; use PUT to change it', 409)
        db.execute(
            'INSERT INTO inventory (bank_id, item_id, quantity, min_level, max_level) VALUES (?, ?, ?, ?, ?)',
            (g.bank_id, item_id, quantity, min_level, max_level),
        )
    return jsonify(get_own_row(item_id)), 201


@bp.put('/api/inventory/<int:item_id>')
@login_required
def update_inventory_item(item_id):
    data = json_body()
    fields = [name for name in ('quantity', 'min_level', 'max_level') if name in data]
    if not fields:
        raise APIError('send at least one of quantity, min_level, max_level', 400)

    with transaction() as db:
        current = db.execute(
            'SELECT quantity, min_level, max_level FROM inventory WHERE bank_id = ? AND item_id = ?',
            (g.bank_id, item_id),
        ).fetchone()
        if current is None:
            raise APIError('item is not in your inventory', 404)

        new = dict(current)
        for name in fields:
            new[name] = non_negative_int(data, name)
        if new['min_level'] > new['max_level']:
            raise APIError('min_level cannot be greater than max_level', 400)

        db.execute(
            'UPDATE inventory SET quantity = ?, min_level = ?, max_level = ? WHERE bank_id = ? AND item_id = ?',
            (new['quantity'], new['min_level'], new['max_level'], g.bank_id, item_id),
        )
    return jsonify(get_own_row(item_id))


@bp.get('/api/shortages')
@login_required
def shortages():
    rows = [row for row in get_inventory() if row['is_shortage']]
    for row in rows:
        row['needed'] = row['min_level'] - row['quantity']
    return jsonify(shortages=rows)


@bp.get('/api/surpluses')
@login_required
def surpluses():
    rows = [row for row in get_inventory() if row['is_surplus']]
    for row in rows:
        row['excess'] = row['quantity'] - row['max_level']
    return jsonify(surpluses=rows)


@bp.get('/api/matches')
@login_required
def matches():
    try:
        result = find_matches(get_inventory(), g.bank_id)
    except NotImplementedError:
        result = []
    return jsonify(matches=result)
