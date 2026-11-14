"""Database administration: overview, bank/user management, health checks, backups.

Rules (see docs/DATABASE_MAINTENANCE.md and docs/API.md, section 11):
* Every function here is reached only through admin-only routes.
* Health checks open the database READ-ONLY (SQLite URI mode=ro).
* Backups use SQLite's online backup API, are verified after writing, and are
  never deleted by this module.
* Restore verification restores into a separate temporary database; the active
  database is never overwritten from here. A live restore is a manual,
  documented maintenance procedure (restore_database.py).
* Password hashes and tokens are never returned.
"""
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import secrets
import sqlite3
import tempfile
from urllib.parse import quote

from database import read_db, write_db, utc_now
from .records import record
from .validation import Problem, clean_text, whole

ROLES = ('staff', 'admin', 'viewer', 'driver', 'dispatcher', 'manager')
BASE_ROLES = ('staff', 'admin')          # stored in users.role; others use user_role_overrides
REQUIRED_TABLES = {'banks', 'items', 'inventory', 'exchanges', 'lots', 'users'}
COUNT_TABLES = ('banks', 'items', 'inventory', 'lots', 'exchanges', 'users', 'donations')
BACKUP_DIR_NAME = 'admin_backups'
BACKUP_NAME = re.compile(r'^food_bank_admin_[0-9]{8}_[0-9]{6}_[0-9a-f]{6}\.db$')
# Rows a brand-new, never-used bank always has; removed together with the bank.
CREATION_ACTIVITY = ('bank_added', 'user_added')


# --------------------------------------------------------------------------- helpers
def _ro(path):
    """Read-only connection. Raises sqlite3.OperationalError if the file is missing."""
    uri = 'file:' + quote(str(Path(path).resolve()).replace('\\', '/'), safe='/:') + '?mode=ro'
    con = sqlite3.connect(uri, uri=True, timeout=20)
    con.row_factory = sqlite3.Row
    return con


def _today():
    return date.today().isoformat()


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _search_text(value, label='Search'):
    value = '' if value is None else str(value).strip()
    if len(value) > 80:
        raise Problem(f'{label} must be 80 characters or fewer.')
    return value


def _audit(con, bank_id, actor, operation, detail):
    """Successful sensitive operations also go to audit_events (failures are added by app.business_error)."""
    con.execute('''INSERT INTO audit_events(bank_id, actor_user_id, operation, result, detail)
                   VALUES(?,?,?,?,?)''', (bank_id, actor, 'admin: ' + operation, 'success', detail[:500]))


