import os; os.environ.setdefault('FOOD_BANK_DEMO_MODE', '1')  # classroom demo accounts (see tests/__init__.py)
"""Security regression tests.

1. Connection-outage simulation: admin only, demo mode only, local requests only, logged,
   and failed requests never change the outage state.
2. Demo accounts: public demo passwords are only created and accepted in explicit demo mode;
   passwords and tokens are stored as hashes; account tools work outside demo mode.
3. Cross-bank access: direct API attempts to read or change another bank's data are refused.
4. Error handling: oversized and unexpected errors return JSON without internal details.
Every test uses its own temporary database.
"""
import importlib.util
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

FLASK_PRESENT = importlib.util.find_spec('flask') is not None


def rows(db, sql, *args):
    con = sqlite3.connect(db)
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


# --------------------------------------------------------------------------- 1. outage simulation
@unittest.skipUnless(FLASK_PRESENT, 'Flask required')
class OutageSimulationTests(unittest.TestCase):
    def setUp(self):
        import node
        self.node = node
        self.temp = tempfile.TemporaryDirectory()
        self.queue = Path(self.temp.name) / 'queue.db'
        self.original = node.api_call

        def offline_api(*_args, **_kwargs):  # the node never reaches a real API in these tests
            raise node.APIError(503, 'Central API is offline or not responding.')
        node.api_call = offline_api

    def tearDown(self):
        self.node.api_call = self.original
        self.temp.cleanup()

    def client(self, role='admin', enabled=True, logged_in=True):
        app = self.node.create_node_app(1, 'http://127.0.0.1:1', queue_path=self.queue, outage_simulation=enabled)
        self.app = app
        client = app.test_client()
        with client.session_transaction() as s:
            s['_csrf'] = 'csrf-ok'
            if logged_in:
                s.update({'token': 'tok', 'bank_id': 1, 'role': role, 'username': role + '-user'})
        return client

    def toggle(self, client, **kw):
        return client.post('/offline/simulate', data={'_csrf': 'csrf-ok'}, **kw)

    def logged(self):
        from offline_queue import local_events
        return [e['result'] for e in local_events(self.queue) if e['path'] == '/offline/simulate']

    def test_admin_can_switch_outage_on_and_off_and_it_is_logged(self):
        c = self.client('admin')
        r = self.toggle(c)
        self.assertEqual(r.status_code, 302)
        self.assertTrue(self.app.config['SIMULATED_API_OFFLINE'])
        self.assertEqual(c.get('/node-health').status_code, 503)
        self.toggle(c)
        self.assertFalse(self.app.config['SIMULATED_API_OFFLINE'])
        self.assertEqual(c.get('/node-health').status_code, 200)
        log = self.logged()
        self.assertTrue(any('set OFFLINE' in x and 'admin-user' in x for x in log), log)
        self.assertTrue(any('set ONLINE' in x for x in log), log)

    def test_non_admin_roles_are_refused_with_403_and_state_unchanged(self):
        for role in ('staff', 'manager', 'dispatcher', 'driver', 'viewer'):
            with self.subTest(role=role):
                c = self.client(role)
                r = self.toggle(c)
                self.assertEqual(r.status_code, 403)
                self.assertFalse(self.app.config['SIMULATED_API_OFFLINE'])
        denied = [x for x in self.logged() if x.startswith('DENIED')]
        self.assertEqual(len(denied), 5, denied)

    def test_non_admin_cannot_restore_an_active_outage_either(self):
        admin = self.client('admin')
        self.toggle(admin)
        staff = self.app.test_client()
        with staff.session_transaction() as s:
            s.update({'_csrf': 'csrf-ok', 'token': 'tok', 'bank_id': 1, 'role': 'staff', 'username': 's'})
        self.assertEqual(self.toggle(staff).status_code, 403)
        self.assertTrue(self.app.config['SIMULATED_API_OFFLINE'])

    def test_not_logged_in_gets_401(self):
        c = self.client(logged_in=False)
        self.assertEqual(self.toggle(c).status_code, 401)
        self.assertFalse(self.app.config['SIMULATED_API_OFFLINE'])

    def test_disabled_outside_demo_mode_even_for_admin(self):
        c = self.client('admin', enabled=False)
        self.assertEqual(self.toggle(c).status_code, 404)
        self.assertFalse(self.app.config['SIMULATED_API_OFFLINE'])
        self.assertTrue(any('disabled' in x for x in self.logged()))

    def test_default_follows_demo_mode_setting(self):
        with mock.patch.dict(os.environ, {'FOOD_BANK_DEMO_MODE': '0'}):
            app = self.node.create_node_app(1, 'http://127.0.0.1:1', queue_path=self.queue)
            self.assertFalse(app.config['OUTAGE_SIMULATION_ENABLED'])
        with mock.patch.dict(os.environ, {'FOOD_BANK_DEMO_MODE': '1'}):
            app = self.node.create_node_app(1, 'http://127.0.0.1:1', queue_path=self.queue)
            self.assertTrue(app.config['OUTAGE_SIMULATION_ENABLED'])
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop('FOOD_BANK_DEMO_MODE', None)
            app = self.node.create_node_app(1, 'http://127.0.0.1:1', queue_path=self.queue)
            self.assertFalse(app.config['OUTAGE_SIMULATION_ENABLED'])
            os.environ['FOOD_BANK_DEMO_MODE'] = '1'  # restore for the rest of the suite

    def test_remote_request_is_refused_even_for_admin(self):
        c = self.client('admin')
        r = self.toggle(c, environ_base={'REMOTE_ADDR': '192.168.1.50'})
        self.assertEqual(r.status_code, 403)
        self.assertFalse(self.app.config['SIMULATED_API_OFFLINE'])
        self.assertTrue(any('192.168.1.50' in x for x in self.logged()))

    def test_missing_csrf_token_changes_nothing(self):
        c = self.client('admin')
        self.assertEqual(c.post('/offline/simulate', data={}).status_code, 400)
        self.assertFalse(self.app.config['SIMULATED_API_OFFLINE'])

    def test_outage_button_is_only_shown_to_admins(self):
        admin = self.client('admin')
        self.toggle(admin)
        page = admin.get('/').get_data(as_text=True)
        self.assertIn('Restore simulated connection', page)
        staff = self.app.test_client()
        with staff.session_transaction() as s:
            s.update({'_csrf': 'csrf-ok', 'token': 'tok', 'bank_id': 1, 'role': 'staff', 'username': 's'})
        page = staff.get('/').get_data(as_text=True)
        self.assertNotIn('Restore simulated connection', page)
        self.assertIn('An administrator can restore the connection', page)

    def test_real_outage_retry_is_a_reload_not_the_simulation_toggle(self):
        c = self.client('staff')
        r = c.get('/')
        page = r.get_data(as_text=True)
        self.assertEqual(r.status_code, 503)
        self.assertIn('Retry central API connection', page)
        self.assertNotIn('action="/offline/simulate"', page)
        self.assertFalse(self.app.config['SIMULATED_API_OFFLINE'])


