from flask import Blueprint

bp = Blueprint('inventory', __name__)


@bp.get('/api/banks')
def list_banks():
    raise NotImplementedError


@bp.get('/api/items')
def list_items():
    raise NotImplementedError


@bp.get('/api/banks/<int:bank_id>/inventory')
def bank_inventory(bank_id):
    raise NotImplementedError


@bp.post('/api/inventory')
def add_inventory_item():
    raise NotImplementedError


@bp.put('/api/inventory/<int:item_id>')
def update_inventory_item(item_id):
    raise NotImplementedError


@bp.get('/api/shortages')
def shortages():
    raise NotImplementedError


@bp.get('/api/surpluses')
def surpluses():
    raise NotImplementedError


@bp.get('/api/matches')
def matches():
    raise NotImplementedError
