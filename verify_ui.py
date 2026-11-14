r"""End-to-end check of the multi-page web interface, for use after any design change.

Starts the real central API and three bank nodes on spare ports (default 29000-29003) with a
brand-new fictional database in a temporary folder, in classroom demo mode. It then uses every
page like a browser: navigation, inventory search/filter/sort/edit, other banks, matching, the
full transfer lifecycle, donations, batches and recalls, transportation, more tools, roles,
administration, an outage with sync, system status, old-URL compatibility, and logout.
data/food_bank.db is never used. Every process it starts is stopped at the end.

    .venv\Scripts\python.exe verify_ui.py            (from the project folder)
    .venv\Scripts\python.exe verify_ui.py . 29500    (folder, base port)

Each step prints PASS or FAIL; the exit code is 0 only if every step passed. The checks rely on
the routes, section ids, form actions and field names listed in docs/FRONTEND_HANDOFF.md."""
import html as htmllib, http.cookiejar, json, os, re, socket, subprocess, sys, tempfile, time
import urllib.error, urllib.parse, urllib.request

SRC = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.abspath(__file__)))
BASE = int(sys.argv[2]) if len(sys.argv) > 2 else 29000
PY = sys.executable
tmp = tempfile.mkdtemp(prefix='fb_ui_')
env = dict(os.environ, FOOD_BANK_DB_PATH=os.path.join(tmp, 'ui.db'), FOOD_BANK_QUEUE_DIR=os.path.join(tmp, 'q'),
           FOOD_BANK_DEMO_MODE='1', FOOD_BANK_NODE_URL_TEMPLATE='http://127.0.0.1:%d{bank_id}' % (BASE // 10))
env.pop('FOOD_BANK_SESSION_SECRET', None)
results, procs = [], []
PAGES = {'/': 'Dashboard', '/my-inventory': 'My inventory', '/other-banks': 'Other food banks',
         '/matching': 'Supply matching', '/transfers': 'Transfers', '/donations': 'Donations and waste',
         '/batches': 'Batches, expiration and recalls', '/transport': 'Transportation',
         '/offline': 'Offline synchronization', '/project-status': 'System status', '/more': 'More tools'}


def step(name, ok, detail=''):
    results.append((name, bool(ok)))
    print(('PASS ' if ok else 'FAIL ') + name + (f'  [{str(detail)[:220]}]' if detail and not ok else ''), flush=True)
    return ok


def up(port):
    with socket.socket() as s:
        s.settimeout(.5)
        return s.connect_ex(('127.0.0.1', port)) == 0


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


class Browser:
    def __init__(self, port):
        self.base = f'http://127.0.0.1:{port}'
        self.jar = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.raw = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar), NoRedirect)

    def get(self, path):
        """(status, body, final path+query after redirects)"""
        try:
            r = self.op.open(self.base + path, timeout=20)
            final = urllib.parse.urlsplit(r.geturl())
            return r.status, r.read().decode('utf8', 'replace'), final.path + ('?' + final.query if final.query else '')
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode('utf8', 'replace'), path

    def csrf(self):
        page = self.get('/login')[1] if not any('foodbank_node' in c.name for c in self.jar) else self.get('/project-status')[1]
        return re.search(r'name="_csrf" value="([^"]+)"', page).group(1)

    def post(self, path, data, follow=True, csrf=True):
        if csrf:
            data = dict(data, _csrf=self.csrf())
        body = urllib.parse.urlencode(data).encode()
        opener = self.op if follow else self.raw
        try:
            r = opener.open(self.base + path, body, timeout=30)
            return r.status, r.read().decode('utf8', 'replace'), r.headers.get('Location')
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode('utf8', 'replace'), e.headers.get('Location')

    def login(self, user, pw):
        page = self.get('/login')[1]
        tok = re.search(r'name="_csrf" value="([^"]+)"', page).group(1)
        body = urllib.parse.urlencode({'_csrf': tok, 'username': user, 'password': pw}).encode()
        r = self.op.open(self.base + '/login', body, timeout=20)
        return r.status, r.read().decode('utf8', 'replace')


