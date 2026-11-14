"""Additional stock, donation, waste, CSV, forecasting and conflict-aware sync logic."""
import csv
import io
import re
import sqlite3
from datetime import date,datetime,timedelta,timezone
from database import read_db,write_db,utc_now
from .validation import Problem,clean_text,whole,expiration
from .inventory import stock,reserved,available
from .lots import add_lot,consume_lots,usable_qty,set_quantity,_recount
from .records import record,notify

BARCODE=re.compile(r'^[a-zA-Z0-9._-]{4,40}$')
OPKEY=re.compile(r'^[a-zA-Z0-9_-]{8,100}$')


def barcode_text(value):
    if value in (None,''):
        return None
    if not isinstance(value,str) or not BARCODE.fullmatch(value):
        raise Problem('Barcode must be 4-40 letters, numbers, dots, dashes or underscores.')
    return value


def donation(path,bank_id,data,actor=None):
    item_id=whole(data.get('item_id'),1)
    qty=whole(data.get('quantity'),1)
    donor=clean_text(data.get('donor','Anonymous'),'Donor label',80)
    exp=expiration(data.get('expiration_date'))
    with write_db(path) as con:
        item=stock(con,bank_id,item_id)
        if not item:raise Problem('Add the item to this bank inventory first.',404)
        lot_id=add_lot(con,bank_id,item_id,qty,exp,'donation',data.get('lot_code') or None)
        donor_id=con.execute('''INSERT INTO donations(bank_id,item_id,donor,quantity,lot_id)
                VALUES(?,?,?,?,?)''',(bank_id,item_id,donor,qty,lot_id)).lastrowid
        if data.get('donor_id') not in (None,''):
            profile_id=whole(data['donor_id'],1)
            donor_record=con.execute('SELECT id,name FROM donors WHERE id=? AND bank_id=?',(profile_id,bank_id)).fetchone()
            if not donor_record:raise Problem('Donor not found at this bank.',404)
            con.execute('UPDATE donations SET donor_id=? WHERE id=?',(profile_id,donor_id))
        record(con,bank_id,'donation_received',f'Donation: +{qty} {item["name"]} ({donor}).',actor)
    return {'message':'Donation recorded.','donation_id':donor_id,'lot_id':lot_id}


def dispose(path,bank_id,data,actor=None):
    item_id=whole(data.get('item_id'),1)
    qty=whole(data.get('quantity'),1)
    reason=clean_text(data.get('reason'),'Waste reason',80)
    lot_id=data.get('lot_id')
    lot_id=whole(lot_id,1) if lot_id not in ('',None) else None
    with write_db(path) as con:
        item=stock(con,bank_id,item_id)
        if not item:raise Problem('Item does not exist at this bank.',404)
        # A donation, waste, or correction must never consume reserved stock.
        if qty>item['quantity']-reserved(con,bank_id,item_id):
            raise Problem('Not enough non-reserved stock to dispose.',409)
        portions=consume_lots(con,bank_id,item_id,qty,'waste',lot_id=lot_id)
        if usable_qty(con,bank_id,item_id)<reserved(con,bank_id,item_id):
            raise Problem('Waste would take stock reserved for approved transfers.',409)
        for part in portions:
            con.execute('''INSERT INTO waste(bank_id,item_id,lot_id,quantity,reason)
                VALUES(?,?,?,?,?)''',(bank_id,item_id,part['lot_id'],part['quantity'],reason))
        record(con,bank_id,'waste_recorded',f'Removed {qty} {item["name"]} ({reason}).',actor)
    return {'message':'Waste recorded.','quantity':qty}


def lots_for_bank(path,bank_id):
    with read_db(path) as con:
        return [dict(r) for r in con.execute('''SELECT l.id,l.item_id,i.name AS item_name,i.unit,
            l.quantity,l.expiration_date,l.source,l.received_at,l.lot_code,l.recalled
            FROM lots l JOIN items i ON i.id=l.item_id
            WHERE l.bank_id=? AND l.quantity>0 ORDER BY l.expiration_date,l.id''',(bank_id,))]


