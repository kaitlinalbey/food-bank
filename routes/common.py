"""Shared API authorization and request validation helpers."""
from functools import wraps
import hashlib
import time

from flask import current_app, g, request
from database import read_db
from services.core import Problem


def json_body():
    value=request.get_json(silent=True)
    if not isinstance(value,dict):
        raise Problem('Request body must be a JSON object.')
    return value


def require_login(fn):
    @wraps(fn)
    def wrapper(*args,**kwargs):
        auth=request.headers.get('Authorization','')
        if not auth.startswith('Bearer '):
            raise Problem('Login required.',401)
        digest=hashlib.sha256(auth[7:].strip().encode()).hexdigest()
        with read_db(current_app.config['DB_PATH']) as con:
            user=con.execute('''SELECT u.id,u.bank_id,u.username,COALESCE(o.role,u.role) role,u.active
                FROM auth_tokens t JOIN users u ON u.id=t.user_id
                LEFT JOIN user_role_overrides o ON o.user_id=u.id
                WHERE t.token_hash=? AND t.expires_at>?''',(digest,int(time.time()))).fetchone()
        if not user or not user['active']:
            raise Problem('Login expired or invalid.',401)
        g.user=dict(user)
        g.bank_id=user['bank_id']
        g.user_id=user['id']
        g.token_hash=digest
        return fn(*args,**kwargs)
    return wrapper


def require_admin(fn):
    @wraps(fn)
    @require_login
    def wrapper(*args,**kwargs):
        if g.user['role']!='admin':
            raise Problem('Administrator account required.',403)
        return fn(*args,**kwargs)
    return wrapper


def require_roles(*roles):
    """Centralize write permissions; viewer and driver accounts cannot edit stock."""
    def decorator(fn):
        @wraps(fn)
        @require_login
        def wrapper(*args,**kwargs):
            if g.user['role'] not in roles:
                raise Problem('Action not permitted for role '+g.user['role']+'.',403)
            return fn(*args,**kwargs)
        return wrapper
    return decorator