def section(page, sid):
    m = re.search(r'<section[^>]*\bid="%s"[^>]*>(.*?)</section>' % re.escape(sid), page, re.S)
    return m.group(1) if m else ''


def flashes(page):
    m = re.search(r'<div class="messages">(.*?)</div>', page, re.S)
    return [htmllib.unescape(re.sub('<[^>]+>', '', x)).strip() for x in re.findall(r'<p>(.*?)</p>', m.group(1), re.S)] if m else []


def api(method, path, body=None, bank=1, user='staff', pw='demo123'):
    tok = json.loads(urllib.request.urlopen(urllib.request.Request(
        f'http://127.0.0.1:{BASE}/api/login', json.dumps({'bank_id': bank, 'username': user, 'password': pw}).encode(),
        {'Content-Type': 'application/json'}), timeout=15).read())['token']
    req = urllib.request.Request(f'http://127.0.0.1:{BASE}{path}', json.dumps(body).encode() if body is not None else None,
                                 {'Authorization': 'Bearer ' + tok, 'Content-Type': 'application/json'}, method=method)
    return json.loads(urllib.request.urlopen(req, timeout=15).read())


def item(bank, name):
    return next(r for r in api('GET', '/api/dashboard', bank=bank)['inventory'] if r['item_name'] == name)


def rows_of(page, sid):
    return re.findall(r'<tr>(.*?)</tr>', section(page, sid), re.S)


busy = [BASE + i for i in range(4) if up(BASE + i)]
if busy:
    raise SystemExit(f'Ports already in use: {busy}. Nothing was started. Choose another base port.')

