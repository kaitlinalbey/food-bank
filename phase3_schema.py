"""Additive schema upgrades for the final classroom release.

Does not delete pre-existing records or replace a teammate's database. All new
features use separate tables or nullable/new columns. Back up before migration.
"""
from contextlib import closing
from pathlib import Path
import sqlite3
from datetime import datetime


def _columns(con, table):
    return {r['name'] for r in con.execute(f'PRAGMA table_info({table})')}


def upgrade(con, path, needs_backup=None):
    """Apply additive final-release tables/columns.

    ``needs_backup`` should be decided by the caller from the database state
    *before* any base tables were created; otherwise a brand-new database would
    look like an old one and receive a pointless "pre-upgrade" backup.
    """
    if needs_backup is None:
        existing={r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        needs_backup='banks' in existing and 'lots' in existing and 'recalls' not in existing
    if needs_backup:
        root=Path(path).parent/'backups_before_upgrade'
        root.mkdir(parents=True, exist_ok=True)
        backup=root/('food_bank_pre_final_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'.db')
        # closing() is required: sqlite3's own context manager only commits and
        # leaves the file open, which locks it on Windows.
        with closing(sqlite3.connect(str(backup))) as dest:
            con.backup(dest)
    if 'storage_type' not in _columns(con,'items'):
        con.execute("ALTER TABLE items ADD COLUMN storage_type TEXT NOT NULL DEFAULT 'ambient'")
    if 'lot_code' not in _columns(con,'lots'):
        con.execute('ALTER TABLE lots ADD COLUMN lot_code TEXT')
    if 'recalled' not in _columns(con,'lots'):
        con.execute('ALTER TABLE lots ADD COLUMN recalled INTEGER NOT NULL DEFAULT 0')
    if 'lot_code' not in _columns(con,'shipment_lots'):
        con.execute('ALTER TABLE shipment_lots ADD COLUMN lot_code TEXT')
    if 'donor_id' not in _columns(con,'donations'):
        con.execute('ALTER TABLE donations ADD COLUMN donor_id INTEGER')
    con.executescript('''
        CREATE TABLE IF NOT EXISTS donors (
          id INTEGER PRIMARY KEY, bank_id INTEGER NOT NULL REFERENCES banks(id),
          name TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(bank_id,name)
        );
        CREATE TABLE IF NOT EXISTS storage_zones (
          bank_id INTEGER NOT NULL REFERENCES banks(id),
          storage_type TEXT NOT NULL CHECK(storage_type IN ('ambient','refrigerated','frozen')),
          capacity_units INTEGER NOT NULL CHECK(capacity_units>=0),
          PRIMARY KEY(bank_id,storage_type)
        );
        CREATE TABLE IF NOT EXISTS recalls (
          id INTEGER PRIMARY KEY, item_id INTEGER NOT NULL REFERENCES items(id),
          lot_code TEXT NOT NULL, reason TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','closed')),
          created_by INTEGER REFERENCES users(id),
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          closed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS reconciliations (
          id INTEGER PRIMARY KEY, bank_id INTEGER NOT NULL REFERENCES banks(id),
          item_id INTEGER NOT NULL REFERENCES items(id),
          expected_qty INTEGER NOT NULL, counted_qty INTEGER NOT NULL,
          base_version INTEGER NOT NULL, reason TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','applied','rejected')),
          requested_by INTEGER REFERENCES users(id), approved_by INTEGER REFERENCES users(id),
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          completed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS drivers (
          id INTEGER PRIMARY KEY, bank_id INTEGER NOT NULL REFERENCES banks(id),
          display_name TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
          UNIQUE(bank_id,display_name)
        );
        CREATE TABLE IF NOT EXISTS vehicles (
          id INTEGER PRIMARY KEY, bank_id INTEGER NOT NULL REFERENCES banks(id),
          label TEXT NOT NULL, capacity_units INTEGER NOT NULL CHECK(capacity_units>0),
          refrigerated INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1,
          UNIQUE(bank_id,label)
        );
        CREATE TABLE IF NOT EXISTS deliveries (
          exchange_id INTEGER PRIMARY KEY REFERENCES exchanges(id),
          driver_id INTEGER NOT NULL REFERENCES drivers(id),
          vehicle_id INTEGER NOT NULL REFERENCES vehicles(id),
          assigned_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          picked_up_at TEXT, delivered_at TEXT, proof_note TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS discrepancies (
          id INTEGER PRIMARY KEY, exchange_id INTEGER NOT NULL UNIQUE REFERENCES exchanges(id),
          expected_qty INTEGER NOT NULL, received_qty INTEGER NOT NULL,
          missing_qty INTEGER NOT NULL, damaged_qty INTEGER NOT NULL,
          notes TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open'
            CHECK(status IN ('open','reviewed')),
          recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS user_role_overrides (
          user_id INTEGER PRIMARY KEY REFERENCES users(id),
          role TEXT NOT NULL CHECK(role IN ('viewer','driver','dispatcher','manager'))
        );
        CREATE TABLE IF NOT EXISTS distributed_events (
          id INTEGER PRIMARY KEY, bank_id INTEGER REFERENCES banks(id),
          origin TEXT NOT NULL, method TEXT NOT NULL, path TEXT NOT NULL,
          status_code INTEGER NOT NULL, correlation_id TEXT NOT NULL,
          latency_ms REAL NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_distributed_events_bank ON distributed_events(bank_id,id);
        CREATE TABLE IF NOT EXISTS offline_receipts (
          id INTEGER PRIMARY KEY, bank_id INTEGER NOT NULL REFERENCES banks(id),
          op_key TEXT NOT NULL, item_id INTEGER NOT NULL REFERENCES items(id),
          quantity INTEGER NOT NULL, donor TEXT NOT NULL,
          lot_code TEXT NOT NULL, expiration_date TEXT,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(bank_id,op_key)
        );
        CREATE TABLE IF NOT EXISTS login_attempts (
          bank_id INTEGER NOT NULL, username TEXT NOT NULL,
          attempts INTEGER NOT NULL DEFAULT 0, window_start INTEGER NOT NULL,
          PRIMARY KEY(bank_id,username)
        );
        -- Administrator backup register (admin page). File names only, never paths.
        CREATE TABLE IF NOT EXISTS db_backups (
          id INTEGER PRIMARY KEY, file_name TEXT NOT NULL UNIQUE,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          created_by INTEGER REFERENCES users(id),
          size_bytes INTEGER NOT NULL, sha256 TEXT NOT NULL, integrity TEXT NOT NULL,
          verified INTEGER NOT NULL DEFAULT 0, table_counts TEXT NOT NULL,
          note TEXT NOT NULL DEFAULT '', restore_checked_at TEXT, restore_result TEXT
        );
    ''')
    con.execute("UPDATE lots SET lot_code='LEGACY-' || id WHERE lot_code IS NULL OR lot_code='' ")
    for b in con.execute('SELECT id FROM banks'):
        for kind,capacity in [('ambient',100000),('refrigerated',10000),('frozen',10000)]:
            con.execute('INSERT OR IGNORE INTO storage_zones(bank_id,storage_type,capacity_units) VALUES(?,?,?)',
                        (b['id'],kind,capacity))