def _referencing(con, parent):
    """(table, column) pairs whose foreign keys point at ``parent``."""
    pairs = []
    for (table,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
        for fk in con.execute(f'PRAGMA foreign_key_list("{table}")'):
            if fk['table'] == parent:
                pairs.append((table, fk['from']))
    return pairs


# --------------------------------------------------------------------------- overview
def overview(path):
    today = _today()
    soon = (date.today() + timedelta(days=7)).isoformat()
    week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S')
    with closing(_ro(path)) as con:
        one = lambda sql, args=(): con.execute(sql, args).fetchone()[0]
        transfers = {r['status']: r['n'] for r in con.execute('SELECT status, COUNT(*) n FROM exchanges GROUP BY status')}
        sync_errors = [dict(r) for r in con.execute(
            '''SELECT id, bank_id, operation, detail, created_at FROM audit_events
               WHERE result='failed' AND (operation LIKE 'POST /api/inventory/%/adjust'
                     OR operation='POST /api/offline/receipts')
               ORDER BY id DESC LIMIT 10''')]
        backup = con.execute('''SELECT id, file_name, created_at, size_bytes, restore_checked_at, restore_result
                                FROM db_backups WHERE verified=1 ORDER BY id DESC LIMIT 1''').fetchone()
        result = {
            'food_banks': one('SELECT COUNT(*) FROM banks'),
            'catalog_items': one('SELECT COUNT(*) FROM items'),
            'inventory_rows': one('SELECT COUNT(*) FROM inventory'),
            'active_batches': one('SELECT COUNT(*) FROM lots WHERE quantity>0'),
            'units_in_stock': one('SELECT COALESCE(SUM(quantity),0) FROM lots'),
            'users_active': one('SELECT COUNT(*) FROM users WHERE active=1'),
            'users_disabled': one('SELECT COUNT(*) FROM users WHERE active=0'),
            'transfers_pending': transfers.get('pending', 0),
            'transfers_in_progress': transfers.get('approved', 0) + transfers.get('in_transit', 0),
            'transfers_completed': transfers.get('received', 0),
            'transfers_by_status': transfers,
            'alerts': {
                'expired_batches': one('''SELECT COUNT(*) FROM lots WHERE quantity>0
                                          AND expiration_date IS NOT NULL AND expiration_date<?''', (today,)),
                'expiring_within_7_days': one('''SELECT COUNT(*) FROM lots WHERE quantity>0
                    AND expiration_date IS NOT NULL AND expiration_date>=? AND expiration_date<=?''', (today, soon)),
                'quarantined_batches': one('SELECT COUNT(*) FROM lots WHERE quantity>0 AND recalled=1'),
                'active_recalls': one("SELECT COUNT(*) FROM recalls WHERE status='active'"),
            },
            'sync_errors_last_7_days': one(
                '''SELECT COUNT(*) FROM audit_events WHERE result='failed' AND created_at>=?
                   AND (operation LIKE 'POST /api/inventory/%/adjust' OR operation='POST /api/offline/receipts')''',
                (week_ago,)),
            'recent_sync_errors': sync_errors,
            'integrity_quick_check': con.execute('PRAGMA quick_check').fetchone()[0],
        }
    if backup:
        b = dict(backup)
        b['restore_result'] = json.loads(b['restore_result']) if b['restore_result'] else None
        b['file_present'] = (_backup_dir(path) / b['file_name']).is_file()
        result['latest_verified_backup'] = b
    else:
        result['latest_verified_backup'] = None
    resolved = Path(path).resolve()
    result['database'] = {
        'file_name': resolved.name,
        'size_bytes': resolved.stat().st_size,
        # Presentation launchers put each fictional demo in data/presentation_sessions/<run>/.
        'presentation_session': 'presentation_sessions' in resolved.parts,
        'session_folder': resolved.parent.name if 'presentation_sessions' in resolved.parts else None,
    }
    result['notice'] = ('Counts are read live from the central SQLite database. Offline changes still '
                        'queued on a node are not visible centrally until that node synchronizes.')
    return result


# --------------------------------------------------------------------------- banks
def list_banks(path, query='', node_status=None):
    query = _search_text(query).casefold()
    with closing(_ro(path)) as con:
        rows = [dict(r) for r in con.execute('''
            SELECT b.id, b.name, b.area, b.latitude, b.longitude,
              (SELECT COUNT(*) FROM inventory i WHERE i.bank_id=b.id) inventory_rows,
              (SELECT COUNT(*) FROM users u WHERE u.bank_id=b.id AND u.active=1) active_users,
              (SELECT COUNT(*) FROM exchanges e WHERE (e.source_bank_id=b.id OR e.destination_bank_id=b.id)
                 AND e.status IN ('pending','approved','in_transit')) open_transfers,
              (SELECT MAX(created_at) FROM distributed_events d WHERE d.bank_id=b.id) last_communication_at
            FROM banks b ORDER BY b.id''')]
    if query:
        rows = [r for r in rows if query in r['name'].casefold() or query in r['area'].casefold()]
    for row in rows:
        row['node_online'], row['node_port'] = None, None   # None = not checked (never guessed)
    if node_status:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=10) as pool:
            status = list(pool.map(node_status, rows[:30]))   # only the first 30 are probed
        for row, s in zip(rows, status):
            row['node_online'] = bool(s.get('online'))
            row['node_port'] = s.get('port')
    return rows