try:
    subprocess.run([PY, '-c', 'from seed_more_demo_data import enrich_demo;import os;enrich_demo(os.environ["FOOD_BANK_DB_PATH"])'],
                   cwd=SRC, env=env, check=True, timeout=60)
    procs.append(subprocess.Popen([PY, 'server.py', '--port', str(BASE)], cwd=SRC, env=env,
                                  stdout=open(os.path.join(tmp, 'api.log'), 'w'), stderr=subprocess.STDOUT))
    for b in (1, 2, 3):
        procs.append(subprocess.Popen([PY, 'node.py', '--bank', str(b), '--port', str(BASE + b), '--api', f'http://127.0.0.1:{BASE}'],
                                      cwd=SRC, env=env, stdout=open(os.path.join(tmp, f'n{b}.log'), 'w'), stderr=subprocess.STDOUT))
    deadline = time.time() + 40
    while not all(up(BASE + i) for i in range(4)):
        if time.time() > deadline:
            raise SystemExit('services did not start')
        time.sleep(.3)

    central, north, admin = Browser(BASE + 1), Browser(BASE + 2), Browser(BASE + 1)
    rice_c0, rice_n0 = item(1, 'Rice'), item(2, 'Rice')

    # ---------------------------------------------------------------- A. login and navigation
    st, page = central.login('staff', 'demo123')
    step('A1. staff logs in and lands on the Dashboard', st == 200 and '<h1>Dashboard</h1>' in page and 'Central Food Pantry' in page)
    step('A2. sidebar lists every page group; no Administration link for staff',
         all(f'>{label}</a>' in page for label in ('My inventory', 'Other food banks', 'Supply matching', 'Transfers', 'Donations and waste',
             'Batches and recalls', 'Transportation', 'Offline sync', 'System status', 'More tools')) and '>Administration</a>' not in page)
    bad = []
    for path, heading in PAGES.items():
        st, page, _ = central.get(path)
        if st != 200 or f'<h1>{heading}</h1>' not in page or 'class="active"' not in page:
            bad.append((path, st))
    step('A3. all 11 pages load for staff with their heading and an active sidebar link', not bad, bad)
    step('A4. Administration page is refused for staff (403)', central.get('/admin')[0] == 403)
    st, page, _ = central.get('/')
    step('A5. dashboard shows overview cards, attention list, alerts and quick links',
         all(x in page for x in ('Inventory items', 'Shortages', 'Needs attention', 'Important alerts', 'Quick links', 'Recent activity')))

    # ---------------------------------------------------------------- B. my inventory
    st, page, _ = central.get('/my-inventory')
    step('B1. My inventory opens read-only (no quantity inputs)', 'name="quantity"' not in section(page, 'inventory') and 'Edit Inventory</a>' in page)
    st, page, _ = central.get('/my-inventory?q=rice')
    names = re.findall(r'<td><strong>([^<]+)</strong>', section(page, 'inventory'))
    step('B2. search "rice" shows only rice items', names and all('rice' in n.lower() for n in names), names)
    st, p1, _ = central.get('/my-inventory?q=boxes')
    st, p2, _ = central.get('/my-inventory?q=Dairy')
    step('B3. search by unit ("boxes") and category ("Dairy") both find items',
         len(re.findall(r'<td><strong>', section(p1, 'inventory'))) > 0 and len(re.findall(r'<td><strong>', section(p2, 'inventory'))) > 0)
    st, page, _ = central.get('/my-inventory?status=shortage')
    conds = re.findall(r'class="tag[^"]*">(Shortage|Surplus|At target|Expired / Unusable)</span>', section(page, 'inventory'))
    step('B4. shortage filter lists only shortages', conds and set(conds) == {'Shortage'}, set(conds))
    st, page, _ = central.get('/my-inventory?q=rice&status=shortage')
    names = re.findall(r'<td><strong>([^<]+)</strong>', section(page, 'inventory'))
    step('B5. search and shortage filter work together', names and all('rice' in n.lower() for n in names), names)
    st, page, _ = central.get('/my-inventory?status=shortage&sort=shortage')
    shortages = [int(x) for x in re.findall(r'<td>(\d+)</td><td>\d+</td>\s*<td>[^<]*</td>\s*<td>(?:<span|Not)', section(page, 'inventory'))]
    rows = rows_of(page, 'inventory')[1:]
    values = [int(re.findall(r'<td>(-?\d+)</td>', r)[5]) for r in rows if len(re.findall(r'<td>(-?\d+)</td>', r)) >= 6]
    step('B6. sorting by shortage puts the largest shortage first', values and values == sorted(values, reverse=True), values[:6])
    st, page, _ = central.get('/my-inventory?q=zzzz-nothing')
    step('B7. empty search shows a helpful message', 'No items match your search and filters' in page)
    st, page, _ = central.get('/my-inventory?inventory=edit&q=rice')
    step('B8. Edit Inventory shows editing forms', 'name="quantity"' in section(page, 'inventory') and 'Add item' in page)
    rice_item = item(1, 'Rice')
    st, page, _ = central.post('/inventory', {'item_id': rice_item['item_id'], 'quantity': rice_item['quantity'],
                                              'target': rice_item['target'] + 1, 'expiration_date': '', 'q': 'rice', 'status': 'all', 'category': ''})
    step('B9. saving an item returns to Edit Inventory with the search kept',
         'Inventory updated.' in flashes(page) and 'name="q" value="rice"' in page and 'name="quantity"' in page, flashes(page))
    central.post('/inventory', {'item_id': rice_item['item_id'], 'quantity': rice_item['quantity'], 'target': rice_item['target'], 'expiration_date': ''})
    st, page, _ = central.post('/inventory/add', {'name': 'Oat Cereal UI', 'unit': 'boxes', 'category': 'Breakfast',
                                                  'quantity': 4, 'target': 10, 'expiration_date': '', 'q': '', 'status': 'all'})
    new_item = next((r for r in api('GET', '/api/dashboard')['inventory'] if r['item_name'] == 'Oat Cereal UI'), None)
    step('B10. adding an item keeps the category typed in the form', new_item and new_item['category'] == 'Breakfast', (flashes(page), new_item))

    # ---------------------------------------------------------------- C. other food banks
    st, page, _ = central.get('/other-banks?other_bank=2&other_q=rice')
    other = section(page, 'other-inventories')
    step('C1. Northside listing shows rice items, read-only, public fields only',
         'Northside Food Pantry' in other and 'Rice' in other and 'name="quantity"' not in other and 'method="post"' not in other
         and '<th>Target' not in other and 'Reserved' not in other)
    st, page, _ = central.get('/other-banks?other_bank=3')
    step('C2. switching to Westside starts with an empty search', 'Westside Food Pantry' in page and 'name="other_q" value=""' in page)
    st, page, _ = central.get('/other-banks?other_bank=2&other_q=zzzz')
    step('C3. empty other-bank search shows a helpful message', "No items match this bank's search" in page)

    # ---------------------------------------------------------------- D/E. matching and transfers
    st, page, _ = central.get('/matching')
    row = re.search(r'<td><strong>Rice</strong> <span class="small">\(bags\)</span></td><td>Northside Food Pantry</td>.*?</tr>', section(page, 'matches'), re.S)
    step('D1. Supply matching lists Rice from Northside with a Request form and our shortages', row and 'Request</button>' in row.group(0)
         and 'Rice' in section(page, 'shortages'), re.sub(r'\s+', ' ', section(page, 'matches'))[:400])
    fields = dict(re.findall(r'name="(source_bank_id|item_id|request_key)" value="([^"]+)"', row.group(0))) if row else {}
    st, body, loc = central.post('/request', dict(fields, quantity=10), follow=False)
    step('D2. requesting returns to Transfers > Our requests', st == 302 and loc and '/transfers?tab=outgoing' in loc, loc)
    st, page, _ = central.get('/transfers?tab=outgoing')
    ex = re.search(r'<td>#(\d+)</td>\s*<td>We requested</td>\s*<td>Northside Food Pantry</td>\s*<td>Rice</td><td>10</td>', page)
    step('D3. the request appears as pending under Our requests', ex and 'pending' in page)
    ex_id = ex.group(1) if ex else '0'
    north.login('staff', 'demo123')
    st, body, loc = north.post(f'/exchange/{ex_id}/approve', {}, follow=False)
    step('E1. Northside approves; returns to Transfers > Requests to us', st == 302 and '/transfers?tab=incoming' in (loc or ''), loc)
    n_after = item(2, 'Rice')
    step('E2. approval reserves 10 bags without moving stock', n_after['reserved'] == rice_n0['reserved'] + 10 and n_after['quantity'] == rice_n0['quantity'])
    st, page, _ = north.get('/transfers?tab=approved')
    step('E3. Approved tab shows "Mark shipped"', f'#{ex_id}</td>' in page and 'Mark shipped' in page)
    north.post(f'/exchange/{ex_id}/ship', {})
    step('E4. shipping deducts 10 bags at Northside', item(2, 'Rice')['quantity'] == rice_n0['quantity'] - 10)
    st, page, _ = central.get('/transfers?tab=in_transit')
    step('E5. In transit tab at Central shows "Confirm received"', 'Confirm received' in page)
    st, body, loc = central.post(f'/exchange/{ex_id}/receive', {}, follow=False)
    step('E6. receiving returns to Transfers > Our requests', st == 302 and '/transfers?tab=outgoing' in (loc or ''), loc)
    step('E7. Central +10 and Northside -10 bags of Rice', item(1, 'Rice')['quantity'] == rice_c0['quantity'] + 10
         and item(2, 'Rice')['quantity'] == rice_n0['quantity'] - 10)
    st, page, _ = central.get('/transfers?tab=completed')
    step('E8. Completed tab lists the received transfer with tab counts', re.search(rf'#{ex_id}</td>.*?received', page, re.S) and 'class="badge"' in page)

    # ---------------------------------------------------------------- F. donations
    apples = item(1, 'Apples')
    st, page, _ = central.post('/donation', {'item_id': apples['item_id'], 'quantity': 6, 'donor': 'Fictional UI Drive',
                                             'donor_id': '', 'lot_code': 'UI-LOT-001', 'expiration_date': ''})
    step('F1. a donation is recorded, listed, and raises stock', 'Donation recorded.' in flashes(page) and 'Fictional UI Drive' in section(page, 'records')
         and item(1, 'Apples')['quantity'] == apples['quantity'] + 6, flashes(page))
    st, page, _ = central.post('/waste', {'item_id': apples['item_id'], 'quantity': 1, 'reason': 'Damaged', 'lot_id': ''})
    step('F2. waste is recorded and listed', 'Waste recorded.' in flashes(page) and 'Damaged' in section(page, 'waste'), flashes(page))
    st, page, _ = central.post('/advanced/action/donor', {'name': 'Fictional Youth Club', 'notes': 'UI check'})
    step('F3. a donor is added to the directory', 'Fictional Youth Club' in section(page, 'donors'), flashes(page))

    # ---------------------------------------------------------------- G. batches and recalls
    st, page, _ = central.get('/batches')
    lot_id = re.search(r'<tr><td>(\d+)</td><td>Apples', section(page, 'batches'))
    step('G1. batch list shows batches with condition tags', lot_id and 'class="tag' in section(page, 'batches'))
    st, page2, _ = central.get('/batches?lots=expiring')
    step('G2. batch filter tabs work', st == 200 and 'aria-current="page">Expiring within 7 days' in page2)
    st, page, _ = central.post('/lot/0/expiry', {'lot_id_entry': lot_id.group(1) if lot_id else '0', 'expiration_date': '2027-06-30'})
    step('G3. one batch expiry is changed (works without JavaScript)', 'Batch expiration updated.' in flashes(page), flashes(page))
    admin.login('admin', 'admin123')
    st, page, _ = admin.post('/advanced/action/recall', {'item_id': apples['item_id'], 'lot_code': 'UI-LOT-001', 'reason': 'UI check recall'})
    rid = re.search(r'<tr><td>(\d+)</td><td>Apples</td><td>UI-LOT-001</td><td><span class="tag tag-bad">active', section(page, 'recalls'))
    step('G4. admin issues a recall; it is listed as active', rid, flashes(page))
    st, page, _ = admin.post(f'/advanced/recall/{rid.group(1) if rid else 0}/close', {})
    step('G5. admin closes the recall', 'closed' in section(page, 'recalls') or 'resolved' in section(page, 'recalls'), flashes(page))

    # ---------------------------------------------------------------- H. transportation
    m = api('GET', '/api/matches', bank=2)['matches']
    pick = next((x for x in m if x['source_bank_id'] == 1), None)
    sched_ok = False
    if pick:
        e = api('POST', '/api/exchanges', {'source_bank_id': 1, 'item_id': pick['item_id'], 'quantity': 1, 'request_key': 'ui-schedule-0001'}, bank=2)
        api('POST', f"/api/exchanges/{e['exchange_id']}/approve", bank=1)
        st, page, _ = central.get('/transport')
        st, page, loc = central.post(f"/exchange/{e['exchange_id']}/schedule", {'pickup_at': '2027-01-05T09:00', 'delivery_at': '2027-01-05T13:00', 'carrier_notes': 'UI van'}, follow=True)
        sched_ok = 'UI van' in section(page, 'schedules')
    step('H1. supplier schedules an approved transfer; it appears under schedules', sched_ok, flashes(page) if pick else 'no match')
    st, page, _ = admin.post('/advanced/action/driver', {'display_name': 'Driver UI'})
    st, page, _ = admin.post('/advanced/action/vehicle', {'label': 'Van UI', 'capacity_units': 200, 'refrigerated': 'on'})
    step('H2. drivers and vehicles are added and listed', 'Driver UI' in section(page, 'fleet') and 'Van UI' in section(page, 'fleet'), flashes(page))
    step('H3. delivery problems section is available', 'Confirm counted receipt' in section(page, 'discrepancies'))

    # ---------------------------------------------------------------- I. more tools
    st, page, _ = central.get('/more')
    step('I1. More tools shows CSV, barcodes, counts, forecast, analytics, API docs; no Test lab for staff',
         all(section(page, s) for s in ('csv', 'barcodes', 'counts', 'forecast', 'analytics', 'api')) and not section(page, 'lab'))
    st, page, _ = central.post('/advanced/action/count', {'item_id': apples['item_id'], 'counted_qty': 3, 'reason': 'UI shelf count'})
    count = re.search(r'<tr><td>(\d+)</td><td>Apples</td>.*?pending', section(page, 'counts'), re.S)
    step('I2. staff submit a physical count (pending)', count, flashes(page))
    st, page, _ = admin.post(f'/advanced/count/{count.group(1) if count else 0}/reject', {})
    step('I3. admin rejects the count', 'rejected' in section(page, 'counts'), flashes(page))
    r = central.op.open(central.base + '/csv/export', timeout=20)
    step('I4. CSV export downloads a CSV file', r.headers.get_content_type() == 'text/csv' and b'name,unit,category' in r.read())
    st, page, final = central.get('/barcode/lookup?barcode=NONE-123')
    step('I5. barcode lookup answers on More tools', final.startswith('/more') and any('Barcode lookup' in f for f in flashes(page)), (final, flashes(page)))
    st, page, _ = admin.get('/more')
    step('I6. admin sees the Test lab', 'Run isolated tests' in section(page, 'lab'))
    st, page, _ = admin.post('/advanced/lab', {'scenario': 'recall'})
    step('I7. a lab scenario runs and shows its result', 'Results: 1/1 passed' in page, flashes(page))

    # ---------------------------------------------------------------- J. roles and server-side protection
    admin.post('/admin/user', {'bank_id': 1, 'username': 'ui-viewer', 'password': 'demo-pass-123', 'role': 'viewer', 'return_to': 'admin'})
    admin.post('/admin/user', {'bank_id': 1, 'username': 'ui-driver', 'password': 'demo-pass-123', 'role': 'driver', 'return_to': 'admin'})
    viewer, driver = Browser(BASE + 1), Browser(BASE + 1)
    viewer.login('ui-viewer', 'demo-pass-123')
    driver.login('ui-driver', 'demo-pass-123')
    leaks = []
    for who, b in (('viewer', viewer), ('driver', driver)):
        for path, marker in (('/my-inventory?inventory=edit', 'name="quantity"'), ('/matching', 'Request</button>'),
                             ('/transfers', 'Confirm received</button>'), ('/transfers', 'Approve</button>'),
                             ('/donations', 'Record donation'), ('/offline', 'Save adjustment locally'),
                             ('/batches', 'Update batch expiry'), ('/more', 'Submit count'), ('/transport', 'Add driver')):
            if marker in b.get(path)[1]:
                leaks.append((who, path, marker))
    step('J1. viewer and driver see no editing, transfer, donation, queue, count or fleet controls', not leaks, leaks)
    step('J2. viewer gets 403 on Administration and no Administration link', viewer.get('/admin')[0] == 403 and '>Administration</a>' not in viewer.get('/')[1])
    before = item(1, 'Apples')['quantity']
    st, page, _ = viewer.post('/donation', {'item_id': apples['item_id'], 'quantity': 50, 'donor': 'x', 'donor_id': '', 'lot_code': '', 'expiration_date': ''})
    step('J3. a viewer posting the donation form directly is refused by the server; stock unchanged',
         any('not permitted' in f for f in flashes(page)) and item(1, 'Apples')['quantity'] == before, flashes(page))
    st, page, _ = central.post('/donation', {'item_id': apples['item_id'], 'quantity': 50, 'donor': 'x'}, follow=False, csrf=False)
    step('J4. a form without its CSRF token is rejected (400); stock unchanged', st == 400 and item(1, 'Apples')['quantity'] == before)

    # ---------------------------------------------------------------- K. administration
    st, page = admin.get('/')[:2]
    step('K1. admin sees the Administration link', '>Administration</a>' in page and 'Database administration' in page)
    st, page, _ = admin.get('/admin')
    step('K2. Administration page shows overview, banks, users, backups and audit records',
         st == 200 and all(f'id="{s}"' in page for s in ('overview', 'banks', 'users', 'health', 'backups', 'audit')))
    st, page, _ = admin.get('/admin?health=1')
    step('K3. database health check runs', st == 200 and 'pass' in section(page, 'health').lower())

    # ---------------------------------------------------------------- L. offline synchronization
    st, body, loc = admin.post('/offline/simulate', {}, follow=False)
    step('L1. admin starts the simulated outage from Offline sync', st == 302 and loc and loc.endswith('/offline'), loc)
    st, page, _ = central.get('/my-inventory')
    step('L2. other pages show the offline screen (503) with the snapshot', st == 503 and 'Bank node is offline' in page and 'Rice' in page)
    st, page, _ = central.get('/offline')
    step('L3. Offline sync page works during the outage and has no restore button for staff',
         st == 503 and 'Save adjustment locally' in page and 'Restore simulated connection' not in page)
    before = item(1, 'Rice')['quantity']
    rice_opt = re.search(r'<option value="(\d+)">Rice \(version', page)
    st, page, _ = central.post('/offline/queue', {'item_id': rice_opt.group(1) if rice_opt else 0, 'delta': 3})
    step('L4. staff queue a +3 adjustment while offline', any('Adjustment saved locally' in f for f in flashes(page)) and item(1, 'Rice')['quantity'] == before, flashes(page))
    st, page, _ = admin.post('/offline/simulate', {})
    step('L5. admin restores the connection', any('ONLINE' in f for f in flashes(page)), flashes(page))
    st, page, _ = central.post('/offline/sync', {})
    step('L6. sync applies the change once and empties the queue', item(1, 'Rice')['quantity'] == before + 3
         and 'No pending offline changes.' in section(page, 'queue'), flashes(page))

    # ---------------------------------------------------------------- M. system status
    st, page, _ = admin.get('/project-status')
    step('M1. System status shows API OK, nodes online, the system summary and request IDs',
         'Central API' in page and '3 / 3' in page and 'Central server and database' in page and re.search(r'[0-9a-f]{24}', page))
    st, page, _ = central.get('/project-status')
    step('M2. staff see health and traces but not the admin-only summary', 'Central server and database' not in page and 'Trace a request' in page)

    # ---------------------------------------------------------------- N. old links keep working
    st, page, final = central.get('/?q=rice&status=shortage')
    step('N1. old /?q=... dashboard links open My inventory with the filters', final.startswith('/my-inventory') and 'name="q" value="rice"' in page, final)
    st, page, final = central.get('/?other_bank=2&other_q=beans')
    step('N2. old /?other_bank=... links open Other food banks for that bank', final.startswith('/other-banks') and 'Northside Food Pantry' in page, final)
    st, page, _ = central.get('/advanced')
    step('N3. old /advanced link shows where each feature moved', st == 200 and 'This page has moved' in page and '/batches' in page)

    # ---------------------------------------------------------------- O. logout
    st, page, _ = central.post('/logout', {})
    step('O1. logout returns to the login page', st == 200 and 'name="password"' in page)
    st, page, final = central.get('/transfers')
    step('O2. pages require login again after logout', final == '/login' and '<h1>Transfers</h1>' not in page, final)
finally:
    for p in procs:
        p.terminate()
    for p in procs:
        try:
            p.wait(10)
        except subprocess.TimeoutExpired:
            p.kill()
    time.sleep(1)
    left = [BASE + i for i in range(4) if up(BASE + i)]
    print('ports still open after stop:', left or 'none')
logs = ''.join(open(os.path.join(tmp, f), encoding='utf8', errors='replace').read() for f in ('api.log', 'n1.log', 'n2.log', 'n3.log'))
print('tracebacks in logs:', logs.count('Traceback'), '| HTTP 500 lines:', len(re.findall(r'" 500 ', logs)))
passed = sum(ok for _, ok in results)
print(f'\nWALKTHROUGH: {passed}/{len(results)} steps passed')
sys.exit(0 if passed == len(results) and not logs.count('Traceback') else 1)
