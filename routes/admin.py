"""Admin-only demo account and food bank onboarding."""
import sqlite3
from flask import Blueprint,current_app,g,jsonify,request
from database import DEMO_PASSWORDS,demo_mode,hash_password,read_db,write_db
from routes.common import require_login,require_admin,json_body
from services.core import Problem,clean_text,record

bp=Blueprint('admin',__name__)


@bp.get('/api/banks')
@require_login
def all_banks():
    with read_db(current_app.config['DB_PATH']) as con:
        rows=[dict(r) for r in con.execute('SELECT id,name,area,latitude,longitude FROM banks ORDER BY id')]
    return jsonify({'banks':rows})


def coordinates(body):
    try:
        lat=float(body.get('latitude'))
        lon=float(body.get('longitude'))
    except (ValueError,TypeError):
        raise Problem('Enter valid latitude and longitude.')
    if not (-90<=lat<=90 and -180<=lon<=180):
        raise Problem('Latitude or longitude is outside its allowed range.')
    return lat,lon


def password_value(value):
    if not isinstance(value,str) or not 8<=len(value)<=128:
        raise Problem('Password must be 8-128 characters.')
    if value in DEMO_PASSWORDS and not demo_mode():
        raise Problem('Public demo passwords can only be used in classroom demo mode.')
    return value


@bp.post('/api/admin/banks')
@require_admin
def create_bank():
    body=json_body()
    name=clean_text(body.get('name'),'Bank name')
    area=clean_text(body.get('area'),'Area')
    lat,lon=coordinates(body)
    password=password_value(body.get('staff_password',''))
    with write_db(current_app.config['DB_PATH']) as con:
        try:
            bank_id=con.execute('''INSERT INTO banks(name,area,latitude,longitude)
                 VALUES(?,?,?,?)''',(name,area,lat,lon)).lastrowid
            con.execute('''INSERT INTO users(bank_id,username,password_hash,role)
                 VALUES(?,?,?,'staff')''',(bank_id,'staff',hash_password(password)))
            for kind,capacity in [('ambient',100000),('refrigerated',10000),('frozen',10000)]:
                con.execute('''INSERT INTO storage_zones(bank_id,storage_type,capacity_units)
                   VALUES(?,?,?)''',(bank_id,kind,capacity))
        except sqlite3.IntegrityError:
            raise Problem('This bank name already exists.',409)
        record(con,bank_id,'bank_added',f'Bank {name} added by administrator.',g.user_id)
    return jsonify({'message':'Bank created.','bank_id':bank_id,'suggested_node_port':5000+bank_id}),201


@bp.post('/api/admin/users')
@require_admin
def create_user():
    body=json_body()
    try:
        bank_id=int(body.get('bank_id'))
    except (ValueError,TypeError):
        raise Problem('Valid bank_id required.')
    username=clean_text(body.get('username'),'Username',40)
    role=body.get('role','staff')
    if role not in ('staff','admin','viewer','driver','dispatcher','manager'):
        raise Problem('Invalid user role.')
    password=password_value(body.get('password',''))
    with write_db(current_app.config['DB_PATH']) as con:
        if not con.execute('SELECT 1 FROM banks WHERE id=?',(bank_id,)).fetchone():
            raise Problem('Bank not found.',404)
        try:
            user_id=con.execute('''INSERT INTO users(bank_id,username,password_hash,role)
                VALUES(?,?,?,?)''',(bank_id,username,hash_password(password),role if role in ('staff','admin') else 'staff')).lastrowid
            if role not in ('staff','admin'):
                con.execute('INSERT INTO user_role_overrides(user_id,role) VALUES(?,?)',(user_id,role))
        except sqlite3.IntegrityError:
            raise Problem('Username already exists at that bank.',409)
        record(con,bank_id,'user_added',f'Account {username} created.',g.user_id)
    return jsonify({'message':'User created.','user_id':user_id}),201


@bp.get('/api/admin/users')
@require_admin
def list_users():
    # Optional filters: q (username contains), bank_id, role, active (1/0).
    # Password hashes and tokens are never selected.
    from services.dbadmin import list_users as search_users
    args=request.args
    return jsonify({'users':search_users(current_app.config['DB_PATH'],args.get('q',''),
                    args.get('bank_id'),args.get('role'),args.get('active'))})