def update_bank(path, bank_id, payload, admin_bank, actor):
    from routes.admin import coordinates
    changes = {}
    if 'name' in payload:
        changes['name'] = clean_text(payload.get('name'), 'Bank name')
    if 'area' in payload:
        changes['area'] = clean_text(payload.get('area'), 'Area')
    if 'latitude' in payload or 'longitude' in payload:
        changes['latitude'], changes['longitude'] = coordinates(payload)
    if not changes:
        raise Problem('Provide name, area, or latitude and longitude to update.')
    with write_db(path) as con:
        old = con.execute('SELECT * FROM banks WHERE id=?', (bank_id,)).fetchone()
        if not old:
            raise Problem('Food bank not found.', 404)
        try:
            con.execute('UPDATE banks SET ' + ','.join(f'{k}=?' for k in changes) + ' WHERE id=?',
                        (*changes.values(), bank_id))
        except sqlite3.IntegrityError:
            raise Problem('Another food bank already uses that name.', 409)
        summary = ', '.join(f'{k}: {old[k]} -> {v}' for k, v in changes.items() if old[k] != v) or 'no changes'
        record(con, bank_id, 'bank_updated', f'Bank #{bank_id} details updated by administrator ({summary}).', actor)
        _audit(con, admin_bank, actor, 'update bank', f'Bank #{bank_id}: {summary}')
        if admin_bank != bank_id:
            record(con, admin_bank, 'admin_bank_updated', f'Updated bank #{bank_id}: {summary}.', actor)
    return {'message': 'Food bank updated.', 'bank_id': bank_id, 'changed': sorted(changes)}


def bank_dependents(con, bank_id):
    """Records that block deleting a bank. Creation-time rows are not blockers."""
    blockers = {}
    users = [r['id'] for r in con.execute('SELECT id FROM users WHERE bank_id=?', (bank_id,))]
    for table, column in _referencing(con, 'banks'):
        if table in ('storage_zones', 'users'):
            continue
        sql = f'SELECT COUNT(*) FROM "{table}" WHERE "{column}"=?'
        args = [bank_id]
        if table == 'activity':
            sql += ' AND action NOT IN (%s)' % ','.join('?' * len(CREATION_ACTIVITY))
            args += CREATION_ACTIVITY
        n = con.execute(sql, args).fetchone()[0]
        if n:
            blockers[f'{table}.{column}'] = blockers.get(f'{table}.{column}', 0) + n
    for table, column in _referencing(con, 'users'):
        if table in ('user_role_overrides', 'auth_tokens') or not users:
            continue
        marks = ','.join('?' * len(users))
        n = con.execute(f'SELECT COUNT(*) FROM "{table}" WHERE "{column}" IN ({marks})', users).fetchone()[0]
        if n:
            blockers[f'{table}.{column} (bank users)'] = n
    admins = con.execute("SELECT COUNT(*) FROM users WHERE bank_id=? AND role='admin'", (bank_id,)).fetchone()[0]
    if admins:
        blockers['administrator accounts'] = admins
    return blockers


def delete_bank(path, bank_id, payload, admin_bank, actor):
    confirm = payload.get('confirm_name')
    with write_db(path) as con:
        bank = con.execute('SELECT * FROM banks WHERE id=?', (bank_id,)).fetchone()
        if not bank:
            raise Problem('Food bank not found.', 404)
        if bank_id == admin_bank:
            raise Problem('You cannot delete the food bank your administrator account belongs to.', 409)
        if not isinstance(confirm, str) or confirm.strip() != bank['name']:
            raise Problem('Type the exact bank name to confirm deletion.')
        blockers = bank_dependents(con, bank_id)
        if blockers:
            detail = ', '.join(f'{k}: {v}' for k, v in sorted(blockers.items()))
            raise Problem(f'Bank has existing records and cannot be deleted ({detail}).', 409)
        users = [r['id'] for r in con.execute('SELECT id FROM users WHERE bank_id=?', (bank_id,))]
        for uid in users:
            con.execute('DELETE FROM auth_tokens WHERE user_id=?', (uid,))
            con.execute('DELETE FROM user_role_overrides WHERE user_id=?', (uid,))
        con.execute('DELETE FROM login_attempts WHERE bank_id=?', (bank_id,))
        con.execute('DELETE FROM activity WHERE bank_id=? AND action IN (%s)' % ','.join('?' * len(CREATION_ACTIVITY)),
                    (bank_id, *CREATION_ACTIVITY))
        con.execute('DELETE FROM users WHERE bank_id=?', (bank_id,))
        con.execute('DELETE FROM storage_zones WHERE bank_id=?', (bank_id,))
        con.execute('DELETE FROM banks WHERE id=?', (bank_id,))
        record(con, admin_bank, 'bank_deleted',
               f'Deleted unused bank #{bank_id} "{bank["name"]}" ({len(users)} never-used account(s) removed).', actor)
        _audit(con, admin_bank, actor, 'delete bank', f'Unused bank #{bank_id} "{bank["name"]}" deleted')
    return {'message': 'Unused food bank deleted.', 'bank_id': bank_id}


