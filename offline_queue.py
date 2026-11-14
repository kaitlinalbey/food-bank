"""Small per-node durable queue. No passwords or bearer tokens are stored on disk."""
from contextlib import contextmanager
import json
import secrets
import sqlite3
from pathlib import Path


@contextmanager
def _db(path):
    """Commit on success, roll back on error, and ALWAYS close the connection.

    sqlite3's built-in ``with con:`` only commits/rolls back; it leaves the
    file open, which keeps it locked on Windows.
    """
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    con=sqlite3.connect(path,timeout=10)
    try:
        con.row_factory=sqlite3.Row
        con.executescript('''CREATE TABLE IF NOT EXISTS snapshot (
            id INTEGER PRIMARY KEY CHECK(id=1),data_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS queued (
            id INTEGER PRIMARY KEY AUTOINCREMENT,op_key TEXT NOT NULL UNIQUE,
            item_id INTEGER NOT NULL,delta INTEGER NOT NULL,base_version INTEGER NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('pending','conflict','synced')),
            error TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );''')
        with con:
            yield con
    finally:
        con.close()


def save_snapshot(path,data):
    safe={'bank':data['bank'], 'inventory':[{'item_id':r['item_id'],
        'item_name':r['item_name'],'quantity':r['quantity'], 'version':r['version']}
        for r in data['inventory']]}
    with _db(path) as con:
        con.execute('INSERT OR REPLACE INTO snapshot(id,data_json) VALUES(1,?)',(json.dumps(safe),))


def get_snapshot(path):
    with _db(path) as con:
        row=con.execute('SELECT data_json FROM snapshot WHERE id=1').fetchone()
    return json.loads(row['data_json']) if row else None


def all_queue(path):
    snapshot=get_snapshot(path) or {}
    names={r['item_id']:r['item_name'] for r in snapshot.get('inventory',[])}
    with _db(path) as con:
        rows=[dict(row) for row in con.execute('''SELECT * FROM queued WHERE status!='synced' ORDER BY id''')]
    for row in rows:row['item_name']=names.get(row['item_id'],f'Item #{row["item_id"]}')
    return rows


def enqueue(path,item_id,delta):
    try:
        item_id=int(item_id);delta=int(delta)
    except (TypeError,ValueError):raise ValueError('Enter valid item ID and adjustment.')
    if not 1<=item_id<=1000000 or delta==0 or abs(delta)>1000000:
        raise ValueError('Enter a nonzero adjustment within 1,000,000 units.')
    snap=get_snapshot(path)
    if not snap:raise ValueError('No cached inventory yet. Load the dashboard online first.')
    item=next((r for r in snap['inventory'] if r['item_id']==item_id),None)
    if not item:raise ValueError('This item is not in the saved inventory snapshot.')
    with _db(path) as con:
        if con.execute('SELECT 1 FROM queued WHERE item_id=? AND status!=? LIMIT 1',
                       (item_id,'synced')).fetchone():
            raise ValueError('Only one unsynchronized change per item is allowed. Sync or discard it first.')
        key=secrets.token_urlsafe(24)
        con.execute('''INSERT INTO queued(op_key,item_id,delta,base_version,status)
            VALUES(?,?,?,?,'pending')''',(key,item_id,delta,item['version']))
    return key


# HTTP statuses meaning "this queued operation itself can never succeed as sent".
# Such rows are set aside for operator review (status 'conflict', with the reason)
# so they cannot block every later queued change. Network errors, 401/403 (login
# or permission of the person syncing), 408, 429 and 5xx stop the run and leave the
# row pending, because a later retry may succeed.
PERMANENT_REJECTIONS=(400,404,410,413,422)


def _review_reason(exc):
    code=getattr(exc,'status',None)
    if code==409:
        return str(exc)[:250]
    return f'Rejected by central API (HTTP {code}): {exc}'[:250]


def sync_pending(path,send):
    """send(path, payload) -> response; errors must have status and a message."""
    succeeded=conflicted=0
    with _db(path) as con:
        records=[dict(x) for x in con.execute("SELECT * FROM queued WHERE status='pending' ORDER BY id")]
    for row in records:
        payload={'item_id':row['item_id'],'delta':row['delta'],
                 'base_version':row['base_version'],'op_key':row['op_key']}
        try:
            send('POST',f'/api/inventory/{row["item_id"]}/adjust',payload)
        except Exception as exc:
            code=getattr(exc,'status',None)
            if code==409 or code in PERMANENT_REJECTIONS:
                with _db(path) as con:
                    con.execute('UPDATE queued SET status=?,error=? WHERE id=?',
                                ('conflict',_review_reason(exc),row['id']))
                conflicted+=1
                continue
            return {'synced':succeeded,'conflicts':conflicted,'error':str(exc)}
        with _db(path) as con:
            con.execute("UPDATE queued SET status='synced',error='' WHERE id=?",(row['id'],))
        succeeded+=1
    return {'synced':succeeded,'conflicts':conflicted,'error':None}


