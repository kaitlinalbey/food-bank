"""API for advanced classroom features. All mutation routes have bank-scoped roles."""
from flask import Blueprint,current_app,g,jsonify,Response
from routes.common import require_login,require_admin,require_roles,json_body
from database import read_db
from services.advanced import (add_donor,list_donors,receipt_for_donation,
    issue_recall,close_recall,recalls_for_bank,add_zone_capacity,storage_summary,
    set_item_storage,propose_count,decide_count,list_counts,
    add_driver,add_vehicle,list_fleet,assign_transport,
    receipt_with_discrepancy,list_discrepancies,mark_discrepancy,
    independent_receipt,metrics)
from services.validation import Problem

bp=Blueprint('advanced',__name__)
def db():return current_app.config['DB_PATH']

@bp.get('/api/donors')
@require_login
def donors():return jsonify({'donors':list_donors(db(),g.bank_id)})

@bp.post('/api/donors')
@require_roles('staff','manager','admin')
def new_donor():return jsonify(add_donor(db(),g.bank_id,json_body(),g.user_id)),201

@bp.get('/api/donations/<int:donation_id>/receipt')
@require_login
def donor_receipt(donation_id):
    r=receipt_for_donation(db(),g.bank_id,donation_id)
    # Plain-text printable receipt. Demo labels only; no tax valuation promised.
    body='FOOD BANK DONATION ACKNOWLEDGMENT\nDonation #{}\nBank: {}\nDonor: {}\nItem: {}\nQuantity: {} {}\nBatch: {}\nDate: {}\n'.format(
        r['id'],r['bank_name'],r['donor'],r['item_name'],r['quantity'],r['unit'],r['lot_code'] or '-',r['created_at'])
    return Response(body,mimetype='text/plain',headers={'Cache-Control':'no-store'})

@bp.get('/api/recalls')
@require_login
def recalls():
    return jsonify({'recalls':recalls_for_bank(db(),g.bank_id,g.user['role']=='admin')})

@bp.post('/api/admin/recalls')
@require_admin
def new_recall():return jsonify(issue_recall(db(),g.bank_id,json_body(),g.user_id)),201

@bp.post('/api/admin/recalls/<int:recall_id>/close')
@require_admin
def resolve_recall(recall_id):return jsonify(close_recall(db(),g.bank_id,recall_id,g.user_id))

@bp.get('/api/storage')
@require_login
def storage():return jsonify({'storage':storage_summary(db(),g.bank_id)})

@bp.put('/api/storage/<storage_type>')
@require_roles('manager','admin')
def capacity(storage_type):
    body=json_body();body['storage_type']=storage_type
    return jsonify(add_zone_capacity(db(),g.bank_id,body,g.user_id))

@bp.put('/api/admin/items/<int:item_id>/storage')
@require_admin
def change_item_storage(item_id):
    return jsonify(set_item_storage(db(),g.bank_id,item_id,json_body().get('storage_type'),g.user_id))

@bp.get('/api/reconciliations')
@require_login
def reconciliations():return jsonify({'reconciliations':list_counts(db(),g.bank_id)})

@bp.post('/api/reconciliations')
@require_roles('staff','manager','admin')
def new_count():return jsonify(propose_count(db(),g.bank_id,json_body(),g.user_id)),201

@bp.post('/api/reconciliations/<int:recon_id>/<action>')
@require_roles('manager','admin')
def count_action(recon_id,action):return jsonify(decide_count(db(),g.bank_id,recon_id,action,g.user_id))

@bp.get('/api/transport')
@require_login
def fleet():return jsonify(list_fleet(db(),g.bank_id))

@bp.post('/api/drivers')
@require_roles('manager','admin','dispatcher')
def create_driver():return jsonify(add_driver(db(),g.bank_id,json_body(),g.user_id)),201

@bp.post('/api/vehicles')
@require_roles('manager','admin','dispatcher')
def create_vehicle():return jsonify(add_vehicle(db(),g.bank_id,json_body(),g.user_id)),201

@bp.put('/api/exchanges/<int:exchange_id>/transport')
@require_roles('staff','manager','admin','dispatcher')
def set_transport(exchange_id):
    return jsonify(assign_transport(db(),g.bank_id,exchange_id,json_body(),g.user_id))

@bp.post('/api/exchanges/<int:exchange_id>/receive-detailed')
@require_roles('staff','manager','admin')
def partial_receipt(exchange_id):
    return jsonify(receipt_with_discrepancy(db(),g.bank_id,exchange_id,json_body(),g.user_id))

@bp.get('/api/discrepancies')
@require_login
def disputes():return jsonify({'discrepancies':list_discrepancies(db(),g.bank_id)})

@bp.post('/api/discrepancies/<int:discrepancy_id>/review')
@require_roles('manager','admin')
def review_dispute(discrepancy_id):
    return jsonify(mark_discrepancy(db(),g.bank_id,discrepancy_id,g.user_id))

@bp.post('/api/offline/receipts')
@require_roles('staff','manager','admin')
def offline_receipt():return jsonify(independent_receipt(db(),g.bank_id,json_body(),g.user_id)),201

@bp.get('/api/analytics')
@require_login
def analytics():
    return jsonify({'metrics':metrics(db(),g.bank_id,g.user['role']=='admin')})

@bp.get('/api/events')
@require_login
def events():
    with read_db(db()) as con:
        if g.user['role']=='admin':
            rows=con.execute('SELECT * FROM distributed_events ORDER BY id DESC LIMIT 120')
        else:
            rows=con.execute('SELECT * FROM distributed_events WHERE bank_id=? ORDER BY id DESC LIMIT 120',(g.bank_id,))
        return jsonify({'events':[dict(r) for r in rows]})

@bp.get('/api/admin/lab/scenarios')
@require_admin
def lab_scenarios():
    from services.lab import SCENARIOS
    return jsonify({'scenarios':SCENARIOS,'notice':'All scenarios run against temporary databases, not your live inventory.'})

@bp.post('/api/admin/lab/run')
@require_admin
def lab_run():
    from services.lab import run_lab
    scenario=json_body().get('scenario','all')
    return jsonify(run_lab(scenario))
