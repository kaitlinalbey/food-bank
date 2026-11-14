"""Final-phase classroom features: recalls, reconciliation, deliveries and reporting.

All mutations use write_db(BEGIN IMMEDIATE), so checks and changes commit together.
Stock changes always pass through lot operations, preserving inventory invariants.
"""
import re
from datetime import date
from database import read_db,write_db,utc_now
from .validation import Problem,whole,clean_text,expiration
from .inventory import stock,reserved
from .lots import add_lot,set_quantity,usable_qty,_recount
from .records import record,notify

LOT_CODE=re.compile(r'^[A-Za-z0-9._-]{3,60}$')
KEY=re.compile(r'^[A-Za-z0-9_-]{8,100}$')
STORAGE=('ambient','refrigerated','frozen')


def lot_code_value(value):
    if not isinstance(value,str) or not LOT_CODE.fullmatch(value.strip()):
        raise Problem('Batch code must use 3-60 letters, numbers, dots, dashes or underscores.')
    return value.strip()


def add_donor(path,bank_id,payload,actor=None):
    name=clean_text(payload.get('name'),'Donor name or organization',80)
    notes=payload.get('notes','') or ''
    if not isinstance(notes,str) or len(notes)>200:raise Problem('Donor notes must be 200 characters or less.')
    with write_db(path) as con:
        exists=con.execute('SELECT id FROM donors WHERE bank_id=? AND name=? COLLATE NOCASE',(bank_id,name)).fetchone()
        if exists:raise Problem('Donor is already in this bank directory.',409)
        donor_id=con.execute('INSERT INTO donors(bank_id,name,notes) VALUES(?,?,?)',(bank_id,name,notes)).lastrowid
        record(con,bank_id,'donor_created',f'Donor label {name} created.',actor)
    return {'donor_id':donor_id,'message':'Donor added.'}


def list_donors(path,bank_id):
    with read_db(path) as con:
        return [dict(r) for r in con.execute('''SELECT p.id,p.name,p.notes,
            COUNT(d.id) donation_count,COALESCE(SUM(d.quantity),0) total_units
            FROM donors p LEFT JOIN donations d ON d.donor_id=p.id
            WHERE p.bank_id=? GROUP BY p.id ORDER BY p.name''',(bank_id,))]


def receipt_for_donation(path,bank_id,donation_id):
    with read_db(path) as con:
        row=con.execute('''SELECT d.id,d.donor,d.quantity,d.created_at,l.lot_code,
            i.name item_name,i.unit,b.name bank_name FROM donations d
            JOIN items i ON i.id=d.item_id JOIN banks b ON b.id=d.bank_id
            LEFT JOIN lots l ON l.id=d.lot_id WHERE d.bank_id=? AND d.id=?''',(bank_id,donation_id)).fetchone()
        if not row:raise Problem('Donation not found at this bank.',404)
        return dict(row)


def issue_recall(path,bank_id,data,actor=None):
    item_id=whole(data.get('item_id'),1)
    code=lot_code_value(data.get('lot_code'))
    reason=clean_text(data.get('reason'),'Recall reason',200)
    with write_db(path) as con:
        if not con.execute('SELECT 1 FROM items WHERE id=?',(item_id,)).fetchone():
            raise Problem('Item not found.',404)
        if con.execute('''SELECT 1 FROM recalls WHERE item_id=? AND lot_code=? AND status='active' ''',
                       (item_id,code)).fetchone():
            raise Problem('An active recall already exists for this item and batch.',409)
        affected=con.execute('''SELECT id,bank_id,quantity FROM lots
             WHERE item_id=? AND lot_code=? AND quantity>0''',(item_id,code)).fetchall()
        in_transit=con.execute('''SELECT DISTINCT ex.destination_bank_id AS bank_id FROM shipment_lots sl
            JOIN exchanges ex ON ex.id=sl.exchange_id WHERE ex.item_id=? AND sl.lot_code=?
            AND ex.status='in_transit' ''',(item_id,code)).fetchall()
        if not affected and not in_transit:
            raise Problem('No stored or in-transit batches match this item and batch code.',404)
        recall_id=con.execute('''INSERT INTO recalls(item_id,lot_code,reason,created_by)
            VALUES(?,?,?,?)''',(item_id,code,reason,actor)).lastrowid
        con.execute('UPDATE lots SET recalled=1 WHERE item_id=? AND lot_code=?',(item_id,code))
        bank_ids=sorted({r['bank_id'] for r in affected}|{r['bank_id'] for r in in_transit})
        for bank in bank_ids:
            notify(con,bank,f'URGENT food recall #{recall_id}: batch {code}. Stop distributing this stock.')
            record(con,bank,'recall_issued',f'Recall #{recall_id} quarantined batch {code}.',actor)
        record(con,bank_id,'recall_announced',f'Recall #{recall_id}: {reason}',actor)
    return {'recall_id':recall_id,'affected_banks':bank_ids,'affected_lots':len(affected),'in_transit_destinations':len(in_transit),
            'message':'Recall active. Matching/shipping excludes the affected batches.'}


