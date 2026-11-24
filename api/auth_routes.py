from flask import Blueprint

bp = Blueprint('auth', __name__)


@bp.get('/api/health')
def health():
    raise NotImplementedError


@bp.post('/api/login')
def login():
    raise NotImplementedError


@bp.post('/api/logout')
def logout():
    raise NotImplementedError
