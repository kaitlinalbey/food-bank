"""Exchange lifecycle endpoints with idempotent requests."""
from flask import Blueprint,current_app,g,jsonify
from routes.common import require_login,require_roles,json_body
from services.core import create_transfer,change_transfer

bp=Blueprint('transfers',__name__)


@bp.post('/api/exchanges')
@require_roles("staff","manager","admin","dispatcher")
def request_transfer():
    exchange_id,created=create_transfer(current_app.config['DB_PATH'],g.bank_id,json_body(),g.user_id)
    return jsonify({'message':'Request sent.' if created else 'Request already exists.',
                    'exchange_id':exchange_id,'created':created}),201 if created else 200


@bp.post('/api/exchanges/<int:exchange_id>/<action>')
@require_roles("staff","manager","admin","dispatcher")
def transition(exchange_id,action):
    # Legacy "accept" remains available for older clients; approval does not move stock.
    if action=='accept':
        action='approve'
    result=change_transfer(current_app.config['DB_PATH'],g.bank_id,exchange_id,action,g.user_id)
    return jsonify(result)