# --------------------------------------------------------------------------- 2. demo accounts
class DemoAccountTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.temp.name) / 'accounts.db')

    def tearDown(self):
        self.temp.cleanup()

    def test_no_accounts_are_created_outside_demo_mode(self):
        from database import initialize
        with mock.patch.dict(os.environ, {'FOOD_BANK_DEMO_MODE': '0'}):
            initialize(self.db)
        self.assertEqual(rows(self.db, 'SELECT COUNT(*) FROM users')[0][0], 0)
        self.assertEqual(rows(self.db, 'SELECT COUNT(*) FROM banks')[0][0], 3)

    def test_admin_is_not_silently_added_to_an_existing_database(self):
        from database import initialize
        initialize(self.db, demo_accounts=True)
        con = sqlite3.connect(self.db)
        with con:
            con.execute("DELETE FROM users WHERE role='admin'")
        con.close()
        with mock.patch.dict(os.environ, {'FOOD_BANK_DEMO_MODE': '0'}):
            initialize(self.db)
        self.assertEqual(rows(self.db, "SELECT COUNT(*) FROM users WHERE role='admin'")[0][0], 0)

    def test_demo_mode_creates_classroom_accounts(self):
        from database import initialize
        with mock.patch.dict(os.environ, {'FOOD_BANK_DEMO_MODE': '1'}):
            initialize(self.db)
        names = sorted(r[0] for r in rows(self.db, 'SELECT username FROM users'))
        self.assertEqual(names, ['admin', 'staff', 'staff', 'staff'])

    def test_passwords_are_salted_pbkdf2_hashes(self):
        from database import initialize
        initialize(self.db, demo_accounts=True)
        hashes = [r[0] for r in rows(self.db, 'SELECT password_hash FROM users')]
        self.assertTrue(all(h.startswith('pbkdf2:') for h in hashes))
        self.assertFalse(any('demo123' in h or 'admin123' in h for h in hashes))
        self.assertEqual(len(set(hashes)), len(hashes), 'each hash has its own salt')

    def test_account_tool_creates_admin_and_resets_password(self):
        from database import initialize, verify_password
        import manage_accounts
        with mock.patch.dict(os.environ, {'FOOD_BANK_DEMO_MODE': '0'}):
            initialize(self.db)
            print_ = manage_accounts.create_admin(self.db, 1, 'coordinator', 'long-secret-pass-1')
            self.assertIn('coordinator', print_)
            stored = rows(self.db, "SELECT password_hash,role FROM users WHERE username='coordinator'")[0]
            self.assertEqual(stored[1], 'admin')
            self.assertTrue(verify_password(stored[0], 'long-secret-pass-1'))
            manage_accounts.set_password(self.db, 1, 'coordinator', 'another-secret-pass')
            stored = rows(self.db, "SELECT password_hash FROM users WHERE username='coordinator'")[0][0]
            self.assertTrue(verify_password(stored, 'another-secret-pass'))
        answers = iter(['admin123', 'admin123'])
        with self.assertRaises(SystemExit):
            manage_accounts.ask_password(lambda _p: next(answers))
        answers = iter(['short', 'short'])
        with self.assertRaises(SystemExit):
            manage_accounts.ask_password(lambda _p: next(answers))


