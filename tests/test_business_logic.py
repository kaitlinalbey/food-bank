import os; os.environ.setdefault('FOOD_BANK_DEMO_MODE', '1')  # classroom demo accounts (see tests/__init__.py)
"""Runs without Flask. `python -m unittest discover -s tests -v`."""
import concurrent.futures
import sqlite3
import tempfile
import unittest
from pathlib import Path
from datetime import date,timedelta

from database import initialize,read_db,verify_password,hash_password
from services.core import (
    Problem, add_item,update_item,delete_item,create_transfer,change_transfer,
    dashboard,mark_notifications,whole,expiration,haversine
)


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/'test.db'
        initialize(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def request_rice(self,quantity=8,key='request-1234',src=2,dst=1):
        return create_transfer(self.path,dst,{'source_bank_id':src,'item_id':1,
                                             'quantity':quantity,'request_key':key})

    def rice_qty(self,bank):
        with read_db(self.path) as con:
            return con.execute('SELECT quantity FROM inventory WHERE bank_id=? AND item_id=1',(bank,)).fetchone()[0]

    def status(self,id):
        with read_db(self.path) as con:
            return con.execute('SELECT status FROM exchanges WHERE id=?',(id,)).fetchone()[0]

    def test_initialized_data_and_passwords(self):
        with read_db(self.path) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM banks').fetchone()[0],3)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM inventory').fetchone()[0],24)
            staff=con.execute("SELECT password_hash FROM users WHERE username='staff' AND bank_id=1").fetchone()[0]
            self.assertTrue(verify_password(staff,'demo123'))
            self.assertFalse(verify_password(staff,'wrong'))
        self.assertTrue(verify_password(hash_password('password123'),'password123'))

    def test_inventory_add_update_delete_and_logging(self):
        id=add_item(self.path,1,{'name':'Oatmeal','category':'Dry Goods','unit':'bags','quantity':10,'target':2})
        update_item(self.path,1,id,{'quantity':0,'target':2})
        delete_item(self.path,1,id)
        with read_db(self.path) as con:
            self.assertFalse(con.execute('SELECT 1 FROM inventory WHERE bank_id=1 AND item_id=?',(id,)).fetchone())
            self.assertEqual(con.execute("SELECT COUNT(*) FROM activity WHERE bank_id=1").fetchone()[0],3)

    def test_item_validation(self):
        with self.assertRaises(Problem):
            add_item(self.path,1,{'name':'  ','unit':'bags','category':'Dry Goods'})
        with self.assertRaises(Problem):
            update_item(self.path,1,1,{'quantity':-1,'target':1})
        with self.assertRaises(Problem):
            update_item(self.path,1,1,{'quantity':1.5,'target':2})
        with self.assertRaises(Problem):
            delete_item(self.path,1,1)  # quantity is still 5
        with self.assertRaises(Problem):
            add_item(self.path,1,{'name':'Rice','category':'Dry Goods','unit':'bags'})
        with self.assertRaises(Problem):
            whole(True)
        with self.assertRaises(Problem):
            expiration('2026-99-99')

    def test_full_lifecycle_moves_stock_only_at_handoffs(self):
        ex,created=self.request_rice(10)
        self.assertTrue(created)
        self.assertEqual(self.rice_qty(1),5)
        self.assertEqual(self.rice_qty(2),40)
        change_transfer(self.path,2,ex,'approve')
        self.assertEqual(dashboard(self.path,2)['inventory'][0]['item_name'],'Apples')
        with read_db(self.path) as con:
            reserved=con.execute('SELECT SUM(quantity) FROM exchanges WHERE status="approved"').fetchone()[0]
            self.assertEqual(reserved,10)
        self.assertEqual(self.rice_qty(2),40)  # reserve, not ship
        change_transfer(self.path,2,ex,'ship')
        self.assertEqual(self.rice_qty(2),30)
        self.assertEqual(self.rice_qty(1),5)
        change_transfer(self.path,1,ex,'receive')
        self.assertEqual(self.status(ex),'received')
        self.assertEqual(self.rice_qty(1),15)
        with self.assertRaises(Problem):
            change_transfer(self.path,1,ex,'receive')
        self.assertEqual(self.rice_qty(1),15)

    def test_approval_reserves_no_double_spend(self):
        one,_=self.request_rice(10,'request-one')
        two,_=self.request_rice(10,'request-two')
        change_transfer(self.path,2,one,'approve')
        # Northside: 40 qty - 12 target - 10 reserved = 18;
        # Central: 20 target - 5 qty - 10 approved = 5 remaining.
        with self.assertRaises(Problem):
            change_transfer(self.path,2,two,'approve')
        with self.assertRaises(Problem):
            update_item(self.path,2,1,{'quantity':9,'target':12})
        self.assertEqual(self.status(two),'pending')

    def test_cancel_before_shipping_releases_reservation(self):
        ex,_=self.request_rice(8)
        change_transfer(self.path,2,ex,'approve')
        change_transfer(self.path,1,ex,'cancel')
        self.assertEqual(self.status(ex),'canceled')
        self.assertEqual(self.rice_qty(2),40)
        self.assertEqual(dashboard(self.path,2)['inventory'][-1]['item_name'],'Rice')
        another,_=self.request_rice(9,'new-key-567')
        self.assertGreater(another,ex)
        with self.assertRaises(Problem):
            change_transfer(self.path,1,ex,'receive')

    def test_decline_unauthorized_and_invalid_transitions(self):
        ex,_=self.request_rice(8)
        with self.assertRaises(Problem) as ctx:
            change_transfer(self.path,3,ex,'approve')
        self.assertEqual(ctx.exception.status,403)
        change_transfer(self.path,2,ex,'decline')
        self.assertEqual(self.status(ex),'declined')
        with self.assertRaises(Problem):
            change_transfer(self.path,2,ex,'ship')

    def test_idempotent_request(self):
        ex,created=self.request_rice(5)
        again,created2=self.request_rice(5)
        self.assertEqual(ex,again)
        self.assertFalse(created2)
        with self.assertRaises(Problem):
            self.request_rice(7)
        with read_db(self.path) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM exchanges').fetchone()[0],1)

    def test_concurrent_approval_only_one_accepted(self):
        a,_=self.request_rice(12,'parallel-a')
        b,_=self.request_rice(12,'parallel-b')
        def approve(ex):
            try:
                change_transfer(self.path,2,ex,'approve')
                return True
            except Problem:
                return False
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(approve,[a,b]))
        self.assertEqual(results.count(True),1)
        self.assertEqual(results.count(False),1)

    def test_expired_surplus_not_matched(self):
        before=dashboard(self.path,1)['matches']
        self.assertTrue(any(x['source_bank_id']==2 and x['item_id']==1 for x in before))
        expired=(date.today()-timedelta(days=1)).isoformat()
        update_item(self.path,2,1,{'quantity':40,'target':12,'expiration_date':expired})
        after=dashboard(self.path,1)['matches']
        self.assertFalse(any(x['source_bank_id']==2 and x['item_id']==1 for x in after))
        with self.assertRaises(Problem):
            self.request_rice(3)

    def test_notifications_history_mark_read_and_metrics(self):
        ex,_=self.request_rice(5)
        result=dashboard(self.path,2)
        self.assertEqual(result['pending_count'],1)
        self.assertEqual(result['unread_count'],1)
        self.assertEqual(mark_notifications(self.path,2),1)
        self.assertEqual(dashboard(self.path,2)['unread_count'],0)
        change_transfer(self.path,2,ex,'approve')
        change_transfer(self.path,2,ex,'ship')
        change_transfer(self.path,1,ex,'receive')
        self.assertEqual(dashboard(self.path,1)['stats']['completed_transfers'],1)
        self.assertGreater(len(dashboard(self.path,1)['activity']),1)

    def test_original_old_database_migrates_without_losing_data(self):
        old=Path(self.temp.name)/'old.db'
        with sqlite3.connect(old) as con:
            con.executescript('''
            CREATE TABLE banks(id INTEGER PRIMARY KEY,name TEXT NOT NULL,area TEXT NOT NULL,
              latitude REAL NOT NULL,longitude REAL NOT NULL,password_hash TEXT NOT NULL);
            CREATE TABLE items(id INTEGER PRIMARY KEY,name TEXT NOT NULL UNIQUE,unit TEXT NOT NULL);
            CREATE TABLE inventory(bank_id INTEGER NOT NULL,item_id INTEGER NOT NULL,
              quantity INTEGER NOT NULL,target INTEGER NOT NULL,PRIMARY KEY(bank_id,item_id));
            CREATE TABLE exchanges(id INTEGER PRIMARY KEY AUTOINCREMENT,
              source_bank_id INTEGER NOT NULL,destination_bank_id INTEGER NOT NULL,
              item_id INTEGER NOT NULL,quantity INTEGER NOT NULL,status TEXT NOT NULL
              CHECK(status IN ('pending','accepted','declined')),
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            ''')
            con.execute('INSERT INTO banks VALUES(1,"Old Central","Downtown",34.7,-92.2,"oldhash")')
            con.execute('INSERT INTO banks VALUES(2,"Old North","North",34.8,-92.2,"oldhash")')
            con.execute('INSERT INTO items VALUES(1,"Rice","bags")')
            con.execute('INSERT INTO inventory VALUES(1,1,9,20)')
            con.execute('INSERT INTO inventory VALUES(2,1,30,10)')
            con.execute('INSERT INTO exchanges(source_bank_id,destination_bank_id,item_id,quantity,status) VALUES(2,1,1,4,"accepted")')
        con.close()  # sqlite3's "with" only commits; close so Windows can delete the temp dir
        initialize(old)
        with read_db(old) as con:
            self.assertEqual(con.execute('SELECT quantity FROM inventory WHERE bank_id=1').fetchone()[0],9)
            self.assertEqual(con.execute('SELECT status FROM exchanges').fetchone()[0],'received')
            self.assertEqual(con.execute('SELECT COUNT(*) FROM users').fetchone()[0],3)
        initialize(old)
        with read_db(old) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM exchanges').fetchone()[0],1)


if __name__=='__main__':
    unittest.main()