# --------------------------------------------------------------------------- users
def list_users(path, query='', bank_id=None, role=None, active=None):
    query = _search_text(query).casefold()
    if bank_id not in (None, ''):
        bank_id = whole(bank_id, 1)
    else:
        bank_id = None
    if role not in (None, '') and role not in ROLES:
        raise Problem('Unknown role filter.')
    if active not in (None, '') and str(active) not in ('0', '1'):
        raise Problem('Active filter must be 1 or 0.')
    with closing(_ro(path)) as con:
        rows = [dict(r) for r in con.execute('''SELECT u.id, u.bank_id, b.name bank_name, u.username,
                    COALESCE(o.role, u.role) role, u.active FROM users u
                    JOIN banks b ON b.id=u.bank_id
                    LEFT JOIN user_role_overrides o ON o.user_id=u.id
                    ORDER BY u.bank_id, u.username''')]
    return [r for r in rows
            if (not query or query in r['username'].casefold())
            and (bank_id is None or r['bank_id'] == bank_id)
            and (role in (None, '') or r['role'] == role)
            and (active in (None, '') or r['active'] == int(active))]


def _effective_role(con, user_id):
    return con.execute('''SELECT COALESCE(o.role, u.role) FROM users u
        LEFT JOIN user_role_overrides o ON o.user_id=u.id WHERE u.id=?''', (user_id,)).fetchone()[0]


def _active_admins(con):
    return con.execute('''SELECT COUNT(*) FROM users u LEFT JOIN user_role_overrides o ON o.user_id=u.id
        WHERE u.active=1 AND COALESCE(o.role, u.role)='admin' ''').fetchone()[0]


def _apply_role(con, user_id, role):
    if role in BASE_ROLES:
        con.execute('UPDATE users SET role=? WHERE id=?', (role, user_id))
        con.execute('DELETE FROM user_role_overrides WHERE user_id=?', (user_id,))
    else:
        con.execute("UPDATE users SET role='staff' WHERE id=?", (user_id,))
        con.execute('''INSERT INTO user_role_overrides(user_id, role) VALUES(?,?)
                       ON CONFLICT(user_id) DO UPDATE SET role=excluded.role''', (user_id, role))


