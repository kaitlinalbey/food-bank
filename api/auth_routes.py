from flask import Blueprint, g, jsonify, request

from auth import TOKEN_LIFETIME_SECONDS, authenticate, create_token, login_required, revoke_token
from errors import APIError

bp = Blueprint('auth', __name__)


@bp.get('/api/health')
def health():
    return jsonify(status="ok")


@bp.post('/api/login')
def login():
    data = request.get_json(silent=True) or {}
    username = data.get('username')
    password = data.get('password')
    if not isinstance(username, str) or not isinstance(password, str) or not username or not password:
        raise APIError('username and password are required', 400)

    user = authenticate(username, password)
    if user is None:
        raise APIError('invalid username or password', 401)

    return jsonify(
        token=create_token(user['id']),
        bank_id=user['bank_id'],
        username=user['username'],
        role=user['role'],
        expires_in=TOKEN_LIFETIME_SECONDS,
    )


@bp.post('/api/logout')
@login_required
def logout():
    revoke_token(g.token)
    return jsonify(status="ok")
