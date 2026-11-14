import os; os.environ.setdefault('FOOD_BANK_DEMO_MODE', '1')  # classroom demo accounts (see tests/__init__.py)
"""Regression tests for fixes made during the final release check.

1. Shortage ignores expired and recalled stock (it cannot be handed out).
2. The lot-expiry form works without JavaScript (posts to /lot/0/expiry with lot_id_entry).
3. The storage-capacity form only accepts the three known storage types.
4. The dashboard "Add user" form offers every supported role.
"""
import importlib.util
from pathlib import Path
import tempfile
import unittest

FLASK_PRESENT = importlib.util.find_spec('flask') is not None


class ShortageUsesUsableStockTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.temp.name) / 'release_fix.db')
        from database import initialize
        initialize(self.db)

    def tearDown(self):
        self.temp.cleanup()

    def _numbers(self, bank_id, item_id):
        from database import read_db
        from services.core import need, stock
        with read_db(self.db) as con:
            row = stock(con, bank_id, item_id)
            return row['quantity'], row['target'], need(con, bank_id, item_id)

    def test_expired_stock_does_not_hide_a_shortage(self):
        from database import write_db
        quantity, target, before = self._numbers(1, 1)
        self.assertGreater(quantity, 0)
        self.assertEqual(before, max(0, target - quantity))
        with write_db(self.db) as con:
            con.execute("UPDATE lots SET expiration_date='2000-01-01' WHERE bank_id=1 AND item_id=1")
        _, _, after = self._numbers(1, 1)
        self.assertEqual(after, target, 'all stock expired, so the full target is short')

    def test_recalled_stock_does_not_hide_a_shortage(self):
        from database import write_db
        _, target, _ = self._numbers(1, 1)
        with write_db(self.db) as con:
            con.execute('UPDATE lots SET recalled=1 WHERE bank_id=1 AND item_id=1')
        self.assertEqual(self._numbers(1, 1)[2], target)

    def test_expired_shortage_now_produces_matches(self):
        from database import read_db, write_db
        from services.core import matches
        with write_db(self.db) as con:
            con.execute("UPDATE lots SET expiration_date='2000-01-01' WHERE bank_id=1 AND item_id=1")
        with read_db(self.db) as con:
            bank = con.execute('SELECT id,latitude,longitude FROM banks WHERE id=1').fetchone()
            found = [m for m in matches(con, bank) if m['item_id'] == 1]
        self.assertTrue(found, 'other banks with surplus should now be suggested')


@unittest.skipUnless(FLASK_PRESENT, 'Flask required')
class NodeFormFixTests(unittest.TestCase):
    def setUp(self):
        import node
        self.temp = tempfile.TemporaryDirectory()
        self.calls = []
        self.node = node
        self.original = node.api_call

        def fake_api(base, method, path, payload=None, token=None, correlation_id=None):
            self.calls.append((method, path, payload))
            return {'message': 'ok'}
        node.api_call = fake_api
        app = node.create_node_app(1, 'http://127.0.0.1:1', queue_path=Path(self.temp.name) / 'q.db')
        app.config['TESTING'] = True
        self.client = app.test_client()
        with self.client.session_transaction() as s:
            s.update({'token': 'test-token', 'bank_id': 1, 'role': 'admin', 'username': 'admin', '_csrf': 'csrf-test'})

    def tearDown(self):
        self.node.api_call = self.original
        self.temp.cleanup()

    def test_lot_expiry_without_javascript_uses_entered_batch(self):
        r = self.client.post('/lot/0/expiry', data={'_csrf': 'csrf-test', 'lot_id_entry': '42', 'expiration_date': '2027-01-15'})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.calls, [('PATCH', '/api/lots/42', {'expiration_date': '2027-01-15'})])

    def test_lot_expiry_rejects_missing_batch(self):
        r = self.client.post('/lot/0/expiry', data={'_csrf': 'csrf-test', 'lot_id_entry': 'abc'})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.calls, [])

    def test_lot_expiry_with_rewritten_url_still_works(self):
        self.client.post('/lot/7/expiry', data={'_csrf': 'csrf-test', 'expiration_date': ''})
        self.assertEqual(self.calls[-1][1], '/api/lots/7')

    def test_storage_form_rejects_unknown_storage_type(self):
        self.client.post('/advanced/action/storage', data={'_csrf': 'csrf-test', 'storage_type': '../admin', 'capacity_units': '5'})
        self.assertEqual(self.calls, [])
        self.client.post('/advanced/action/storage', data={'_csrf': 'csrf-test', 'storage_type': 'frozen', 'capacity_units': '5'})
        self.assertEqual(self.calls, [('PUT', '/api/storage/frozen', {'capacity_units': '5'})])

    def test_dashboard_add_user_form_offers_every_role(self):
        # In the multi-page interface the "Add user" form lives on the Administration page.
        # Render that template with the role list node.py really passes, then check the form.
        import re
        from jinja2 import Environment, FileSystemLoader
        root = Path(__file__).resolve().parent.parent
        source = (root / 'node.py').read_text(encoding='utf8')
        roles = tuple(re.findall(r"'(\w+)'", re.search(r"roles=\(([^)]*)\)", source).group(1)))
        env = Environment(loader=FileSystemLoader(str(root / 'templates')), autoescape=True)
        env.globals.update(url_for=lambda name, **kw: '/' + name, session={}, get_flashed_messages=lambda: [],
                           csrf_token='k', bank_name='Central')
        html = env.get_template('admin.html').render(denied=False, data={}, errors={}, bank_q='',
                                                     user_filters={'q': '', 'bank_id': '', 'role': '', 'active': ''},
                                                     roles=roles, restore=None, this_user='admin')
        form = html.split('<h3>Add a user</h3>', 1)[1].split('</form>', 1)[0]
        for role in ('staff', 'manager', 'dispatcher', 'driver', 'viewer', 'admin'):
            self.assertIn(f'<option value="{role}">', form)


if __name__ == '__main__':
    unittest.main()
