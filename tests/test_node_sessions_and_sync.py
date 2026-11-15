import os; os.environ.setdefault('FOOD_BANK_DEMO_MODE', '1')  # classroom demo accounts (see tests/__init__.py)
"""Regressions found by the live distributed verification (verify_distributed.py).

H1: bank nodes on one host shared the browser cookie name, so logging into one node
    logged the browser out of the others, and a copied session could be replayed on
    another bank's node when the session secret was shared.
M2: one permanently invalid queued offline change stopped synchronization, leaving
    every later queued change pending forever.
"""
import http.cookiejar
import importlib.util
from pathlib import Path
import re
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request

FLASK_PRESENT = importlib.util.find_spec('flask') is not None


class Status(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class OfflineQueueRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.queue = Path(self.temp.name) / 'node_queue.db'
        from offline_queue import save_snapshot
        save_snapshot(self.queue, {'bank': {'id': 1, 'name': 'Test'}, 'inventory': [
            {'item_id': i, 'item_name': f'Item {i}', 'quantity': 10, 'version': 0} for i in (1, 2, 3)]})

    def tearDown(self):
        self.temp.cleanup()

    def states(self):
        from offline_queue import _db
        with _db(self.queue) as con:
            return {r['item_id']: (r['status'], r['error']) for r in con.execute('SELECT * FROM queued')}

    def test_permanently_rejected_change_does_not_block_later_changes(self):
        from offline_queue import enqueue, sync_pending
        for item in (1, 2, 3):
            enqueue(self.queue, item, 1)
        sent = []

        def send(_method, route, payload):
            sent.append(payload['item_id'])
            if payload['item_id'] == 1:
                raise Status(404, 'Item does not exist at this bank.')
            if payload['item_id'] == 2:
                raise Status(409, 'Conflict: item changed from version 0 to 1.')
            return {'message': 'ok'}
        result = sync_pending(self.queue, send)
        self.assertEqual(sent, [1, 2, 3])
        self.assertEqual(result, {'synced': 1, 'conflicts': 2, 'error': None})
        states = self.states()
        self.assertEqual(states[1][0], 'conflict')
        self.assertIn('Rejected by central API (HTTP 404)', states[1][1])
        self.assertEqual(states[2], ('conflict', 'Conflict: item changed from version 0 to 1.'))
        self.assertEqual(states[3][0], 'synced')

    def test_transient_failures_stop_and_keep_changes_pending(self):
        from offline_queue import enqueue, sync_pending
        for item in (1, 2):
            enqueue(self.queue, item, 1)
        for status in (None, 401, 403, 429, 500, 503):
            with self.subTest(status=status):
                def send(*_args):
                    raise Status(status, 'temporary problem')
                result = sync_pending(self.queue, send)
                self.assertEqual((result['synced'], result['conflicts']), (0, 0))
                self.assertEqual(result['error'], 'temporary problem')
                self.assertEqual({k: v[0] for k, v in self.states().items()}, {1: 'pending', 2: 'pending'})

    def test_receipt_queue_skips_rejected_receipts(self):
        from offline_queue import _db, enqueue_receipt, sync_receipts
        enqueue_receipt(self.queue, 1, 2, 'Drive A')
        enqueue_receipt(self.queue, 2, 3, 'Drive B')

        def send(_method, _route, payload):
            if payload['item_id'] == 1:
                raise Status(404, 'Add the inventory item online first.')
            return {'message': 'ok'}
        self.assertEqual(sync_receipts(self.queue, send), {'synced': 1, 'conflicts': 1, 'error': None})
        with _db(self.queue) as con:
            rows = {r['item_id']: (r['status'], r['error']) for r in con.execute('SELECT * FROM receipt_queue')}
        self.assertEqual(rows[1][0], 'conflict')
        self.assertIn('HTTP 404', rows[1][1])
        self.assertEqual(rows[2][0], 'synced')


@unittest.skipUnless(FLASK_PRESENT, 'Flask must be installed to run HTTP integration tests')
class NodeSessionIsolationTests(unittest.TestCase):
    """Real HTTP servers and ONE cookie jar, exactly like one browser with several tabs."""

    def setUp(self):
        from werkzeug.serving import make_server
        from app import create_app
        from node import create_node_app
        self.temp = tempfile.TemporaryDirectory()
        self.servers = []
        api = create_app(Path(self.temp.name) / 'central.db')
        api_url = self.serve(make_server, api)
        self.nodes = {}
        for bank in (1, 2):
            node = create_node_app(bank, api_url, Path(self.temp.name) / f'node_{bank}.db')
            node.secret_key = 'shared-secret-for-this-test-only'   # worst case: same secret everywhere
            self.nodes[bank] = self.serve(make_server, node)
        self.jar = http.cookiejar.CookieJar()
        self.browser = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def serve(self, make_server, app):
        server = make_server('127.0.0.1', 0, app, threaded=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.servers.append((server, thread))
        return f'http://127.0.0.1:{server.server_port}'

    def tearDown(self):
        for server, thread in self.servers:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        self.temp.cleanup()

    def get(self, url, opener=None):
        with (opener or self.browser).open(url, timeout=10) as resp:
            return resp.geturl(), resp.read().decode()

    def login(self, bank):
        _url, page = self.get(self.nodes[bank] + '/login')
        token = re.search(r'name="_csrf" value="([^"]+)"', page).group(1)
        data = urllib.parse.urlencode({'_csrf': token, 'username': 'staff', 'password': 'demo123'}).encode()
        with self.browser.open(urllib.request.Request(self.nodes[bank] + '/login', data=data), timeout=10) as resp:
            return resp.read().decode()

    def test_logging_into_a_second_node_keeps_the_first_signed_in(self):
        self.assertIn('Central Food Pantry', self.login(1))
        self.assertIn('Northside Food Pantry', self.login(2))
        url, page = self.get(self.nodes[1] + '/')
        self.assertNotIn('/login', url)
        self.assertIn('My inventory', page)
        self.assertIn('Central Food Pantry', page)
        self.assertEqual(sorted(c.name for c in self.jar), ['foodbank_node_1', 'foodbank_node_2'])

    def test_session_copied_to_another_bank_node_is_rejected(self):
        self.login(1)
        stolen = next(c for c in self.jar if c.name == 'foodbank_node_1').value
        # Present bank 1's signed session to bank 2's node under bank 2's cookie name.
        attacker = urllib.request.build_opener()
        attacker.addheaders = [('Cookie', f'foodbank_node_2={stolen}')]
        url, page = self.get(self.nodes[2] + '/', attacker)
        self.assertIn('/login', url)
        self.assertNotIn('My inventory', page)
        self.assertNotIn('Downtown', page)   # bank 1's area must never appear on node 2


if __name__ == '__main__':
    unittest.main()