@unittest.skipUnless(FLASK_PRESENT, 'Flask required')
class DemoLoginTests(unittest.TestCase):
    def setUp(self):
        from app import create_app
        self.temp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.temp.name) / 'login.db')
        from database import initialize
        initialize(self.db, demo_accounts=True)  # an older database that still has the demo accounts
        self.client = create_app(self.db).test_client()

    def tearDown(self):
        self.temp.cleanup()

    def login(self, user='staff', pw='demo123', bank=1):
        return self.client.post('/api/login', json={'bank_id': bank, 'username': user, 'password': pw})

    def test_demo_password_is_refused_outside_demo_mode_and_audited(self):
        with mock.patch.dict(os.environ, {'FOOD_BANK_DEMO_MODE': '0'}):
            r = self.login()
            self.assertEqual(r.status_code, 403)
            self.assertNotIn('token', r.get_json())
            self.assertEqual(self.login('admin', 'admin123').status_code, 403)
        self.assertEqual(rows(self.db, 'SELECT COUNT(*) FROM auth_tokens')[0][0], 0)
        audited = rows(self.db, "SELECT detail FROM audit_events WHERE operation='POST /api/login' AND result='failed'")
        self.assertTrue(any('demo password' in d[0] for d in audited), audited)

    def test_wrong_password_still_gives_the_normal_401(self):
        with mock.patch.dict(os.environ, {'FOOD_BANK_DEMO_MODE': '0'}):
            self.assertEqual(self.login(pw='not-the-password').status_code, 401)

    def test_demo_login_still_works_in_demo_mode(self):
        with mock.patch.dict(os.environ, {'FOOD_BANK_DEMO_MODE': '1'}):
            self.assertEqual(self.login().status_code, 200)
            self.assertEqual(self.login('admin', 'admin123').status_code, 200)

    def test_new_password_works_outside_demo_mode_and_old_sessions_end(self):
        import manage_accounts
        with mock.patch.dict(os.environ, {'FOOD_BANK_DEMO_MODE': '1'}):
            old = self.login('admin', 'admin123').get_json()['token']
        manage_accounts.set_password(self.db, 1, 'admin', 'classroom-admin-2026')
        self.assertEqual(self.client.get('/api/me', headers={'Authorization': 'Bearer ' + old}).status_code, 401)
        with mock.patch.dict(os.environ, {'FOOD_BANK_DEMO_MODE': '0'}):
            r = self.login('admin', 'classroom-admin-2026')
            self.assertEqual(r.status_code, 200)
            token = r.get_json()['token']
        stored = [x[0] for x in rows(self.db, 'SELECT token_hash FROM auth_tokens')]
        self.assertNotIn(token, stored, 'tokens are stored only as hashes')

    def test_admin_cannot_set_demo_password_outside_demo_mode(self):
        import manage_accounts
        manage_accounts.set_password(self.db, 1, 'admin', 'classroom-admin-2026')
        with mock.patch.dict(os.environ, {'FOOD_BANK_DEMO_MODE': '0'}):
            token = self.login('admin', 'classroom-admin-2026').get_json()['token']
            h = {'Authorization': 'Bearer ' + token}
            r = self.client.post('/api/admin/users', headers=h,
                                 json={'bank_id': 1, 'username': 'pat', 'password': 'admin123', 'role': 'staff'})
            self.assertEqual(r.status_code, 400)
            users = self.client.get('/api/admin/users', headers=h).get_json()['users']
        self.assertFalse(any('password' in key for u in users for key in u))

    def test_rendered_pages_never_contain_the_api_token(self):
        import node
        secret = 'SECRET-API-TOKEN-p8-0123456789'
        original = node.api_call
        node.api_call = lambda *a, **k: (_ for _ in ()).throw(node.APIError(503, 'offline'))
        try:
            with tempfile.TemporaryDirectory() as tmp:
                app = node.create_node_app(1, 'http://127.0.0.1:1', queue_path=Path(tmp) / 'q.db')
                c = app.test_client()
                for role in ('admin', 'staff'):
                    with c.session_transaction() as s:
                        s.update({'token': secret, 'bank_id': 1, 'role': role, 'username': role, '_csrf': 'k'})
                    for path in ('/', '/advanced', '/project-status', '/project-trace?id=abcdefgh1234', '/admin', '/login'):
                        with self.subTest(role=role, path=path):
                            self.assertNotIn(secret, c.get(path).get_data(as_text=True))
        finally:
            node.api_call = original