def update_user(path, user_id, payload, admin_bank, actor):
    role = payload.get('role')
    new_bank = payload.get('bank_id')
    if role in (None, '') and new_bank in (None, ''):
        raise Problem('Provide a role and/or bank_id to change.')
    if role not in (None, '') and role not in ROLES:
        raise Problem('Invalid user role.')
    new_bank = whole(new_bank, 1) if new_bank not in (None, '') else None
    with write_db(path) as con:
        user = con.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
        if not user:
            raise Problem('User not found.', 404)
        if user_id == actor:
            raise Problem('Administrators cannot change their own role or bank. Ask another administrator.', 409)
        old_role = _effective_role(con, user_id)
        changes = []
        if role not in (None, '') and role != old_role:
            if role == 'admin':
                # Granting system-wide administrator rights needs a deliberate, typed confirmation.
                confirm = payload.get('confirm_username')
                if not isinstance(confirm, str) or confirm.strip().casefold() != user['username'].casefold():
                    raise Problem('Type the exact username to confirm granting administrator rights.')
            if old_role == 'admin' and user['active'] and _active_admins(con) <= 1:
                raise Problem('This is the last active administrator; it cannot be demoted.', 409)
            _apply_role(con, user_id, role)
            changes.append(f'role {old_role} -> {role}')
        if new_bank is not None and new_bank != user['bank_id']:
            if not con.execute('SELECT 1 FROM banks WHERE id=?', (new_bank,)).fetchone():
                raise Problem('Bank not found.', 404)
            if con.execute('SELECT 1 FROM users WHERE bank_id=? AND username=? COLLATE NOCASE AND id!=?',
                           (new_bank, user['username'], user_id)).fetchone():
                raise Problem('That bank already has a user with this username.', 409)
            con.execute('UPDATE users SET bank_id=? WHERE id=?', (new_bank, user_id))
            changes.append(f'bank #{user["bank_id"]} -> #{new_bank}')
        if not changes:
            return {'message': 'No changes.', 'user_id': user_id}
        # New permissions take effect immediately: existing sessions must log in again.
        revoked = con.execute('DELETE FROM auth_tokens WHERE user_id=?', (user_id,)).rowcount
        text = f'Account {user["username"]} (#{user_id}): ' + '; '.join(changes) + f'; {revoked} session(s) ended.'
        record(con, admin_bank, 'user_updated', text, actor)
        _audit(con, admin_bank, actor, 'update user', text)
        if user['bank_id'] != admin_bank:
            record(con, user['bank_id'], 'user_updated', text, actor)
    return {'message': 'User updated.', 'user_id': user_id, 'changes': changes, 'sessions_ended': revoked}


def set_user_active(path, user_id, active, payload, admin_bank, actor):
    with write_db(path) as con:
        user = con.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
        if not user:
            raise Problem('User not found.', 404)
        if not active:
            if user_id == actor:
                raise Problem('You cannot disable your own account.', 409)
            confirm = payload.get('confirm_username')
            if not isinstance(confirm, str) or confirm.strip().casefold() != user['username'].casefold():
                raise Problem('Type the exact username to confirm disabling this account.')
            reason = clean_text(payload.get('reason'), 'Reason', 200)
            if user['active'] and _effective_role(con, user_id) == 'admin' and _active_admins(con) <= 1:
                raise Problem('This is the last active administrator; it cannot be disabled.', 409)
        else:
            reason = (payload.get('reason') or 'Re-enabled by administrator').strip()[:200] or 'Re-enabled'
        if bool(user['active']) == bool(active):
            return {'message': 'Account already ' + ('enabled.' if active else 'disabled.'), 'user_id': user_id}
        con.execute('UPDATE users SET active=? WHERE id=?', (1 if active else 0, user_id))
        revoked = con.execute('DELETE FROM auth_tokens WHERE user_id=?', (user_id,)).rowcount if not active else 0
        con.execute('DELETE FROM login_attempts WHERE bank_id=? AND username=?',
                    (user['bank_id'], user['username'].casefold()))
        text = (f'Account {user["username"]} (#{user_id}) ' + ('enabled' if active else 'disabled')
                + f': {reason}' + ('' if active else f'; {revoked} session(s) ended.'))
        record(con, admin_bank, 'user_enabled' if active else 'user_disabled', text, actor)
        _audit(con, admin_bank, actor, 'enable user' if active else 'disable user', text)
        if user['bank_id'] != admin_bank:
            record(con, user['bank_id'], 'user_enabled' if active else 'user_disabled', text, actor)
    return {'message': 'Account ' + ('enabled.' if active else 'disabled.'), 'user_id': user_id}