def change_lot_expiry(path,bank_id,lot_id,data,actor=None):
    exp=expiration(data.get('expiration_date'))
    with write_db(path) as con:
        lot=con.execute('SELECT * FROM lots WHERE bank_id=? AND id=?',(bank_id,lot_id)).fetchone()
        if not lot:raise Problem('Lot does not exist at your bank.',404)
        con.execute('UPDATE lots SET expiration_date=? WHERE id=?',(exp,lot_id))
        if usable_qty(con,bank_id,lot['item_id'])<reserved(con,bank_id,lot['item_id']):
            raise Problem('The change would expire stock reserved for transfers.',409)
        _recount(con,bank_id,lot['item_id'],'expiry_updated')
        record(con,bank_id,'lot_expiry_updated',f'Lot #{lot_id} expiration set to {exp or "none"}.',actor)
    return {'message':'Batch expiration updated.'}


def set_barcode(path,item_id,barcode,actor_bank,actor):
    barcode=barcode_text(barcode)
    with write_db(path) as con:
        if not con.execute('SELECT 1 FROM items WHERE id=?',(item_id,)).fetchone():
            raise Problem('Unknown item.',404)
        try:
            con.execute('UPDATE items SET barcode=? WHERE id=?',(barcode,item_id))
        except sqlite3.IntegrityError:
            raise Problem('Barcode belongs to another item.',409)
        record(con,actor_bank,'barcode_updated',f'Updated barcode for item #{item_id}.',actor)
    return {'message':'Barcode updated.'}


def forecast_and_alerts(con,bank_id):
    stocks=con.execute('''SELECT inv.item_id,items.name AS item_name,inv.quantity,inv.target
                          FROM inventory inv JOIN items ON items.id=inv.item_id WHERE inv.bank_id=?''',
                       (bank_id,)).fetchall()
    forecasts=[];alerts=[]
    start=(datetime.now(timezone.utc)-timedelta(days=30)).strftime('%Y-%m-%dT%H:%M:%SZ')
    for row in stocks:
        used=con.execute('''SELECT -COALESCE(SUM(delta),0) FROM stock_events
            WHERE bank_id=? AND item_id=? AND created_at>=? AND delta<0''',
            (bank_id,row['item_id'],start)).fetchone()[0]
        rate=round(used/30,2)
        usable=usable_qty(con,bank_id,row['item_id'])
        forecasts.append({'item_id':row['item_id'],'item_name':row['item_name'],
            'quantity':row['quantity'],'usable':usable,'used_last_30_days':used,
            'daily_outflow_estimate':rate,
            'days_remaining':round(usable/(used/30),1) if used>0 else None})
        if usable<row['target']:
            alerts.append({'type':'low_stock','item_id':row['item_id'],
                'message':f'{row["item_name"]}: {usable} unexpired units available, target {row["target"]}.'})
    today=date.today().isoformat();soon=(date.today()+timedelta(days=7)).isoformat()
    for row in con.execute('''SELECT l.id,items.name,l.quantity,l.expiration_date FROM lots l
        JOIN items ON items.id=l.item_id WHERE l.bank_id=? AND l.quantity>0
        AND l.expiration_date IS NOT NULL AND l.expiration_date<=? ORDER BY l.expiration_date''',
        (bank_id,soon)):
        alerts.append({'type':'expired' if row['expiration_date']<today else 'expiring_soon',
                       'lot_id':row['id'],'message':f'{row["name"]}: lot #{row["id"]}, {row["quantity"]} units, expires {row["expiration_date"]}.'})
    return forecasts,alerts


def _valid_signed_delta(value):
    if isinstance(value,bool) or value is None or not re.fullmatch(r'[+-]?\d+',str(value).strip()):
        raise Problem('Adjustment must be a signed whole number.')
    delta=int(value)
    if delta==0 or abs(delta)>1000000:raise Problem('Adjustment must be nonzero and within 1,000,000 units.')
    return delta


