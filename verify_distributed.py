"""Live distributed-system verification.

Starts the REAL central API (server.py) and three REAL bank node processes
(node.py) on an isolated, freshly created fictional SQLite database in a new
temporary session folder, then drives 16 scenarios over HTTP exactly as
browsers and nodes would. Nothing touches data/food_bank.db or normal node
queues.

Architecture under test: ONE central Flask API owning ONE SQLite database;
bank nodes are separate Flask processes with their own small offline queue
files. It is NOT a replicated or partition-tolerant distributed database.

Safety and bounds:
* refuses to start if any port it needs is already in use;
* every HTTP call has a timeout; every wait/retry loop has a deadline;
* every process it starts is terminated (then killed if needed) in `finally`,
  and the ports are checked again afterwards;
* the session folder is left in the system temp directory for inspection
  (pass --cleanup to remove it; only that one folder, which this run created).

Usage:  python verify_distributed.py [--base-port 25000] [--out reports/distributed_verification.json]
Exit code 0 only if every scenario passed.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
import http.cookiejar as cookiejar
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parent
HTTP_TIMEOUT = 15          # seconds, every single HTTP request
START_TIMEOUT = 40         # seconds, waiting for a process to answer
STOP_TIMEOUT = 10          # seconds, waiting for a process to exit
EXPECTED_SCENARIOS = 18    # must match the list in Verification.run()
BANK_PASSWORD = 'demo123'  # fictional seeded demo credentials only
ADMIN = ('admin', 'admin123')


# ---------------------------------------------------------------------------- helpers
def port_in_use(port):
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(('127.0.0.1', port)) == 0


class HTTPResult:
    def __init__(self, status, body, headers, ms):
        self.status, self.body, self.headers, self.ms = status, body, headers, ms

    def json(self):
        try:
            return json.loads(self.body)
        except ValueError:
            return {}


def http(opener, method, url, data=None, headers=None, form=False, timeout=HTTP_TIMEOUT):
    headers = dict(headers or {})
    body = None
    if data is not None:
        if form:
            body = urllib.parse.urlencode(data).encode()
            headers['Content-Type'] = 'application/x-www-form-urlencoded'
        else:
            body = json.dumps(data).encode()
            headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    started = time.perf_counter()
    try:
        with (opener or urllib.request.build_opener()).open(req, timeout=timeout) as resp:
            return HTTPResult(resp.status, resp.read().decode('utf-8', 'replace'), dict(resp.headers),
                              (time.perf_counter() - started) * 1000)
    except urllib.error.HTTPError as exc:
        return HTTPResult(exc.code, exc.read().decode('utf-8', 'replace'), dict(exc.headers),
                          (time.perf_counter() - started) * 1000)
    except (urllib.error.URLError, OSError) as exc:
        return HTTPResult(0, f'CONNECTION FAILED: {exc}', {}, (time.perf_counter() - started) * 1000)


def pct(values, p):
    if not values:
        return None
    values = sorted(values)
    return round(values[min(len(values) - 1, int(round((len(values) - 1) * p)))], 2)


class Cluster:
    """Owns every process started by this verification run."""

    def __init__(self, base_port, session):
        self.base = base_port
        self.session = session
        self.db = session / 'distributed.db'
        self.queues = session / 'queues'
        self.procs = {}
        self.started = []          # every (name, Popen) ever started, for the final leftover check
        self.env = dict(os.environ,
                        FOOD_BANK_DB_PATH=str(self.db),
                        FOOD_BANK_QUEUE_DIR=str(self.queues),
                        # The central API probes node health at this address for bank N.
                        FOOD_BANK_NODE_URL_TEMPLATE=f'http://127.0.0.1:{base_port // 1000}{{bank_id:03d}}',
                        # Isolated classroom demo: demo accounts and the admin outage control.
                        FOOD_BANK_DEMO_MODE='1',
                        PYTHONDONTWRITEBYTECODE='1')
        self.env.pop('FOOD_BANK_SESSION_SECRET', None)
        self.timeouts = []

    def api_url(self):
        return f'http://127.0.0.1:{self.base}'

    def node_url(self, bank):
        return f'http://127.0.0.1:{self.base + bank}'

    def ports(self):
        return [self.base, self.base + 1, self.base + 2, self.base + 3]

    def start(self, name):
        if name == 'api':
            args = ['server.py', '--port', str(self.base)]
            health = self.api_url() + '/api/health'
        else:
            bank = int(name[-1])
            args = ['node.py', '--bank', str(bank), '--port', str(self.base + bank), '--api', self.api_url()]
            health = self.node_url(bank) + '/node-health'
        log = open(self.session / f'{name}.log', 'a', encoding='utf-8')
        started = time.perf_counter()
        proc = subprocess.Popen([sys.executable, *args], cwd=ROOT, env=self.env, stdout=log,
                                stderr=subprocess.STDOUT)
        self.procs[name] = (proc, log)
        self.started.append((name, proc))
        deadline = time.monotonic() + START_TIMEOUT
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(f'{name} exited early with code {proc.returncode}; see {log.name}')
            r = http(None, 'GET', health)
            if r.status in (200, 503):
                return round((time.perf_counter() - started) * 1000)
            time.sleep(0.25)
        self.timeouts.append(f'start {name} > {START_TIMEOUT}s')
        raise RuntimeError(f'{name} did not answer within {START_TIMEOUT}s')

    def stop(self, name):
        proc, log = self.procs.pop(name)
        proc.terminate()
        try:
            proc.wait(timeout=STOP_TIMEOUT)
        except subprocess.TimeoutExpired:
            self.timeouts.append(f'stop {name} > {STOP_TIMEOUT}s (killed)')
            proc.kill()
            proc.wait(timeout=STOP_TIMEOUT)
        log.close()

    def stop_all(self):
        for name in list(self.procs):
            try:
                self.stop(name)
            except Exception as exc:  # keep stopping the others
                print('WARNING: could not stop', name, exc, flush=True)

    def log_text(self):
        return ''.join(p.read_text(encoding='utf-8', errors='replace') for p in self.session.glob('*.log'))


class Browser:
    """One cookie jar shared by ALL node tabs, like a single real browser."""

    def __init__(self):
        self.jar = cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def get(self, url):
        return http(self.opener, 'GET', url)

    def csrf(self, node):
        page = self.get(node + '/login').body
        match = re.search(r'name="_csrf" value="([^"]+)"', page)
        if not match:
            raise RuntimeError('No CSRF token on ' + node + '/login')
        return match.group(1)

    def post(self, node, path, data):
        data = dict(data, _csrf=self.csrf(node))
        return http(self.opener, 'POST', node + path, data, form=True)

    def login(self, node, username, password):
        return self.post(node, '/login', {'username': username, 'password': password})


def flashes(html):
    block = re.search(r'<div class="messages">(.*?)</div>', html, re.S)
    return [re.sub(r'<[^>]+>', '', m).strip() for m in re.findall(r'<p>(.*?)</p>', block.group(1), re.S)] if block else []


# ---------------------------------------------------------------------------- the run
class Verification:
    def __init__(self, cluster):
        self.c = cluster
        self.results = []
        self.tokens = {}
        self.metrics = {}

    # API helpers -------------------------------------------------------------
    def api(self, method, path, data=None, bank=None, token=None, headers=None, timeout=HTTP_TIMEOUT):
        h = dict(headers or {})
        tok = token or (self.tokens.get(bank) if bank else None)
        if tok:
            h['Authorization'] = 'Bearer ' + tok
        return http(None, method, self.c.api_url() + path, data, h, timeout=timeout)

    def login_api(self, bank, username='staff', password=BANK_PASSWORD):
        r = self.api('POST', '/api/login', {'bank_id': bank, 'username': username, 'password': password})
        if r.status != 200:
            raise RuntimeError(f'API login failed for bank {bank} {username}: {r.status} {r.body[:120]}')
        return r.json()['token']

    def item(self, bank, item_id):
        board = self.api('GET', '/api/dashboard', bank=bank).json()
        return next(i for i in board['inventory'] if i['item_id'] == item_id)

    def qty(self, bank, item_id):
        return self.item(bank, item_id)['quantity']

    def admin_health(self):
        return self.api('GET', '/api/admin/health', token=self.tokens['admin']).json()

    def outage(self, bank):
        """Toggle the simulated outage on a node. This is an administrator-only classroom demo
        control, so it uses a separate admin browser (an admin account is created for banks
        other than Central the first time it is needed)."""
        if not hasattr(self, 'admin_browser'):
            self.admin_browser, self.admin_nodes = Browser(), set()
        if bank not in self.admin_nodes:
            user, password = ADMIN if bank == 1 else (f'demo-admin-{bank}', 'demo-admin-pass-1')
            if bank != 1:
                r = self.api('POST', '/api/admin/users', {'bank_id': bank, 'username': user, 'password': password,
                                                          'role': 'admin'}, token=self.tokens['admin'])
                if r.status not in (201, 409):
                    raise RuntimeError(f'could not create admin for bank {bank}: {r.status} {r.body[:120]}')
            r = self.admin_browser.login(self.c.node_url(bank), user, password)
            if r.status != 200:
                raise RuntimeError(f'admin login on node {bank} failed: {r.status}')
            self.admin_nodes.add(bank)
        return self.admin_browser.post(self.c.node_url(bank), '/offline/simulate', {})

    # scenario runner ---------------------------------------------------------
    def scenario(self, number, title, fn):
        print(f'[{number:2d}] {title} ...', flush=True)
        started = time.perf_counter()
        checks = []

        def check(label, ok, detail=None):
            checks.append({'check': label, 'passed': bool(ok), **({'detail': detail} if detail is not None else {})})
            return ok
        try:
            details = fn(check) or {}
            error = None
        except Exception as exc:
            details = {}
            error = f'{type(exc).__name__}: {exc}'
            traceback.print_exc()
        passed = error is None and bool(checks) and all(c['passed'] for c in checks)
        row = {'scenario': number, 'title': title, 'passed': passed, 'checks': checks, 'details': details,
               'duration_ms': round((time.perf_counter() - started) * 1000)}
        if error:
            row['error'] = error
        self.results.append(row)
        print(f'     -> {"PASS" if passed else "FAIL"} ({row["duration_ms"]} ms)'
              + ('' if passed else ' ' + json.dumps([c for c in checks if not c['passed']] or error)[:400]), flush=True)

    # 1 -----------------------------------------------------------------------
    def s1_launch(self, check):
        times = {'api': self.c.start('api')}
        for bank in (1, 2, 3):
            times[f'node{bank}'] = self.c.start(f'node{bank}')
        check('central API /api/health 200', self.api('GET', '/api/health').status == 200)
        for bank in (1, 2, 3):
            r = http(None, 'GET', self.c.node_url(bank) + '/node-health')
            check(f'node {bank} /node-health reports bank {bank}', r.status == 200 and r.json().get('bank_id') == bank)
        self.tokens = {b: self.login_api(b) for b in (1, 2, 3)}
        self.tokens['admin'] = self.login_api(1, *ADMIN)
        nodes = self.api('GET', '/api/nodes', bank=1).json()['nodes']
        check('central API sees all 3 nodes online', [n['online'] for n in nodes] == [True, True, True], nodes)
        pids = {name: p.pid for name, (p, _log) in self.c.procs.items()}
        check('4 distinct OS processes', len(set(pids.values())) == 4, pids)
        return {'startup_ms': times, 'pids': pids, 'ports': self.c.ports()}

    # 2 -----------------------------------------------------------------------
    def s2_auth(self, check):
        self.browser = Browser()
        names = {1: 'Central Food Pantry', 2: 'Northside Food Pantry', 3: 'Westside Food Pantry'}
        page_ms = {}
        for bank in (1, 2, 3):
            r = self.browser.login(self.c.node_url(bank), 'staff', BANK_PASSWORD)
            page_ms[bank] = round(r.ms)
            check(f'node {bank} login + dashboard', r.status == 200 and 'My inventory' in r.body and names[bank] in r.body)
        # One browser, three tabs: logging into node 2 and 3 must not log out node 1 (audit H1).
        for bank in (1, 2, 3):
            r = self.browser.get(self.c.node_url(bank) + '/')
            check(f'node {bank} still signed in after logging into the other nodes (one cookie jar)',
                  r.status == 200 and 'My inventory' in r.body, r.status)
        cookie_names = sorted({c.name for c in self.browser.jar})
        check('each node uses its own session cookie', len(cookie_names) == 3, cookie_names)
        bad = self.browser.login(self.c.node_url(2), 'staff', 'wrong-password')
        check('wrong password rejected with a clear message', 'Invalid username or password.' in bad.body)
        for bank in (1, 2, 3):
            board = self.api('GET', '/api/dashboard', bank=bank).json()
            check(f'API token for bank {bank} is scoped to bank {bank}', board['bank']['id'] == bank)
        other = self.api('GET', '/api/banks/2/inventory', bank=1).json()['inventory']
        check('another bank\'s inventory is redacted (no physical quantity)', all('quantity' not in r for r in other))
        r = self.api('PUT', '/api/inventory/1', {'quantity': 6, 'target': 20}, bank=1)
        check('bank 1 edit applies only to bank 1', r.status == 200 and self.qty(1, 1) == 6 and self.qty(2, 1) == 40,
              r.status)
        return {'node_login_page_ms': page_ms, 'cookie_names': cookie_names}

    # 3 -----------------------------------------------------------------------
    def s3_shared_view(self, check):
        before = self.api('GET', '/api/banks/2/inventory', bank=1).json()['inventory']
        before_avail = next(r['available'] for r in before if r['item_id'] == 7)
        r = self.browser.post(self.c.node_url(2), '/inventory', {'item_id': 7, 'quantity': 30, 'target': 8,
                                                                  'expiration_date': ''})
        check('node 2 form update accepted', r.status == 200 and 'Inventory updated.' in r.body, flashes(r.body))
        own = self.item(2, 7)
        check('central DB holds the new quantity', own['quantity'] == 30)
        shared = next(x for x in self.api('GET', '/api/banks/2/inventory', bank=1).json()['inventory'] if x['item_id'] == 7)
        check('bank 1 sees bank 2 availability equal to bank 2\'s own surplus', shared['available'] == own['surplus'],
              {'bank1_view': shared['available'], 'bank2_surplus': own['surplus']})
        page = self.browser.get(self.c.node_url(1) + '/?other_bank=2&other_q=Apples')
        check('node 1 page shows updated Northside Apples availability',
              page.status == 200 and re.search(rf'Apples.*?{own["surplus"]}', page.body, re.S) is not None)
        return {'apples_available_before': before_avail, 'after': own['surplus']}

    # 4 -----------------------------------------------------------------------
    def s4_exchange(self, check):
        total_before = sum(self.qty(b, 1) for b in (1, 2, 3))
        src_before, dst_before = self.qty(2, 1), self.qty(1, 1)
        key = 'p5-exchange-' + secrets.token_hex(6)
        timings = {}
        r = self.browser.post(self.c.node_url(1), '/request', {'source_bank_id': 2, 'item_id': 1, 'quantity': 6,
                                                                'request_key': key})
        timings['request'] = round(r.ms)
        check('node 1 request sent', 'Request sent.' in r.body, flashes(r.body))
        with closing(sqlite3.connect(f'file:{self.c.db.as_posix()}?mode=ro', uri=True)) as con:
            ex_id = con.execute('SELECT id FROM exchanges WHERE request_key=?', (key,)).fetchone()[0]
        for action, bank in (('approve', 2), ('ship', 2), ('receive', 1)):
            r = self.browser.post(self.c.node_url(bank), f'/exchange/{ex_id}/{action}', {})
            timings[action] = round(r.ms)
            check(f'{action} via node {bank}', r.status == 200 and not any(f.startswith('Error') for f in flashes(r.body)),
                  flashes(r.body))
        with closing(sqlite3.connect(f'file:{self.c.db.as_posix()}?mode=ro', uri=True)) as con:
            status = con.execute('SELECT status FROM exchanges WHERE id=?', (ex_id,)).fetchone()[0]
            shipped = con.execute('SELECT COALESCE(SUM(quantity),0) FROM shipment_lots WHERE exchange_id=?', (ex_id,)).fetchone()[0]
        check('transfer status is received', status == 'received', status)
        check('source decreased by 6', self.qty(2, 1) == src_before - 6, (src_before, self.qty(2, 1)))
        check('destination increased by 6', self.qty(1, 1) == dst_before + 6, (dst_before, self.qty(1, 1)))
        check('shipped batches equal transfer quantity', shipped == 6, shipped)
        total_after = sum(self.qty(b, 1) for b in (1, 2, 3))
        check('total Rice across banks conserved', total_after == total_before, (total_before, total_after))
        health = self.admin_health()
        check('database invariants pass after the exchange', health['overall'] == 'pass',
              [c for c in health['checks'] if c['status'] != 'pass'])
        return {'exchange_id': ex_id, 'node_action_ms': timings, 'rice_total': total_after}

    # 5 -----------------------------------------------------------------------
    def s5_node_disconnect(self, check):
        self.c.stop('node3')
        nodes = {n['bank_id']: n['online'] for n in self.api('GET', '/api/nodes', bank=1).json()['nodes']}
        check('node 3 process stopped -> central reports it offline', nodes.get(3) is False, nodes)
        check('nodes 1 and 2 still online', nodes.get(1) and nodes.get(2), nodes)
        r = self.browser.get(self.c.node_url(1) + '/')
        check('node 1 keeps working while node 3 is down', r.status == 200 and 'My inventory' in r.body)
        r = self.api('POST', '/api/exchanges', {'source_bank_id': 3, 'item_id': 2, 'quantity': 1,
                                                'request_key': 'p5-to-offline-node-01'}, bank=2)
        check('central API still accepts requests addressed to the offline bank', r.status in (201, 409), r.body[:160])
        # Simulated link failure on node 1 (node process up, its link to the API "cut").
        r = self.outage(1)
        check('node 1 simulated disconnect enabled', 'OFFLINE' in r.body)
        hc = http(None, 'GET', self.c.node_url(1) + '/node-health')
        check('node 1 health reports simulated_offline (503)', hc.status == 503 and hc.json().get('status') == 'simulated_offline')
        page = self.browser.get(self.c.node_url(1) + '/')
        check('node 1 shows its offline page with cached snapshot (503)', page.status == 503 and 'Simulated connection failure' in page.body)
        return {'node_status_while_node3_down': nodes}

    # 6 -----------------------------------------------------------------------
    def s6_queue_offline(self, check):
        central_before = {i: self.qty(1, i) for i in (2, 6)}
        r = self.browser.post(self.c.node_url(1), '/offline/queue', {'item_id': 2, 'delta': 3})
        check('stock adjustment queued locally', 'Adjustment saved locally' in r.body, flashes(r.body))
        r = self.browser.post(self.c.node_url(1), '/offline/queue', {'item_id': 2, 'delta': 1})
        check('second change to the same item refused (one pending change per item)',
              'Only one unsynchronized change per item' in r.body, flashes(r.body))
        r = self.browser.post(self.c.node_url(1), '/advanced/receipt/queue', {'item_id': 6, 'quantity': 4,
                                                                             'donor': 'Offline school drive'})
        check('independent receipt queued locally', r.status in (200, 503) and 'queued safely' in r.body, flashes(r.body))
        with closing(sqlite3.connect(f'file:{(self.c.queues / "node_1_queue.db").as_posix()}?mode=ro', uri=True)) as q:
            pending = q.execute("SELECT COUNT(*) FROM queued WHERE status='pending'").fetchone()[0]
            receipts = q.execute("SELECT COUNT(*) FROM receipt_queue WHERE status='pending'").fetchone()[0]
            secrets_on_disk = q.execute("SELECT COUNT(*) FROM snapshot WHERE data_json LIKE '%token%' "
                                        "OR data_json LIKE '%password%'").fetchone()[0]
        check('queue file holds 1 adjustment + 1 receipt', (pending, receipts) == (1, 1), (pending, receipts))
        check('no token or password stored in the node queue', secrets_on_disk == 0)
        check('central database unchanged while offline', {i: self.qty(1, i) for i in (2, 6)} == central_before)
        self.offline_before = central_before
        return {'queued_adjustments': pending, 'queued_receipts': receipts}

    # 7 -----------------------------------------------------------------------
    def s7_reconnect(self, check):
        r = self.outage(1)
        check('node 1 reconnected', 'ONLINE' in r.body)
        t = time.perf_counter()
        r = self.browser.post(self.c.node_url(1), '/offline/sync', {})
        sync_ms = round((time.perf_counter() - t) * 1000)
        check('adjustment synchronized', 'Synchronized 1 changes; 0 conflicts.' in r.body, flashes(r.body))
        r = self.browser.post(self.c.node_url(1), '/advanced/receipt/sync', {})
        check('receipt synchronized', 'Receipt sync: 1 synchronized; 0 conflicts.' in r.body, flashes(r.body))
        after = {i: self.qty(1, i) for i in (2, 6)}
        check('central quantities include exactly the offline changes',
              after == {2: self.offline_before[2] + 3, 6: self.offline_before[6] + 4}, (self.offline_before, after))
        r = self.browser.post(self.c.node_url(1), '/offline/sync', {})
        check('re-running sync applies nothing twice', 'Synchronized 0 changes' in r.body and
              {i: self.qty(1, i) for i in (2, 6)} == after)
        t = time.perf_counter()
        self.c.start('node3')
        restart_ms = round((time.perf_counter() - t) * 1000)
        nodes = {n['bank_id']: n['online'] for n in self.api('GET', '/api/nodes', bank=1).json()['nodes']}
        check('node 3 restarted and reported online again', nodes == {1: True, 2: True, 3: True}, nodes)
        r = self.browser.get(self.c.node_url(3) + '/')
        check('restarted node 3 asks for a fresh login (new process secret)', 'Log in' in r.body or '/login' in r.body)
        r = self.browser.login(self.c.node_url(3), 'staff', BANK_PASSWORD)
        check('node 3 login works after restart', r.status == 200 and 'My inventory' in r.body)
        return {'sync_ms': sync_ms, 'node3_restart_ms': restart_ms}

    # 8 -----------------------------------------------------------------------
    def s8_conflicts(self, check):
        node2 = self.c.node_url(2)
        self.browser.get(node2 + '/')                      # refresh node 2 snapshot (versions)
        # Item that will disappear centrally while node 2 is offline (audit M2 reproduction).
        r = self.api('POST', '/api/inventory', {'name': 'P5 Test Crackers', 'unit': 'boxes', 'category': 'Dry Goods',
                                                'quantity': 0, 'target': 5}, bank=2)
        check('helper item created', r.status == 201, r.body[:120])
        temp_item = next(i['item_id'] for i in self.api('GET', '/api/dashboard', bank=2).json()['inventory']
                         if i['item_name'] == 'P5 Test Crackers')
        self.browser.get(node2 + '/')
        before3, before4 = self.item(2, 3), self.item(2, 4)
        self.outage(2)
        q1 = self.browser.post(node2, '/offline/queue', {'item_id': 3, 'delta': -2})   # will go stale
        q2 = self.browser.post(node2, '/offline/queue', {'item_id': temp_item, 'delta': 5})  # item will vanish
        q3 = self.browser.post(node2, '/offline/queue', {'item_id': 4, 'delta': 2})   # independent and valid
        check('3 changes queued offline', all('Adjustment saved locally' in q.body for q in (q1, q2, q3)),
              [flashes(q.body) for q in (q1, q2, q3)])
        # Meanwhile someone edits item 3 centrally and removes the helper item.
        r = self.api('PUT', '/api/inventory/3', {'quantity': before3['quantity'] + 10, 'target': before3['target']}, bank=2)
        check('concurrent central edit applied', r.status == 200)
        r = self.api('DELETE', f'/api/inventory/{temp_item}', bank=2)
        check('helper item removed centrally', r.status == 200, r.body[:120])
        self.outage(2)
        r = self.browser.post(node2, '/offline/sync', {})
        msg = flashes(r.body)
        check('stale adjustment detected as a conflict, not applied',
              self.item(2, 3)['quantity'] == before3['quantity'] + 10, msg)
        check('a permanently invalid queued change does not block later ones (M2)',
              self.item(2, 4)['quantity'] == before4['quantity'] + 2, msg)
        with closing(sqlite3.connect(f'file:{(self.c.queues / "node_2_queue.db").as_posix()}?mode=ro', uri=True)) as q:
            states = {row[0]: (row[1], row[2]) for row in q.execute('SELECT item_id,status,error FROM queued')}
        check('stale change kept for operator review with a reason', states.get(3, ('',))[0] == 'conflict'
              and 'Conflict' in states[3][1], states.get(3))
        check('rejected change kept for review with the API reason', states.get(temp_item, ('',))[0] == 'conflict'
              and 'not exist' in states[temp_item][1], states.get(temp_item))
        check('valid change synced', states.get(4, ('',))[0] == 'synced', states.get(4))
        # Direct API: stale base_version is refused.
        v = self.item(2, 5)['version']
        r1 = self.api('POST', '/api/inventory/5/adjust', {'delta': 1, 'base_version': v, 'op_key': 'p5-stale-aaaa1'}, bank=2)
        r2 = self.api('POST', '/api/inventory/5/adjust', {'delta': 1, 'base_version': v, 'op_key': 'p5-stale-aaaa2'}, bank=2)
        check('second adjustment with the same old version gets 409', (r1.status, r2.status) == (200, 409),
              (r1.status, r2.json().get('error')))
        return {'sync_message': msg, 'queue_states': {str(k): v[0] for k, v in states.items()}}

    # 9 -----------------------------------------------------------------------
    def s9_idempotent(self, check):
        before = self.qty(3, 2)
        payload = {'op_key': 'p5-receipt-' + secrets.token_hex(8), 'item_id': 2, 'quantity': 5,
                   'donor': 'Fictional food drive', 'lot_code': 'P5-LOT-0001'}
        replies = [self.api('POST', '/api/offline/receipts', payload, bank=3) for _ in range(3)]
        check('first receipt merged (201), retries report duplicate',
              replies[0].status == 201 and all(r.json().get('duplicate') for r in replies[1:]),
              [(r.status, r.json().get('duplicate')) for r in replies])
        check('quantity added exactly once', self.qty(3, 2) == before + 5, (before, self.qty(3, 2)))
        changed = dict(payload, quantity=6)
        r = self.api('POST', '/api/offline/receipts', changed, bank=3)
        check('same key with different data refused (409)', r.status == 409, r.json().get('error'))
        # Commutative: a receipt still merges after other edits changed the version.
        v = self.item(3, 2)['version']
        self.api('PUT', '/api/inventory/2', {'quantity': self.qty(3, 2) + 1, 'target': self.item(3, 2)['target']}, bank=3)
        r = self.api('POST', '/api/offline/receipts', dict(payload, op_key='p5-receipt-' + secrets.token_hex(8),
                                                           lot_code='P5-LOT-0002'), bank=3)
        check('independent receipt merges across a version change', r.status == 201 and self.item(3, 2)['version'] != v)
        # Adjustment replay with the same op_key.
        v = self.item(3, 3)['version']
        a = {'delta': 2, 'base_version': v, 'op_key': 'p5-adjust-replay-01'}
        r1, r2 = (self.api('POST', '/api/inventory/3/adjust', a, bank=3) for _ in range(2))
        check('replayed adjustment is a no-op duplicate', r1.status == 200 and r2.json().get('duplicate') is True)
        # Two different banks replaying receipts concurrently.
        def receipt(i):
            return self.api('POST', '/api/offline/receipts', {'op_key': f'p5-concurrent-receipt-{i:03d}', 'item_id': 8,
                            'quantity': 1, 'donor': 'Parallel drive', 'lot_code': f'P5-PAR-{i:03d}'}, bank=1).status
        b8 = self.qty(1, 8)
        with ThreadPoolExecutor(max_workers=8) as pool:
            codes = [f.result(timeout=60) for f in [pool.submit(receipt, i % 10) for i in range(30)]]
        check('30 concurrent replays of 10 receipts add exactly 10 units', self.qty(1, 8) == b8 + 10 and
              all(c in (200, 201) for c in codes), (b8, self.qty(1, 8), sorted(set(codes))))
        return {}

    # 10 ----------------------------------------------------------------------
    def s10_simultaneous(self, check):
        # Cereal: bank 2 has a known surplus of 10; banks 1 and 3 each need more than that.
        self.api('PUT', '/api/inventory/5', {'quantity': 10, 'target': 0}, bank=2)
        for bank in (1, 3):
            self.api('PUT', '/api/inventory/5', {'quantity': 0, 'target': 40}, bank=bank)
        surplus = self.item(2, 5)['surplus']
        check('bank 2 surplus set to 10', surplus == 10, surplus)
        reqs = {}
        def request(bank):
            return bank, self.api('POST', '/api/exchanges', {'source_bank_id': 2, 'item_id': 5, 'quantity': 7,
                                  'request_key': f'p5-race-bank{bank}-0001'}, bank=bank)
        with ThreadPoolExecutor(max_workers=2) as pool:
            for bank, r in pool.map(request, (1, 3)):
                reqs[bank] = r
        check('both competing requests recorded (201)', all(r.status == 201 for r in reqs.values()),
              {b: r.status for b, r in reqs.items()})
        ids = [r.json()['exchange_id'] for r in reqs.values()]
        barrier = threading.Barrier(len(ids))
        def approve(ex_id):
            barrier.wait(timeout=10)
            return self.api('POST', f'/api/exchanges/{ex_id}/approve', bank=2)
        with ThreadPoolExecutor(max_workers=len(ids)) as pool:
            outcomes = [f.result(timeout=60) for f in [pool.submit(approve, i) for i in ids]]
        codes = sorted(r.status for r in outcomes)
        check('exactly one simultaneous approval succeeds, the other gets 409', codes == [200, 409],
              [(r.status, r.json().get('error')) for r in outcomes])
        item = self.item(2, 5)
        check('reserved stock equals the one approved transfer', item['reserved'] == 7, item['reserved'])
        check('surplus never goes negative', item['surplus'] == 3, item['surplus'])
        # Burst: 10 concurrent requests with distinct keys, then 10 concurrent approvals.
        self.api('PUT', '/api/inventory/6', {'quantity': 25, 'target': 0}, bank=2)
        self.api('PUT', '/api/inventory/6', {'quantity': 0, 'target': 200}, bank=1)
        def req6(i):
            return self.api('POST', '/api/exchanges', {'source_bank_id': 2, 'item_id': 6, 'quantity': 4,
                            'request_key': f'p5-burst-{i:04d}-xx'}, bank=1)
        with ThreadPoolExecutor(max_workers=10) as pool:
            created = [f.result(timeout=60) for f in [pool.submit(req6, i) for i in range(10)]]
        burst_ids = [r.json()['exchange_id'] for r in created if r.status == 201]
        with ThreadPoolExecutor(max_workers=10) as pool:
            approvals = [f.result(timeout=60) for f in
                         [pool.submit(lambda e: self.api('POST', f'/api/exchanges/{e}/approve', bank=2), e) for e in burst_ids]]
        ok = sum(r.status == 200 for r in approvals)
        check('10 parallel approvals of 4 units against 25 available: exactly 6 succeed', ok == 6,
              sorted(r.status for r in approvals))
        check('no over-reservation', self.item(2, 6)['reserved'] == 24, self.item(2, 6)['reserved'])
        health = self.admin_health()
        check('invariants pass after the race', health['overall'] == 'pass',
              [c for c in health['checks'] if c['status'] != 'pass'])
        self.race_ids = ids
        return {'race_codes': codes, 'burst_approved': ok}

    # 11 ----------------------------------------------------------------------
    def s11_retries(self, check):
        key = 'p5-retry-' + secrets.token_hex(6)
        body = {'source_bank_id': 3, 'item_id': 8, 'quantity': 2, 'request_key': key}
        self.api('PUT', '/api/inventory/8', {'quantity': self.qty(1, 8), 'target': self.qty(1, 8) + 20}, bank=1)
        replies = [self.api('POST', '/api/exchanges', body, bank=1) for _ in range(3)]
        ids = {r.json().get('exchange_id') for r in replies}
        check('3 retries of one request return one exchange id', len(ids) == 1 and replies[0].status == 201 and
              all(r.status == 200 for r in replies[1:]), [(r.status, r.json().get('exchange_id')) for r in replies])
        with ThreadPoolExecutor(max_workers=8) as pool:
            par = [f.result(timeout=60) for f in [pool.submit(self.api, 'POST', '/api/exchanges', body, 1) for _ in range(8)]]
        check('8 concurrent retries still one exchange', {r.json().get('exchange_id') for r in par} == ids,
              sorted({r.status for r in par}))
        with closing(sqlite3.connect(f'file:{self.c.db.as_posix()}?mode=ro', uri=True)) as con:
            rows = con.execute('SELECT COUNT(*) FROM exchanges WHERE request_key=?', (key,)).fetchone()[0]
        check('database has exactly one row for the key', rows == 1, rows)
        r = self.api('POST', '/api/exchanges', dict(body, quantity=3), bank=1)
        check('same key with different details refused', r.status == 409, r.json().get('error'))
        node_key = 'p5-node-retry-' + secrets.token_hex(4)
        for _ in range(2):
            self.browser.post(self.c.node_url(1), '/request', {'source_bank_id': 3, 'item_id': 8, 'quantity': 1,
                                                               'request_key': node_key})
        with closing(sqlite3.connect(f'file:{self.c.db.as_posix()}?mode=ro', uri=True)) as con:
            rows = con.execute('SELECT COUNT(*) FROM exchanges WHERE request_key=?', (node_key,)).fetchone()[0]
        check('double-submitted node form creates one transfer', rows == 1, rows)
        ex = ids.pop()
        a1 = self.api('POST', f'/api/exchanges/{ex}/approve', bank=3)
        a2 = self.api('POST', f'/api/exchanges/{ex}/approve', bank=3)
        s1 = self.api('POST', f'/api/exchanges/{ex}/ship', bank=3)
        s2 = self.api('POST', f'/api/exchanges/{ex}/ship', bank=3)
        check('retried approve/ship are refused, stock moved once', (a1.status, a2.status, s1.status, s2.status) ==
              (200, 409, 200, 409), (a1.status, a2.status, s1.status, s2.status))
        return {}

    # 12 ----------------------------------------------------------------------
    def s12_api_down(self, check):
        self.browser.get(self.c.node_url(1) + '/')            # fresh snapshot
        before = self.qty(1, 7)
        self.c.stop('api')
        down_at = time.perf_counter()
        r = http(None, 'GET', self.c.api_url() + '/api/health')
        check('central API really unreachable', r.status == 0, r.body[:80])
        page = self.browser.get(self.c.node_url(1) + '/')
        check('node 1 shows offline page (503) with a clear message',
              page.status == 503 and 'Central API is offline or not responding.' in page.body, page.status)
        check('offline page still shows the cached snapshot', 'Rice' in page.body)
        hc = http(None, 'GET', self.c.node_url(1) + '/node-health')
        check('node process itself stays healthy', hc.status == 200)
        r = self.browser.post(self.c.node_url(1), '/offline/queue', {'item_id': 7, 'delta': 2})
        check('change queued during the real outage', 'Adjustment saved locally' in r.body, flashes(r.body))
        r = self.browser.post(self.c.node_url(1), '/offline/sync', {})
        check('sync during outage fails safely with a reason, nothing lost',
              'Central API is offline or not responding.' in r.body, flashes(r.body))
        fresh = Browser()
        r = fresh.login(self.c.node_url(2), 'staff', BANK_PASSWORD)
        check('login during outage gives a clear error', 'Central API is offline or not responding.' in r.body,
              flashes(r.body))
        restart = self.c.start('api')
        outage_s = round(time.perf_counter() - down_at, 2)
        page = self.browser.get(self.c.node_url(1) + '/')
        check('after API restart node 1 session still valid (tokens are stored centrally)',
              page.status == 200 and 'My inventory' in page.body, page.status)
        r = self.browser.post(self.c.node_url(1), '/offline/sync', {})
        check('queued change synchronized after recovery', 'Synchronized 1 changes; 0 conflicts.' in r.body, flashes(r.body))
        check('central quantity includes the outage change once', self.qty(1, 7) == before + 2, (before, self.qty(1, 7)))
        return {'outage_seconds': outage_s, 'api_restart_ms': restart}

    # 13 ----------------------------------------------------------------------
    def s13_errors(self, check):
        cases = {
            'over-request': self.api('POST', '/api/exchanges', {'source_bank_id': 2, 'item_id': 5, 'quantity': 999,
                                                                'request_key': 'p5-too-much-0001'}, bank=1),
            'wrong bank approves': self.api('POST', f'/api/exchanges/{self.race_ids[0]}/approve', bank=3),
            'malformed JSON': http(None, 'POST', self.c.api_url() + '/api/exchanges', None,
                                   {'Authorization': 'Bearer ' + self.tokens[1], 'Content-Type': 'application/json'}),
            'unknown item': self.api('PUT', '/api/inventory/99999', {'quantity': 1, 'target': 1}, bank=1),
            'expired token': self.api('GET', '/api/dashboard', token='not-a-real-token'),
            'staff calls admin API': self.api('GET', '/api/admin/overview', bank=2),
        }
        summary = {}
        for label, r in cases.items():
            err = r.json().get('error', '')
            summary[label] = (r.status, err)
            check(f'{label}: 4xx with a readable message', 400 <= r.status < 500 and len(err) > 8, (r.status, err))
        r = self.browser.post(self.c.node_url(1), '/request', {'source_bank_id': 2, 'item_id': 5, 'quantity': 999,
                                                                'request_key': 'p5-node-too-much-01'})
        check('node shows API error as a flash message', any(f.startswith('Error:') for f in flashes(r.body)),
              flashes(r.body))
        with closing(sqlite3.connect(f'file:{self.c.db.as_posix()}?mode=ro', uri=True)) as con:
            audited = con.execute("SELECT COUNT(*) FROM audit_events WHERE result='failed'").fetchone()[0]
            five = con.execute('SELECT COUNT(*) FROM distributed_events WHERE status_code>=500').fetchone()[0]
        check('failed operations audited centrally', audited >= 5, audited)
        check('no HTTP 5xx recorded by the central API', five == 0, five)
        ok = self.api('POST', '/api/exchanges', {'source_bank_id': 2, 'item_id': 5, 'quantity': 1,
                                                 'request_key': 'p5-after-errors-01'}, bank=1)
        check('valid operation succeeds right after the failures', ok.status == 201, ok.body[:120])
        health = self.admin_health()
        check('database invariants still pass', health['overall'] == 'pass',
              [c for c in health['checks'] if c['status'] != 'pass'])
        return {'errors': {k: list(v) for k, v in summary.items()}}

    # 14 ----------------------------------------------------------------------
    def s14_scale(self, check):
        admin = self.tokens['admin']
        for bank in range(4, 21):
            r = self.api('POST', '/api/admin/banks', {'name': f'Simulated Pantry {bank:02d}', 'area': 'Load test',
                         'latitude': 34.7 + bank / 1000, 'longitude': -92.3, 'staff_password': 'simulated-pass'},
                         token=admin)
            if r.status != 201:
                raise RuntimeError(f'could not add simulated bank {bank}: {r.status} {r.body[:120]}')
        for bank in range(4, 21):
            self.tokens[bank] = self.login_api(bank, 'staff', 'simulated-pass')
            r = self.api('POST', '/api/inventory', {'name': 'Rice', 'unit': 'bags', 'category': 'Dry Goods',
                                                    'quantity': 50, 'target': 10}, bank=bank)
            if r.status != 201:
                raise RuntimeError(f'could not stock bank {bank}: {r.status} {r.body[:120]}')
        rounds = 10
        results = []
        for count in (5, 10, 20):
            banks = list(range(1, count + 1))
            start_qty = {b: self.qty(b, 1) for b in banks}
            latencies, errors = [], []
            lock = threading.Lock()

            def worker(bank):
                for i in range(rounds):
                    r1 = self.api('GET', '/api/dashboard', bank=bank)
                    item = next(x for x in r1.json()['inventory'] if x['item_id'] == 1)
                    r2 = self.api('POST', '/api/inventory/1/adjust', {'delta': 1, 'base_version': item['version'],
                                  'op_key': f'p5-scale-{count}-{bank}-{i}-{secrets.token_hex(3)}'}, bank=bank)
                    with lock:
                        latencies.extend([r1.ms, r2.ms])
                        for r in (r1, r2):
                            if r.status != 200:
                                errors.append((bank, r.status, r.body[:100]))
            t0 = time.perf_counter()
            with ThreadPoolExecutor(max_workers=count) as pool:
                for f in [pool.submit(worker, b) for b in banks]:
                    f.result(timeout=300)
            duration = time.perf_counter() - t0
            correct = all(self.qty(b, 1) == start_qty[b] + rounds for b in banks)
            row = {'banks': count, 'concurrent_clients': count, 'requests': len(latencies),
                   'errors': len(errors), 'duration_s': round(duration, 2),
                   'throughput_req_per_s': round(len(latencies) / duration, 1),
                   'p50_ms': pct(latencies, .5), 'p95_ms': pct(latencies, .95), 'max_ms': round(max(latencies), 1),
                   'final_quantities_correct': correct, 'error_examples': errors[:3]}
            results.append(row)
            check(f'{count} banks: {len(latencies)} HTTP requests, 0 errors, quantities exact',
                  not errors and correct, {k: row[k] for k in ('errors', 'final_quantities_correct')})
        health = self.admin_health()
        check('integrity and invariants pass after load', health['overall'] == 'pass',
              [c for c in health['checks'] if c['status'] != 'pass'])
        from benchmark import run_all
        sqlite_bench = run_all()
        check('benchmark.py SQLite cases (5/10/20) pass', all(r['passed'] for r in sqlite_bench['results']))
        self.metrics['http_scale'] = results
        self.metrics['sqlite_benchmark'] = sqlite_bench['results']
        return {'http_scale': results}

    # 15 ----------------------------------------------------------------------
    def s15_backup(self, check):
        admin = self.tokens['admin']
        r = self.api('POST', '/api/admin/backups', {'note': 'Distributed verification run'}, token=admin)
        check('online backup created and verified while 4 services run', r.status == 201 and r.json().get('verified'),
              r.body[:200])
        b = r.json()
        live_banks = self.api('GET', '/api/admin/overview', token=admin).json()['food_banks']
        v = self.api('POST', f'/api/admin/backups/{b["backup_id"]}/verify-restore', {}, token=admin)
        check('isolated restore verification passed', v.status == 200 and v.json().get('passed'),
              [s for s in v.json().get('steps', []) if not s['passed']])
        check('live database not overwritten', self.api('GET', '/api/admin/overview', token=admin).json()['food_banks']
              == live_banks)
        dest = self.c.session / 'cli_restore_candidate.db'
        src = self.c.db.parent / 'admin_backups' / b['file_name']
        cli = subprocess.run([sys.executable, 'restore_database.py', '--from-backup', str(src), '--destination',
                              str(dest), '--apply'], cwd=ROOT, capture_output=True, text=True, timeout=60)
        check('restore_database.py restores to a NEW file and verifies it', cli.returncode == 0 and dest.is_file(),
              (cli.stdout + cli.stderr)[-200:])
        cli2 = subprocess.run([sys.executable, 'restore_database.py', '--from-backup', str(src), '--destination',
                               str(self.c.db), '--apply'], cwd=ROOT, capture_output=True, text=True, timeout=60)
        check('restore_database.py refuses to overwrite the live database', cli2.returncode != 0
              and 'NEW, unused path' in cli2.stdout + cli2.stderr)
        lab = self.api('POST', '/api/admin/lab/run', {'scenario': 'all'}, token=admin, timeout=120)
        check('isolated test lab 8/8 via the running API', lab.status == 200 and lab.json().get('passed') == 8
              and lab.json().get('total') == 8, lab.body[:200])
        return {'backup_file': b.get('file_name'), 'size_bytes': b.get('size_bytes'),
                'lab': [(x['scenario'], x['passed'], x['duration_ms']) for x in lab.json().get('results', [])]}

    # 16 ----------------------------------------------------------------------
    def s16_observability(self, check):
        admin = self.tokens['admin']
        for bank in (1, 2, 3):
            r = http(None, 'GET', self.c.node_url(bank) + '/node-health')
            check(f'node {bank} health ok', r.status == 200 and r.json() == {'status': 'ok', 'bank_id': bank})
        status = self.api('GET', '/api/admin/system/status', token=admin).json()
        check('system status: database healthy', status['database']['healthy'] is True)
        check('system status: 3 nodes online (simulated banks 4-20 have no node process)', status['online_nodes'] == 3,
              status['online_nodes'])
        trace_id = 'p5trace' + secrets.token_hex(8)
        r = self.api('GET', '/api/dashboard', bank=2, headers={'X-Request-ID': trace_id})
        check('X-Request-ID echoed by the API', r.headers.get('X-Request-ID') == trace_id)
        t = self.api('GET', f'/api/traces/{trace_id}', token=admin).json()
        check('trace lookup finds the request', t['count'] == 1 and t['events'][0]['path'] == '/api/dashboard', t)
        t1 = self.api('GET', f'/api/traces/{trace_id}', bank=1).json()
        check('bank 1 cannot see bank 2 trace (bank-scoped)', t1['count'] == 0)
        # Correlation from a node-originated request to the central log.
        self.browser.get(self.c.node_url(3) + '/')
        with closing(sqlite3.connect(f'file:{(self.c.queues / "node_3_queue.db").as_posix()}?mode=ro', uri=True)) as q:
            cid, path = q.execute("SELECT correlation_id,path FROM node_events WHERE path='/api/dashboard' "
                                  'ORDER BY id DESC LIMIT 1').fetchone()
        t = self.api('GET', f'/api/traces/{cid}', token=admin).json()
        check('node 3 local event ID found in the central trace log', t['count'] == 1 and t['events'][0]['bank_id'] == 3,
              (cid, t['count']))
        events = self.api('GET', '/api/events', token=admin)
        check('event log endpoint returns recent events', events.status == 200 and len(events.json().get('events', [])) > 0)
        # The node's own request log is on the System status page in the multi-page interface.
        page = self.browser.get(self.c.node_url(1) + '/project-status')
        check('node page lists its own request IDs', page.status == 200 and re.search(r'[0-9a-f]{24}', page.body))
        return {'status_overall': status['overall'], 'http_events_recorded': status['http_events_recorded'],
                'http_errors_recorded': status['http_errors_recorded']}

    # 17 ----------------------------------------------------------------------
    def s17_outage_authorization(self, check):
        node1 = self.c.node_url(1)

        def health():
            return http(None, 'GET', node1 + '/node-health').status

        check('node 1 starts online', health() == 200)
        r = self.browser.post(node1, '/offline/simulate', {})          # staff session from scenario 2
        check('staff outage attempt refused with 403', r.status == 403, r.status)
        check('state unchanged after staff attempt', health() == 200)
        r = self.api('POST', '/api/admin/users', {'bank_id': 1, 'username': 'p8-viewer', 'password': 'demo-pass-123',
                                                  'role': 'viewer'}, token=self.tokens['admin'])
        viewer = Browser()
        viewer.login(node1, 'p8-viewer', 'demo-pass-123')
        r = viewer.post(node1, '/offline/simulate', {})
        check('viewer outage attempt refused with 403', r.status == 403, r.status)
        anonymous = Browser()
        r = anonymous.post(node1, '/offline/simulate', {})
        check('logged-out outage attempt refused with 401', r.status == 401, r.status)
        check('state unchanged after refused attempts', health() == 200)
        r = self.outage(1)
        check('administrator switches the simulated outage on', 'OFFLINE' in r.body and health() == 503)
        page = self.browser.get(node1 + '/')
        check('staff see the offline page without a restore button',
              page.status == 503 and 'Restore simulated connection' not in page.body
              and 'An administrator can restore the connection' in page.body)
        r = self.browser.post(node1, '/offline/simulate', {})
        check('staff cannot end the outage either (403, still offline)', r.status == 403 and health() == 503)
        r = self.outage(1)
        check('administrator restores the connection', 'ONLINE' in r.body and health() == 200)
        page = self.browser.get(node1 + '/')
        check('staff dashboard works again after recovery', page.status == 200 and 'My inventory' in page.body)
        with closing(sqlite3.connect(f'file:{(self.c.queues / "node_1_queue.db").as_posix()}?mode=ro', uri=True)) as q:
            results = [row[0] for row in q.execute("SELECT result FROM node_events WHERE path='/offline/simulate'")]
        denied = [x for x in results if x.startswith('DENIED')]
        allowed = [x for x in results if x.startswith('simulated connection set')]
        check('refused attempts are logged on the node', len(denied) >= 4, denied)
        check('successful outage changes are logged with the admin name', len(allowed) >= 2
              and all('admin' in x for x in allowed), allowed)
        return {'denied_logged': len(denied), 'changes_logged': len(allowed)}

    # 18 ----------------------------------------------------------------------
    def s18_independent_search(self, check):
        for bank in (1, 2, 3):
            data = self.api('GET', f'/api/banks/{bank}/inventory?q=rice', bank=bank).json()
            names = [i['item_name'] for i in data['inventory']]
            check(f'bank {bank} searches its own inventory', not data['shared'] and names
                  and all('rice' in n.lower() for n in names) and data['filtered_total'] < data['total'], names)
            page = self.browser.get(self.c.node_url(bank) + '/?q=rice&status=all#inventory')
            check(f'node {bank} search page loads', page.status == 200 and 'Rice' in page.body)
        other = self.api('GET', '/api/banks/2/inventory?q=beans&status=available', bank=1).json()
        check('bank 1 searches bank 2 separately and sees only public fields', other['shared'] and all(
            set(i) == {'item_id', 'item_name', 'category', 'unit', 'available'} and i['available'] > 0 and 'beans' in i['item_name'].lower()
            for i in other['inventory']), other['inventory'][:2])
        before = self.qty(2, 1)
        r = self.api('PUT', '/api/inventory/1?bank_id=2', {'bank_id': 2, 'quantity': 1, 'target': 1}, bank=1)
        check("bank 1 cannot change bank 2's inventory through request parameters", self.qty(2, 1) == before, r.status)
        return {}

    def run(self):
        order = [(1, 'Launch central API and three separate bank nodes', self.s1_launch),
                 (2, 'Each node authenticates and reads its own inventory', self.s2_auth),
                 (3, 'Update at one bank is visible, accurately, from another', self.s3_shared_view),
                 (4, 'Exchange from request through receipt across nodes', self.s4_exchange),
                 (5, 'One node disconnects', self.s5_node_disconnect),
                 (6, 'Supported offline actions are queued safely', self.s6_queue_offline),
                 (7, 'Reconnect and synchronize', self.s7_reconnect),
                 (8, 'Stale updates and synchronization conflicts', self.s8_conflicts),
                 (9, 'Idempotent additive operations synchronize safely', self.s9_idempotent),
                 (10, 'Simultaneous requests for the same supplies', self.s10_simultaneous),
                 (11, 'Duplicate API retries do not duplicate transfers', self.s11_retries),
                 (12, 'Central API unavailable', self.s12_api_down),
                 (13, 'Failed operations give meaningful errors and recover', self.s13_errors),
                 (14, 'Controlled load with 5, 10 and 20 banks', self.s14_scale),
                 (15, 'Backup and isolated restore checks', self.s15_backup),
                 (16, 'Node health, trace IDs, status endpoints and event logs', self.s16_observability),
                 (17, 'Only administrators can simulate an outage; recovery works', self.s17_outage_authorization),
                 (18, 'Each bank searches its inventory independently; no cross-bank edits', self.s18_independent_search)]
        for number, title, fn in order:
            self.scenario(number, title, fn)
            if number == 1 and not self.results[-1]['passed']:
                print('Launch failed; remaining scenarios cannot run.', flush=True)
                break


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--base-port', type=int, default=25000,
                    help='multiple of 1000; API uses it, nodes use +1..+3 (default 25000)')
    ap.add_argument('--out', default=str(ROOT / 'reports' / 'distributed_verification.json'))
    ap.add_argument('--cleanup', action='store_true', help='remove this run\'s temporary session folder at the end')
    args = ap.parse_args()
    if args.base_port % 1000 or not 10000 <= args.base_port <= 60000:
        raise SystemExit('--base-port must be a multiple of 1000 between 10000 and 60000.')
    session = Path(tempfile.mkdtemp(prefix='foodbank_distributed_'))
    cluster = Cluster(args.base_port, session)
    busy = [p for p in cluster.ports() if port_in_use(p)]
    if busy:
        raise SystemExit(f'Ports already in use: {busy}. Nothing was started. Choose another --base-port.')
    print('Isolated session:', session, flush=True)
    v = Verification(cluster)
    started = datetime.now(timezone.utc)
    try:
        v.run()
    finally:
        cluster.stop_all()
    # Windows may take a moment to release a terminated process's listening socket.
    deadline = time.monotonic() + 10
    still_open = [p for p in cluster.ports() if port_in_use(p)]
    while still_open and time.monotonic() < deadline:
        time.sleep(0.5)
        still_open = [p for p in still_open if port_in_use(p)]
    leftover = [f'{name} pid {proc.pid}' for name, proc in cluster.started if proc.poll() is None]
    logs = cluster.log_text()
    report = {
        'generated_at_utc': started.isoformat(), 'python': sys.version.split()[0], 'platform': sys.platform,
        'architecture': 'one central Flask API + one central SQLite database; 3 bank node processes with local '
                        'offline queues; NOT a replicated database',
        'session_folder': str(session), 'ports': cluster.ports(),
        'scenarios_total': len(v.results), 'scenarios_passed': sum(r['passed'] for r in v.results),
        'checks_total': sum(len(r['checks']) for r in v.results),
        'checks_passed': sum(c['passed'] for r in v.results for c in r['checks']),
        'timeouts': cluster.timeouts, 'ports_still_open_after_cleanup': still_open,
        'processes_still_running_after_cleanup': leftover,
        'server_tracebacks_in_logs': logs.count('Traceback'),
        'http_5xx_in_logs': len(re.findall(r'" 5\d\d ', logs)),
        'results': v.results, 'metrics': v.metrics,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str) + '\n', encoding='utf-8')
    print(f"\n{report['scenarios_passed']}/{report['scenarios_total']} scenarios passed; "
          f"{report['checks_passed']}/{report['checks_total']} checks; timeouts: {report['timeouts'] or 'none'}; "
          f"ports still open: {still_open or 'none'}; tracebacks in logs: {report['server_tracebacks_in_logs']}")
    print('Report:', out)
    if args.cleanup:
        shutil.rmtree(session, ignore_errors=False)
    # Every planned scenario must have run (a failed launch stops early) and passed.
    ok = (report['scenarios_passed'] == report['scenarios_total'] == EXPECTED_SCENARIOS
          and not still_open and not leftover)
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