def discard(path,row_id):
    with _db(path) as con:
        return con.execute("DELETE FROM queued WHERE id=? AND status!='synced'",(row_id,)).rowcount

# Additional, explicitly independent physical receipts can be safely replayed
# after reconnect, unlike stale physical-count corrections. No credentials saved.
def _extra_tables(con):
    con.executescript('''
    CREATE TABLE IF NOT EXISTS receipt_queue (
        id INTEGER PRIMARY KEY,op_key TEXT NOT NULL UNIQUE,
        item_id INTEGER NOT NULL,quantity INTEGER NOT NULL,donor TEXT NOT NULL,
        lot_code TEXT NOT NULL,expiration_date TEXT,
        status TEXT NOT NULL DEFAULT 'pending',error TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS node_events (
        id INTEGER PRIMARY KEY,method TEXT NOT NULL,path TEXT NOT NULL,
        result TEXT NOT NULL,correlation_id TEXT NOT NULL,latency_ms REAL NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );''')


def enqueue_receipt(path,item_id,quantity,donor,expiration_date=None):
    import re
    from datetime import date
    try:item_id=int(item_id);quantity=int(quantity)
    except (TypeError,ValueError):raise ValueError('Enter a valid item and quantity.')
    if item_id<1 or not 1<=quantity<=1000000:raise ValueError('Receipt quantity must be 1-1,000,000.')
    donor=str(donor or 'Offline donation').strip()
    if not 1<=len(donor)<=80:raise ValueError('Donor label must be 1-80 characters.')
    if expiration_date:
        try:date.fromisoformat(expiration_date)
        except ValueError:raise ValueError('Invalid expiration date.')
    else:expiration_date=None
    snapshot=get_snapshot(path)
    if not snapshot or not any(i['item_id']==item_id for i in snapshot['inventory']):
        raise ValueError('Load this item online at least once before queuing receipts.')
    op_key=secrets.token_hex(20)
    lot_code='OFFLINE-'+secrets.token_hex(10).upper()
    with _db(path) as con:
        _extra_tables(con)
        con.execute('''INSERT INTO receipt_queue(op_key,item_id,quantity,donor,lot_code,expiration_date)
             VALUES(?,?,?,?,?,?)''',(op_key,item_id,quantity,donor,lot_code,expiration_date))
    return op_key


def all_receipts(path):
    snapshot=get_snapshot(path) or {}
    names={r['item_id']:r['item_name'] for r in snapshot.get('inventory',[])}
    with _db(path) as con:
        _extra_tables(con)
        items=[dict(r) for r in con.execute("SELECT * FROM receipt_queue WHERE status!='synced' ORDER BY id")]
    for item in items:item['item_name']=names.get(item['item_id'],'Item #'+str(item['item_id']))
    return items


def sync_receipts(path,send):
    with _db(path) as con:
        _extra_tables(con)
        rows=[dict(r) for r in con.execute("SELECT * FROM receipt_queue WHERE status='pending' ORDER BY id")]
    synced=conflicts=0
    for row in rows:
        payload={k:row[k] for k in ('op_key','item_id','quantity','donor','lot_code','expiration_date')}
        try:send('POST','/api/offline/receipts',payload)
        except Exception as exc:
            code=getattr(exc,'status',None)
            if code==409 or code in PERMANENT_REJECTIONS:
                with _db(path) as con:
                    con.execute("UPDATE receipt_queue SET status='conflict',error=? WHERE id=?",
                                (_review_reason(exc)[:200],row['id']))
                conflicts+=1
                continue
            return {'synced':synced,'conflicts':conflicts,'error':str(exc)}
        with _db(path) as con:
            con.execute("UPDATE receipt_queue SET status='synced',error='' WHERE id=?",(row['id'],))
        synced+=1
    return {'synced':synced,'conflicts':conflicts,'error':None}


def discard_receipt(path,row_id):
    with _db(path) as con:
        _extra_tables(con)
        return con.execute("DELETE FROM receipt_queue WHERE id=? AND status!='synced'",(row_id,)).rowcount


def log_local_event(path,method,route,result,correlation_id,latency_ms):
    with _db(path) as con:
        _extra_tables(con)
        con.execute('''INSERT INTO node_events(method,path,result,correlation_id,latency_ms)
                       VALUES(?,?,?,?,?)''',(method,route,result,correlation_id,round(latency_ms,2)))
        con.execute('''DELETE FROM node_events WHERE id NOT IN
                       (SELECT id FROM node_events ORDER BY id DESC LIMIT 2000)''')


def local_events(path):
    with _db(path) as con:
        _extra_tables(con)
        return [dict(r) for r in con.execute('SELECT * FROM node_events ORDER BY id DESC LIMIT 60')]
