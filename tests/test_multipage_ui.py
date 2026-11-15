import os; os.environ.setdefault('FOOD_BANK_DEMO_MODE', '1')  # classroom demo accounts (see tests/__init__.py)
"""Regression tests for the multi-page interface.

A real central API runs in a background thread on a temporary database; the node is driven with
Flask's test client, so every page, form redirect and role check goes through the real code.
"""
import importlib.util
from pathlib import Path
import re
import tempfile
import threading
import unittest

FLASK_PRESENT = importlib.util.find_spec('flask') is not None
PAGES = {'/': 'Dashboard', '/my-inventory': 'My inventory', '/other-banks': 'Other food banks',
         '/matching': 'Supply matching', '/transfers': 'Transfers', '/donations': 'Donations and waste',
         '/batches': 'Batches, expiration and recalls', '/transport': 'Transportation',
         '/offline': 'Offline synchronization', '/project-status': 'System status', '/more': 'More tools'}


@unittest.skipUnless(FLASK_PRESENT, 'Flask required')
class MultiPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app import create_app
        from seed_more_demo_data import enrich_demo
        from werkzeug.serving import make_server
        cls.temp = tempfile.TemporaryDirectory()
        cls.db = str(Path(cls.temp.name) / 'pages.db')
        enrich_demo(cls.db)
        cls.api_app = create_app(cls.db)
        cls.server = make_server('127.0.0.1', 0, cls.api_app, threaded=True)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.api_url = f'http://127.0.0.1:{cls.server.server_port}'
        api = cls.api_app.test_client()
        admin = {'Authorization': 'Bearer ' + api.post('/api/login', json={'bank_id': 1, 'username': 'admin', 'password': 'admin123'}).get_json()['token']}
        for name, role in (('p10-viewer', 'viewer'), ('p10-driver', 'driver'), ('p10-dispatcher', 'dispatcher'), ('p10-manager', 'manager')):
            api.post('/api/admin/users', headers=admin, json={'bank_id': 1, 'username': name, 'password': 'demo-pass-123', 'role': role})

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join(timeout=5)
        cls.temp.cleanup()

    def node(self, user='staff', password='demo123', bank=1):
        from node import create_node_app
        app = create_node_app(bank, self.api_url, queue_path=Path(self.temp.name) / f'q_{user}_{bank}.db')
        self.addCleanup(lambda: None)
        client = app.test_client()
        page = client.get('/login').get_data(as_text=True)
        csrf = re.search(r'name="_csrf" value="([^"]+)"', page).group(1)
        response = client.post('/login', data={'_csrf': csrf, 'username': user, 'password': password})
        self.assertEqual(response.status_code, 302, f'login failed for {user}')
        client.app_ref = app
        return client

    def csrf(self, client):
        return re.search(r'name="_csrf" value="([^"]+)"', client.get('/project-status').get_data(as_text=True)).group(1)

    def post(self, client, path, data):
        return client.post(path, data=dict(data, _csrf=self.csrf(client)))

    # ---------------------------------------------------------------- pages and navigation
    def test_every_page_loads_for_staff_with_heading_and_active_link(self):
        c = self.node()
        for path, heading in PAGES.items():
            with self.subTest(path=path):
                r = c.get(path)
                html = r.get_data(as_text=True)
                self.assertEqual(r.status_code, 200)
                self.assertIn(f'<h1>{heading}</h1>', html)
                self.assertIn('class="active" aria-current="page"', html)
                self.assertIn('>Log out</button>', html)

    def test_every_page_loads_for_read_only_roles(self):
        for user in ('p10-viewer', 'p10-driver', 'p10-dispatcher', 'p10-manager'):
            c = self.node(user, 'demo-pass-123')
            for path in PAGES:
                with self.subTest(user=user, path=path):
                    self.assertEqual(c.get(path).status_code, 200)
            self.assertEqual(c.get('/admin').status_code, 403)

    def test_administration_link_only_for_admin(self):
        self.assertNotIn('>Administration</a>', self.node().get('/').get_data(as_text=True))
        admin = self.node('admin', 'admin123')
        self.assertIn('>Administration</a>', admin.get('/').get_data(as_text=True))
        page = admin.get('/admin').get_data(as_text=True)
        for section in ('overview', 'banks', 'users', 'health', 'backups', 'demo', 'audit'):
            self.assertIn(f'<section id="{section}">', page)

    def test_pages_require_login(self):
        from node import create_node_app
        c = create_node_app(1, self.api_url, queue_path=Path(self.temp.name) / 'anon.db').test_client()
        for path in list(PAGES) + ['/admin', '/advanced']:
            with self.subTest(path=path):
                r = c.get(path)
                self.assertEqual(r.status_code, 302)
                self.assertTrue(r.headers['Location'].endswith('/login'))

    # ---------------------------------------------------------------- role-based controls
    def test_read_only_roles_see_no_change_controls(self):
        markers = {'/my-inventory?inventory=edit': 'name="quantity"', '/matching': 'Request</button>',
                   '/donations': 'Record donation', '/offline': 'Save adjustment locally',
                   '/batches': 'Update batch expiry', '/more': 'Submit count', '/transport': 'Save schedule'}
        for user in ('p10-viewer', 'p10-driver'):
            c = self.node(user, 'demo-pass-123')
            for path, marker in markers.items():
                with self.subTest(user=user, path=path):
                    self.assertNotIn(marker, c.get(path).get_data(as_text=True))
        staff = self.node()
        self.assertIn('Request</button>', staff.get('/matching').get_data(as_text=True))
        self.assertIn('name="quantity"', staff.get('/my-inventory?inventory=edit').get_data(as_text=True))
        dispatcher = self.node('p10-dispatcher', 'demo-pass-123')
        self.assertIn('Request</button>', dispatcher.get('/matching').get_data(as_text=True))
        self.assertNotIn('name="quantity"', dispatcher.get('/my-inventory?inventory=edit').get_data(as_text=True))
        self.assertIn('Add driver', dispatcher.get('/transport').get_data(as_text=True))

    def test_hidden_controls_are_still_refused_by_the_server(self):
        viewer = self.node('p10-viewer', 'demo-pass-123')
        r = self.post(viewer, '/donation', {'item_id': 1, 'quantity': 99, 'donor': 'x'})
        self.assertEqual(r.status_code, 302)
        page = viewer.get(r.headers['Location']).get_data(as_text=True)
        self.assertIn('not permitted for role viewer', page)
        r = viewer.post('/donation', data={'item_id': 1, 'quantity': 99, 'donor': 'x'})  # no CSRF token
        self.assertEqual(r.status_code, 400)

    def test_test_lab_only_for_admin(self):
        self.assertNotIn('id="lab"', self.node().get('/more').get_data(as_text=True))
        self.assertIn('Run isolated tests', self.node('admin', 'admin123').get('/more').get_data(as_text=True))

    # ---------------------------------------------------------------- inventory pages
    def test_add_item_form_has_a_single_category_field(self):
        html = self.node().get('/my-inventory?inventory=edit').get_data(as_text=True)
        add_form = html.split('<h2>Add item</h2>', 1)[1].split('</form>', 1)[0]
        self.assertEqual(add_form.count('name="category"'), 1)
        c = self.node()
        r = self.post(c, '/inventory/add', {'name': 'P10 Granola', 'unit': 'boxes', 'category': 'Breakfast',
                                           'quantity': 2, 'target': 5, 'expiration_date': '', 'q': '', 'status': 'all'})
        self.assertIn('/my-inventory?inventory=edit', r.headers['Location'])
        import sqlite3
        con = sqlite3.connect(self.db)
        try:
            self.assertEqual(con.execute("SELECT category FROM items WHERE name='P10 Granola'").fetchone()[0], 'Breakfast')
        finally:
            con.close()

    def test_inventory_sorting_and_filters(self):
        c = self.node()
        html = c.get('/my-inventory?status=shortage&sort=shortage').get_data(as_text=True)
        section = html.split('<section id="inventory">', 1)[1].split('</section>', 1)[0]
        values = [int(re.findall(r'<td>(-?\d+)</td>', row)[5]) for row in re.findall(r'<tr>(.*?)</tr>', section, re.S)
                  if len(re.findall(r'<td>(-?\d+)</td>', row)) >= 6]
        self.assertTrue(values)
        self.assertEqual(values, sorted(values, reverse=True))
        self.assertIn('Shortage &#9660;', section)
        html = c.get('/my-inventory?sort=not-a-column').get_data(as_text=True)
        self.assertIn('sorted by name', html)

    def test_other_banks_page_is_private_and_read_only(self):
        html = self.node().get('/other-banks?other_bank=2').get_data(as_text=True)
        section = html.split('<section id="other-inventories">', 1)[1].split('</section>', 1)[0]
        self.assertIn('Northside Food Pantry', section)
        self.assertNotIn('method="post"', section)
        for private in ('Target', 'Reserved', 'In stock</th>', 'name="quantity"'):
            self.assertNotIn(private, section)
        self.assertNotIn('Central Food Pantry</a>', section, 'our own bank is not listed as an "other" bank')

    # ---------------------------------------------------------------- redirects after actions
    def test_actions_return_to_the_page_they_belong_to(self):
        c = self.node()
        cases = [('/notifications/read', {}, '/#notifications'),
                 ('/offline/queue', {'item_id': 1, 'delta': 1}, '/offline#queue'),
                 ('/offline/discard/999999', {}, '/offline#queue'),
                 ('/waste', {'item_id': 1, 'quantity': 999999, 'reason': 'Damaged'}, '/donations#waste'),
                 ('/advanced/action/count', {'item_id': 1, 'counted_qty': 1, 'reason': 'x'}, '/more#counts'),
                 ('/advanced/action/storage', {'storage_type': 'bogus', 'capacity_units': 1}, '/batches#storage'),
                 ('/lot/0/expiry', {'lot_id_entry': 'x'}, '/batches#batches'),
                 ('/advanced/receipt/sync', {}, '/offline#receipts'),
                 ('/exchange/999999/approve', {}, '/transfers?tab=incoming'),
                 ('/exchange/999999/receive', {}, '/transfers?tab=outgoing')]
        for path, data, target in cases:
            with self.subTest(path=path):
                r = self.post(c, path, data)
                self.assertEqual(r.status_code, 302)
                self.assertTrue(r.headers['Location'].endswith(target), r.headers['Location'])

    # ---------------------------------------------------------------- old links
    def test_old_dashboard_links_are_redirected_safely(self):
        c = self.node()
        r = c.get('/?q=rice&status=shortage&inventory=edit')
        self.assertEqual(r.status_code, 302)
        self.assertIn('/my-inventory?', r.headers['Location'])
        self.assertIn('inventory=edit', r.headers['Location'])
        r = c.get('/?other_bank=2&other_q=beans')
        self.assertIn('/other-banks?', r.headers['Location'])
        self.assertIn('other_bank=2', r.headers['Location'])
        r = c.get('/?other_bank=//evil.example&q=x')
        self.assertTrue(r.headers['Location'].startswith('/my-inventory'), 'only fixed internal pages are used')
        page = c.get('/advanced').get_data(as_text=True)
        self.assertIn('This page has moved', page)
        self.assertIn('/batches#recalls', page)

    # ---------------------------------------------------------------- offline behaviour
    def test_offline_pages_during_simulated_outage(self):
        admin = self.node('admin', 'admin123')
        staff = self.node()
        # Outage state is per node process; use the admin's node app for both users.
        staff_on_same_node = admin.app_ref.test_client()
        page = staff_on_same_node.get('/login').get_data(as_text=True)
        csrf = re.search(r'name="_csrf" value="([^"]+)"', page).group(1)
        staff_on_same_node.post('/login', data={'_csrf': csrf, 'username': 'staff', 'password': 'demo123'})
        r = self.post(admin, '/offline/simulate', {})
        self.assertTrue(r.headers['Location'].endswith('/offline'))
        try:
            r = staff_on_same_node.get('/transfers')
            self.assertEqual(r.status_code, 503)
            self.assertIn('Bank node is offline', r.get_data(as_text=True))
            r = staff_on_same_node.get('/offline')
            self.assertEqual(r.status_code, 503)
            html = r.get_data(as_text=True)
            self.assertIn('Save adjustment locally', html)
            self.assertNotIn('Restore simulated connection', html)
        finally:
            self.post(admin, '/offline/simulate', {})
        self.assertEqual(staff.get('/offline').status_code, 200)


if __name__ == '__main__':
    unittest.main()
