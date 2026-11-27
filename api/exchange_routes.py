from flask import Blueprint, g, jsonify

from api.inventory_routes import json_body
from api.notification_routes import notify
from auth import login_required
from db import get_db, transaction
from errors import APIError

bp = Blueprint('exchanges', __name__)

TRANSITIONS = {
    'accept':   ('pending',  'accepted',  'supplier'),
    'decline':  ('pending',  'declined',  'supplier'),
    'cancel':   ('pending',  'cancelled', 'requester'),
    'complete': ('accepted', 'completed', 'requester'),
}

EXCHANGE_SQL = """
    SELECT e.id, e.request_id, e.status, e.quantity, e.created_at, e.updated_at,
           e.requester_bank_id, rb.name AS requester_name,
           e.supplier_bank_id, sb.name AS supplier_name,
           e.item_id, it.name AS item_name, it.unit
    FROM exchanges e
    JOIN banks rb ON rb.id = e.requester_bank_id
    JOIN banks sb ON sb.id = e.supplier_bank_id
    JOIN items it ON it.id = e.item_id
"""


def get_exchange(db, exchange_id):
    return db.execute(EXCHANGE_SQL + ' WHERE e.id = ?', (exchange_id,)).fetchone()


def describe(ex):
    return f"exchange #{ex['id']} ({ex['quantity']} {ex['unit']} of {ex['item_name']})"


@bp.get('/api/exchanges')
@login_required
def list_exchanges():
    rows = get_db().execute(
        EXCHANGE_SQL + ' WHERE e.requester_bank_id = ? OR e.supplier_bank_id = ? ORDER BY e.id DESC',
        (g.bank_id, g.bank_id),
    ).fetchall()
    return jsonify(exchanges=[dict(row) for row in rows])


@bp.post('/api/exchanges')
@login_required
def create_exchange():

    data = json_body()
    request_id = data.get('request_id')
    supplier_bank_id = data.get('supplier_bank_id')
    item_id = data.get('item_id')
    quantity = data.get('quantity')

    if not isinstance(request_id, str) or not 0 < len(request_id) <= 100:
        raise APIError('request_id must be a string of 1-100 characters', 400)
    if type(supplier_bank_id) is not int or type(item_id) is not int:
        raise APIError('supplier_bank_id and item_id must be integers', 400)
    if type(quantity) is not int or quantity <= 0:
        raise APIError('quantity must be a positive integer', 400)
    if supplier_bank_id == g.bank_id:
        raise APIError('cannot request an exchange from your own bank', 400)

    with transaction() as db:
        existing = db.execute(
            'SELECT id FROM exchanges WHERE requester_bank_id = ? AND request_id = ?',
            (g.bank_id, request_id),
        ).fetchone()
        if existing is not None:
            return jsonify(dict(get_exchange(db, existing['id']))), 200

        if db.execute('SELECT 1 FROM banks WHERE id = ?', (supplier_bank_id,)).fetchone() is None:
            raise APIError('supplier bank not found', 404)
        if db.execute('SELECT 1 FROM items WHERE id = ?', (item_id,)).fetchone() is None:
            raise APIError('item not found', 404)

        cur = db.execute(
            """INSERT INTO exchanges (request_id, requester_bank_id, supplier_bank_id, item_id, quantity, status)
               VALUES (?, ?, ?, ?, ?, 'pending')""",
            (request_id, g.bank_id, supplier_bank_id, item_id, quantity),
        )
        ex = get_exchange(db, cur.lastrowid)
        notify(db, supplier_bank_id, f"{ex['requester_name']} requested {describe(ex)}")
    return jsonify(dict(ex)), 201


@bp.post('/api/exchanges/<int:exchange_id>/<action>')
@login_required
def change_exchange(exchange_id, action):
    if action not in TRANSITIONS:
        raise APIError(f'unknown action: {action}', 404)
    required_status, new_status, allowed_side = TRANSITIONS[action]

    with transaction() as db:
        ex = get_exchange(db, exchange_id)
        if ex is None or g.bank_id not in (ex['requester_bank_id'], ex['supplier_bank_id']):
            raise APIError('exchange not found', 404)

        side = 'supplier' if g.bank_id == ex['supplier_bank_id'] else 'requester'
        if side != allowed_side:
            raise APIError(f'only the {allowed_side} can {action} this exchange', 403)
        if ex['status'] != required_status:
            raise APIError(f"cannot {action} an exchange that is {ex['status']}", 409)

        if action == 'complete':
            cur = db.execute(
                'UPDATE inventory SET quantity = quantity - ? WHERE bank_id = ? AND item_id = ? AND quantity >= ?',
                (ex['quantity'], ex['supplier_bank_id'], ex['item_id'], ex['quantity']),
            )
            if cur.rowcount == 0:
                raise APIError('supplier does not have enough stock to complete this exchange', 409)
            db.execute(
                """INSERT INTO inventory (bank_id, item_id, quantity, min_level, max_level)
                   VALUES (?, ?, ?, 0, ?)
                   ON CONFLICT (bank_id, item_id) DO UPDATE SET quantity = quantity + excluded.quantity""",
                (ex['requester_bank_id'], ex['item_id'], ex['quantity'], ex['quantity']),
            )

        db.execute(
            'UPDATE exchanges SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?',
            (new_status, exchange_id),
        )
        if side == 'supplier':
            other_bank, my_name = ex['requester_bank_id'], ex['supplier_name']
        else:
            other_bank, my_name = ex['supplier_bank_id'], ex['requester_name']
        notify(db, other_bank, f"{my_name} {new_status} {describe(ex)}")

        ex = get_exchange(db, exchange_id)
    return jsonify(dict(ex))