def close_recall(path,bank_id,recall_id,actor=None):
    with write_db(path) as con:
        row=con.execute('SELECT * FROM recalls WHERE id=?',(recall_id,)).fetchone()
        if not row:raise Problem('Recall not found.',404)
        if row['status']!='active':raise Problem('Recall is already closed.',409)
        con.execute("UPDATE recalls SET status='closed',closed_at=? WHERE id=?",(utc_now(),recall_id))
        # Still quarantine if another active recall targets the same item/batch.
        still=con.execute("SELECT 1 FROM recalls WHERE item_id=? AND lot_code=? AND status='active' LIMIT 1",
                          (row['item_id'],row['lot_code'])).fetchone()
        if not still:
            con.execute('UPDATE lots SET recalled=0 WHERE item_id=? AND lot_code=?',
                        (row['item_id'],row['lot_code']))
        affected=[r[0] for r in con.execute('SELECT DISTINCT bank_id FROM lots WHERE item_id=? AND lot_code=?',
                                            (row['item_id'],row['lot_code']))]
        for bank in affected:
            notify(con,bank,f'Recall #{recall_id} was closed; review stock before distribution.')
        record(con,bank_id,'recall_closed',f'Recall #{recall_id} closed.',actor)
    return {'message':'Recall closed. Other active recalls (if any) remain effective.'}


def recalls_for_bank(path,bank_id,global_view=False):
    with read_db(path) as con:
        if global_view:
            rows=con.execute('''SELECT r.*,i.name item_name FROM recalls r JOIN items i ON i.id=r.item_id
                ORDER BY r.id DESC LIMIT 200''')
        else:
            rows=con.execute('''SELECT r.*,i.name item_name FROM recalls r JOIN items i ON i.id=r.item_id
                WHERE EXISTS (SELECT 1 FROM lots l WHERE l.bank_id=?
                AND l.item_id=r.item_id AND l.lot_code=r.lot_code)
                ORDER BY r.id DESC LIMIT 200''',(bank_id,))
        return [dict(x) for x in rows]


def add_zone_capacity(path,bank_id,payload,actor=None):
    kind=payload.get('storage_type')
    if kind not in STORAGE:raise Problem('storage_type must be ambient, refrigerated, or frozen.')
    capacity=whole(payload.get('capacity_units'))
    with write_db(path) as con:
        occupied=con.execute('''SELECT COALESCE(SUM(l.quantity),0) FROM lots l JOIN items i ON i.id=l.item_id
            WHERE l.bank_id=? AND i.storage_type=?''',(bank_id,kind)).fetchone()[0]
        if capacity<occupied:raise Problem(f'Capacity cannot be below {occupied} units currently in storage.',409)
        con.execute('''INSERT INTO storage_zones(bank_id,storage_type,capacity_units)
            VALUES(?,?,?) ON CONFLICT(bank_id,storage_type) DO UPDATE SET capacity_units=excluded.capacity_units''',
            (bank_id,kind,capacity))
        record(con,bank_id,'storage_capacity_changed',f'{kind} capacity: {capacity} units.',actor)
    return {'message':'Capacity updated.','occupied_units':occupied}


def storage_summary(path,bank_id):
    with read_db(path) as con:
        return [dict(r) for r in con.execute('''SELECT z.storage_type,z.capacity_units,
            COALESCE(SUM(l.quantity),0) occupied_units
            FROM storage_zones z LEFT JOIN items i ON i.storage_type=z.storage_type
            LEFT JOIN lots l ON l.item_id=i.id AND l.bank_id=z.bank_id
            WHERE z.bank_id=? GROUP BY z.storage_type,z.capacity_units ORDER BY z.storage_type''',(bank_id,))]


