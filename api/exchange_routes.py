from flask import Blueprint

bp = Blueprint('exchanges', __name__)


@bp.get('/api/exchanges')
def list_exchanges():
    raise NotImplementedError


@bp.post('/api/exchanges')
def create_exchange():
    raise NotImplementedError


@bp.post('/api/exchanges/<int:exchange_id>/<action>')
def change_exchange(exchange_id, action):
    raise NotImplementedError
