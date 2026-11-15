"""Dashboard, activity, notifications and node health checks."""
from concurrent.futures import ThreadPoolExecutor
import os
import json
from urllib.error import URLError
from urllib.request import urlopen

from flask import Blueprint,current_app,g,jsonify
from database import read_db
from routes.common import require_login,require_roles
from services.core import dashboard,mark_notifications

bp=Blueprint('stats',__name__)


@bp.get('/api/health')
def health():
    return jsonify({'status':'ok','service':'food-bank-central-api'})


@bp.get('/api/dashboard')
@require_login
def get_dashboard():
    result=dashboard(current_app.config['DB_PATH'],g.bank_id)
    result['user']=g.user
    return jsonify(result)


@bp.get('/api/stats')
@require_login
def get_stats():
    with read_db(current_app.config['DB_PATH']) as con:
        banks=con.execute('SELECT COUNT(*) FROM banks').fetchone()[0]
    result=dashboard(current_app.config['DB_PATH'],g.bank_id)
    return jsonify({'bank_count':banks,**result['stats']})


@bp.get('/api/activity')
@require_login
def get_activity():
    with read_db(current_app.config['DB_PATH']) as con:
        entries=[dict(r) for r in con.execute('''SELECT id,action,details,created_at,exchange_id
                  FROM activity WHERE bank_id=? ORDER BY id DESC LIMIT 100''',(g.bank_id,))]
    return jsonify({'activity':entries})


@bp.get('/api/notifications')
@require_login
def get_notifications():
    with read_db(current_app.config['DB_PATH']) as con:
        entries=[dict(r) for r in con.execute('''SELECT id,message,is_read,created_at,exchange_id
                 FROM notifications WHERE bank_id=? ORDER BY id DESC LIMIT 100''',(g.bank_id,))]
    return jsonify({'notifications':entries})


@bp.post('/api/notifications/read')
@require_roles("staff","manager","admin","dispatcher")
def read_notifications():
    count=mark_notifications(current_app.config['DB_PATH'],g.bank_id)
    return jsonify({'message':f'Marked {count} notifications as read.','count':count})


def _node_status(bank):
    # Only probes fixed loopback ports calculated from database IDs, no untrusted URLs.
    port=5000+bank['id']
    if port>65535:
        return {'bank_id':bank['id'],'bank_name':bank['name'],'online':False,'port':None}
    # Optional deployment-only template; URLs never come from an HTTP request.
    # Local Windows demo defaults to loopback; Docker uses bank1/bank2/bank3.
    template=os.environ.get('FOOD_BANK_NODE_URL_TEMPLATE','http://127.0.0.1:{port}')
    try:
        endpoint=template.format(bank_id=bank['id'],port=port)
        with urlopen(endpoint+'/node-health',timeout=.65) as response:
            info=json.loads(response.read())
            online=response.status==200 and info.get('bank_id')==bank['id']
    except (URLError,TimeoutError,ValueError,OSError):
        online=False
    return {'bank_id':bank['id'],'bank_name':bank['name'],'online':online,'port':port}


@bp.get('/api/nodes')
@require_login
def node_health():
    with read_db(current_app.config['DB_PATH']) as con:
        banks=[dict(r) for r in con.execute('SELECT id,name FROM banks ORDER BY id LIMIT 30')]
    with ThreadPoolExecutor(max_workers=10) as pool:
        nodes=list(pool.map(_node_status,banks))
    return jsonify({'nodes':nodes})