def set_item_storage(path,bank_id,item_id,kind,actor=None):
    if kind not in STORAGE:raise Problem('Invalid storage_type.')
    with write_db(path) as con:
        row=con.execute('SELECT storage_type,name FROM items WHERE id=?',(item_id,)).fetchone()
        if not row:raise Problem('Item not found.',404)
        if row['storage_type']==kind:return {'message':'Storage type unchanged.'}
        moving=con.execute('SELECT COALESCE(SUM(quantity),0) FROM lots WHERE item_id=?',(item_id,)).fetchone()[0]
        # Item storage is global, so capacity must be valid across ALL banks.
        for b in con.execute('SELECT id FROM banks'):
            used=con.execute('''SELECT COALESCE(SUM(l.quantity),0) FROM lots l JOIN items i ON i.id=l.item_id
                WHERE l.bank_id=? AND i.storage_type=?''',(b['id'],kind)).fetchone()[0]
            total_here=con.execute('SELECT COALESCE(SUM(quantity),0) FROM lots WHERE bank_id=? AND item_id=?',
                (b['id'],item_id)).fetchone()[0]
            cap=con.execute('SELECT capacity_units FROM storage_zones WHERE bank_id=? AND storage_type=?',
                (b['id'],kind)).fetchone()
            if cap and used+total_here>cap['capacity_units']:
                raise Problem(f'Bank #{b["id"]} lacks {kind} storage capacity.',409)
        con.execute('UPDATE items SET storage_type=? WHERE id=?',(kind,item_id))
        record(con,bank_id,'storage_rule_changed',f'{row["name"]}: {row["storage_type"]} -> {kind}.',actor)
    return {'message':'Item storage requirement updated.'}


def propose_count(path,bank_id,data,actor=None):
    item_id=whole(data.get('item_id'),1)
    counted=whole(data.get('counted_qty'))
    reason=clean_text(data.get('reason'),'Count explanation',200)
    with write_db(path) as con:
        inv=stock(con,bank_id,item_id)
        if not inv:raise Problem('Item not found.',404)
        rid=con.execute('''INSERT INTO reconciliations(bank_id,item_id,expected_qty,counted_qty,
              base_version,reason,requested_by) VALUES(?,?,?,?,?,?,?)''',
              (bank_id,item_id,inv['quantity'],counted,inv['version'],reason,actor)).lastrowid
        record(con,bank_id,'reconciliation_proposed',f'Count #{rid}: {inv["quantity"]} vs observed {counted}.',actor)
    return {'reconciliation_id':rid,'message':'Physical count saved for manager review.'}


def decide_count(path,bank_id,recon_id,action,actor=None):
    if action not in ('apply','reject'):raise Problem('Unknown reconciliation action.',404)
    with write_db(path) as con:
        row=con.execute('SELECT * FROM reconciliations WHERE id=? AND bank_id=?',(recon_id,bank_id)).fetchone()
        if not row:raise Problem('Count not found at this bank.',404)
        if row['status']!='pending':raise Problem('Count already resolved.',409)
        if action=='apply':
            inv=stock(con,bank_id,row['item_id'])
            if not inv or inv['version']!=row['base_version']:
                raise Problem('Inventory changed since the count. Create a new physical count.',409)
            if row['counted_qty']<reserved(con,bank_id,row['item_id']):
                raise Problem('Physical count cannot consume stock reserved for transfers.',409)
            set_quantity(con,bank_id,row['item_id'],row['counted_qty'],inv['expiration_date'],'reconciliation')
            if usable_qty(con,bank_id,row['item_id'])<reserved(con,bank_id,row['item_id']):
                raise Problem('Physical count would leave too little usable stock for reserved transfers.',409)
        status='applied' if action=='apply' else 'rejected'
        con.execute('''UPDATE reconciliations SET status=?,approved_by=?,completed_at=? WHERE id=?''',
                    (status,actor,utc_now(),recon_id))
        record(con,bank_id,'reconciliation_'+status,f'Physical count #{recon_id} {status}.',actor)
    return {'message':f'Count {status}.','status':status}


