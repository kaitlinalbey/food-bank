"""Inventory editing, item catalog, matching results and bank inventory visibility."""
from flask import Blueprint,current_app,g,jsonify,request
from database import read_db
from routes.common import require_login,require_roles,json_body
from services.core import add_item,update_item,delete_item,inventory_list,matches,Problem
from inventory_filters import parse_filters,filter_inventory,categories_for,shared_inventory_view

bp=Blueprint('inventory',__name__)


@bp.get('/api/items')
@require_login
def items():
    with read_db(current_app.config['DB_PATH']) as con:
        rows=con.execute('SELECT id,name,unit,category,barcode FROM items ORDER BY name')
        return jsonify({'items':[dict(x) for x in rows]})


@bp.post('/api/inventory')
@require_roles("staff","manager","admin")
def create_item():
    item_id=add_item(current_app.config['DB_PATH'],g.bank_id,json_body(),g.user_id)
    return jsonify({'message':'Item added.','item_id':item_id}),201


@bp.put('/api/inventory/<int:item_id>')
@require_roles("staff","manager","admin")
def put_item(item_id):
    return jsonify(update_item(current_app.config['DB_PATH'],g.bank_id,item_id,json_body(),g.user_id))


@bp.delete('/api/inventory/<int:item_id>')
@require_roles("staff","manager","admin")
def remove_item(item_id):
    delete_item(current_app.config['DB_PATH'],g.bank_id,item_id,g.user_id)
    return jsonify({'message':'Item removed.'})


@bp.get('/api/matches')
@require_login
def find_matches():
    distance=request.args.get('max_miles')
    if distance not in (None,''):
        try:distance=float(distance)
        except (TypeError,ValueError):raise Problem('max_miles must be numeric.')
        if not 0<distance<=500:raise Problem('max_miles must be between 0 and 500.')
    else:distance=None
    with read_db(current_app.config['DB_PATH']) as con:
        bank=con.execute('SELECT id,latitude,longitude FROM banks WHERE id=?',(g.bank_id,)).fetchone()
        return jsonify({'matches':matches(con,bank,distance)})


@bp.get('/api/banks/<int:bank_id>/inventory')
@require_login
def bank_inventory(bank_id):
    with read_db(current_app.config['DB_PATH']) as con:
        bank=con.execute('SELECT id,name,area FROM banks WHERE id=?',(bank_id,)).fetchone()
        if bank is None:
            raise Problem('Food bank not found.',404)
        rows=inventory_list(con,bank_id)
        shared = bank_id != g.bank_id
        if shared:
            # List ALL advertised item names, but never expose another bank's
            # physical counts, targets, reservations, or lot metadata.
            rows = shared_inventory_view(rows)
        categories = categories_for(rows)
        filters = parse_filters(request.args.get('q'),request.args.get('category'),
                                request.args.get('status'),shared=shared)
        filtered = filter_inventory(rows,filters,shared=shared)
        return jsonify({'bank':dict(bank),'inventory':filtered,'total':len(rows),
                        'filtered_total':len(filtered),'categories':categories,
                        'filters':filters,'shared':shared})
