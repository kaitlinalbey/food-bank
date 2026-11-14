import os; os.environ.setdefault('FOOD_BANK_DEMO_MODE', '1')  # classroom demo accounts (see tests/__init__.py)
"""HTTP/API/UI integration tests. Requires Flask; run on Windows after installation."""
import importlib.util
import re
import tempfile
import threading
import unittest
from pathlib import Path

FLASK=importlib.util.find_spec('flask') is not None

@unittest.skipUnless(FLASK,'Flask required for HTTP tests')
class ExtendedAPITests(unittest.TestCase):
    def setUp(self):
        from app import create_app
        self.temp=tempfile.TemporaryDirectory()
        self.db=Path(self.temp.name)/'test.db'
        self.app=create_app(self.db)
        self.app.testing=True
        self.client=self.app.test_client()
        self.node_port=None

    def tearDown(self):self.temp.cleanup()

    def auth(self,bank=1,user='staff',password='demo123'):
        resp=self.client.post('/api/login',json={'bank_id':bank,'username':user,'password':password})
        self.assertEqual(resp.status_code,200,resp.get_json())
        return {'Authorization':'Bearer '+resp.json['token']}

    def test_donation_expiry_waste_forecast_and_audit(self):
        headers=self.auth()
        donated=self.client.post('/api/donations',headers=headers,json={'item_id':1,
            'quantity':7,'expiration_date':'2026-12-05','donor':'Demo contributor'})
        self.assertEqual(donated.status_code,201,donated.json)
        lot=donated.json['lot_id']
        self.assertEqual(self.client.get('/api/lots',headers=headers).status_code,200)
        self.assertEqual(self.client.patch(f'/api/lots/{lot}',headers=headers,json={
            'expiration_date':'2026-12-01'}).status_code,200)
        self.assertEqual(self.client.post('/api/waste',headers=headers,json={
            'item_id':1,'lot_id':lot,'quantity':2,'reason':'Damaged'}).status_code,201)
        self.assertEqual(self.client.get('/api/forecast',headers=headers).status_code,200)
        self.assertEqual(self.client.get('/api/waste',headers=headers).json['waste'][0]['quantity'],2)
        self.assertEqual(self.client.get('/api/donations',headers=headers).json['donations'][0]['quantity'],7)
        self.assertEqual(self.client.get('/api/admin/audit',headers=headers).status_code,403)
        admin=self.auth(user='admin',password='admin123')
        self.assertGreater(len(self.client.get('/api/admin/audit',headers=admin).json['failed_operations']),0)

    def test_csv_export_import_scope_and_atomicity(self):
        one,two=self.auth(1),self.auth(2)
        exported=self.client.get('/api/inventory/export.csv',headers=one)
        self.assertEqual(exported.status_code,200)
        self.assertIn(b'Rice',exported.data)
        before=self.client.get('/api/dashboard',headers=two).json['bank']['id']
        self.assertEqual(before,2)
        self.assertEqual(self.client.post('/api/inventory/import.csv',headers=one,
            data=exported.data,content_type='text/csv').status_code,200)
        invalid=b'name,unit,category,quantity,target\nNew Item,bags,Other,2,5\nRice,bags,Invalid,3,4\n'
        self.assertEqual(self.client.post('/api/inventory/import.csv',headers=one,
            data=invalid,content_type='text/csv').status_code,409)
        self.assertFalse(any(r['item_name']=='New Item' for r in
            self.client.get('/api/dashboard',headers=one).json['inventory']))

    def test_barcode_and_offline_api_locking(self):
        staff=self.auth()
        admin=self.auth(user='admin',password='admin123')
        self.assertEqual(self.client.put('/api/admin/items/1/barcode',headers=staff,
            json={'barcode':'RICE-ABC-123'}).status_code,403)
        self.assertEqual(self.client.put('/api/admin/items/1/barcode',headers=admin,
            json={'barcode':'RICE-ABC-123'}).status_code,200)
        self.assertEqual(self.client.get('/api/barcodes/RICE-ABC-123',headers=staff).json['item']['id'],1)
        dashboard=self.client.get('/api/dashboard',headers=staff).json
        rice=next(r for r in dashboard['inventory'] if r['item_id']==1)
        payload={'delta':2,'base_version':rice['version'],'op_key':'offline-api-000001'}
        first=self.client.post('/api/inventory/1/adjust',headers=staff,json=payload)
        self.assertEqual(first.status_code,200,first.json)
        self.assertFalse(first.json['duplicate'])
        self.assertTrue(self.client.post('/api/inventory/1/adjust',headers=staff,json=payload).json['duplicate'])
        self.assertEqual(self.client.post('/api/inventory/1/adjust',headers=staff,
            json={'delta':4,'base_version':rice['version'],'op_key':'offline-api-000002'}).status_code,409)

    def test_schedule_and_openapi(self):
        self.assertEqual(self.client.get('/docs').status_code,200)
        spec=self.client.get('/api/openapi.json')
        self.assertEqual(spec.status_code,200)
        self.assertIn('/api/donations',spec.json['paths'])
        one,two=self.auth(1),self.auth(2)
        ex=self.client.post('/api/exchanges',headers=one,json={'source_bank_id':2,
            'item_id':1,'quantity':5,'request_key':'schedule-api-0001'}).json['exchange_id']
        self.assertEqual(self.client.post(f'/api/exchanges/{ex}/approve',headers=two).status_code,200)
        schedule={'pickup_at':'2026-12-02T12:00:00Z','delivery_at':'2026-12-02T15:00:00Z',
                  'carrier_notes':'Demo truck'}
        self.assertEqual(self.client.put(f'/api/exchanges/{ex}/schedule',headers=one,json=schedule).status_code,403)
        self.assertEqual(self.client.put(f'/api/exchanges/{ex}/schedule',headers=two,json=schedule).status_code,200)

    def test_node_offline_queue_simulation_and_sync(self):
        from werkzeug.serving import make_server
        from node import create_node_app
        central=make_server('127.0.0.1',0,self.app,threaded=True)
        thread=threading.Thread(target=central.serve_forever,daemon=True);thread.start()
        try:
            node=create_node_app(1,f'http://127.0.0.1:{central.server_port}',
                                 Path(self.temp.name)/'local_queue.db')
            node.testing=True
            client=node.test_client()
            login=client.get('/login').get_data(as_text=True)
            csrf=re.search(r'name="_csrf" value="([^"]+)"',login).group(1)
            # Outage simulation is an administrator-only classroom demo control.
            response=client.post('/login',data={'_csrf':csrf,'username':'admin','password':'admin123'},
                                 follow_redirects=True)
            self.assertEqual(response.status_code,200)
            self.assertIn('Donations and food waste',response.get_data(as_text=True))
            # Login deliberately rotates the session (and CSRF token); use the new one.
            csrf=re.search(r'name="_csrf" value="([^"]+)"',response.get_data(as_text=True)).group(1)
            response=client.post('/offline/simulate',data={'_csrf':csrf},follow_redirects=True)
            self.assertEqual(response.status_code,503)
            self.assertIn('Bank node is offline',response.get_data(as_text=True))
            response=client.post('/offline/queue',data={'_csrf':csrf,'item_id':1,'delta':2},follow_redirects=True)
            self.assertEqual(response.status_code,503)
            self.assertIn('Queued changes',response.get_data(as_text=True))
            client.post('/offline/simulate',data={'_csrf':csrf})
            response=client.post('/offline/sync',data={'_csrf':csrf},follow_redirects=True)
            self.assertEqual(response.status_code,200)
            self.assertIn('Synchronized 1 changes',response.get_data(as_text=True))
            rice=next(i for i in self.client.get('/api/dashboard',headers=self.auth()).json['inventory'] if i['item_id']==1)
            self.assertEqual(rice['quantity'],7)
        finally:
            central.shutdown();thread.join(timeout=3)

if __name__=='__main__':unittest.main()
