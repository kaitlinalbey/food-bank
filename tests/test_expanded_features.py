import os; os.environ.setdefault('FOOD_BANK_DEMO_MODE', '1')  # classroom demo accounts (see tests/__init__.py)
"""New backend features. Runs with standard-library Python, without Flask."""
import csv
import io
import tempfile
import unittest
from datetime import date,timedelta
from pathlib import Path
from database import initialize,read_db
from services.core import create_transfer,change_transfer,Problem
from services.operations import (donation,dispose,lots_for_bank,change_lot_expiry,
    forecast_and_alerts,sync_adjustment,schedule_exchange,export_csv,import_csv,set_barcode)
from offline_queue import save_snapshot,enqueue,all_queue,sync_pending,discard


class ExpandedTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/'demo.db'
        initialize(self.path)

    def tearDown(self):self.tmp.cleanup()

    def qty(self,bank,item):
        with read_db(self.path) as con:
            return con.execute('SELECT quantity FROM inventory WHERE bank_id=? AND item_id=?',(bank,item)).fetchone()[0]

    def version(self,bank,item):
        with read_db(self.path) as con:
            return con.execute('SELECT version FROM inventory WHERE bank_id=? AND item_id=?',(bank,item)).fetchone()[0]

    def test_donation_adds_batch_and_stock(self):
        later=(date.today()+timedelta(days=4)).isoformat()
        result=donation(self.path,1,{'item_id':1,'quantity':6,'donor':'Example fundraiser','expiration_date':later})
        self.assertEqual(self.qty(1,1),11)
        lots=lots_for_bank(self.path,1)
        self.assertTrue(any(l['id']==result['lot_id'] and l['expiration_date']==later for l in lots))
        with read_db(self.path) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM donations').fetchone()[0],1)

    def test_waste_removes_batches_not_reserved_stock(self):
        result=dispose(self.path,2,{'item_id':1,'quantity':2,'reason':'Damaged'})
        self.assertEqual(result['quantity'],2)
        self.assertEqual(self.qty(2,1),38)
        with read_db(self.path) as con:
            self.assertEqual(con.execute('SELECT SUM(quantity) FROM waste').fetchone()[0],2)
        with self.assertRaises(Problem):
            dispose(self.path,2,{'item_id':1,'quantity':1000,'reason':'Damaged'})

    def test_waste_rejects_reserved_and_rolls_back(self):
        ex,_=create_transfer(self.path,1,{'source_bank_id':2,'item_id':1,
            'quantity':12,'request_key':'reserved-test-999'})
        change_transfer(self.path,2,ex,'approve')
        with self.assertRaises(Problem):
            dispose(self.path,2,{'item_id':1,'quantity':35,'reason':'Damaged'})
        self.assertEqual(self.qty(2,1),40)

    def test_expiration_alert_and_expired_lot_rejected(self):
        soon=(date.today()+timedelta(days=2)).isoformat()
        row=donation(self.path,1,{'item_id':1,'quantity':4,'donor':'Demo','expiration_date':soon})
        with read_db(self.path) as con:
            forecasts,alerts=forecast_and_alerts(con,1)
            self.assertTrue(any(a.get('lot_id')==row['lot_id'] for a in alerts))
        yesterday=(date.today()-timedelta(days=1)).isoformat()
        change_lot_expiry(self.path,1,row['lot_id'],{'expiration_date':yesterday})
        with read_db(self.path) as con:
            _,alerts=forecast_and_alerts(con,1)
            self.assertTrue(any(a.get('lot_id')==row['lot_id'] and a['type']=='expired' for a in alerts))

    def test_transfer_preserves_lot_expiration(self):
        soon=(date.today()+timedelta(days=2)).isoformat()
        donation(self.path,2,{'item_id':1,'quantity':6,'donor':'Demo','expiration_date':soon})
        ex,_=create_transfer(self.path,1,{'source_bank_id':2,'item_id':1,
            'quantity':5,'request_key':'lot-shipment-999'})
        change_transfer(self.path,2,ex,'approve')
        change_transfer(self.path,2,ex,'ship')
        change_transfer(self.path,1,ex,'receive')
        self.assertEqual(self.qty(1,1),10)
        self.assertTrue(any(l['item_id']==1 and l['expiration_date']==soon and l['quantity']==5
                            for l in lots_for_bank(self.path,1)))

    def test_forecast_tracks_stock_outflow(self):
        dispose(self.path,1,{'item_id':2,'quantity':5,'reason':'Spoiled'})
        with read_db(self.path) as con:
            rows,_=forecast_and_alerts(con,1)
        beans=next(x for x in rows if x['item_id']==2)
        self.assertEqual(beans['used_last_30_days'],5)
        self.assertGreater(beans['days_remaining'],0)

    def test_offline_version_conflict_and_once_only(self):
        base=self.version(1,1)
        payload={'item_id':1,'delta':2,'base_version':base,'op_key':'offline-demo-0001'}
        result=sync_adjustment(self.path,1,payload)
        self.assertFalse(result['duplicate'])
        self.assertEqual(self.qty(1,1),7)
        self.assertTrue(sync_adjustment(self.path,1,payload)['duplicate'])
        self.assertEqual(self.qty(1,1),7)
        with self.assertRaises(Problem) as ctx:
            sync_adjustment(self.path,1,{'item_id':1,'delta':3,'base_version':base,
                                         'op_key':'offline-demo-0002'})
        self.assertEqual(ctx.exception.status,409)
        self.assertIn('Conflict',str(ctx.exception))

    def test_offline_queue_replay_and_conflict(self):
        queue=Path(self.tmp.name)/'node.db'
        save_snapshot(queue,{'bank':{'id':1,'name':'Demo'},'inventory':[
            {'item_id':1,'item_name':'Rice','quantity':5,'version':0}]})
        enqueue(queue,1,3)
        with self.assertRaises(ValueError):enqueue(queue,1,4)
        r=sync_pending(queue,lambda method,path,data:sync_adjustment(self.path,1,data))
        self.assertEqual(r['synced'],1)
        self.assertEqual(self.qty(1,1),8)
        # Intentional stale cache becomes a conflict rather than overwriting stock.
        enqueue(queue,1,2)
        r=sync_pending(queue,lambda method,path,data:sync_adjustment(self.path,1,data))
        self.assertEqual(r['conflicts'],1)
        self.assertEqual(all_queue(queue)[0]['status'],'conflict')
        self.assertEqual(discard(queue,all_queue(queue)[0]['id']),1)

    def test_csv_roundtrip_with_unchanged_existing_stock(self):
        text=export_csv(self.path,1)
        self.assertIn('Rice',text)
        result=import_csv(self.path,1,text)
        self.assertEqual(result['items'],8)
        self.assertEqual(self.qty(1,1),5)

    def test_csv_add_and_atomic_rollback(self):
        valid='name,unit,category,quantity,target,expiration_date,barcode\nOatmeal,bags,Dry Goods,7,12,,OAT-001\n'
        self.assertEqual(import_csv(self.path,1,valid)['items'],1)
        with read_db(self.path) as con:
            self.assertEqual(con.execute("SELECT barcode FROM items WHERE name='Oatmeal'").fetchone()[0],'OAT-001')
        invalid='name,unit,category,quantity,target\nNew Food,bags,Other,2,3\nRice,cans,Other,2,3\n'
        with self.assertRaises(Problem):import_csv(self.path,1,invalid)
        with read_db(self.path) as con:
            self.assertFalse(con.execute("SELECT 1 FROM items WHERE name='New Food'").fetchone())

    def test_csv_does_not_override_reserved_stock(self):
        ex,_=create_transfer(self.path,1,{'source_bank_id':2,'item_id':1,'quantity':7,
            'request_key':'import-reserve-000'})
        change_transfer(self.path,2,ex,'approve')
        csv_text='name,unit,category,quantity,target\nRice,bags,Dry Goods,2,1\n'
        with self.assertRaises(Problem):import_csv(self.path,2,csv_text)
        self.assertEqual(self.qty(2,1),40)

    def test_barcode_validation_unique(self):
        set_barcode(self.path,1,'RICE-001',1,1)
        with read_db(self.path) as con:
            self.assertEqual(con.execute('SELECT barcode FROM items WHERE id=1').fetchone()[0],'RICE-001')
        with self.assertRaises(Problem) as ctx:set_barcode(self.path,2,'RICE-001',1,1)
        self.assertEqual(ctx.exception.status,409)
        with self.assertRaises(Problem):set_barcode(self.path,2,'a',1,1)

    def test_transfer_schedule_requires_supplier_and_approval(self):
        ex,_=create_transfer(self.path,1,{'source_bank_id':2,'item_id':1,'quantity':5,
            'request_key':'schedule-ex-0001'})
        data={'pickup_at':'2026-11-12T10:00:00+00:00',
              'delivery_at':'2026-11-12T15:00:00+00:00','carrier_notes':'Volunteer van'}
        with self.assertRaises(Problem):schedule_exchange(self.path,2,ex,data)
        change_transfer(self.path,2,ex,'approve')
        with self.assertRaises(Problem) as ctx:schedule_exchange(self.path,1,ex,data)
        self.assertEqual(ctx.exception.status,403)
        schedule_exchange(self.path,2,ex,data)
        with read_db(self.path) as con:
            self.assertEqual(con.execute('SELECT carrier_notes FROM exchanges WHERE id=?',(ex,)).fetchone()[0],'Volunteer van')
        with self.assertRaises(Problem):schedule_exchange(self.path,2,ex,{
            'pickup_at':'2026-11-12T15:00+00:00','delivery_at':'2026-11-12T10:00+00:00'})

    def test_old_schema_has_lots_after_upgrade(self):
        initialize(self.path)
        with read_db(self.path) as con:
            inv=con.execute('SELECT COALESCE(SUM(quantity),0) FROM inventory').fetchone()[0]
            lots=con.execute('SELECT COALESCE(SUM(quantity),0) FROM lots').fetchone()[0]
            self.assertEqual(inv,lots)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM donations').fetchone()[0],0)


if __name__=='__main__':unittest.main()
