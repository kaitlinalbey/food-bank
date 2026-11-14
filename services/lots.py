"""Lot-level inventory helpers. All mutations run inside an existing BEGIN IMMEDIATE transaction."""
from datetime import date
import secrets
import re
from database import utc_now
from .validation import Problem


def _lots(con,bank_id,item_id,allow_expired=True,lot_id=None):
    query='SELECT id,quantity,expiration_date,lot_code,recalled FROM lots WHERE bank_id=? AND item_id=? AND quantity>0'
    params=[bank_id,item_id]
    if lot_id is not None:
        query+=' AND id=?';params.append(lot_id)
    if not allow_expired:
        query+=' AND recalled=0 AND (expiration_date IS NULL OR expiration_date>=?)';params.append(date.today().isoformat())
    query+=' ORDER BY CASE WHEN expiration_date IS NULL THEN 1 ELSE 0 END, expiration_date ASC, id ASC'
    return con.execute(query,params).fetchall()


def usable_qty(con,bank_id,item_id):
    return sum(r['quantity'] for r in _lots(con,bank_id,item_id,False))


def _recount(con,bank_id,item_id,reason,old_qty=None):
    """Refresh materialized total, summary expiry, version and optional consumption log."""
    inv=con.execute('SELECT quantity FROM inventory WHERE bank_id=? AND item_id=?',(bank_id,item_id)).fetchone()
    if inv is None:
        raise Problem('Inventory item not found.',404)
    total=con.execute('SELECT COALESCE(SUM(quantity),0) FROM lots WHERE bank_id=? AND item_id=?',(bank_id,item_id)).fetchone()[0]
    earliest=con.execute('''SELECT MIN(expiration_date) FROM lots WHERE bank_id=? AND item_id=?
        AND quantity>0 AND expiration_date IS NOT NULL''',(bank_id,item_id)).fetchone()[0]
    previous=inv['quantity'] if old_qty is None else old_qty
    con.execute('''UPDATE inventory SET quantity=?,expiration_date=?,updated_at=?,version=version+1
        WHERE bank_id=? AND item_id=?''',(total,earliest,utc_now(),bank_id,item_id))
    delta=total-previous
    if delta:
        con.execute('INSERT INTO stock_events(bank_id,item_id,delta,reason) VALUES(?,?,?,?)',
                    (bank_id,item_id,delta,reason))
    return total


def add_lot(con,bank_id,item_id,qty,expiration_date=None,source='donation',lot_code=None,allow_recalled=False):
    if not lot_code:
        lot_code='LOT-'+secrets.token_hex(9).upper()
    if not isinstance(lot_code,str) or not re.fullmatch(r'[A-Za-z0-9._-]{3,60}',lot_code):
        raise Problem('Batch code must be 3-60 valid characters.')
    recalled=bool(con.execute("SELECT 1 FROM recalls WHERE item_id=? AND lot_code=? AND status='active'",
                   (item_id,lot_code)).fetchone())
    if recalled and not allow_recalled:
        raise Problem('Batch is under an active recall and cannot be accepted as usable stock.',409)
    kind=con.execute('SELECT storage_type FROM items WHERE id=?',(item_id,)).fetchone()
    if kind:
        cap=con.execute('SELECT capacity_units FROM storage_zones WHERE bank_id=? AND storage_type=?',
                        (bank_id,kind['storage_type'])).fetchone()
        used=con.execute('''SELECT COALESCE(SUM(l.quantity),0) FROM lots l JOIN items i ON i.id=l.item_id
                           WHERE l.bank_id=? AND i.storage_type=?''',(bank_id,kind['storage_type'])).fetchone()[0]
        if cap and used+qty>cap['capacity_units']:
            raise Problem('Not enough '+kind['storage_type']+' storage capacity.',409)
    lot_id=con.execute('''INSERT INTO lots(bank_id,item_id,quantity,expiration_date,source,lot_code,recalled)
        VALUES(?,?,?,?,?,?,?)''',(bank_id,item_id,qty,expiration_date,source,lot_code,int(recalled))).lastrowid
    _recount(con,bank_id,item_id,source)
    return lot_id


def consume_lots(con,bank_id,item_id,qty,reason,unexpired_only=False,lot_id=None):
    if qty<=0:
        raise Problem('Quantity must be greater than zero.')
    rows=_lots(con,bank_id,item_id,not unexpired_only,lot_id)
    if sum(row['quantity'] for row in rows)<qty:
        raise Problem('Not enough stock in the selected lot(s).',409)
    remaining=qty
    allocation=[]
    for row in rows:
        if not remaining:break
        take=min(remaining,row['quantity'])
        con.execute('UPDATE lots SET quantity=quantity-? WHERE id=?',(take,row['id']))
        allocation.append({'lot_id':row['id'],'expiration_date':row['expiration_date'],'quantity':take,'lot_code':row['lot_code']})
        remaining-=take
    _recount(con,bank_id,item_id,reason)
    return allocation


def set_quantity(con,bank_id,item_id,new_quantity,new_expiration=None,source='manual'):
    inv=con.execute('SELECT quantity,expiration_date FROM inventory WHERE bank_id=? AND item_id=?',
                    (bank_id,item_id)).fetchone()
    if inv is None:
        raise Problem('Inventory item not found.',404)
    rows=_lots(con,bank_id,item_id)
    # A change of the summary expiration is only safe for a single lot.
    if new_expiration!=inv['expiration_date']:
        if len(rows)>1:
            raise Problem('Change individual lot expiration dates through the lot records, not the summary.',409)
        if rows:
            con.execute('UPDATE lots SET expiration_date=? WHERE id=?',(new_expiration,rows[0]['id']))
    difference=new_quantity-inv['quantity']
    if difference>0:
        # Manual/CSV quantity increases use the same capacity check as donations.
        kind=con.execute('SELECT storage_type FROM items WHERE id=?',(item_id,)).fetchone()['storage_type']
        cap=con.execute('SELECT capacity_units FROM storage_zones WHERE bank_id=? AND storage_type=?',
                        (bank_id,kind)).fetchone()
        used=con.execute('''SELECT COALESCE(SUM(l.quantity),0) FROM lots l JOIN items i ON i.id=l.item_id
                           WHERE l.bank_id=? AND i.storage_type=?''',(bank_id,kind)).fetchone()[0]
        if cap and used+difference>cap['capacity_units']:
            raise Problem('Not enough '+kind+' storage capacity.',409)
        con.execute('''INSERT INTO lots(bank_id,item_id,quantity,expiration_date,source,lot_code)
                       VALUES(?,?,?,?,?,?)''',
                    (bank_id,item_id,difference,new_expiration,source,'MANUAL-'+secrets.token_hex(9).upper()))
    elif difference<0:
        remaining=-difference
        for lot in rows:
            take=min(remaining,lot['quantity'])
            if take:
                con.execute('UPDATE lots SET quantity=quantity-? WHERE id=?',(take,lot['id']))
                remaining-=take
            if remaining==0:break
        if remaining:raise Problem('Not enough stock.',409)
    return _recount(con,bank_id,item_id,'adjustment_out' if difference<0 else 'adjustment_in')