# --------------------------------------------------------------------------- 3. cross-bank access
@unittest.skipUnless(FLASK_PRESENT, 'Flask required')
class CrossBankAccessTests(unittest.TestCase):
    def setUp(self):
        from app import create_app
        from database import initialize
        self.temp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.temp.name) / 'cross.db')
        initialize(self.db, demo_accounts=True)
        self.client = create_app(self.db).test_client()
        self.s1, self.s2, self.s3 = (self.auth(b) for b in (1, 2, 3))
        self.admin = self.auth(1, 'admin', 'admin123')
        for bank, name, role in ((1, 'mgr1', 'manager'), (2, 'mgr2', 'manager'), (1, 'view1', 'viewer'),
                                 (1, 'drv1', 'driver'), (1, 'disp1', 'dispatcher')):
            r = self.client.post('/api/admin/users', headers=self.admin,
                                 json={'bank_id': bank, 'username': name, 'password': 'demo-pass-123', 'role': role})
            self.assertEqual(r.status_code, 201)
        self.m1, self.m2 = self.auth(1, 'mgr1', 'demo-pass-123'), self.auth(2, 'mgr2', 'demo-pass-123')
        self.others = {role: self.auth(1, name, 'demo-pass-123') for name, role in
                       (('view1', 'viewer'), ('drv1', 'driver'), ('disp1', 'dispatcher'))}

    def tearDown(self):
        self.temp.cleanup()

    def auth(self, bank, user='staff', pw='demo123'):
        r = self.client.post('/api/login', json={'bank_id': bank, 'username': user, 'password': pw})
        return {'Authorization': 'Bearer ' + r.get_json()['token']}

    def inv(self, bank, item):
        return rows(self.db, 'SELECT quantity,target FROM inventory WHERE bank_id=? AND item_id=?', bank, item)[0]

    def exchange_2_to_1(self):
        """Central (1) requests Rice from Northside (2)."""
        r = self.client.post('/api/exchanges', headers=self.s1, json={'source_bank_id': 2, 'item_id': 1, 'quantity': 5,
                                                                     'request_key': 'p8-cross-bank-0001'})
        self.assertEqual(r.status_code, 201)
        return r.get_json()['exchange_id']

    def test_bank_id_in_body_or_query_cannot_redirect_inventory_changes(self):
        before = self.inv(2, 1)
        self.client.put('/api/inventory/1', headers=self.s1, json={'bank_id': 2, 'quantity': 99, 'target': 1})
        self.client.put('/api/inventory/1?bank_id=2', headers=self.s1, json={'quantity': 98, 'target': 1})
        self.client.post('/api/inventory/1/adjust', headers=self.s1,
                         json={'bank_id': 2, 'delta': 5, 'base_version': 0, 'op_key': 'p8-adjust-0001'})
        self.assertEqual(self.inv(2, 1), before)

    def test_other_bank_cannot_change_a_transfer_it_is_not_part_of(self):
        ex = self.exchange_2_to_1()
        for action in ('approve', 'decline', 'ship', 'receive', 'cancel', 'accept'):
            with self.subTest(action=action):
                self.assertEqual(self.client.post(f'/api/exchanges/{ex}/{action}', headers=self.s3).status_code, 403)
        self.assertEqual(self.client.put(f'/api/exchanges/{ex}/schedule', headers=self.s3,
                                         json={'pickup_at': '2027-01-01T10:00:00Z', 'delivery_at': '2027-01-01T12:00:00Z'}).status_code, 403)
        self.assertEqual(rows(self.db, 'SELECT status FROM exchanges WHERE id=?', ex)[0][0], 'pending')

    def test_requester_cannot_approve_or_ship_its_own_request(self):
        ex = self.exchange_2_to_1()
        self.assertEqual(self.client.post(f'/api/exchanges/{ex}/approve', headers=self.s1).status_code, 403)
        self.assertEqual(self.client.post(f'/api/exchanges/{ex}/approve', headers=self.s2).status_code, 200)
        self.assertEqual(self.client.post(f'/api/exchanges/{ex}/ship', headers=self.s1).status_code, 403)
        self.assertEqual(self.client.post(f'/api/exchanges/{ex}/ship', headers=self.s2).status_code, 200)
        self.assertEqual(self.client.post(f'/api/exchanges/{ex}/receive-detailed', headers=self.s3,
                                          json={'received_qty': 5, 'damaged_qty': 0, 'notes': 'probe'}).status_code, 404)
        self.assertEqual(rows(self.db, 'SELECT status FROM exchanges WHERE id=?', ex)[0][0], 'in_transit')

    def test_destination_bank_always_comes_from_the_login(self):
        r = self.client.post('/api/exchanges', headers=self.s1, json={'source_bank_id': 2, 'destination_bank_id': 3,
                                                                     'item_id': 1, 'quantity': 1, 'request_key': 'p8-dest-0001'})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(rows(self.db, 'SELECT destination_bank_id FROM exchanges WHERE id=?', r.get_json()['exchange_id'])[0][0], 1)

    def test_other_bank_lots_donations_and_counts_are_out_of_reach(self):
        lot2 = rows(self.db, 'SELECT id FROM lots WHERE bank_id=2 LIMIT 1')[0][0]
        self.assertEqual(self.client.patch(f'/api/lots/{lot2}', headers=self.s1, json={'expiration_date': '2030-01-01'}).status_code, 404)
        d = self.client.post('/api/donations', headers=self.s2, json={'item_id': 1, 'quantity': 2, 'donor': 'Fictional Drive'}).get_json()
        self.assertEqual(self.client.get(f"/api/donations/{d['donation_id']}/receipt", headers=self.s1).status_code, 404)
        count = self.client.post('/api/reconciliations', headers=self.m2, json={'item_id': 1, 'counted_qty': 1, 'reason': 'count'}).get_json()
        self.assertEqual(self.client.post(f"/api/reconciliations/{count['reconciliation_id']}/apply", headers=self.m1).status_code, 404)
        self.assertEqual(rows(self.db, 'SELECT status FROM reconciliations WHERE id=?', count['reconciliation_id'])[0][0], 'pending')

    def test_drivers_and_vehicles_of_another_bank_cannot_be_assigned(self):
        driver = self.client.post('/api/drivers', headers=self.m2, json={'display_name': 'Driver B'}).get_json()['driver_id']
        vehicle = self.client.post('/api/vehicles', headers=self.m2, json={'label': 'Van B', 'capacity_units': 500,
                                                                           'refrigerated': True}).get_json()['vehicle_id']
        r = self.client.post('/api/exchanges', headers=self.s2, json={'source_bank_id': 1, 'item_id': 2, 'quantity': 5,
                                                                     'request_key': 'p8-transport-0001'})
        ex = r.get_json()['exchange_id']
        self.assertEqual(self.client.post(f'/api/exchanges/{ex}/approve', headers=self.s1).status_code, 200)
        r = self.client.put(f'/api/exchanges/{ex}/transport', headers=self.m1, json={'driver_id': driver, 'vehicle_id': vehicle})
        self.assertEqual(r.status_code, 409)

    def test_storage_change_only_affects_own_bank(self):
        self.client.put('/api/storage/ambient', headers=self.m2, json={'capacity_units': 90000, 'bank_id': 1})
        caps = dict(rows(self.db, "SELECT bank_id,capacity_units FROM storage_zones WHERE storage_type='ambient'"))
        self.assertEqual(caps[1], 100000)
        self.assertEqual(caps[2], 90000)

    def test_other_bank_inventory_view_is_redacted(self):
        data = self.client.get('/api/banks/2/inventory', headers=self.s1).get_json()
        self.assertTrue(data['shared'])
        for row in data['inventory']:
            self.assertEqual(set(row), {'item_id', 'item_name', 'category', 'unit', 'available'})

    def test_events_are_scoped_to_the_users_bank(self):
        self.client.get('/api/dashboard', headers=self.s2)
        events = self.client.get('/api/events', headers=self.s1).get_json()['events']
        self.assertTrue(events)
        self.assertEqual({e['bank_id'] for e in events}, {1})

    def test_non_admins_cannot_use_admin_endpoints(self):
        target = rows(self.db, "SELECT id FROM users WHERE username='staff' AND bank_id=2")[0][0]
        attempts = [('GET', '/api/admin/users', None),
                    ('PUT', f'/api/admin/users/{target}', {'role': 'admin', 'confirm_username': 'staff'}),
                    ('POST', f'/api/admin/users/{target}/disable', {'confirm_username': 'staff', 'reason': 'x'}),
                    ('POST', '/api/admin/users', {'bank_id': 1, 'username': 'sneaky', 'password': 'demo-pass-123', 'role': 'admin'}),
                    ('DELETE', '/api/admin/banks/3', {'confirm_name': 'Westside Food Pantry'}),
                    ('POST', '/api/admin/banks', {'name': 'X', 'area': 'Y', 'latitude': 1, 'longitude': 1, 'staff_password': 'demo-pass-123'}),
                    ('POST', '/api/admin/recalls', {'item_id': 1, 'lot_code': 'LEGACY-1', 'reason': 'x'}),
                    ('POST', '/api/admin/backups', {}), ('GET', '/api/admin/audit', None),
                    ('POST', '/api/admin/lab/run', {'scenario': 'recall'})]
        users = {'staff': self.s1, 'manager': self.m1, **self.others}
        for role, headers in users.items():
            for method, path, body in attempts:
                with self.subTest(role=role, path=path):
                    r = self.client.open(path, method=method, headers=headers, json=body)
                    self.assertEqual(r.status_code, 403)
        self.assertEqual(rows(self.db, 'SELECT role,active FROM users WHERE id=?', target)[0], ('staff', 1))
        self.assertEqual(rows(self.db, "SELECT COUNT(*) FROM users WHERE username='sneaky'")[0][0], 0)
        self.assertEqual(rows(self.db, 'SELECT COUNT(*) FROM banks')[0][0], 3)

    def test_read_only_roles_cannot_write(self):
        for role in ('viewer', 'driver'):
            h = self.others[role]
            for method, path, body in (('PUT', '/api/inventory/1', {'quantity': 1, 'target': 1}),
                                       ('POST', '/api/donations', {'item_id': 1, 'quantity': 1, 'donor': 'x'}),
                                       ('POST', '/api/exchanges', {'source_bank_id': 2, 'item_id': 1, 'quantity': 1, 'request_key': 'p8-ro-00001'}),
                                       ('POST', '/api/inventory/1/adjust', {'delta': 1, 'base_version': 0, 'op_key': 'p8-ro-adjust-1'})):
                with self.subTest(role=role, path=path):
                    self.assertEqual(self.client.open(path, method=method, headers=h, json=body).status_code, 403)
        self.assertEqual(self.client.put('/api/inventory/1', headers=self.others['dispatcher'],
                                         json={'quantity': 1, 'target': 1}).status_code, 403)


