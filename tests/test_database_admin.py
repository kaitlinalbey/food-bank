import os; os.environ.setdefault('FOOD_BANK_DEMO_MODE', '1')  # classroom demo accounts (see tests/__init__.py)
"""Database Administration page and admin-only API.

Every test uses its own temporary database. Temporary directories are cleaned
WITHOUT ignore_cleanup_errors, so any leaked SQLite handle fails on Windows.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import threading
import unittest

FLASK_PRESENT = importlib.util.find_spec('flask') is not None


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@unittest.skipUnless(FLASK_PRESENT, 'Flask must be installed to run HTTP integration tests')
class AdminAPITests(unittest.TestCase):
    def setUp(self):
        from app import create_app
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / 'admin_test.db'
        self.app = create_app(self.db)
        self.app.config['TESTING'] = True
        self.client = self.app.test_client()
        self.admin = self.login(1, 'admin', 'admin123')

    def tearDown(self):
        self.temp.cleanup()

    def login(self, bank=1, user='staff', password='demo123', expect=200):
        resp = self.client.post('/api/login', json={'bank_id': bank, 'username': user, 'password': password})
        self.assertEqual(resp.status_code, expect, resp.get_json())
        return {'Authorization': 'Bearer ' + resp.json['token']} if expect == 200 else None

    def sql(self, query, args=()):
        with sqlite3.connect(self.db) as con:  # read helper; closed explicitly below
            rows = con.execute(query, args).fetchall()
        con.close()
        return rows

    def user_id(self, bank, username):
        return self.sql('SELECT id FROM users WHERE bank_id=? AND username=?', (bank, username))[0][0]

    def new_user(self, bank, username, role='staff', password='a-long-demo-password'):
        r = self.client.post('/api/admin/users', headers=self.admin, json={
            'bank_id': bank, 'username': username, 'password': password, 'role': role})
        self.assertEqual(r.status_code, 201, r.json)
        return r.json['user_id']

    # ------------------------------------------------------------------ access control
    ADMIN_ROUTES = [
        ('get', '/api/admin/overview', None), ('get', '/api/admin/banks?probe=0', None),
        ('put', '/api/admin/banks/2', {'area': 'X'}), ('delete', '/api/admin/banks/2', {'confirm_name': 'x'}),
        ('get', '/api/admin/users', None), ('put', '/api/admin/users/1', {'role': 'admin'}),
        ('post', '/api/admin/users/1/disable', {}), ('post', '/api/admin/users/1/enable', {}),
        ('get', '/api/admin/health', None), ('get', '/api/admin/backups', None),
        ('post', '/api/admin/backups', {}), ('post', '/api/admin/backups/1/verify-restore', {}),
    ]

    def call(self, method, path, body, headers):
        kwargs = {'headers': headers}
        if body is not None:
            kwargs['json'] = body
        return getattr(self.client, method)(path, **kwargs)

    def test_admin_access_granted_and_non_admin_denied(self):
        staff_bank2 = self.login(2)
        self.new_user(3, 'mgr3', 'manager')
        manager_bank3 = self.login(3, 'mgr3', 'a-long-demo-password')
        before = self.sql('SELECT COUNT(*) FROM db_backups')[0][0]
        for method, path, body in self.ADMIN_ROUTES:
            with self.subTest(path=path, who='anonymous'):
                self.assertEqual(self.call(method, path, body, {}).status_code, 401)
            for who, headers in (('staff bank 2', staff_bank2), ('manager bank 3', manager_bank3)):
                with self.subTest(path=path, who=who):
                    self.assertEqual(self.call(method, path, body, headers).status_code, 403)
        # Denied requests changed nothing and were audited as failures.
        self.assertEqual(self.sql('SELECT COUNT(*) FROM db_backups')[0][0], before)
        self.assertEqual(self.sql("SELECT area FROM banks WHERE id=2")[0][0], 'North Little Rock area')
        self.assertGreater(self.sql("SELECT COUNT(*) FROM audit_events WHERE result='failed' "
                                    "AND detail LIKE 'HTTP 403%'")[0][0], 0)
        for path in ('/api/admin/overview', '/api/admin/banks?probe=0', '/api/admin/users',
                     '/api/admin/health', '/api/admin/backups'):
            with self.subTest(path=path, who='admin'):
                self.assertEqual(self.client.get(path, headers=self.admin).status_code, 200)

    def test_overview_reports_actual_database_counts(self):
        o = self.client.get('/api/admin/overview', headers=self.admin).json
        self.assertEqual(o['food_banks'], self.sql('SELECT COUNT(*) FROM banks')[0][0])
        self.assertEqual(o['inventory_rows'], self.sql('SELECT COUNT(*) FROM inventory')[0][0])
        self.assertEqual(o['active_batches'], self.sql('SELECT COUNT(*) FROM lots WHERE quantity>0')[0][0])
        self.assertEqual(o['users_active'], self.sql('SELECT COUNT(*) FROM users WHERE active=1')[0][0])
        self.assertEqual(o['integrity_quick_check'], 'ok')
        self.assertIsNone(o['latest_verified_backup'])
        self.assertFalse(o['database']['presentation_session'])
        # A real transfer and a real failed sync change the numbers.
        one, two = self.login(1), self.login(2)
        ex = self.client.post('/api/exchanges', headers=one, json={
            'source_bank_id': 2, 'item_id': 1, 'quantity': 5, 'request_key': 'p3-overview-0001'})
        self.assertEqual(ex.status_code, 201, ex.json)
        bad = self.client.post('/api/inventory/999/adjust', headers=two, json={
            'delta': 1, 'base_version': 0, 'op_key': 'p3-sync-err-000001'})
        self.assertGreaterEqual(bad.status_code, 400)
        o = self.client.get('/api/admin/overview', headers=self.admin).json
        self.assertEqual(o['transfers_pending'], 1)
        self.assertEqual(o['sync_errors_last_7_days'], 1)
        self.assertEqual(o['recent_sync_errors'][0]['bank_id'], 2)
        self.assertNotIn('pbkdf2', json.dumps(o))

    # ------------------------------------------------------------------ banks
    def test_bank_search_update_and_validation(self):
        banks = self.client.get('/api/admin/banks?probe=0&q=north', headers=self.admin).json['banks']
        self.assertEqual([b['name'] for b in banks], ['Northside Food Pantry'])
        self.assertIn('last_communication_at', banks[0])
        self.assertIsNone(banks[0]['node_online'])  # probe=0: not checked, not invented
        probed = self.client.get('/api/admin/banks', headers=self.admin).json['banks']
        self.assertTrue(all(b['node_online'] is False for b in probed))  # no nodes running in tests
        r = self.client.put('/api/admin/banks/2', headers=self.admin, json={'name': 'Northside Community Pantry'})
        self.assertEqual(r.status_code, 200, r.json)
        self.assertEqual(self.sql('SELECT name FROM banks WHERE id=2')[0][0], 'Northside Community Pantry')
        self.assertEqual(self.client.put('/api/admin/banks/2', headers=self.admin,
                                         json={'name': 'Central Food Pantry'}).status_code, 409)
        for body in ({}, {'name': ''}, {'name': 'x' * 81}, {'latitude': 95, 'longitude': 0},
                     {'latitude': 'abc', 'longitude': 1}, {'area': 7}):
            with self.subTest(body=body):
                self.assertEqual(self.client.put('/api/admin/banks/2', headers=self.admin, json=body).status_code, 400)
        self.assertEqual(self.client.put('/api/admin/banks/99', headers=self.admin, json={'area': 'X'}).status_code, 404)
        self.assertEqual(self.client.put('/api/admin/banks/2', headers=self.admin, data='not json').status_code, 400)
        self.assertEqual(self.client.get('/api/admin/banks?q=' + 'x' * 81, headers=self.admin).status_code, 400)
        self.assertTrue(self.sql("SELECT 1 FROM activity WHERE action='bank_updated' AND bank_id=2"))
        self.assertTrue(self.sql("SELECT 1 FROM audit_events WHERE operation='admin: update bank' AND result='success'"))

    def test_bank_deletion_is_blocked_when_records_exist(self):
        r = self.client.delete('/api/admin/banks/2', headers=self.admin, json={'confirm_name': 'Northside Food Pantry'})
        self.assertEqual(r.status_code, 409)
        self.assertIn('existing records', r.json['error'])
        self.assertEqual(self.client.delete('/api/admin/banks/1', headers=self.admin,
                                            json={'confirm_name': 'Central Food Pantry'}).status_code, 409)
        made = self.client.post('/api/admin/banks', headers=self.admin, json={
            'name': 'Temporary Pantry', 'area': 'Test', 'latitude': 34.7, 'longitude': -92.3,
            'staff_password': 'temporary-password'})
        self.assertEqual(made.status_code, 201, made.json)
        new_id = made.json['bank_id']
        self.assertEqual(self.client.delete(f'/api/admin/banks/{new_id}', headers=self.admin, json={}).status_code, 400)
        self.assertEqual(self.client.delete(f'/api/admin/banks/{new_id}', headers=self.admin,
                                            json={'confirm_name': 'Wrong'}).status_code, 400)
        # Once the bank has inventory it is protected too.
        token = self.login(new_id, 'staff', 'temporary-password')
        self.assertEqual(self.client.post('/api/inventory', headers=token, json={
            'name': 'Rice', 'unit': 'bags', 'category': 'Dry Goods', 'quantity': 1, 'target': 1}).status_code, 201)
        self.assertEqual(self.client.delete(f'/api/admin/banks/{new_id}', headers=self.admin,
                                            json={'confirm_name': 'Temporary Pantry'}).status_code, 409)
        # A never-used bank can be deleted with the typed name.
        unused = self.client.post('/api/admin/banks', headers=self.admin, json={
            'name': 'Unused Pantry', 'area': 'Test', 'latitude': 34.7, 'longitude': -92.3,
            'staff_password': 'temporary-password'}).json['bank_id']
        r = self.client.delete(f'/api/admin/banks/{unused}', headers=self.admin, json={'confirm_name': 'Unused Pantry'})
        self.assertEqual(r.status_code, 200, r.json)
        self.assertFalse(self.sql('SELECT 1 FROM banks WHERE id=?', (unused,)))
        self.assertFalse(self.sql('SELECT 1 FROM users WHERE bank_id=?', (unused,)))
        self.assertEqual(self.sql('PRAGMA foreign_key_check'), [])

    # ------------------------------------------------------------------ users
    def test_user_list_search_never_exposes_secrets(self):
        self.new_user(2, 'north-viewer', 'viewer')
        r = self.client.get('/api/admin/users?q=viewer&bank_id=2&role=viewer&active=1', headers=self.admin)
        self.assertEqual(r.status_code, 200)
        self.assertEqual([u['username'] for u in r.json['users']], ['north-viewer'])
        self.assertEqual(r.json['users'][0]['bank_name'], 'Northside Food Pantry')
        everything = self.client.get('/api/admin/users', headers=self.admin).get_data(as_text=True)
        for secret in ('password', 'pbkdf2', 'hash', 'token'):
            self.assertNotIn(secret, everything)
        for query in ('role=wizard', 'active=yes', 'bank_id=abc', 'q=' + 'x' * 81):
            with self.subTest(query=query):
                self.assertEqual(self.client.get('/api/admin/users?' + query, headers=self.admin).status_code, 400)

    def test_role_and_bank_changes_with_escalation_guards(self):
        uid = self.new_user(1, 'helper')
        old = self.login(1, 'helper', 'a-long-demo-password')
        r = self.client.put(f'/api/admin/users/{uid}', headers=self.admin, json={'role': 'manager'})
        self.assertEqual(r.status_code, 200, r.json)
        self.assertEqual(r.json['sessions_ended'], 1)
        self.assertEqual(self.client.get('/api/me', headers=old).status_code, 401)  # permissions apply at once
        self.assertEqual(self.client.get('/api/me', headers=self.login(1, 'helper', 'a-long-demo-password')).json['role'],
                         'manager')
        # Granting admin requires typing the username.
        self.assertEqual(self.client.put(f'/api/admin/users/{uid}', headers=self.admin,
                                         json={'role': 'admin'}).status_code, 400)
        self.assertEqual(self.client.put(f'/api/admin/users/{uid}', headers=self.admin,
                                         json={'role': 'admin', 'confirm_username': 'helper'}).status_code, 200)
        self.assertEqual(self.sql('SELECT role FROM users WHERE id=?', (uid,))[0][0], 'admin')
        self.assertFalse(self.sql('SELECT 1 FROM user_role_overrides WHERE user_id=?', (uid,)))
        # An admin cannot change their own role or bank.
        admin_id = self.user_id(1, 'admin')
        self.assertEqual(self.client.put(f'/api/admin/users/{admin_id}', headers=self.admin,
                                         json={'role': 'staff'}).status_code, 409)
        # Invalid inputs.
        for body in ({}, {'role': 'superuser'}, {'bank_id': 'two'}, {'bank_id': 0}):
            with self.subTest(body=body):
                self.assertEqual(self.client.put(f'/api/admin/users/{uid}', headers=self.admin, json=body).status_code, 400)
        self.assertEqual(self.client.put(f'/api/admin/users/{uid}', headers=self.admin,
                                         json={'bank_id': 99}).status_code, 404)
        self.assertEqual(self.client.put('/api/admin/users/9999', headers=self.admin,
                                         json={'role': 'staff'}).status_code, 404)
        # Non-admins cannot escalate themselves or anyone else.
        staff = self.login(1)
        staff_id = self.user_id(1, 'staff')
        self.assertEqual(self.client.put(f'/api/admin/users/{staff_id}', headers=staff,
                                         json={'role': 'admin', 'confirm_username': 'staff'}).status_code, 403)
        self.assertEqual(self.client.post('/api/admin/users', headers=staff, json={
            'bank_id': 1, 'username': 'sneaky', 'password': 'a-long-demo-password', 'role': 'admin'}).status_code, 403)
        self.assertEqual(self.sql("SELECT role FROM users WHERE id=?", (staff_id,))[0][0], 'staff')

    def test_last_admin_cannot_be_demoted_or_disabled(self):
        second = self.new_user(2, 'admin2', 'admin')
        other = self.login(2, 'admin2', 'a-long-demo-password')
        admin_id = self.user_id(1, 'admin')
        # admin2 disables admin (allowed: another admin remains), then admin2 is the last one.
        self.assertEqual(self.client.post(f'/api/admin/users/{admin_id}/disable', headers=other, json={
            'confirm_username': 'admin', 'reason': 'test rotation'}).status_code, 200)
        self.assertEqual(self.client.get('/api/me', headers=self.admin).status_code, 401)
        self.assertEqual(self.client.post(f'/api/admin/users/{second}/disable', headers=other, json={
            'confirm_username': 'admin2', 'reason': 'self'}).status_code, 409)  # own account
        # Defence in depth: even a caller that is not the target cannot remove the last admin.
        from services import dbadmin
        from services.validation import Problem
        for action in (lambda: dbadmin.set_user_active(self.db, second, False, {
                           'confirm_username': 'admin2', 'reason': 'x'}, 1, 9999),
                       lambda: dbadmin.update_user(self.db, second, {'role': 'staff'}, 1, 9999)):
            with self.assertRaises(Problem) as caught:
                action()
            self.assertEqual(caught.exception.status, 409)
            self.assertIn('last active administrator', str(caught.exception))
        self.assertEqual(self.client.post(f'/api/admin/users/{admin_id}/enable', headers=other,
                                          json={'reason': 'back'}).status_code, 200)
        self.login(1, 'admin', 'admin123')

    def test_cross_bank_membership_and_disable_workflow(self):
        uid = self.new_user(1, 'mover')
        token = self.login(1, 'mover', 'a-long-demo-password')
        r = self.client.put(f'/api/admin/users/{uid}', headers=self.admin, json={'bank_id': 3})
        self.assertEqual(r.status_code, 200, r.json)
        self.assertEqual(self.client.get('/api/dashboard', headers=token).status_code, 401)
        self.login(1, 'mover', 'a-long-demo-password', expect=401)  # no longer a bank-1 account
        moved = self.login(3, 'mover', 'a-long-demo-password')
        self.assertEqual(self.client.get('/api/dashboard', headers=moved).json['bank']['id'], 3)
        # Username collision at the target bank.
        self.assertEqual(self.client.put(f'/api/admin/users/{uid}', headers=self.admin,
                                         json={'bank_id': 1}).status_code, 200)
        self.new_user(2, 'mover')
        self.assertEqual(self.client.put(f'/api/admin/users/{uid}', headers=self.admin,
                                         json={'bank_id': 2}).status_code, 409)
        # Disable needs the typed username and a reason; it ends sessions and blocks login.
        live = self.login(1, 'mover', 'a-long-demo-password')
        for body in ({}, {'confirm_username': 'wrong', 'reason': 'x'}, {'confirm_username': 'mover'},
                     {'confirm_username': 'mover', 'reason': ''}):
            with self.subTest(body=body):
                self.assertEqual(self.client.post(f'/api/admin/users/{uid}/disable', headers=self.admin,
                                                  json=body).status_code, 400)
        admin_id = self.user_id(1, 'admin')
        self.assertEqual(self.client.post(f'/api/admin/users/{admin_id}/disable', headers=self.admin, json={
            'confirm_username': 'admin', 'reason': 'oops'}).status_code, 409)
        r = self.client.post(f'/api/admin/users/{uid}/disable', headers=self.admin, json={
            'confirm_username': 'mover', 'reason': 'Left the volunteer program'})
        self.assertEqual(r.status_code, 200, r.json)
        self.assertEqual(self.client.get('/api/me', headers=live).status_code, 401)
        self.login(1, 'mover', 'a-long-demo-password', expect=401)
        self.assertEqual(self.client.post(f'/api/admin/users/{uid}/enable', headers=self.admin, json={}).status_code, 200)
        self.login(1, 'mover', 'a-long-demo-password')
        self.assertTrue(self.sql("SELECT 1 FROM audit_events WHERE operation='admin: disable user' AND result='success'"))

    # ------------------------------------------------------------------ health
    def test_health_check_is_read_only_and_detects_problems(self):
        from services import dbadmin
        r = self.client.get('/api/admin/health', headers=self.admin)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json['overall'], 'pass', r.json)
        self.assertTrue(r.json['read_only'])
        names = {c['check'] for c in r.json['checks']}
        self.assertTrue({'sqlite_integrity', 'foreign_keys', 'inventory_matches_batches',
                         'no_negative_stock', 'reservations_covered', 'recalls_quarantined'} <= names)
        # The service itself never writes: the file is byte-for-byte unchanged.
        before = sha(self.db)
        dbadmin.health(self.db)
        self.assertEqual(sha(self.db), before)
        # Introduce a real inconsistency directly in SQLite; the check must report it.
        con = sqlite3.connect(self.db)
        con.execute('UPDATE inventory SET quantity=quantity+3 WHERE bank_id=1 AND item_id=1')
        con.commit()
        con.close()
        h = self.client.get('/api/admin/health', headers=self.admin).json
        self.assertEqual(h['overall'], 'fail')
        bad = next(c for c in h['checks'] if c['check'] == 'inventory_matches_batches')
        self.assertEqual((bad['status'], bad['count']), ('fail', 1))
        self.assertEqual(bad['examples'][0]['bank_id'], 1)

    # ------------------------------------------------------------------ backups
    def make_backup(self, note=''):
        r = self.client.post('/api/admin/backups', headers=self.admin, json={'note': note})
        self.assertEqual(r.status_code, 201, r.json)
        return r.json

    def test_backup_creation_and_integrity(self):
        b = self.make_backup('before demo')
        self.assertTrue(b['verified'])
        path = self.db.parent / 'admin_backups' / b['file_name']
        self.assertTrue(path.is_file())
        self.assertEqual(sha(path), b['sha256'])
        con = sqlite3.connect(path)
        try:
            self.assertEqual(con.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(con.execute('SELECT COUNT(*) FROM banks').fetchone()[0], 3)
        finally:
            con.close()
        self.assertEqual(b['table_counts']['banks'], 3)
        listed = self.client.get('/api/admin/backups', headers=self.admin).json
        self.assertEqual(listed['backups'][0]['id'], b['backup_id'])
        self.assertTrue(listed['backups'][0]['file_present'])
        self.assertEqual(listed['backups'][0]['created_by'], 'admin')
        self.assertIn('DATABASE_MAINTENANCE', listed['live_restore'])
        latest = self.client.get('/api/admin/overview', headers=self.admin).json['latest_verified_backup']
        self.assertEqual(latest['id'], b['backup_id'])
        self.assertEqual(self.client.post('/api/admin/backups', headers=self.admin,
                                          json={'note': 'x' * 201}).status_code, 400)
        self.assertEqual(self.client.post('/api/admin/backups', headers=self.admin,
                                          json={'note': 5}).status_code, 400)
        self.assertTrue(self.sql("SELECT 1 FROM activity WHERE action='database_backup'"))

    def test_isolated_restore_verification_never_touches_live_data(self):
        b = self.make_backup()
        # Live data changes after the backup.
        self.client.post('/api/admin/banks', headers=self.admin, json={
            'name': 'After Backup Pantry', 'area': 'Test', 'latitude': 34.7, 'longitude': -92.3,
            'staff_password': 'temporary-password'})
        live_banks = self.sql('SELECT id,name FROM banks ORDER BY id')
        r = self.client.post(f'/api/admin/backups/{b["backup_id"]}/verify-restore', headers=self.admin, json={})
        self.assertEqual(r.status_code, 200, r.json)
        self.assertTrue(r.json['passed'], r.json)
        self.assertFalse(r.json['active_database_overwritten'])
        self.assertEqual(len(r.json['steps']), 5)
        self.assertEqual(self.sql('SELECT id,name FROM banks ORDER BY id'), live_banks)  # not rolled back
        self.assertEqual(len(live_banks), 4)
        leftovers = [p for p in Path(tempfile.gettempdir()).glob('foodbank_restore_verify_*')]
        self.assertEqual(leftovers, [])
        recorded = self.client.get('/api/admin/backups', headers=self.admin).json['backups'][0]
        self.assertTrue(recorded['restore_result']['passed'])
        # Unknown backup id, damaged file, missing file.
        self.assertEqual(self.client.post('/api/admin/backups/999/verify-restore', headers=self.admin,
                                          json={}).status_code, 404)
        damaged = self.make_backup()
        dpath = self.db.parent / 'admin_backups' / damaged['file_name']
        with open(dpath, 'r+b') as fh:
            fh.seek(-200, 2)
            fh.write(b'\xff' * 50)  # free pages are already zero, so write non-zero bytes
        r = self.client.post(f'/api/admin/backups/{damaged["backup_id"]}/verify-restore', headers=self.admin, json={})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json['passed'])
        self.assertFalse(r.json['steps'][0]['passed'])  # checksum mismatch detected
        missing = self.make_backup()
        moved = Path(self.temp.name) / 'moved_away.db'
        shutil.move(str(self.db.parent / 'admin_backups' / missing['file_name']), str(moved))
        self.assertEqual(self.client.post(f'/api/admin/backups/{missing["backup_id"]}/verify-restore',
                                          headers=self.admin, json={}).status_code, 409)
        # A tampered register row cannot point outside the backup folder.
        con = sqlite3.connect(self.db)
        con.execute("UPDATE db_backups SET file_name='../admin_test.db' WHERE id=?", (missing['backup_id'],))
        con.commit()
        con.close()
        self.assertEqual(self.client.post(f'/api/admin/backups/{missing["backup_id"]}/verify-restore',
                                          headers=self.admin, json={}).status_code, 409)

    # ------------------------------------------------------------------ persistence
    def test_changes_persist_after_restart(self):
        from app import create_app
        self.client.put('/api/admin/banks/3', headers=self.admin, json={'area': 'West Little Rock (renamed)'})
        uid = self.new_user(2, 'persist-me', 'viewer')
        self.client.put(f'/api/admin/users/{uid}', headers=self.admin, json={'role': 'dispatcher'})
        self.client.post(f'/api/admin/users/{uid}/disable', headers=self.admin,
                         json={'confirm_username': 'persist-me', 'reason': 'restart test'})
        b = self.make_backup('restart test')
        # "Restart": a brand-new app instance on the same database file.
        restarted = create_app(self.db)
        restarted.config['TESTING'] = True
        client = restarted.test_client()
        token = client.post('/api/login', json={'bank_id': 1, 'username': 'admin', 'password': 'admin123'}).json['token']
        h = {'Authorization': 'Bearer ' + token}
        banks = {x['id']: x for x in client.get('/api/admin/banks?probe=0', headers=h).json['banks']}
        self.assertEqual(banks[3]['area'], 'West Little Rock (renamed)')
        user = next(u for u in client.get('/api/admin/users?q=persist', headers=h).json['users'])
        self.assertEqual((user['role'], user['active']), ('dispatcher', 0))
        backups = client.get('/api/admin/backups', headers=h).json['backups']
        self.assertEqual(backups[0]['id'], b['backup_id'])
        self.assertTrue(client.post(f'/api/admin/backups/{b["backup_id"]}/verify-restore', headers=h,
                                    json={}).json['passed'])
        self.assertEqual(client.get('/api/admin/health', headers=h).json['overall'], 'pass')
        self.assertEqual(self.sql('SELECT COUNT(*) FROM users WHERE username=?', ('admin',))[0][0], 1)


@unittest.skipUnless(FLASK_PRESENT, 'Flask must be installed to run HTTP integration tests')
class AdminNodePageTests(unittest.TestCase):
    """The plain HTML page served by a bank node, talking to a real central API."""

    def setUp(self):
        from werkzeug.serving import make_server
        from app import create_app
        from node import create_node_app
        self.temp = tempfile.TemporaryDirectory()
        self.api = create_app(Path(self.temp.name) / 'node_admin.db')
        self.server = make_server('127.0.0.1', 0, self.api, threaded=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        url = f'http://127.0.0.1:{self.server.server_port}'
        self.node = create_node_app(1, url, Path(self.temp.name) / 'node_1_queue.db')
        self.node.config['TESTING'] = True

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.temp.cleanup()

    @staticmethod
    def csrf(body):
        return re.search(r'name="_csrf" value="([^"]+)"', body).group(1)

    def signed_in(self, username, password):
        c = self.node.test_client()
        token = self.csrf(c.get('/login').get_data(as_text=True))
        page = c.post('/login', data={'_csrf': token, 'username': username, 'password': password},
                      follow_redirects=True)
        self.assertEqual(page.status_code, 200)
        return c

    def test_admin_page_sections_and_actions(self):
        c = self.signed_in('admin', 'admin123')
        self.assertIn('Database administration', c.get('/').get_data(as_text=True))  # nav link
        page = c.get('/admin')
        self.assertEqual(page.status_code, 200)
        body = page.get_data(as_text=True)
        for heading in ('1. System overview', '2. Food bank management', '3. User management',
                        '4. Database health', '5. Backup and recovery', '6. Classroom demo tools'):
            self.assertIn(heading, body)
        self.assertIn('Northside Food Pantry', body)
        self.assertIn('No backups recorded yet.', body)
        self.assertNotIn('pbkdf2', body)
        self.assertNotIn('<button>Reset', body)  # no destructive reset control on this page
        token = self.csrf(body)
        # POST without CSRF token is rejected before reaching the API.
        self.assertEqual(c.post('/admin/backups', data={'note': 'x'}).status_code, 400)
        done = c.post('/admin/backups', data={'_csrf': token, 'note': 'from node page'}, follow_redirects=True)
        text = done.get_data(as_text=True)
        self.assertIn('Backup created and verified.', text)
        self.assertIn('from node page', text)
        backup_id = re.search(r'/admin/backups/(\d+)/verify-restore', text).group(1)
        verified = c.post(f'/admin/backups/{backup_id}/verify-restore', data={'_csrf': token},
                          follow_redirects=True).get_data(as_text=True)
        self.assertIn('Isolated restore verification passed.', verified)
        self.assertIn('PASSED', verified)
        health = c.get('/admin?health=1').get_data(as_text=True)
        self.assertIn('Overall result: PASS', health)
        updated = c.post('/admin/banks/2/update', data={'_csrf': token, 'name': 'Northside Pantry Renamed',
                         'area': 'North', 'latitude': '34.77', 'longitude': '-92.27'},
                         follow_redirects=True).get_data(as_text=True)
        self.assertIn('Food bank updated.', updated)
        blocked = c.post('/admin/banks/2/delete', data={'_csrf': token, 'confirm_name': 'Northside Pantry Renamed'},
                         follow_redirects=True).get_data(as_text=True)
        self.assertIn('cannot be deleted', blocked)
        added = c.post('/admin/user', data={'_csrf': token, 'bank_id': '2', 'username': 'node-viewer',
                       'password': 'a-long-demo-password', 'role': 'viewer', 'return_to': 'admin'},
                       follow_redirects=True).get_data(as_text=True)
        self.assertIn('User created.', added)
        self.assertIn('node-viewer', added)

    def test_non_admin_is_denied_the_page_and_actions(self):
        c = self.signed_in('staff', 'demo123')
        self.assertNotIn('Database administration</a>', c.get('/').get_data(as_text=True))
        page = c.get('/admin')
        self.assertEqual(page.status_code, 403)
        body = page.get_data(as_text=True)
        self.assertIn('Administrator account required.', body)
        token = self.csrf(body)
        self.assertEqual(c.post('/admin/backups', data={'_csrf': token}).status_code, 403)
        self.assertEqual(c.post('/admin/users/1/disable', data={'_csrf': token, 'confirm_username': 'admin',
                                                                'reason': 'x'}).status_code, 403)
        anonymous = self.node.test_client()
        self.assertEqual(anonymous.get('/admin').status_code, 302)


if __name__ == '__main__':
    unittest.main()