def list_counts(path,bank_id):
    with read_db(path) as con:
        return [dict(r) for r in con.execute('''SELECT r.*,i.name item_name FROM reconciliations r
             JOIN items i ON i.id=r.item_id WHERE r.bank_id=? ORDER BY r.id DESC LIMIT 100''',(bank_id,))]


def add_driver(path,bank_id,data,actor=None):
    name=clean_text(data.get('display_name'),'Driver display name',60)
    with write_db(path) as con:
        if con.execute('SELECT 1 FROM drivers WHERE bank_id=? AND display_name=? COLLATE NOCASE',(bank_id,name)).fetchone():
            raise Problem('Driver already exists.',409)
        driver_id=con.execute('INSERT INTO drivers(bank_id,display_name) VALUES(?,?)',(bank_id,name)).lastrowid
        record(con,bank_id,'driver_created',f'Driver {name} added.',actor)
    return {'driver_id':driver_id,'message':'Driver added.'}


def add_vehicle(path,bank_id,data,actor=None):
    label=clean_text(data.get('label'),'Vehicle label',60)
    capacity=whole(data.get('capacity_units'),1)
    refrigerated=1 if data.get('refrigerated') in (True,1,'1','true','on') else 0
    with write_db(path) as con:
        if con.execute('SELECT 1 FROM vehicles WHERE bank_id=? AND label=? COLLATE NOCASE',(bank_id,label)).fetchone():
            raise Problem('Vehicle already exists.',409)
        vehicle_id=con.execute('''INSERT INTO vehicles(bank_id,label,capacity_units,refrigerated)
              VALUES(?,?,?,?)''',(bank_id,label,capacity,refrigerated)).lastrowid
        record(con,bank_id,'vehicle_created',f'Vehicle {label} added.',actor)
    return {'vehicle_id':vehicle_id,'message':'Vehicle added.'}


def list_fleet(path,bank_id):
    with read_db(path) as con:
        drivers=[dict(r) for r in con.execute('SELECT * FROM drivers WHERE bank_id=? ORDER BY id',(bank_id,))]
        vehicles=[dict(r) for r in con.execute('SELECT * FROM vehicles WHERE bank_id=? ORDER BY id',(bank_id,))]
        assignments=[dict(r) for r in con.execute('''SELECT d.*,ex.status,ex.quantity,i.name item_name,
          dr.display_name driver_name,v.label vehicle_label FROM deliveries d JOIN exchanges ex ON ex.id=d.exchange_id
          JOIN items i ON i.id=ex.item_id JOIN drivers dr ON dr.id=d.driver_id
          JOIN vehicles v ON v.id=d.vehicle_id WHERE ex.source_bank_id=? ORDER BY ex.id DESC LIMIT 100''',(bank_id,))]
        return {'drivers':drivers,'vehicles':vehicles,'deliveries':assignments}


def assign_transport(path,bank_id,exchange_id,data,actor=None):
    driver_id=whole(data.get('driver_id'),1);vehicle_id=whole(data.get('vehicle_id'),1)
    with write_db(path) as con:
        ex=con.execute('SELECT * FROM exchanges WHERE id=?',(exchange_id,)).fetchone()
        if not ex or ex['source_bank_id']!=bank_id:raise Problem('Supplier transfer not found.',404)
        if ex['status']!='approved':raise Problem('Assign transportation before shipping an approved transfer.',409)
        driver=con.execute('SELECT * FROM drivers WHERE id=? AND bank_id=? AND active=1',(driver_id,bank_id)).fetchone()
        vehicle=con.execute('SELECT * FROM vehicles WHERE id=? AND bank_id=? AND active=1',(vehicle_id,bank_id)).fetchone()
        if not driver or not vehicle:raise Problem('Active driver and vehicle must belong to the supplying bank.',409)
        if ex['quantity']>vehicle['capacity_units']:raise Problem('Vehicle does not have enough capacity.',409)
        kind=con.execute('SELECT storage_type FROM items WHERE id=?',(ex['item_id'],)).fetchone()['storage_type']
        if kind!='ambient' and not vehicle['refrigerated']:
            raise Problem('Cold-chain supplies require a refrigerated vehicle.',409)
        con.execute('''INSERT INTO deliveries(exchange_id,driver_id,vehicle_id) VALUES(?,?,?)
          ON CONFLICT(exchange_id) DO UPDATE SET driver_id=excluded.driver_id,vehicle_id=excluded.vehicle_id''',
          (exchange_id,driver_id,vehicle_id))
        record(con,bank_id,'transport_assigned',f'Transfer #{exchange_id} assigned to {driver["display_name"]}, {vehicle["label"]}.',actor,exchange_id)
        notify(con,ex['destination_bank_id'],f'Transport assigned to transfer #{exchange_id}.',exchange_id)
    return {'message':'Driver and vehicle assigned.'}


