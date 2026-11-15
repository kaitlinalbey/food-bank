import os; os.environ.setdefault('FOOD_BANK_DEMO_MODE', '1')  # classroom demo accounts (see tests/__init__.py)
"""Permission and request tracing regression checks (require Flask on tester's PC)."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

@unittest.skipUnless(importlib.util.find_spec('flask'),'Flask must be installed to verify HTTP behavior')
class StabilizationAPITests(unittest.TestCase):
    def setUp(self):
        from app import create_app
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.app=create_app(Path(self.tmp.name)/'release.db')
        self.app.testing=True;self.client=self.app.test_client()

    def token(self,bank=1,user='staff',pw='demo123'):
        response=self.client.post('/api/login',json={'bank_id':bank,'username':user,'password':pw})
        self.assertEqual(response.status_code,200,response.get_json())
        return {'Authorization':'Bearer '+response.get_json()['token']}

    def test_status_requires_admin(self):
        url='/api/admin/system/status'
        self.assertEqual(self.client.get(url).status_code,401)
        self.assertEqual(self.client.get(url,headers=self.token()).status_code,403)
        import unittest.mock
        with unittest.mock.patch('routes.stabilization._node_status',side_effect=lambda row:{
            'bank_id':row['id'],'bank_name':row['name'],'online':True,'port':5000+row['id']}):
            result=self.client.get(url,headers=self.token(1,'admin','admin123'))
        self.assertEqual(result.status_code,200,result.get_json())
        self.assertTrue(result.get_json()['database']['healthy'])

    def test_trace_permissions_and_bad_ids(self):
        trace='test-trace-bank2-123'
        supplier=self.token(2)
        self.client.get('/api/dashboard',headers={**supplier,'X-Request-ID':trace})
        anonymous=self.client.get('/api/traces/'+trace)
        self.assertEqual(anonymous.status_code,401)
        central=self.client.get('/api/traces/'+trace,headers=self.token(1))
        self.assertEqual(central.status_code,200)
        self.assertEqual(central.get_json()['count'],0)
        owner=self.client.get('/api/traces/'+trace,headers=supplier)
        self.assertEqual(owner.status_code,200)
        self.assertGreaterEqual(owner.get_json()['count'],1)
        bad=self.client.get('/api/traces/%%%',headers=supplier)
        self.assertEqual(bad.status_code,400)

    def test_no_request_body_or_token_in_traces(self):
        staff=self.token()
        self.client.post('/api/donors',headers={**staff,'X-Request-ID':'test-trace-safe-123'},
                         json={'name':'fictional-private-token-test'})
        admin=self.token(1,'admin','admin123')
        trace=self.client.get('/api/traces/test-trace-safe-123',headers=admin)
        self.assertEqual(trace.status_code,200)
        self.assertNotIn('fictional-private-token-test',trace.get_data(as_text=True))
        self.assertNotIn(staff['Authorization'][7:],trace.get_data(as_text=True))

if __name__=='__main__':unittest.main()
