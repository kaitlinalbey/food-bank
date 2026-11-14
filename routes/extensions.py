"""Additional JSON endpoints for demo reporting, stock lots, CSV and offline synchronization."""
from flask import Blueprint,current_app,g,jsonify,request,Response
from database import read_db
from routes.common import require_login,require_admin,require_roles,json_body
from services.core import Problem
from services.operations import (donation,dispose,lots_for_bank,change_lot_expiry,
    forecast_and_alerts,sync_adjustment,schedule_exchange,export_csv,import_csv,set_barcode)

bp=Blueprint('extensions',__name__)

def db():return current_app.config['DB_PATH']

@bp.post('/api/donations')
@require_roles("staff","manager","admin")
def donate():
    return jsonify(donation(db(),g.bank_id,json_body(),g.user_id)),201

@bp.get('/api/donations')
@require_login
def donations():
    with read_db(db()) as con:
        rows=con.execute('''SELECT d.id,d.donor,d.quantity,d.created_at,i.name AS item_name,
          l.expiration_date FROM donations d JOIN items i ON i.id=d.item_id
          LEFT JOIN lots l ON l.id=d.lot_id WHERE d.bank_id=? ORDER BY d.id DESC LIMIT 100''',
          (g.bank_id,)).fetchall()
    return jsonify({'donations':[dict(r) for r in rows]})

@bp.post('/api/waste')
@require_roles("staff","manager","admin")
def waste():
    return jsonify(dispose(db(),g.bank_id,json_body(),g.user_id)),201

@bp.get('/api/waste')
@require_login
def waste_history():
    with read_db(db()) as con:
        rows=con.execute('''SELECT w.id,w.quantity,w.reason,w.created_at,i.name AS item_name,w.lot_id
          FROM waste w JOIN items i ON i.id=w.item_id WHERE w.bank_id=? ORDER BY w.id DESC LIMIT 100''',
          (g.bank_id,)).fetchall()
    return jsonify({'waste':[dict(r) for r in rows]})

@bp.get('/api/lots')
@require_login
def lots():
    return jsonify({'lots':lots_for_bank(db(),g.bank_id)})

@bp.patch('/api/lots/<int:lot_id>')
@require_roles("staff","manager","admin")
def set_expiry(lot_id):
    return jsonify(change_lot_expiry(db(),g.bank_id,lot_id,json_body(),g.user_id))

@bp.get('/api/forecast')
@require_login
def forecast():
    with read_db(db()) as con:
        forecasts,alerts=forecast_and_alerts(con,g.bank_id)
    return jsonify({'forecast':forecasts,'alerts':alerts,'method':'30-day average recorded stock outflow; demonstration estimate only'})

@bp.post('/api/inventory/<int:item_id>/adjust')
@require_roles("staff","manager","admin")
def sync(item_id):
    body=json_body()
    if 'item_id' in body and str(body['item_id'])!=str(item_id):
        raise Problem('Item ID does not match the URL.')
    body['item_id']=item_id
    return jsonify(sync_adjustment(db(),g.bank_id,body,g.user_id))

@bp.put('/api/exchanges/<int:exchange_id>/schedule')
@require_roles("staff","manager","admin")
def schedule(exchange_id):
    return jsonify(schedule_exchange(db(),g.bank_id,exchange_id,json_body(),g.user_id))

@bp.get('/api/inventory/export.csv')
@require_login
def export_inventory():
    return Response(export_csv(db(),g.bank_id),mimetype='text/csv',headers={
        'Content-Disposition':'attachment; filename="food_bank_inventory.csv"',
        'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})

@bp.post('/api/inventory/import.csv')
@require_roles("staff","manager","admin")
def upload_inventory():
    data=request.get_data(cache=False,as_text=False)
    if len(data)>131072:raise Problem('CSV exceeds 128 KB.',413)
    try:decoded=data.decode('utf-8-sig')
    except UnicodeDecodeError:raise Problem('CSV must be UTF-8.')
    return jsonify(import_csv(db(),g.bank_id,decoded,g.user_id))

@bp.get('/api/barcodes/<code>')
@require_login
def lookup_barcode(code):
    with read_db(db()) as con:
        row=con.execute('SELECT id,name,unit,category,barcode FROM items WHERE barcode=?',(code,)).fetchone()
    if not row:raise Problem('Barcode not found.',404)
    return jsonify({'item':dict(row)})

@bp.put('/api/admin/items/<int:item_id>/barcode')
@require_admin
def change_barcode(item_id):
    return jsonify(set_barcode(db(),item_id,json_body().get('barcode'),g.bank_id,g.user_id))

@bp.get('/api/admin/audit')
@require_admin
def audit():
    with read_db(db()) as con:
        entries=[dict(r) for r in con.execute('''SELECT id,bank_id,actor_user_id,operation,result,detail,created_at
            FROM audit_events ORDER BY id DESC LIMIT 150''')]
        recent=[dict(r) for r in con.execute('''SELECT id,bank_id,actor_user_id,action,details,created_at
            FROM activity ORDER BY id DESC LIMIT 150''')]
    return jsonify({'failed_operations':entries,'activity':recent})
