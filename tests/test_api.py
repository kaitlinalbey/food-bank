import os; os.environ.setdefault('FOOD_BANK_DEMO_MODE', '1')  # classroom demo accounts (see tests/__init__.py)
"""Flask integration tests. Auto-skipped if Flask is not installed."""
import importlib.util
from pathlib import Path
import re
import tempfile
import threading
import unittest

FLASK_PRESENT=importlib.util.find_spec('flask') is not None


@unittest.skipUnless(FLASK_PRESENT,'Flask must be installed to run HTTP integration tests')
class APITests(unittest.TestCase):
    def setUp(self):
        from app import create_app
        self.temp=tempfile.TemporaryDirectory()
        self.app=create_app(Path(self.temp.name)/'test.db')
        self.app.config['TESTING']=True
        self.client=self.app.test_client()

    def tearDown(self):
        self.temp.cleanup()

    def login(self,bank=1,user='staff',password='demo123'):
        resp=self.client.post('/api/login',json={'bank_id':bank,'username':user,'password':password})
        self.assertEqual(resp.status_code,200,resp.get_json())
        return {'Authorization':'Bearer '+resp.json['token']}

    def test_auth_and_bank_scope(self):
        self.assertEqual(self.client.get('/api/dashboard').status_code,401)
        self.assertEqual(self.client.post('/api/login',json={
            'bank_id':1,'username':'staff','password':'bad'}).status_code,401)
        a,b=self.login(1),self.login(2)
        self.assertEqual(self.client.get('/api/me',headers=a).json['bank_id'],1)
        self.assertEqual(self.client.put('/api/inventory/1',json={
            'quantity':11,'target':20},headers=a).status_code,200)
        # Central can't change Northside's inventory: update targets bank 1 only.
        self.assertEqual(self.client.get('/api/dashboard',headers=b).json['bank']['id'],2)
        result=self.client.get('/api/banks/2/inventory',headers=a)
        self.assertEqual(result.status_code,200)
        self.assertTrue(all('quantity' not in r for r in result.json['inventory']))
        self.assertEqual(self.client.post('/api/admin/banks',headers=a,json={}).status_code,403)
        self.assertEqual(self.client.post('/api/logout',headers=a).status_code,200)
        self.assertEqual(self.client.get('/api/dashboard',headers=a).status_code,401)

    def test_transfer_flow_stats_activity_and_nodes(self):
        one,two,three=self.login(1),self.login(2),self.login(3)
        payload={'source_bank_id':2,'item_id':1,'quantity':7,'request_key':'api-key-xyz789'}
        created=self.client.post('/api/exchanges',headers=one,json=payload)
        self.assertEqual(created.status_code,201,created.json)
        ex=created.json['exchange_id']
        duplicate=self.client.post('/api/exchanges',headers=one,json=payload)
        self.assertEqual(duplicate.status_code,200)
        self.assertEqual(duplicate.json['exchange_id'],ex)
        self.assertEqual(self.client.post(f'/api/exchanges/{ex}/approve',headers=three).status_code,403)
        self.assertEqual(self.client.post(f'/api/exchanges/{ex}/approve',headers=two).status_code,200)
        self.assertEqual(self.client.post(f'/api/exchanges/{ex}/ship',headers=two).status_code,200)
        self.assertEqual(self.client.post(f'/api/exchanges/{ex}/receive',headers=one).status_code,200)
        self.assertEqual(self.client.post(f'/api/exchanges/{ex}/receive',headers=one).status_code,409)
        self.assertEqual(self.client.get('/api/stats',headers=one).json['completed_transfers'],1)
        self.assertGreater(len(self.client.get('/api/activity',headers=one).json['activity']),0)
        self.assertGreater(len(self.client.get('/api/notifications',headers=one).json['notifications']),0)
        self.assertEqual(self.client.post('/api/notifications/read',headers=one).status_code,200)
        self.assertEqual(len(self.client.get('/api/nodes',headers=one).json['nodes']),3)

    def test_admin_onboarding_and_user_creation(self):
        admin=self.login(1,'admin','admin123')
        response=self.client.post('/api/admin/banks',headers=admin,json={
            'name':'New Demo Pantry','area':'South','latitude':34.7,'longitude':-92.4,
            'staff_password':'a-good-demo-password'})
        self.assertEqual(response.status_code,201,response.json)
        bank_id=response.json['bank_id']
        self.assertEqual(self.login(bank_id,password='a-good-demo-password') is not None,True)
        response=self.client.post('/api/admin/users',headers=admin,json={
            'bank_id':bank_id,'username':'volunteer','password':'secure-demo-password','role':'staff'})
        self.assertEqual(response.status_code,201,response.json)
        self.login(bank_id,'volunteer','secure-demo-password')
        self.assertTrue(any(u['username']=='volunteer' for u in
          self.client.get('/api/admin/users',headers=admin).json['users']))

    def test_frontend_node_post_protection_and_exchange_request(self):
        from werkzeug.serving import make_server
        from node import create_node_app
        server=make_server('127.0.0.1',0,self.app,threaded=True)
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            # Explicit temp queue: never create data/node_1_queue.db in the real project.
            node=create_node_app(1,f'http://127.0.0.1:{server.server_port}',
                                 Path(self.temp.name)/'node_1_queue.db')
            node.config['TESTING']=True
            c=node.test_client()
            login=c.get('/login')
            csrf=re.search(r'name="_csrf" value="([^"]+)"',login.get_data(as_text=True)).group(1)
            self.assertEqual(c.post('/login',data={'username':'staff','password':'demo123'}).status_code,400)
            response=c.post('/login',data={'_csrf':csrf,'username':'staff','password':'demo123'},follow_redirects=True)
            self.assertEqual(response.status_code,200)
            body=response.get_data(as_text=True)
            self.assertIn('My inventory',body)
            self.assertIn('Available supply matches',body)
            csrf=re.search(r'name="_csrf" value="([^"]+)"',body).group(1)
            response=c.post('/request',data={'_csrf':csrf,'source_bank_id':2,'item_id':1,
                               'quantity':5,'request_key':'node-api-req-00001'},follow_redirects=True)
            self.assertEqual(response.status_code,200)
            self.assertIn('Request sent.',response.get_data(as_text=True))
        finally:
            server.shutdown()
            thread.join(timeout=3)


if __name__=='__main__':
    unittest.main()
