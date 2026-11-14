"""Stateful, transaction-safe food transfers."""
from database import write_db,utc_now
from .inventory import stock,need,available
from .records import record,notify
from .validation import Problem,whole
from .lots import consume_lots,add_lot,usable_qty

def create_transfer(path,destination_id,payload,actor=None):
    source_id=whole(payload.get('source_bank_id'),1)
    item_id=whole(payload.get('item_id'),1)
    qty=whole(payload.get('quantity'),1)
    key=payload.get('request_key')
    if not isinstance(key,str) or not 8<=len(key)<=100 or not all(c.isalnum() or c in '-_' for c in key):
        raise Problem('A unique request_key (8-100 letters/numbers/dashes) is required.')
    if destination_id==source_id:
        raise Problem('You cannot request from your own bank.')
    with write_db(path) as con:
        old=con.execute('SELECT * FROM exchanges WHERE destination_bank_id=? AND request_key=?',
                        (destination_id,key)).fetchone()
        if old:
            if (old['source_bank_id'],old['item_id'],old['quantity'])!=(source_id,item_id,qty):
                raise Problem('Request key is already associated with a different request.',409)
            return old['id'],False
        if not stock(con,destination_id,item_id) or not stock(con,source_id,item_id):
            raise Problem('Inventory item or food bank not found.',404)
        if qty>need(con,destination_id,item_id) or qty>available(con,source_id,item_id):
            raise Problem('Quantity exceeds the current shortage or unreserved surplus.',409)
        ex_id=con.execute('''INSERT INTO exchanges
          (source_bank_id,destination_bank_id,item_id,quantity,status,request_key,created_at,updated_at)
          VALUES(?,?,?,?,'pending',?,?,?)''',
          (source_id,destination_id,item_id,qty,key,utc_now(),utc_now())).lastrowid
        name=stock(con,source_id,item_id)['name']
        record(con,destination_id,'transfer_requested',f'Requested {qty} {name} from bank #{source_id}.',actor,ex_id)
        notify(con,source_id,f'New request #{ex_id}: {qty} {name} needed by bank #{destination_id}.',ex_id)
    return ex_id,True


TRANSITIONS={
    'approve':('pending','approved','source'),
    'decline':('pending','declined','source'),
    'ship':('approved','in_transit','source'),
    'receive':('in_transit','received','destination'),
    'cancel':(('pending','approved'),'canceled','destination'),
}


def change_transfer(path,bank_id,exchange_id,action,actor=None):
    if action not in TRANSITIONS:
        raise Problem('Unknown transfer action.',404)
    old_states,new_state,side=TRANSITIONS[action]
    if isinstance(old_states,str):
        old_states=(old_states,)
    with write_db(path) as con:
        ex=con.execute('SELECT * FROM exchanges WHERE id=?',(exchange_id,)).fetchone()
        if not ex:
            raise Problem('Transfer not found.',404)
        owner=ex['source_bank_id'] if side=='source' else ex['destination_bank_id']
        if bank_id!=owner:
            raise Problem('Only the appropriate food bank can perform this action.',403)
        if ex['status'] not in old_states:
            raise Problem(f'Cannot {action} a transfer with status {ex["status"]}.',409)
        source,destination,item,qty=(ex[k] for k in ('source_bank_id','destination_bank_id','item_id','quantity'))
        row=stock(con,source,item)
        name=row['name'] if row else f'item #{item}'
        if action=='approve':
            if qty>available(con,source,item) or qty>need(con,destination,item):
                raise Problem('Not enough unreserved surplus or outstanding shortage to approve.',409)
        if action=='ship':
            if not row or qty>usable_qty(con,source,item):
                raise Problem('Supplier has insufficient unexpired stock to ship.',409)
            portions=consume_lots(con,source,item,qty,'shipping',unexpired_only=True)
            for portion in portions:
                con.execute('''INSERT INTO shipment_lots(exchange_id,expiration_date,quantity,lot_id,lot_code) VALUES(?,?,?,?,?)''',
                    (exchange_id,portion['expiration_date'],portion['quantity'],portion['lot_id'],portion['lot_code']))
            record(con,source,'stock_shipped',f'Shipped {qty} {name} to bank #{destination}.',actor,exchange_id)
        if action=='ship':
            con.execute('UPDATE deliveries SET picked_up_at=? WHERE exchange_id=?',(utc_now(),exchange_id))
        if action=='receive':
            if not stock(con,destination,item):
                # Allows receiving an item that was taken off the inventory list.
                con.execute('INSERT INTO inventory(bank_id,item_id,quantity,target) VALUES(?,?,0,0)',
                            (destination,item))
            portions=con.execute('SELECT expiration_date,quantity,lot_code FROM shipment_lots WHERE exchange_id=?',
                                (exchange_id,)).fetchall()
            if portions:
                for portion in portions:
                    add_lot(con,destination,item,portion['quantity'],portion['expiration_date'],'transfer_received',portion['lot_code'],allow_recalled=True)
            else:
                # Handles in-progress exchanges created by the pre-lot version.
                add_lot(con,destination,item,qty,None,'transfer_received')
            record(con,destination,'stock_received',f'Received {qty} {name} from bank #{source}.',actor,exchange_id)
        if action=='receive':
            con.execute('UPDATE deliveries SET delivered_at=? WHERE exchange_id=?',(utc_now(),exchange_id))
        con.execute('UPDATE exchanges SET status=?,updated_at=? WHERE id=?',(new_state,utc_now(),exchange_id))
        record(con,bank_id,'transfer_'+new_state,f'Transfer #{exchange_id}: {new_state} ({qty} {name}).',actor,exchange_id)
        other=destination if bank_id==source else source
        notify(con,other,f'Transfer #{exchange_id} for {qty} {name}: {new_state}.',exchange_id)
    return {'message':f'Transfer {new_state.replace("_"," ")}.','status':new_state}


def exchange_list(con,bank_id):
    rows=con.execute('''SELECT ex.id,ex.source_bank_id,ex.destination_bank_id,ex.item_id,ex.quantity,
        ex.status,ex.pickup_at,ex.delivery_at,ex.carrier_notes,ex.created_at,ex.updated_at,items.name AS item_name,items.unit,
        s.name AS source_bank_name,d.name AS destination_bank_name
        FROM exchanges ex JOIN banks s ON s.id=ex.source_bank_id
        JOIN banks d ON d.id=ex.destination_bank_id JOIN items ON items.id=ex.item_id
        WHERE ex.source_bank_id=? OR ex.destination_bank_id=? ORDER BY ex.id DESC LIMIT 100''',
        (bank_id,bank_id)).fetchall()
    return [dict(x) for x in rows]