# --------------------------------------------------------------------------- health
def run_checks(con):
    """Integrity and inventory-invariant checks on an open (read-only) connection."""
    today = _today()
    checks = []

    def add(name, label, status, count=0, detail='', examples=None):
        checks.append({'check': name, 'label': label, 'status': status, 'count': count,
                       'detail': detail, 'examples': examples or []})

    integrity = [r[0] for r in con.execute('PRAGMA integrity_check').fetchall()]
    add('sqlite_integrity', 'SQLite integrity_check', 'pass' if integrity == ['ok'] else 'fail',
        0 if integrity == ['ok'] else len(integrity), '; '.join(integrity[:5]))
    fk = [dict(zip(('table', 'rowid', 'parent', 'fkid'), r)) for r in con.execute('PRAGMA foreign_key_check').fetchall()]
    add('foreign_keys', 'Foreign key references', 'fail' if fk else 'pass', len(fk),
        'Rows reference missing parent records.' if fk else 'All references resolve.', fk[:5])
    rows = [dict(r) for r in con.execute('''SELECT i.bank_id, i.item_id, i.quantity,
             COALESCE((SELECT SUM(l.quantity) FROM lots l WHERE l.bank_id=i.bank_id AND l.item_id=i.item_id),0) lot_total
             FROM inventory i WHERE i.quantity != COALESCE((SELECT SUM(l.quantity) FROM lots l
             WHERE l.bank_id=i.bank_id AND l.item_id=i.item_id),0)''')]
    add('inventory_matches_batches', 'Inventory totals equal batch totals', 'fail' if rows else 'pass', len(rows),
        'Stock totals disagree with batch records.' if rows else 'Every total matches its batches.', rows[:5])
    rows = [dict(r) for r in con.execute('''SELECT 'inventory' tbl, bank_id, item_id, quantity, target FROM inventory
             WHERE quantity<0 OR target<0 UNION ALL SELECT 'lots', bank_id, item_id, quantity, NULL FROM lots
             WHERE quantity<0''')]
    add('no_negative_stock', 'No negative quantities or targets', 'fail' if rows else 'pass', len(rows),
        '', rows[:5])
    rows = [dict(r) for r in con.execute('''SELECT e.source_bank_id bank_id, e.item_id, SUM(e.quantity) reserved,
             COALESCE((SELECT SUM(l.quantity) FROM lots l WHERE l.bank_id=e.source_bank_id AND l.item_id=e.item_id
               AND l.recalled=0 AND (l.expiration_date IS NULL OR l.expiration_date>=?)),0) usable
             FROM exchanges e WHERE e.status='approved' GROUP BY e.source_bank_id, e.item_id
             HAVING reserved > usable''', (today,))]
    add('reservations_covered', 'Approved transfers covered by usable stock', 'warn' if rows else 'pass', len(rows),
        'Some approved transfers cannot ship until stock is replenished (stock expired or was recalled).'
        if rows else 'All reservations are covered.', rows[:5])
    rows = [dict(r) for r in con.execute('''SELECT e.id exchange_id, e.status, e.quantity, SUM(s.quantity) shipped
             FROM exchanges e JOIN shipment_lots s ON s.exchange_id=e.id
             WHERE e.status IN ('in_transit','received') GROUP BY e.id HAVING shipped != e.quantity''')]
    add('shipments_match_transfers', 'Shipped batch quantities equal transfer quantities',
        'fail' if rows else 'pass', len(rows), '', rows[:5])
    rows = [dict(r) for r in con.execute('''SELECT l.id lot_id, l.bank_id, l.item_id, l.lot_code FROM lots l
             JOIN recalls r ON r.item_id=l.item_id AND r.lot_code=l.lot_code AND r.status='active'
             WHERE l.recalled=0 AND l.quantity>0''')]
    add('recalls_quarantined', 'Batches under active recall are quarantined', 'fail' if rows else 'pass', len(rows),
        '', rows[:5])
    failed = sum(c['status'] == 'fail' for c in checks)
    warned = sum(c['status'] == 'warn' for c in checks)
    return {'overall': 'fail' if failed else ('warn' if warned else 'pass'),
            'failed': failed, 'warnings': warned, 'checks': checks}


