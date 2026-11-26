from flask import Blueprint, jsonify

bp = Blueprint('auth', __name__)


@bp.get('/api/health')
def health():
    return jsonify(status="ok")


@bp.post('/api/login')
def login():
    raise NotImplementedError


@bp.post('/api/logout')
def logout():
    raise NotImplementedError