# --------------------------------------------------------------------------- 4. error handling
@unittest.skipUnless(FLASK_PRESENT, 'Flask required')
class ErrorHandlingTests(unittest.TestCase):
    def setUp(self):
        from app import create_app
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app(str(Path(self.temp.name) / 'errors.db'))

        @self.app.get('/api/p8-test-failure')
        def boom():
            raise RuntimeError('internal detail: C:/secret/path.db')
        self.app.config['PROPAGATE_EXCEPTIONS'] = False
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp.cleanup()

    def test_unexpected_errors_return_json_without_internal_details(self):
        import logging
        logging.disable(logging.CRITICAL)
        try:
            r = self.client.get('/api/p8-test-failure', headers={'X-Request-ID': 'p8-error-test-01'})
        finally:
            logging.disable(logging.NOTSET)
        self.assertEqual(r.status_code, 500)
        body = r.get_json()
        self.assertIn('p8-error-test-01', body['error'])
        self.assertNotIn('secret', r.get_data(as_text=True))
        self.assertNotIn('Traceback', r.get_data(as_text=True))

    def test_oversized_request_returns_json_413(self):
        r = self.client.post('/api/login', data=b'x' * (2 * 1024 * 1024 + 10), content_type='application/json')
        self.assertEqual(r.status_code, 413)
        self.assertIn('too large', r.get_json()['error'])


if __name__ == '__main__':
    unittest.main()