def health(path):
    with closing(_ro(path)) as con:
        result = run_checks(con)
        result['recent_failed_operations'] = [dict(r) for r in con.execute(
            '''SELECT id, bank_id, operation, detail, created_at FROM audit_events
               WHERE result='failed' ORDER BY id DESC LIMIT 15''')]
        result['recent_admin_audit'] = [dict(r) for r in con.execute(
            '''SELECT id, bank_id, actor_user_id, operation, result, detail, created_at FROM audit_events
               WHERE operation LIKE 'admin: %' ORDER BY id DESC LIMIT 15''')]
        result['recent_server_errors'] = [dict(r) for r in con.execute(
            '''SELECT created_at, bank_id, method, path, status_code, correlation_id FROM distributed_events
               WHERE status_code>=500 ORDER BY id DESC LIMIT 15''')]
        result['recent_admin_activity'] = [dict(r) for r in con.execute(
            '''SELECT id, bank_id, action, details, created_at FROM activity
               WHERE action IN ('bank_added','bank_updated','admin_bank_updated','bank_deleted','user_added',
                 'user_updated','user_enabled','user_disabled','database_backup','backup_restore_verified')
               ORDER BY id DESC LIMIT 20''')]
    result['checked_at'] = utc_now()
    result['read_only'] = True
    return result


# --------------------------------------------------------------------------- backups
def _backup_dir(db_path):
    return Path(db_path).resolve().parent / BACKUP_DIR_NAME


def inspect_database(path):
    """Read-only integrity/FK/table/row-count inspection of a database file."""
    with closing(_ro(path)) as con:
        integrity = [r[0] for r in con.execute('PRAGMA integrity_check').fetchall()]
        fk = con.execute('PRAGMA foreign_key_check').fetchall()
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        counts = {t: con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in COUNT_TABLES if t in tables}
    missing = sorted(REQUIRED_TABLES - tables)
    ok = integrity == ['ok'] and not fk and not missing
    return {'integrity': '; '.join(integrity[:3]), 'foreign_key_problems': len(fk),
            'missing_tables': missing, 'table_counts': counts, 'verified': ok}


def safe_backup(source, destination):
    """Consistent copy with SQLite's online backup API, then read-only verification.

    Shared by the admin page and backup_database.py. Never overwrites a file."""
    destination = Path(destination)
    if destination.exists():
        raise Problem('Backup destination already exists.', 409)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with closing(sqlite3.connect(str(source), timeout=20)) as src,                 closing(sqlite3.connect(str(destination))) as dst:
            src.backup(dst)
    except sqlite3.Error as exc:
        # Remove only the half-written file this call just created.
        if destination.exists():
            destination.unlink()
        raise Problem(f'Backup failed: {exc}', 500)
    result = inspect_database(destination)
    result['size_bytes'] = destination.stat().st_size
    result['sha256'] = _sha256(destination)
    return result


