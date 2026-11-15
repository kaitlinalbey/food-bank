"""Scoped username/password authentication for classroom demo."""
from flask import Blueprint, current_app, g, jsonify
import hashlib
import secrets
import time

from database import DEMO_PASSWORDS, demo_mode, read_db, verify_password, write_db
from services.core import Problem, whole
from routes.common import json_body, require_login

bp=Blueprint('auth',__name__)


@bp.post('/api/login')
def login():
    body=json_body()
    bank_id=whole(body.get('bank_id'),1)
    username=body.get('username','staff')
    password=body.get('password','')
    if not isinstance(username,str) or not isinstance(password,str):
        raise Problem('Invalid username or password.',401)
    with read_db(current_app.config['DB_PATH']) as con:
        user=con.execute('''SELECT users.*,COALESCE(o.role,users.role) effective_role,banks.name AS bank_name FROM users
             JOIN banks ON banks.id=users.bank_id
             LEFT JOIN user_role_overrides o ON o.user_id=users.id WHERE users.bank_id=?
             AND users.username=? COLLATE NOCASE AND active=1''',
             (bank_id,username.strip())).fetchone()
    # Account-scoped local demo login throttling, without storing IP addresses.
    now=int(time.time());normalized=username.strip().casefold()[:40]
    with write_db(current_app.config['DB_PATH']) as con:
        attempt=con.execute('SELECT attempts,window_start FROM login_attempts WHERE bank_id=? AND username=?',
                            (bank_id,normalized)).fetchone()
        if attempt and now-attempt['window_start']<900 and attempt['attempts']>=8:
            raise Problem('Too many failed attempts. Retry in 15 minutes.',429)
    if not user or not verify_password(user['password_hash'],password):
        with write_db(current_app.config['DB_PATH']) as con:
            if not attempt or now-attempt['window_start']>=900:
                con.execute('INSERT OR REPLACE INTO login_attempts(bank_id,username,attempts,window_start) VALUES(?,?,1,?)',
                            (bank_id,normalized,now))
            else:
                con.execute('UPDATE login_attempts SET attempts=attempts+1 WHERE bank_id=? AND username=?',
                            (bank_id,normalized))
        raise Problem('Invalid username or password.',401)
    with write_db(current_app.config['DB_PATH']) as con:
        con.execute('DELETE FROM login_attempts WHERE bank_id=? AND username=?',(bank_id,normalized))
    if password in DEMO_PASSWORDS and not demo_mode():
        # A public classroom password must never work outside an explicit demo setup.
        # Checked only after the password matched, so it reveals nothing to a guesser.
        raise Problem('This account still uses a public demo password, which is disabled outside '
                      'classroom demo mode. An administrator must set a new password with '
                      'python manage_accounts.py set-password.',403)
    token=secrets.token_urlsafe(32)
    digest=hashlib.sha256(token.encode()).hexdigest()
    with write_db(current_app.config['DB_PATH']) as con:
        con.execute('DELETE FROM auth_tokens WHERE expires_at<=?',(int(time.time()),))
        con.execute('INSERT INTO auth_tokens(token_hash,user_id,expires_at) VALUES(?,?,?)',
                    (digest,user['id'],int(time.time())+12*3600))
    return jsonify({'token':token,'bank_id':bank_id,'bank_name':user['bank_name'],
                    'username':user['username'],'role':user['effective_role']})


@bp.post('/api/logout')
@require_login
def logout():
    with write_db(current_app.config['DB_PATH']) as con:
        con.execute('DELETE FROM auth_tokens WHERE token_hash=?',(g.token_hash,))
    return jsonify({'message':'Logged out.'})


@bp.get('/api/me')
@require_login
def me():
    return jsonify(g.user)