def receipt_with_discrepancy(path,bank_id,exchange_id,data,actor=None):
    """Explicit partial receipt, after the original supplier-shipping transition.

    Recipient receives only actual non-damaged units; remaining units are
    documented as missing or damaged and are never added to destination stock.
    """
    received=whole(data.get('received_qty'))
    damaged=whole(data.get('damaged_qty',0))
    note=clean_text(data.get('notes'),'Discrepancy explanation',200)
    with write_db(path) as con:
        ex=con.execute('SELECT * FROM exchanges WHERE id=?',(exchange_id,)).fetchone()
        if not ex or ex['destination_bank_id']!=bank_id:raise Problem('Recipient transfer not found.',404)
        if ex['status']!='in_transit':raise Problem('Transfer must be in transit.',409)
        if received+damaged>ex['quantity']:raise Problem('Received and damaged cannot exceed shipped quantity.',409)
        missing=ex['quantity']-received-damaged
        item_id=ex['item_id']
        if received:
            inv=stock(con,bank_id,item_id)
            if not inv:
                con.execute('INSERT INTO inventory(bank_id,item_id,quantity,target) VALUES(?,?,0,0)',(bank_id,item_id))
            portions=con.execute('''SELECT quantity,expiration_date,lot_code FROM shipment_lots
                 WHERE exchange_id=? ORDER BY expiration_date,lot_id''',(exchange_id,)).fetchall()
            remaining=received
            for part in portions:
                take=min(remaining,part['quantity'])
                if take:
                    add_lot(con,bank_id,item_id,take,part['expiration_date'],'transfer_partial',part['lot_code'],allow_recalled=True)
                    remaining-=take
                if remaining==0:break
            if remaining:
                add_lot(con,bank_id,item_id,remaining,None,'transfer_partial_legacy')
        if damaged or missing:
            con.execute('''INSERT INTO discrepancies(exchange_id,expected_qty,received_qty,missing_qty,damaged_qty,notes)
               VALUES(?,?,?,?,?,?)''',(exchange_id,ex['quantity'],received,missing,damaged,note))
        con.execute("UPDATE exchanges SET status='received',updated_at=? WHERE id=?",(utc_now(),exchange_id))
        con.execute('UPDATE deliveries SET delivered_at=?,proof_note=? WHERE exchange_id=?',(utc_now(),note,exchange_id))
        record(con,bank_id,'partial_receipt',f'Transfer #{exchange_id}: {received} received, {missing} missing, {damaged} damaged.',actor,exchange_id)
        notify(con,ex['source_bank_id'],f'Transfer #{exchange_id} receipt: {received} received; {missing} missing, {damaged} damaged.',exchange_id)
    return {'message':'Receipt confirmed with item count recorded.','received_qty':received,'missing_qty':missing,'damaged_qty':damaged}


def list_discrepancies(path,bank_id):
    with read_db(path) as con:
        return [dict(r) for r in con.execute('''SELECT d.*,ex.source_bank_id,ex.destination_bank_id,
          i.name item_name FROM discrepancies d JOIN exchanges ex ON ex.id=d.exchange_id
          JOIN items i ON i.id=ex.item_id WHERE ex.source_bank_id=? OR ex.destination_bank_id=?
          ORDER BY d.id DESC LIMIT 100''',(bank_id,bank_id))]


