import os; os.environ.setdefault('FOOD_BANK_DEMO_MODE', '1')  # classroom demo accounts (see tests/__init__.py)
"""Full backend safety and integration-like tests without requiring Flask."""
import tempfile
import unittest
from pathlib import Path
from datetime import date,timedelta
from database import initialize,read_db,write_db
from services.validation import Problem
from services.inventory import update_item,available
from services.operations import donation
from services.transfers import create_transfer,change_transfer
from services.lots import usable_qty
from services.advanced import (
    add_donor,list_donors,receipt_for_donation,issue_recall,close_recall,
    recalls_for_bank,storage_summary,add_zone_capacity,set_item_storage,
    propose_count,decide_count,add_driver,add_vehicle,assign_transport,
    receipt_with_discrepancy,list_discrepancies,mark_discrepancy,
    independent_receipt,metrics)
from offline_queue import save_snapshot,enqueue_receipt,sync_receipts,all_receipts,discard_receipt,log_local_event,local_events
from services.lab import run_lab

class FinalFeatureTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'bank.db'
        initialize(self.path)

    def inventory(self,bank,item):
        with read_db(self.path) as c:
            return c.execute('SELECT * FROM inventory WHERE bank_id=? AND item_id=?',(bank,item)).fetchone()

    def test_donor_directory_receipt_and_bank_scope(self):
        donor_id=add_donor(self.path,1,{'name':'Neighborhood Church','notes':'demo only'})['donor_id']
        donated=donation(self.path,1,{'item_id':1,'quantity':3,'donor':'Neighborhood Church',
                      'lot_code':'CHURCH-001','donor_id':donor_id})
        receipt=receipt_for_donation(self.path,1,donated['donation_id'])
        self.assertEqual(receipt['lot_code'],'CHURCH-001')
        self.assertEqual(list_donors(self.path,1)[0]['total_units'],3)
        with self.assertRaises(Problem):receipt_for_donation(self.path,2,donated['donation_id'])

    def test_recall_quarantines_stock_and_remembers_transfer_batch(self):
        donation(self.path,2,{'item_id':1,'quantity':3,'donor':'Test','lot_code':'TRACE-ABC'})
        before=self.inventory(2,1)['quantity']
        result=issue_recall(self.path,1,{'item_id':1,'lot_code':'TRACE-ABC','reason':'Safety alert'})
        self.assertIn(2,result['affected_banks'])
        with read_db(self.path) as c:
            self.assertEqual(self.inventory(2,1)['quantity'],before)
            self.assertTrue(c.execute("SELECT recalled FROM lots WHERE lot_code='TRACE-ABC'").fetchone()[0])
            self.assertEqual(usable_qty(c,2,1),before-3)
        with self.assertRaises(Problem):donation(self.path,2,{'item_id':1,'quantity':1,'lot_code':'TRACE-ABC'})
        close_recall(self.path,1,result['recall_id'])
        with read_db(self.path) as c:self.assertEqual(usable_qty(c,2,1),before)

    def test_recall_during_transit_quarantines_received_stock(self):
        donation(self.path,2,{'item_id':1,'quantity':25,'donor':'Test','lot_code':'TRACE-TRANSIT'})
        # Use separate new batch by removing older stock to prefer the traceable batch.
        with write_db(self.path) as c:
            c.execute('UPDATE lots SET quantity=0 WHERE bank_id=2 AND item_id=1 AND source="legacy"')
            c.execute('UPDATE inventory SET quantity=25 WHERE bank_id=2 AND item_id=1')
        ex=create_transfer(self.path,1,{'source_bank_id':2,'item_id':1,'quantity':5,'request_key':'transit-abc-001'})[0]
        change_transfer(self.path,2,ex,'approve');change_transfer(self.path,2,ex,'ship')
        issue_recall(self.path,1,{'item_id':1,'lot_code':'TRACE-TRANSIT','reason':'In transit recall'})
        change_transfer(self.path,1,ex,'receive')
        with read_db(self.path) as c:
            recalled=c.execute("SELECT quantity,recalled FROM lots WHERE bank_id=1 AND lot_code='TRACE-TRANSIT'").fetchone()
            self.assertIsNotNone(recalled)
            self.assertEqual((recalled['quantity'],recalled['recalled']),(5,1))

    def test_storage_capacity_blocks_new_and_manual_quantity(self):
        original=self.inventory(1,1)['quantity']
        with read_db(self.path) as c:used=c.execute('SELECT SUM(quantity) FROM lots WHERE bank_id=1').fetchone()[0]
        add_zone_capacity(self.path,1,{'storage_type':'ambient','capacity_units':used})
        with self.assertRaises(Problem):donation(self.path,1,{'item_id':1,'quantity':1,'donor':'Test'})
        with self.assertRaises(Problem):update_item(self.path,1,1,{'quantity':original+1,'target':20,'expiration_date':''})
        self.assertEqual(self.inventory(1,1)['quantity'],original)
        with self.assertRaises(Problem):add_zone_capacity(self.path,1,{'storage_type':'ambient','capacity_units':used-1})

    def test_item_cold_storage_assignment(self):
        set_item_storage(self.path,1,1,'refrigerated')
        with self.assertRaises(Problem):set_item_storage(self.path,1,1,'unknown')
        with read_db(self.path) as c:self.assertEqual(c.execute('SELECT storage_type FROM items WHERE id=1').fetchone()[0],'refrigerated')

    def test_reconciliation_stale_count_rejected(self):
        count=propose_count(self.path,1,{'item_id':2,'counted_qty':35,'reason':'Observed count'})['reconciliation_id']
        donation(self.path,1,{'item_id':2,'quantity':2,'donor':'Test'})
        with self.assertRaises(Problem) as ctx:decide_count(self.path,1,count,'apply')
        self.assertEqual(ctx.exception.status,409)
        count2=propose_count(self.path,1,{'item_id':2,'counted_qty':40,'reason':'Updated count'})['reconciliation_id']
        self.assertEqual(decide_count(self.path,1,count2,'apply')['status'],'applied')
        self.assertEqual(self.inventory(1,2)['quantity'],40)

    def test_reconciliation_approval_is_once_only(self):
        count=propose_count(self.path,1,{'item_id':2,'counted_qty':40,'reason':'Shelf check'})['reconciliation_id']
        decide_count(self.path,1,count,'reject')
        with self.assertRaises(Problem):decide_count(self.path,1,count,'apply')

    def test_delivery_partial_receipt_and_review(self):
        ex=create_transfer(self.path,1,{'source_bank_id':2,'item_id':1,'quantity':5,'request_key':'partial-receipt-01'})[0]
        change_transfer(self.path,2,ex,'approve')
        driver=add_driver(self.path,2,{'display_name':'Test Driver'})['driver_id']
        van=add_vehicle(self.path,2,{'label':'Blue van','capacity_units':10})['vehicle_id']
        assign_transport(self.path,2,ex,{'driver_id':driver,'vehicle_id':van})
        change_transfer(self.path,2,ex,'ship')
        result=receipt_with_discrepancy(self.path,1,ex,{'received_qty':3,'damaged_qty':1,'notes':'One lost, one damaged'})
        self.assertEqual((result['missing_qty'],result['damaged_qty']),(1,1))
        self.assertEqual(self.inventory(1,1)['quantity'],8)
        with self.assertRaises(Problem):receipt_with_discrepancy(self.path,1,ex,{'received_qty':5,'damaged_qty':0,'notes':'Retry'})
        issue=list_discrepancies(self.path,1)[0]
        mark_discrepancy(self.path,1,issue['id'])
        with self.assertRaises(Problem):mark_discrepancy(self.path,1,issue['id'])

    def test_transport_enforces_vehicle_capacity_and_cold_chain(self):
        ex=create_transfer(self.path,1,{'source_bank_id':2,'item_id':1,'quantity':7,'request_key':'cold-safety-001'})[0]
        change_transfer(self.path,2,ex,'approve')
        driver=add_driver(self.path,2,{'display_name':'Volunteer B'})['driver_id']
        van=add_vehicle(self.path,2,{'label':'Small van','capacity_units':5})['vehicle_id']
        with self.assertRaises(Problem):assign_transport(self.path,2,ex,{'driver_id':driver,'vehicle_id':van})
        cold=add_vehicle(self.path,2,{'label':'Refrigerated van','capacity_units':9,'refrigerated':True})['vehicle_id']
        set_item_storage(self.path,1,1,'refrigerated')
        with self.assertRaises(Problem):assign_transport(self.path,2,ex,{'driver_id':driver,'vehicle_id':van})
        assign_transport(self.path,2,ex,{'driver_id':driver,'vehicle_id':cold})

    def test_independent_offline_receipt_dedupe_and_edit_merge(self):
        payload={'item_id':2,'quantity':2,'donor':'Food drive','expiration_date':None,
                 'lot_code':'OFFLINE-LAB','op_key':'offline-op-001'}
        first=independent_receipt(self.path,1,payload)
        self.assertFalse(first['duplicate'])
        self.assertTrue(independent_receipt(self.path,1,payload)['duplicate'])
        self.assertEqual(self.inventory(1,2)['quantity'],44)
        with self.assertRaises(Problem):independent_receipt(self.path,1,{**payload,'quantity':5})

    def test_local_receipt_queue_sync_once_only(self):
        queue=Path(self.temp.name)/'nodequeue.db'
        save_snapshot(queue,{'bank':{'id':1,'name':'Central'},'inventory':[
            {'item_id':2,'item_name':'Beans','quantity':42,'version':0}]})
        enqueue_receipt(queue,2,3,'Test offline donation')
        result=sync_receipts(queue,lambda _m,_r,data:independent_receipt(self.path,1,data))
        self.assertEqual(result['synced'],1)
        self.assertEqual(len(all_receipts(queue)),0)
        self.assertEqual(self.inventory(1,2)['quantity'],45)
        log_local_event(queue,'POST','/api/donations','success','trace-demo-0001',12.5)
        self.assertEqual(local_events(queue)[0]['correlation_id'],'trace-demo-0001')

    def test_analytics_are_grounded_counts(self):
        donation(self.path,1,{'item_id':1,'quantity':5,'donor':'Demo'})
        m=metrics(self.path,1)
        self.assertEqual(m['donated_units'],5)
        self.assertIn('note',m)

    def test_backups_before_final_schema_and_repeat_init(self):
        with read_db(self.path) as c:
            self.assertIn('recalls',{r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")})
        initialize(self.path)
        self.assertEqual(self.inventory(1,1)['quantity'],5)

    def test_recall_scope_and_invalid_lot_code(self):
        with self.assertRaises(Problem):issue_recall(self.path,1,{'item_id':1,'lot_code':'<bad>','reason':'Invalid'})
        self.assertEqual(recalls_for_bank(self.path,1),[])

    def test_lab_subset(self):
        result=run_lab('concurrent')
        self.assertEqual(result['passed'],1)
        self.assertEqual(result['total'],1)

if __name__=='__main__':unittest.main()