def create_backup(db_path, payload, admin_bank, actor):
    note = payload.get('note') or ''
    if not isinstance(note, str) or len(note) > 200:
        raise Problem('Backup note must be 200 characters or fewer.')
    name = ('food_bank_admin_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + secrets.token_hex(3) + '.db')
    target = _backup_dir(db_path) / name
    result = safe_backup(db_path, target)
    with write_db(db_path) as con:
        backup_id = con.execute('''INSERT INTO db_backups(file_name, created_by, size_bytes, sha256, integrity,
              verified, table_counts, note) VALUES(?,?,?,?,?,?,?,?)''',
              (name, actor, result['size_bytes'], result['sha256'], result['integrity'],
               int(result['verified']), json.dumps(result['table_counts']), note.strip())).lastrowid
        record(con, admin_bank, 'database_backup',
               f'Backup #{backup_id} {name}: ' + ('verified' if result['verified'] else 'VERIFICATION FAILED')
               + f' ({result["size_bytes"]} bytes).', actor)
        _audit(con, admin_bank, actor, 'create backup',
               f'Backup #{backup_id} {name} verified={bool(result["verified"])} sha256={result["sha256"][:16]}...')
    return {'message': 'Backup created and verified.' if result['verified'] else
            'Backup written but FAILED verification; do not rely on it.',
            'backup_id': backup_id, 'file_name': name, **result}


def list_backups(db_path):
    with closing(_ro(db_path)) as con:
        rows = [dict(r) for r in con.execute('''SELECT b.id, b.file_name, b.created_at, b.size_bytes, b.sha256,
                 b.integrity, b.verified, b.table_counts, b.note, b.restore_checked_at, b.restore_result,
                 u.username created_by FROM db_backups b LEFT JOIN users u ON u.id=b.created_by
                 ORDER BY b.id DESC LIMIT 50''')]
    folder = _backup_dir(db_path)
    for row in rows:
        row['table_counts'] = json.loads(row['table_counts'])
        row['restore_result'] = json.loads(row['restore_result']) if row['restore_result'] else None
        row['file_present'] = (folder / row['file_name']).is_file()
    return rows


def verify_restore(db_path, backup_id, admin_bank, actor):
    """Restore a recorded backup into a NEW temporary database and check it.

    The active database is only written to record the verification result."""
    backup_id = whole(backup_id, 1)
    with closing(_ro(db_path)) as con:
        row = con.execute('SELECT * FROM db_backups WHERE id=?', (backup_id,)).fetchone()
    if not row:
        raise Problem('Backup record not found.', 404)
    if not BACKUP_NAME.fullmatch(row['file_name']):
        raise Problem('Backup record has an unexpected file name.', 409)
    folder = _backup_dir(db_path)
    source = (folder / row['file_name']).resolve()
    if source.parent != folder or not source.is_file():
        raise Problem('Backup file is missing from the backup folder.', 409)
    if source == Path(db_path).resolve():
        raise Problem('Refusing to treat the active database as a backup.', 409)
    expected_counts = json.loads(row['table_counts'])
    steps = []
    checksum_ok = _sha256(source) == row['sha256']
    steps.append({'step': 'Checksum matches the value recorded at backup time', 'passed': checksum_ok})
    with tempfile.TemporaryDirectory(prefix='foodbank_restore_verify_') as tmp:
        restored = Path(tmp) / 'isolated_restore.db'
        with closing(_ro(source)) as src, closing(sqlite3.connect(str(restored))) as dst:
            src.backup(dst)
        steps.append({'step': 'Restored into a separate temporary database', 'passed': restored.is_file()})
        inspected = inspect_database(restored)
        steps.append({'step': 'Restored copy passes integrity, foreign key and table checks',
                      'passed': inspected['verified'], 'detail': inspected['integrity']})
        counts_ok = inspected['table_counts'] == expected_counts
        steps.append({'step': 'Row counts equal the counts recorded at backup time', 'passed': counts_ok,
                      'detail': {'expected': expected_counts, 'restored': inspected['table_counts']}})
        with closing(_ro(restored)) as con:
            invariants = run_checks(con)
        steps.append({'step': 'Inventory invariant checks on the restored copy',
                      'passed': invariants['failed'] == 0, 'detail': invariants['overall']})
    passed = all(s['passed'] for s in steps)
    summary = {'passed': passed, 'checked_at': utc_now(), 'steps': [
        {k: v for k, v in s.items() if k != 'detail'} for s in steps]}
    with write_db(db_path) as con:
        con.execute('UPDATE db_backups SET restore_checked_at=?, restore_result=? WHERE id=?',
                    (summary['checked_at'], json.dumps(summary), backup_id))
        record(con, admin_bank, 'backup_restore_verified',
               f'Isolated restore test of backup #{backup_id}: ' + ('PASSED' if passed else 'FAILED') + '.', actor)
        _audit(con, admin_bank, actor, 'verify restore',
               f'Backup #{backup_id} isolated restore ' + ('passed' if passed else 'FAILED'))
    return {'message': 'Isolated restore verification ' + ('passed.' if passed else 'FAILED.'),
            'backup_id': backup_id, 'passed': passed, 'steps': steps,
            'active_database_overwritten': False,
            'notice': 'The temporary restored copy was discarded. To restore the live database, follow the '
                      'maintenance procedure in docs/DATABASE_MAINTENANCE.md (servers stopped, restore_database.py).'}