def mark_discrepancy(path,bank_id,discrepancy_id,actor=None):
    with write_db(path) as con:
        row=con.execute('''SELECT d.*,ex.source_bank_id,ex.destination_bank_id FROM discrepancies d
            JOIN exchanges ex ON ex.id=d.exchange_id WHERE d.id=?''',(discrepancy_id,)).fetchone()
        if not row or bank_id not in (row['source_bank_id'],row['destination_bank_id']):
            raise Problem('Discrepancy not found.',404)
        if row['status']!='open':raise Problem('Discrepancy already reviewed.',409)
        con.execute("UPDATE discrepancies SET status='reviewed' WHERE id=?",(discrepancy_id,))
        record(con,bank_id,'discrepancy_reviewed',f'Discrepancy #{discrepancy_id} reviewed.',actor,row['exchange_id'])
    return {'message':'Discrepancy marked reviewed; no automatic inventory change.'}


def independent_receipt(path,bank_id,data,actor=None):
    """Commutative offline donation/receipt. Unlike a stock correction, additions
    from distinct physical deliveries safely merge across inventory versions.
    Unique op_key prevents duplicates after retry or reconnection.
    """
    item_id=whole(data.get('item_id'),1);qty=whole(data.get('quantity'),1)
    donor=clean_text(data.get('donor','Offline receipt'),'Donor label',80)
    exp=expiration(data.get('expiration_date'))
    key=data.get('op_key');code=lot_code_value(data.get('lot_code'))
    if not isinstance(key,str) or not KEY.fullmatch(key):raise Problem('Invalid offline operation key.')
    with write_db(path) as con:
        old=con.execute('SELECT * FROM offline_receipts WHERE bank_id=? AND op_key=?',(bank_id,key)).fetchone()
        if old:
            if (old['item_id'],old['quantity'],old['donor'],old['lot_code'],old['expiration_date'])!=(item_id,qty,donor,code,exp):
                raise Problem('Offline receipt key already belongs to a different receipt.',409)
            return {'message':'Offline receipt already synchronized.','duplicate':True,'receipt_id':old['id']}
        inv=stock(con,bank_id,item_id)
        if not inv:raise Problem('Add the inventory item online first.',404)
        lot_id=add_lot(con,bank_id,item_id,qty,exp,'offline_receipt',code)
        con.execute('INSERT INTO donations(bank_id,item_id,donor,quantity,lot_id) VALUES(?,?,?,?,?)',
                    (bank_id,item_id,donor,qty,lot_id))
        rid=con.execute('''INSERT INTO offline_receipts(bank_id,op_key,item_id,quantity,donor,lot_code,expiration_date)
               VALUES(?,?,?,?,?,?,?)''',(bank_id,key,item_id,qty,donor,code,exp)).lastrowid
        record(con,bank_id,'offline_receipt',f'Synced distinct offline receipt: +{qty} {inv["name"]}.',actor)
    return {'message':'Offline receipt safely merged.','duplicate':False,'receipt_id':rid}


def metrics(path,bank_id,global_view=False):
    with read_db(path) as con:
        scope='1=1' if global_view else 'bank_id=?'
        vals=[] if global_view else [bank_id]
        def one(sql,args=()):return con.execute(sql,args).fetchone()[0]
        donation_count=one('SELECT COUNT(*) FROM donations WHERE '+scope,vals)
        donated_units=one('SELECT COALESCE(SUM(quantity),0) FROM donations WHERE '+scope,vals)
        waste_units=one('SELECT COALESCE(SUM(quantity),0) FROM waste WHERE '+scope,vals)
        incoming=one('SELECT COALESCE(SUM(quantity),0) FROM exchanges WHERE status=? AND '+
             ('1=1' if global_view else 'destination_bank_id=?'),['received']+([] if global_view else [bank_id]))
        transfers=one('SELECT COUNT(*) FROM exchanges WHERE status=? AND '+
             ('1=1' if global_view else 'destination_bank_id=?'),['received']+([] if global_view else [bank_id]))
        issues=one('''SELECT COUNT(*) FROM discrepancies d JOIN exchanges ex ON ex.id=d.exchange_id WHERE '''+
            ('1=1' if global_view else 'ex.destination_bank_id=?'),[] if global_view else [bank_id])
        active_recalls=one('SELECT COUNT(*) FROM recalls WHERE status=?',('active',))
        return {'donations':donation_count,'donated_units':donated_units,'wasted_units':waste_units,
            'received_transfer_units_nominal':incoming,'completed_transfers':transfers,
            'discrepancies':issues,'active_recalls_systemwide':active_recalls,
            'note':'Transfer units are requested/shipped quantities. Partial receipts are reported separately; no waste-prevented estimate is claimed.'}
