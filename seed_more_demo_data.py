"""Optional, repeatable enrichment of fictional Food Bank Database demo data.

The initial eight catalog products are intentionally unchanged. Existing stock rows,
account credentials, donation history, and transfers are never replaced or reset.
Use --apply explicitly when enriching an existing database. Fresh demo launchers
call enrich_demo() only for brand-new, isolated classroom databases.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path
import sqlite3

from database import DEFAULT_DB, initialize, read_db, write_db
from services.lots import add_lot
from services.records import record
from services.transfers import create_transfer, change_transfer

# name, unit, category, storage, days until expiry (None means no expiry),
# (Central, Northside, Westside) initial quantity, (Central, Northside, Westside) target
# All names, quantities, donors, banks and dates in this file are FICTIONAL.
SAMPLE_PRODUCTS = [
    ('Oatmeal','canisters','Dry Goods','ambient',360,(5,38,15),(24,14,18)),
    ('Lentils','bags','Dry Goods','ambient',420,(34,7,16),(12,22,14)),
    ('Brown Rice','bags','Dry Goods','ambient',450,(6,21,38),(20,18,12)),
    ('Dry Black Beans','bags','Dry Goods','ambient',540,(42,8,12),(15,25,16)),
    ('All-Purpose Flour','bags','Baking','ambient',240,(9,28,11),(20,10,18)),
    ('Granulated Sugar','bags','Baking','ambient',540,(28,6,22),(10,16,12)),
    ('Cooking Oil','bottles','Pantry','ambient',280,(7,25,19),(18,10,12)),
    ('Macaroni and Cheese','boxes','Dry Goods','ambient',420,(25,4,30),(12,16,14)),
    ('Instant Mashed Potatoes','boxes','Dry Goods','ambient',300,(10,18,33),(18,12,11)),
    ('Whole Wheat Bread','loaves','Bakery','ambient',7,(8,18,5),(16,10,15)),
    ('Flour Tortillas','packs','Bakery','ambient',20,(25,8,11),(12,18,12)),
    ('Canned Tuna','cans','Protein','ambient',650,(4,17,42),(22,14,14)),
    ('Canned Chicken','cans','Protein','ambient',680,(28,9,18),(11,20,14)),
    ('Canned Tomatoes','cans','Canned Goods','ambient',600,(39,7,13),(15,20,16)),
    ('Canned Corn','cans','Canned Goods','ambient',550,(50,5,18),(18,20,13)),
    ('Canned Peas','cans','Canned Goods','ambient',600,(13,35,7),(20,12,18)),
    ('Canned Fruit','cans','Canned Goods','ambient',480,(5,31,16),(17,11,18)),
    ('Tomato Sauce','jars','Canned Goods','ambient',430,(29,8,18),(12,18,14)),
    ('Granola Bars','boxes','Snacks','ambient',220,(7,30,12),(18,10,18)),
    ('Saltine Crackers','boxes','Snacks','ambient',240,(25,5,23),(12,19,12)),
    ('Shelf-Stable Milk','cartons','Beverages','ambient',230,(30,7,19),(12,18,11)),
    ('Baby Formula','cans','Infant Supplies','ambient',300,(8,17,28),(16,14,12)),
    ('Baby Food Puree','jars','Infant Supplies','ambient',300,(22,6,18),(10,19,11)),
    ('Diapers','packs','Hygiene','ambient',None,(6,25,12),(20,12,17)),
    ('Toothpaste','tubes','Hygiene','ambient',None,(26,6,13),(11,20,13)),
    ('Bar Soap','bars','Hygiene','ambient',None,(33,15,5),(14,11,18)),
    ('Dish Soap','bottles','Hygiene','ambient',None,(8,22,15),(19,10,13)),
    ('Applesauce','jars','Pantry','ambient',390,(19,6,29),(10,18,12)),
    ('Fresh Carrots','bags','Produce','refrigerated',18,(13,24,6),(19,11,20)),
    ('Potatoes','bags','Produce','ambient',45,(32,7,14),(13,18,17)),
    ('Oranges','bags','Produce','refrigerated',20,(6,20,25),(16,12,11)),
    ('Bananas','bunches','Produce','ambient',6,(8,18,5),(15,10,16)),
    ('Eggs','cartons','Dairy & Eggs','refrigerated',27,(18,6,29),(12,17,12)),
    ('Whole Milk','gallons','Dairy & Eggs','refrigerated',12,(9,3,25),(16,13,12)),
    ('Cheddar Cheese','packs','Dairy & Eggs','refrigerated',30,(5,26,15),(18,11,17)),
    ('Plain Yogurt','cups','Dairy & Eggs','refrigerated',16,(21,7,28),(12,17,13)),
    ('Frozen Vegetables','bags','Frozen','frozen',170,(12,3,40),(18,20,15)),
    ('Frozen Chicken','packs','Frozen','frozen',110,(23,8,24),(12,19,14)),
    ('Frozen Fish','packs','Frozen','frozen',140,(4,21,29),(17,12,15)),
    ('Frozen Berries','bags','Frozen','frozen',180,(18,9,25),(12,18,12)),
]

# All pairs are newly created sample inventory rows, so existing inventory is not
# touched when an older, user-edited database is enriched on purpose.
SAMPLE_DONATIONS = [
    (1, 'Oatmeal', 4, 'Neighborhood Food Drive'),
    (1, 'Canned Tuna', 5, 'Local Grocery Partner'),
    (1, 'Whole Wheat Bread', 3, 'Bakery Partner'),
    (2, 'Canned Tomatoes', 6, 'Community Food Drive'),
    (2, 'Frozen Vegetables', 3, 'Local Grocery Partner'),
    (2, 'Diapers', 4, 'Family Donation Drive'),
    (3, 'Canned Tuna', 4, 'Local Grocery Partner'),
    (3, 'Oranges', 5, 'Community Food Drive'),
    (3, 'Eggs', 2, 'Neighborhood Food Drive'),
]

SAMPLE_TRANSFERS = [
    # Source, destination, item, quantity, final stage
    (2, 1, 'Oatmeal', 6, 'approved'),
    (3, 1, 'Canned Tuna', 6, 'received'),
    (1, 2, 'Canned Corn', 7, 'in_transit'),
    (3, 2, 'Frozen Vegetables', 5, 'pending'),
]


def enrich_demo(path):
    """Add missing fictional examples only. Does not change existing inventory rows.

    Returns counts of newly inserted records. Safe to run again (no duplicates).
    """
    path = Path(path)
    initialize(path, demo_accounts=True)  # explicit classroom demo seeding
    newly_added = set()
    item_ids = {}
    added_products = 0
    added_inventory = 0
    added_lots = 0
    added_donations = 0
    added_donors = 0
    with write_db(path) as con:
        bank_ids = {r['id'] for r in con.execute('SELECT id FROM banks')}
        for name, unit, category, storage, expires_in, stock, targets in SAMPLE_PRODUCTS:
            row = con.execute('SELECT id,unit,category FROM items WHERE name=? COLLATE NOCASE',(name,)).fetchone()
            if row is None:
                item_id = con.execute('INSERT INTO items(name,unit,category,storage_type) VALUES(?,?,?,?)',
                                      (name,unit,category,storage)).lastrowid
                added_products += 1
            elif row['unit'].lower() != unit.lower() or row['category'].lower() != category.lower():
                # Do not overwrite an existing item which happens to use the same name.
                continue
            else:
                item_id = row['id']
            item_ids[name] = item_id
            exp = (date.today() + timedelta(days=expires_in)).isoformat() if expires_in else None
            for bank_id, (quantity, target) in enumerate(zip(stock,targets),start=1):
                if bank_id not in bank_ids: continue
                already = con.execute('SELECT 1 FROM inventory WHERE bank_id=? AND item_id=?',
                                      (bank_id,item_id)).fetchone()
                if already: continue
                con.execute('INSERT INTO inventory(bank_id,item_id,quantity,target) VALUES(?,?,0,?)',
                            (bank_id,item_id,target))
                newly_added.add((bank_id,item_id))
                added_inventory += 1
                if quantity:
                    # Different lots support the expiration/traceability demonstration.
                    chunks = (quantity//2, quantity-quantity//2) if quantity >= 20 else (quantity,)
                    for n, chunk in enumerate(chunks,1):
                        if chunk <= 0:continue
                        lot_exp = ((date.today() + timedelta(days=expires_in + (n-1)*5)).isoformat()
                                   if expires_in else None)
                        add_lot(con,bank_id,item_id,chunk,lot_exp,'fictional_demo_seed',
                                f'DEMO-B{bank_id}-I{item_id}-L{n}')
                        added_lots += 1
        for bank_id, name, quantity, donor in SAMPLE_DONATIONS:
            item_id = item_ids.get(name)
            if not item_id or (bank_id,item_id) not in newly_added:continue
            con.execute('INSERT OR IGNORE INTO donors(bank_id,name,notes) VALUES(?,?,?)',
                        (bank_id,donor,'Fictional demonstration organization'))
            donor_row = con.execute('SELECT id FROM donors WHERE bank_id=? AND name=?',(bank_id,donor)).fetchone()
            if not donor_row:continue
            # Deterministic, idempotent key for fictional donations.
            code=f'DEMO-DON-B{bank_id}-I{item_id}'
            if con.execute('SELECT 1 FROM lots WHERE bank_id=? AND item_id=? AND lot_code=?',
                           (bank_id,item_id,code)).fetchone():continue
            shelf_days = next(row[4] for row in SAMPLE_PRODUCTS if row[0]==name)
            exp=(date.today()+timedelta(days=shelf_days)).isoformat() if shelf_days else None
            lot_id=add_lot(con,bank_id,item_id,quantity,exp,'fictional_demo_donation',code)
            con.execute('INSERT INTO donations(bank_id,item_id,donor,quantity,lot_id,donor_id) VALUES(?,?,?,?,?,?)',
                        (bank_id,item_id,donor,quantity,lot_id,donor_row['id']))
            record(con,bank_id,'demo_donation',f'Fictional sample: +{quantity} {name} from {donor}.')
            added_donations += 1
            added_lots += 1
        added_donors = con.execute("SELECT COUNT(*) FROM donors WHERE notes='Fictional demonstration organization'").fetchone()[0]
    new_transfers = 0
    for source,dest,name,quantity,status in SAMPLE_TRANSFERS:
        item_id = item_ids.get(name)
        if not item_id or (source,item_id) not in newly_added or (dest,item_id) not in newly_added:
            continue
        key=f'fictional-sample-v2-{source}-{dest}-{item_id}'
        transfer_id, created = create_transfer(path,dest,{'source_bank_id':source,
            'item_id':item_id,'quantity':quantity,'request_key':key})
        if not created: continue
        new_transfers += 1
        if status in ('approved','in_transit','received'):
            change_transfer(path,source,transfer_id,'approve')
        if status in ('in_transit','received'):
            change_transfer(path,source,transfer_id,'ship')
        if status == 'received':
            change_transfer(path,dest,transfer_id,'receive')
    with read_db(path) as con:
        counts = {key:con.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                  for key,table in [('banks','banks'),('items','items'),('inventory_rows','inventory'),
                                    ('lots','lots'),('donations','donations'),('transfers','exchanges')]}
    return {'new_products':added_products,'new_inventory_rows':added_inventory,
            'new_lots':added_lots,'new_donations':added_donations,
            'sample_donors_total':added_donors,'new_transfers':new_transfers,
            **counts}


def _backup(path):
    if not path.exists() or path.stat().st_size == 0:
        return None
    backup_dir=path.parent/'backups_before_demo_enrichment'
    backup_dir.mkdir(exist_ok=True,parents=True)
    target=backup_dir/('food_bank_before_sample_fill_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'.db')
    with closing(sqlite3.connect(path)) as source,closing(sqlite3.connect(target)) as destination:
        source.backup(destination)
    return target


def main():
    parser=argparse.ArgumentParser(description='Add optional fictional inventory to a local demo database.')
    parser.add_argument('--db',default=str(DEFAULT_DB),help='SQLite database path')
    parser.add_argument('--apply',action='store_true',help='Back up and add missing fictional records')
    args=parser.parse_args()
    path=Path(args.db).resolve()
    if not args.apply:
        print('PREVIEW ONLY — no data changed.')
        print('Database:',path)
        print(f'Adds up to {len(SAMPLE_PRODUCTS)} fictional food products, {len(SAMPLE_PRODUCTS)*3} inventory rows,')
        print('plus several example donations and transfers. Existing inventory rows remain untouched.')
        print('To back up your database and fill missing examples:')
        print(f'  python seed_more_demo_data.py --db "{path}" --apply')
        return
    backup=_backup(path)
    print('Original database backup:',backup or '(database did not exist)')
    result=enrich_demo(path)
    for key,value in result.items():print(f'{key}: {value}')
    print('Sample additions are idempotent; re-running does not add duplicates.')


if __name__=='__main__':
    main()
