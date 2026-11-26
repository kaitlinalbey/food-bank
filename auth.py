import hashlib
import secrets
from functools import wraps

from flask import g, request
from werkzeug.security import check_password_hash, generate_password_hash

from db import get_db, transaction
from errors import APIError

TOKEN_LIFETIME = '+8 hours' 
TOKEN_LIFETIME_SECONDS = 8 * 60 * 60

_DUMMY_HASH = generate_password_hash('dummy-password-for-timing')


def hash_password(password):
    return generate_password_hash(password)


def check_password(password_hash, password):
    return check_password_hash(password_hash, password)


def _hash_token(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def authenticate(username, password):
    user = get_db().execute(
        'SELECT id, username, bank_id, role, password_hash FROM users WHERE username = ?',
        (username,),
    ).fetchone()
    if user is None:
        check_password(_DUMMY_HASH, password)
        return None
    if not check_password(user['password_hash'], password):
        return None
    return user


def create_token(user_id):
    token = secrets.token_urlsafe(32)
    with transaction() as db:
        db.execute("DELETE FROM tokens WHERE expires_at <= datetime('now')")
        db.execute(
            "INSERT INTO tokens (token_hash, user_id, expires_at) VALUES (?, ?, datetime('now', ?))",
            (_hash_token(token), user_id, TOKEN_LIFETIME),
        )
    return token


def revoke_token(token):
    with transaction() as db:
        db.execute('DELETE FROM tokens WHERE token_hash = ?', (_hash_token(token),))


def user_for_token(token):
    return get_db().execute(
        """
        SELECT u.id, u.username, u.bank_id, u.role
        FROM tokens t
        JOIN users u ON u.id = t.user_id
        WHERE t.token_hash = ? AND t.expires_at > datetime('now')
        """,
        (_hash_token(token),),
    ).fetchone()


def _bearer_token():
    scheme, _, token = request.headers.get('Authorization', '').partition(' ')
    token = token.strip()
    if scheme.lower() != 'bearer' or not token:
        return None
    return token


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        token = _bearer_token()
        user = user_for_token(token) if token else None
        if user is None:
            raise APIError('authentication required', 401)
        g.user = user
        g.bank_id = user['bank_id']
        g.token = token
        return view(*args, **kwargs)
    return wrapper


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapper(*args, **kwargs):
        if g.user['role'] != 'admin':
            raise APIError('admin role required', 403)
        return view(*args, **kwargs)
    return wrapper
