"""Inventory operations, stock reservations and item visibility."""
from database import write_db,utc_now
from .validation import Problem,whole,clean_text,expiration,good_date
from .records import record
from .lots import usable_qty,add_lot,set_quantity

def reserved(con, bank_id, item_id):
    return con.execute('''SELECT COALESCE(SUM(quantity),0) AS total FROM exchanges
       WHERE source_bank_id=? AND item_id=? AND status='approved' ''',
       (bank_id,item_id)).fetchone()['total']


def expected_inbound(con, bank_id, item_id):
    return con.execute('''SELECT COALESCE(SUM(quantity),0) AS total FROM exchanges
       WHERE destination_bank_id=? AND item_id=? AND status IN ('approved','in_transit')''',
       (bank_id,item_id)).fetchone()['total']


def stock(con, bank_id, item_id):
    return con.execute('''SELECT inv.*, items.name,items.unit,items.category
      FROM inventory inv JOIN items ON items.id=inv.item_id
      WHERE inv.bank_id=? AND inv.item_id=?''',(bank_id,item_id)).fetchone()


def available(con, bank_id, item_id):
    row = stock(con,bank_id,item_id)
    if not row:
        return 0
    return max(0,usable_qty(con,bank_id,item_id)-row['target']-reserved(con,bank_id,item_id))


def need(con, bank_id, item_id):
    row=stock(con,bank_id,item_id)
    if not row:
        return 0
    # Expired and recalled lots cannot be given out, so they do not count toward the target.
    return max(0,row['target']-usable_qty(con,bank_id,item_id)-expected_inbound(con,bank_id,item_id))


def add_item(path,bank_id,payload,actor=None):
    name=clean_text(payload.get('name'),'Item name')
    unit=clean_text(payload.get('unit','units'),'Unit',30)
    category=clean_text(payload.get('category','Other'),'Category',40)
    qty=whole(payload.get('quantity',0))
    target=whole(payload.get('target',0))
    exp=expiration(payload.get('expiration_date'))
    with write_db(path) as con:
        found=con.execute('SELECT * FROM items WHERE name=? COLLATE NOCASE',(name,)).fetchone()
        if found:
            if (found['unit'].lower(),found['category'].lower())!=(unit.lower(),category.lower()):
                raise Problem(f'Item already exists with unit {found["unit"]} and category {found["category"]}.',409)
            item_id=found['id']
        else:
            item_id=con.execute('INSERT INTO items(name,unit,category) VALUES(?,?,?)',
                                (name,unit,category)).lastrowid
        if stock(con,bank_id,item_id):
            raise Problem('This item is already in your inventory.',409)
        con.execute('''INSERT INTO inventory(bank_id,item_id,quantity,target,expiration_date,updated_at)
          VALUES(?,?,0,?,?,?)''',(bank_id,item_id,target,None,utc_now()))
        if qty:
            add_lot(con,bank_id,item_id,qty,exp,'initial_stock')
        record(con,bank_id,'inventory_added',f'Added {name}: {qty} {unit}, target {target}.',actor)
    return item_id


def update_item(path,bank_id,item_id,payload,actor=None):
    qty=whole(payload.get('quantity'))
    target=whole(payload.get('target'))
    exp=expiration(payload.get('expiration_date'))
    with write_db(path) as con:
        row=stock(con,bank_id,item_id)
        if not row:
            raise Problem('Inventory item not found.',404)
        held=reserved(con,bank_id,item_id)
        if qty < held:
            raise Problem(f'{held} units are reserved for approved transfers. Quantity cannot drop below that.',409)
        con.execute('UPDATE inventory SET target=? WHERE bank_id=? AND item_id=?',
                    (target,bank_id,item_id))
        set_quantity(con,bank_id,item_id,qty,exp)
        if usable_qty(con,bank_id,item_id)<reserved(con,bank_id,item_id):
            raise Problem('Update would consume stock reserved for approved transfers.',409)
        record(con,bank_id,'inventory_updated',
               f'{row["name"]}: {row["quantity"]} -> {qty}, target {row["target"]} -> {target}.',actor)
    return {'message':'Inventory updated.'}


def delete_item(path,bank_id,item_id,actor=None):
    with write_db(path) as con:
        row=stock(con,bank_id,item_id)
        if not row:
            raise Problem('Inventory item not found.',404)
        if row['quantity']:
            raise Problem('Set quantity to zero before removing an item.',409)
        active=con.execute('''SELECT 1 FROM exchanges WHERE item_id=?
          AND (source_bank_id=? OR destination_bank_id=?)
          AND status IN ('pending','approved','in_transit') LIMIT 1''',
          (item_id,bank_id,bank_id)).fetchone()
        if active:
            raise Problem('Resolve open transfers for this item before removing it.',409)
        con.execute('DELETE FROM inventory WHERE bank_id=? AND item_id=?',(bank_id,item_id))
        record(con,bank_id,'inventory_removed',f'Removed {row["name"]} from inventory.',actor)


def inventory_list(con,bank_id):
    rows=con.execute('''SELECT inv.item_id,items.name AS item_name,items.category,items.unit,
          inv.quantity,inv.target,inv.expiration_date,inv.updated_at,inv.version
          FROM inventory inv JOIN items ON items.id=inv.item_id
          WHERE inv.bank_id=? ORDER BY items.name''',(bank_id,)).fetchall()
    result=[]
    for row in rows:
        row=dict(row)
        row['reserved']=reserved(con,bank_id,row['item_id'])
        row['surplus']=available(con,bank_id,row['item_id'])
        row['shortage']=need(con,bank_id,row['item_id'])
        row['expired']=bool(row['quantity']) and usable_qty(con,bank_id,row['item_id'])==0
        row['usable']=usable_qty(con,bank_id,row['item_id'])
        row['lot_count']=con.execute('SELECT COUNT(*) FROM lots WHERE bank_id=? AND item_id=? AND quantity>0',
                                     (bank_id,row['item_id'])).fetchone()[0]
        result.append(row)
    return result


