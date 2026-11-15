import os; os.environ.setdefault('FOOD_BANK_DEMO_MODE', '1')  # classroom demo accounts (see tests/__init__.py)
"""Extra Flask integration coverage. Executes on Windows/CI after pip installing Flask."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

@unittest.skipUnless(importlib.util.find_spec('flask'),'Flask must be installed for HTTP integration tests')
class FinalAPITests(unittest.TestCase):
    def setUp(self):
        from app import create_app
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app=create_app(Path(self.temp.name)/'test.db')
        self.app.testing=True
        self.client=self.app.test_client()

    def token(self,bank=1,user='staff',password='demo123'):
        data=self.client.post('/api/login',json={'bank_id':bank,'username':user,'password':password})
        self.assertEqual(data.status_code,200,data.get_json())
        return {'Authorization':'Bearer '+data.get_json()['token']}

    def test_account_permissions_and_admin_roles(self):
        admin=self.token(1,'admin','admin123')
        result=self.client.post('/api/admin/users',headers=admin,json={
            'bank_id':1,'username':'readonly','password':'viewpass123','role':'viewer'})
        self.assertEqual(result.status_code,201,result.get_json())
        viewer=self.token(1,'readonly','viewpass123')
        self.assertEqual(self.client.get('/api/dashboard',headers=viewer).status_code,200)
        self.assertEqual(self.client.post('/api/donors',headers=viewer,json={'name':'Blocked'}).status_code,403)
        self.assertEqual(self.client.post('/api/reconciliations',headers=viewer,json={'item_id':1,'counted_qty':5,'reason':'Not allowed'}).status_code,403)
        self.assertEqual(self.client.get('/api/events',headers=viewer).status_code,200)

    def test_donors_and_receipts(self):
        h=self.token()
        donor=self.client.post('/api/donors',headers=h,json={'name':'Community pantry sponsor'})
        self.assertEqual(donor.status_code,201)
        donated=self.client.post('/api/donations',headers=h,json={'item_id':1,'quantity':2,
             'donor':'Community pantry sponsor','donor_id':donor.get_json()['donor_id'],
             'lot_code':'BATCH-RECEIPT-1'})
        self.assertEqual(donated.status_code,201,donated.get_json())
        receipt=self.client.get('/api/donations/'+str(donated.get_json()['donation_id'])+'/receipt',headers=h)
        self.assertEqual(receipt.status_code,200)
        self.assertIn(b'BATCH-RECEIPT-1',receipt.data)

    def test_recall_and_capacity(self):
        staff2=self.token(2)
        donated=self.client.post('/api/donations',headers=staff2,json={'item_id':1,'quantity':4,
             'donor':'Test','lot_code':'RECALL-TEST-1'})
        self.assertEqual(donated.status_code,201,donated.get_json())
        admin=self.token(1,'admin','admin123')
        result=self.client.post('/api/admin/recalls',headers=admin,json={'item_id':1,
            'lot_code':'RECALL-TEST-1','reason':'Class test'})
        self.assertEqual(result.status_code,201,result.get_json())
        recalls=self.client.get('/api/recalls',headers=staff2).get_json()['recalls']
        self.assertTrue(any(x['id']==result.get_json()['recall_id'] for x in recalls))
        self.assertEqual(self.client.put('/api/storage/ambient',headers=admin,json={'capacity_units':0}).status_code,409)
        self.assertEqual(self.client.post('/api/admin/recalls/'+str(result.get_json()['recall_id'])+'/close',
            headers=admin,json={}).status_code,200)

    def test_physical_count_requires_manager(self):
        staff=self.token();admin=self.token(1,'admin','admin123')
        count=self.client.post('/api/reconciliations',headers=staff,json={'item_id':2,'counted_qty':40,'reason':'Shelf count'})
        self.assertEqual(count.status_code,201,count.get_json())
        url='/api/reconciliations/'+str(count.get_json()['reconciliation_id'])+'/apply'
        self.assertEqual(self.client.post(url,headers=staff,json={}).status_code,403)
        self.assertEqual(self.client.post(url,headers=admin,json={}).status_code,200)
        self.assertEqual(self.client.post(url,headers=admin,json={}).status_code,409)

    def test_discrepancy_and_delivery_api(self):
        central=self.token();supplier=self.token(2)
        ex=self.client.post('/api/exchanges',headers=central,json={'source_bank_id':2,'item_id':1,
            'quantity':5,'request_key':'partial-delivery-0001'})
        self.assertEqual(ex.status_code,201,ex.get_json())
        eid=ex.get_json()['exchange_id']
        self.assertEqual(self.client.post(f'/api/exchanges/{eid}/approve',headers=supplier).status_code,200)
        d=self.client.post('/api/drivers',headers=supplier,json={'display_name':'Volunteer C'})
        self.assertEqual(d.status_code,403)  # Staff needs manager/dispatcher for fleet changes.
        admin=self.token(1,'admin','admin123')
        # Admin account on Central can set its own bank transport; bank2 needs a manager/dispatcher.
        self.assertEqual(self.client.post(f'/api/exchanges/{eid}/ship',headers=supplier).status_code,200)
        result=self.client.post(f'/api/exchanges/{eid}/receive-detailed',headers=central,json={
            'received_qty':3,'damaged_qty':1,'notes':'Missing one unit'})
        self.assertEqual(result.status_code,200,result.get_json())
        self.assertEqual(result.get_json()['missing_qty'],1)
        self.assertEqual(self.client.post(f'/api/exchanges/{eid}/receive',headers=central).status_code,409)

    def test_test_lab_openapi_and_tracing(self):
        admin=self.token(1,'admin','admin123')
        paths=self.client.get('/api/openapi.json').get_json()['paths']
        self.assertIn('/api/admin/lab/run',paths)
        run=self.client.post('/api/admin/lab/run',headers=admin,json={'scenario':'concurrent'})
        self.assertEqual(run.status_code,200,run.get_json())
        self.assertEqual(run.get_json()['passed'],1)
        events=self.client.get('/api/events',headers=admin).get_json()['events']
        self.assertTrue(any(x['path']=='/api/admin/lab/run' for x in events))

    def test_login_throttle(self):
        for i in range(8):
            failure=self.client.post('/api/login',json={'bank_id':1,'username':'staff','password':'wrongpass'})
            self.assertEqual(failure.status_code,401)
        blocked=self.client.post('/api/login',json={'bank_id':1,'username':'staff','password':'demo123'})
        self.assertEqual(blocked.status_code,429)

if __name__=='__main__':unittest.main()