def sync_adjustment(path,bank_id,data,actor=None):
    """Once-only operation with optimistic locking. Conflicts require explicit operator review."""
    item_id=whole(data.get('item_id'),1)
    delta=_valid_signed_delta(data.get('delta'))
    version=whole(data.get('base_version'))
    key=data.get('op_key')
    if not isinstance(key,str) or not OPKEY.fullmatch(key):
        raise Problem('op_key must contain 8-100 valid characters.')
    with write_db(path) as con:
        old=con.execute('SELECT * FROM sync_operations WHERE bank_id=? AND op_key=?',
                        (bank_id,key)).fetchone()
        if old:
            if (old['item_id'],old['delta'],old['base_version'])!=(item_id,delta,version):
                raise Problem('An operation with this key already has different details.',409)
            return {'message':'Already synchronized.','duplicate':True,'operation_id':old['id']}
        inv=stock(con,bank_id,item_id)
        if not inv:raise Problem('Item does not exist at this bank.',404)
        if inv['version']!=version:
            raise Problem(f'Conflict: item changed from version {version} to {inv["version"]}. Review and re-enter the adjustment.',409)
        new_qty=inv['quantity']+delta
        if new_qty<reserved(con,bank_id,item_id) or new_qty<0 or new_qty>1000000:
            raise Problem('Adjustment would exceed limits or consume reserved stock.',409)
        set_quantity(con,bank_id,item_id,new_qty,inv['expiration_date'],'offline_sync')
        if usable_qty(con,bank_id,item_id)<reserved(con,bank_id,item_id):
            raise Problem('Adjustment would consume reserved unexpired stock.',409)
        op_id=con.execute('''INSERT INTO sync_operations(bank_id,op_key,item_id,delta,base_version)
            VALUES(?,?,?,?,?)''',(bank_id,key,item_id,delta,version)).lastrowid
        record(con,bank_id,'offline_adjustment',f'Offline sync: {delta:+} {inv["name"]}.',actor)
    return {'message':'Offline change synchronized.','duplicate':False,'operation_id':op_id}


def schedule_exchange(path,bank_id,exchange_id,data,actor=None):
    def optional_date(v):
        if v in (None,''):return None
        if not isinstance(v,str) or len(v)>40:raise Problem('Invalid date or time.')
        try:
            d=datetime.fromisoformat(v.replace('Z','+00:00'))
            if d.tzinfo is None:raise ValueError('Timezone required')
            return d.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        except ValueError:raise Problem('Pickup and delivery times must include a timezone.')
    pickup=optional_date(data.get('pickup_at'))
    delivery=optional_date(data.get('delivery_at'))
    notes=data.get('carrier_notes') or ''
    if not isinstance(notes,str) or len(notes)>240:raise Problem('Notes must be 240 characters or fewer.')
    if pickup and delivery and delivery<pickup:raise Problem('Delivery cannot be before pickup.')
    with write_db(path) as con:
        ex=con.execute('SELECT * FROM exchanges WHERE id=?',(exchange_id,)).fetchone()
        if not ex:raise Problem('Transfer not found.',404)
        if ex['source_bank_id']!=bank_id:raise Problem('Supplier must set the schedule.',403)
        if ex['status']!='approved':raise Problem('Schedule an approved transfer before shipment.',409)
        con.execute('''UPDATE exchanges SET pickup_at=?,delivery_at=?,carrier_notes=?,updated_at=? WHERE id=?''',
                    (pickup,delivery,notes,utc_now(),exchange_id))
        record(con,bank_id,'transfer_scheduled',f'Transfer #{exchange_id} scheduled.',actor,exchange_id)
        notify(con,ex['destination_bank_id'],f'Transfer #{exchange_id} pickup/delivery details updated.',exchange_id)
    return {'message':'Schedule updated.'}


def export_csv(path,bank_id):
    with read_db(path) as con:
        rows=con.execute('''SELECT i.name,i.unit,i.category,COALESCE(i.barcode,'') barcode,
            inv.quantity,inv.target,COALESCE(inv.expiration_date,'') expiration_date
            FROM inventory inv JOIN items i ON i.id=inv.item_id WHERE inv.bank_id=? ORDER BY i.name''',
            (bank_id,)).fetchall()
    output=io.StringIO(newline='')
    cols=['name','unit','category','barcode','quantity','target','expiration_date']
    writer=csv.DictWriter(output,fieldnames=cols)
    writer.writeheader()
    for r in rows:
        # Excel interprets leading formula characters; export as inert text.
        line={key:r[key] for key in cols}
        for key in ['name','unit','category','barcode']:
            if str(line[key]).startswith(('=','+','-','@')):
                line[key]="'"+line[key]
        writer.writerow(line)
    return output.getvalue()


def import_csv(path,bank_id,content,actor=None):
    if not isinstance(content,str) or len(content.encode('utf-8'))>131072:
        raise Problem('CSV must be UTF-8 and no larger than 128 KB.',413)
    try:
        reader=csv.DictReader(io.StringIO(content,newline=''))
        expected={'name','unit','category','quantity','target'}
        if not reader.fieldnames or not expected.issubset(set(reader.fieldnames)):
            raise Problem('CSV columns required: name, unit, category, quantity, target.')
        rows=list(reader)
        if not rows or len(rows)>100:raise Problem('CSV must contain 1 to 100 inventory rows.')
        clean=[];seen=set()
        for index,r in enumerate(rows,2):
            if None in r:raise Problem(f'Extra columns in row {index}.')
            name=clean_text(r.get('name'),f'Row {index} name')
            if name.casefold() in seen:raise Problem(f'Duplicate CSV item {name}.')
            seen.add(name.casefold())
            clean.append({'name':name,'unit':clean_text(r.get('unit'),f'Row {index} unit',30),
                'category':clean_text(r.get('category'),f'Row {index} category',40),
                'quantity':whole(r.get('quantity')),'target':whole(r.get('target')),
                'expiration':expiration(r.get('expiration_date')),
                'barcode':barcode_text(r.get('barcode'))})
    except (csv.Error,UnicodeError) as err:
        raise Problem('Could not parse the CSV.') from err
    # All-or-nothing: either the complete import commits, or nothing changes.
    with write_db(path) as con:
        for row in clean:
            item=con.execute('SELECT * FROM items WHERE name=? COLLATE NOCASE',(row['name'],)).fetchone()
            if item:
                if (item['unit'].casefold(),item['category'].casefold())!=(row['unit'].casefold(),row['category'].casefold()):
                    raise Problem(f'Catalog unit/category conflict for {row["name"]}.',409)
                item_id=item['id']
            else:
                try:
                    item_id=con.execute('INSERT INTO items(name,unit,category,barcode) VALUES(?,?,?,?)',
                        (row['name'],row['unit'],row['category'],row['barcode'])).lastrowid
                except sqlite3.IntegrityError:raise Problem(f'Barcode already assigned for {row["name"]}.',409)
            inv=stock(con,bank_id,item_id)
            if not inv:
                con.execute('INSERT INTO inventory(bank_id,item_id,quantity,target,expiration_date) VALUES(?,?,0,?,NULL)',
                            (bank_id,item_id,row['target']))
            else:
                if row['quantity']<reserved(con,bank_id,item_id):
                    raise Problem(f'CSV row {row["name"]} would consume reserved stock.',409)
                con.execute('UPDATE inventory SET target=? WHERE bank_id=? AND item_id=?',
                            (row['target'],bank_id,item_id))
            set_quantity(con,bank_id,item_id,row['quantity'],row['expiration'],'csv_import')
            if usable_qty(con,bank_id,item_id)<reserved(con,bank_id,item_id):
                raise Problem(f'CSV row {row["name"]} conflicts with reserved stock.',409)
        record(con,bank_id,'csv_import',f'Imported {len(clean)} inventory rows.',actor)
    return {'message':f'Imported {len(clean)} items.','items':len(clean)}
