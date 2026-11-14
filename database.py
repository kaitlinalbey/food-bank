"""SQLite schema, small backwards migration, and fictional sample dataset."""
from contextlib import contextmanager, closing
from datetime import datetime, timezone
from pathlib import Path
from datetime import datetime as local_datetime
import hashlib
import os
import hmac
import secrets
import sqlite3

# Presentation launchers may direct all services to a separate fictional demo database.
DEFAULT_DB = Path(os.environ.get('FOOD_BANK_DB_PATH') or (Path(__file__).resolve().parent / 'data' / 'food_bank.db')).resolve()


def utc_now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


# Public classroom passwords. They are only created, and only accepted at login,
# when classroom demo mode is switched on explicitly (FOOD_BANK_DEMO_MODE=1).
# The Windows launchers and the Docker Compose file switch it on; running
# server.py on its own does not.
DEMO_PASSWORDS = ('demo123', 'admin123')


def demo_mode():
    return os.environ.get('FOOD_BANK_DEMO_MODE', '').strip().lower() in ('1', 'true', 'yes', 'on')


def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 240000)
    return f'pbkdf2:{salt.hex()}:{digest.hex()}'


def verify_password(stored, password):
    if stored.startswith('pbkdf2:'):
        try:
            _, salt, expected = stored.split(':', 2)
            digest = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 240000)
            return hmac.compare_digest(digest, bytes.fromhex(expected))
        except ValueError:
            return False
    # Support passwords from the original version, which used Werkzeug hashes.
    try:
        from werkzeug.security import check_password_hash
        return check_password_hash(stored, password)
    except (ImportError, ValueError):
        return False


def connect(db_path=DEFAULT_DB):
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), isolation_level=None, timeout=20)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    con.execute('PRAGMA busy_timeout=20000')
    return con


@contextmanager
def read_db(db_path=DEFAULT_DB):
    con = connect(db_path)
    try:
        yield con
    finally:
        con.close()


@contextmanager
def write_db(db_path=DEFAULT_DB):
    con = connect(db_path)
    try:
        con.execute('BEGIN IMMEDIATE')
        yield con
        con.commit()
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()


def _columns(con, table):
    return {row['name'] for row in con.execute(f'PRAGMA table_info({table})')}


def _make_exchanges(con):
    con.execute('''CREATE TABLE IF NOT EXISTS exchanges (
       id INTEGER PRIMARY KEY AUTOINCREMENT,
       source_bank_id INTEGER NOT NULL REFERENCES banks(id),
       destination_bank_id INTEGER NOT NULL REFERENCES banks(id),
       item_id INTEGER NOT NULL REFERENCES items(id),
       quantity INTEGER NOT NULL CHECK(quantity>0),
       status TEXT NOT NULL DEFAULT 'pending'
         CHECK(status IN ('pending','approved','in_transit','received','declined','canceled')),
       request_key TEXT,
       created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
       updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
       CHECK(source_bank_id != destination_bank_id),
       UNIQUE(destination_bank_id,request_key)
    )''')


def _existing_tables(db_path):
    """Table names present before initialize() changes anything (empty for a new DB)."""
    path=Path(db_path)
    if not path.exists() or path.stat().st_size==0:
        return set()
    with closing(sqlite3.connect(path)) as source:
        return {r[0] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _backup_if_upgrading(db_path, existing=None):
    """Safely save a real pre-upgrade database before adding the expanded schema."""
    path=Path(db_path)
    if not path.exists() or path.stat().st_size==0:
        return None
    with closing(sqlite3.connect(path)) as source:
        if existing is None:
            existing={r[0] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'banks' not in existing or 'lots' in existing:
            return None
        backup_dir=path.parent/'backups_before_upgrade'
        backup_dir.mkdir(parents=True,exist_ok=True)
        name='food_bank_pre_expansion_'+local_datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'.db'
        backup_path=backup_dir/name
        with closing(sqlite3.connect(backup_path)) as backup:
            source.backup(backup)
        return backup_path


def initialize(db_path=DEFAULT_DB, demo_accounts=None):
    """Create or upgrade the schema. demo_accounts: True creates the public classroom
    accounts when missing, False never does, None (default) follows demo_mode()."""
    if demo_accounts is None:
        demo_accounts = demo_mode()
    # Decide what kind of upgrade this is from the state on disk BEFORE any
    # CREATE TABLE runs, so new databases are never mistaken for old ones.
    before=_existing_tables(db_path)
    _backup_if_upgrading(db_path,before)
    with read_db(db_path) as con:
        con.executescript('''
        CREATE TABLE IF NOT EXISTS banks (
          id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
          area TEXT NOT NULL, latitude REAL NOT NULL, longitude REAL NOT NULL,
          password_hash TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS items (
          id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
          unit TEXT NOT NULL DEFAULT 'units', category TEXT NOT NULL DEFAULT 'Other'
        );
        CREATE TABLE IF NOT EXISTS inventory (
          bank_id INTEGER NOT NULL REFERENCES banks(id),
          item_id INTEGER NOT NULL REFERENCES items(id),
          quantity INTEGER NOT NULL DEFAULT 0 CHECK(quantity>=0),
          target INTEGER NOT NULL DEFAULT 0 CHECK(target>=0),
          expiration_date TEXT, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY(bank_id,item_id)
        );
        ''')
        # Upgrade old prototype without deleting the user's database.
        if 'category' not in _columns(con, 'items'):
            con.execute("ALTER TABLE items ADD COLUMN category TEXT NOT NULL DEFAULT 'Other'")
        inv_columns = _columns(con, 'inventory')
        if 'expiration_date' not in inv_columns:
            con.execute('ALTER TABLE inventory ADD COLUMN expiration_date TEXT')
        if 'updated_at' not in inv_columns:
            con.execute('ALTER TABLE inventory ADD COLUMN updated_at TEXT')
        if 'version' not in inv_columns:
            con.execute('ALTER TABLE inventory ADD COLUMN version INTEGER NOT NULL DEFAULT 0')
        existing = con.execute("SELECT sql FROM sqlite_master WHERE name='exchanges'").fetchone()
        if existing and 'in_transit' not in existing['sql']:
            # Old schema only allowed pending/accepted/declined. Rebuild to add states.
            con.execute('ALTER TABLE exchanges RENAME TO exchanges_v1_backup')
            _make_exchanges(con)
            con.execute('''INSERT INTO exchanges
              (id,source_bank_id,destination_bank_id,item_id,quantity,status,created_at,updated_at)
              SELECT id,source_bank_id,destination_bank_id,item_id,quantity,
                CASE WHEN status='accepted' THEN 'received' ELSE status END,
                created_at,updated_at FROM exchanges_v1_backup''')
            con.execute('DROP TABLE exchanges_v1_backup')
        else:
            _make_exchanges(con)
        if 'barcode' not in _columns(con,'items'):
            con.execute('ALTER TABLE items ADD COLUMN barcode TEXT')
        for column in ('pickup_at','delivery_at','carrier_notes'):
            if column not in _columns(con,'exchanges'):
                con.execute(f'ALTER TABLE exchanges ADD COLUMN {column} TEXT')
        con.executescript('''
        CREATE UNIQUE INDEX IF NOT EXISTS idx_item_barcode ON items(barcode) WHERE barcode IS NOT NULL;
        CREATE TABLE IF NOT EXISTS lots (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          bank_id INTEGER NOT NULL REFERENCES banks(id),
          item_id INTEGER NOT NULL REFERENCES items(id),
          quantity INTEGER NOT NULL CHECK(quantity>=0),
          expiration_date TEXT, source TEXT NOT NULL DEFAULT 'manual',
          received_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_lots_bank_item ON lots(bank_id,item_id,expiration_date);
        CREATE TABLE IF NOT EXISTS shipment_lots (
          exchange_id INTEGER NOT NULL REFERENCES exchanges(id),
          expiration_date TEXT, quantity INTEGER NOT NULL CHECK(quantity>0),
          lot_id INTEGER REFERENCES lots(id)
        );
        CREATE TABLE IF NOT EXISTS donations (
          id INTEGER PRIMARY KEY AUTOINCREMENT, bank_id INTEGER NOT NULL REFERENCES banks(id),
          item_id INTEGER NOT NULL REFERENCES items(id), donor TEXT NOT NULL,
          quantity INTEGER NOT NULL CHECK(quantity>0), lot_id INTEGER REFERENCES lots(id),
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS waste (
          id INTEGER PRIMARY KEY AUTOINCREMENT, bank_id INTEGER NOT NULL REFERENCES banks(id),
          item_id INTEGER NOT NULL REFERENCES items(id), lot_id INTEGER REFERENCES lots(id),
          quantity INTEGER NOT NULL CHECK(quantity>0),
          reason TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS stock_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT, bank_id INTEGER NOT NULL REFERENCES banks(id),
          item_id INTEGER NOT NULL REFERENCES items(id), delta INTEGER NOT NULL,
          reason TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_stock_events_forecast ON stock_events(bank_id,item_id,created_at);
        CREATE TABLE IF NOT EXISTS sync_operations (
          id INTEGER PRIMARY KEY AUTOINCREMENT, bank_id INTEGER NOT NULL REFERENCES banks(id),
          op_key TEXT NOT NULL, item_id INTEGER NOT NULL REFERENCES items(id),
          delta INTEGER NOT NULL, base_version INTEGER NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(bank_id,op_key)
        );
        CREATE TABLE IF NOT EXISTS audit_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT, bank_id INTEGER REFERENCES banks(id),
          actor_user_id INTEGER REFERENCES users(id), operation TEXT NOT NULL,
          result TEXT NOT NULL, detail TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS users (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          bank_id INTEGER NOT NULL REFERENCES banks(id),
          username TEXT NOT NULL, password_hash TEXT NOT NULL,
          role TEXT NOT NULL DEFAULT 'staff' CHECK(role IN ('staff','admin')),
          active INTEGER NOT NULL DEFAULT 1,
          UNIQUE(bank_id,username)
        );
        CREATE TABLE IF NOT EXISTS auth_tokens (
          token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
          expires_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS activity (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          bank_id INTEGER NOT NULL REFERENCES banks(id),
          actor_user_id INTEGER REFERENCES users(id),
          action TEXT NOT NULL, details TEXT NOT NULL,
          exchange_id INTEGER REFERENCES exchanges(id),
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS notifications (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          bank_id INTEGER NOT NULL REFERENCES banks(id),
          message TEXT NOT NULL, exchange_id INTEGER REFERENCES exchanges(id),
          is_read INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_exchanges_source_status ON exchanges(source_bank_id,status);
        CREATE INDEX IF NOT EXISTS idx_exchanges_dest_status ON exchanges(destination_bank_id,status);
        CREATE INDEX IF NOT EXISTS idx_activity_bank ON activity(bank_id,id);
        CREATE INDEX IF NOT EXISTS idx_notifications_bank ON notifications(bank_id,id);
        ''')
        if not con.execute('SELECT 1 FROM banks LIMIT 1').fetchone():
            for row in [
                (1,'Central Food Pantry','Downtown',34.744,-92.278),
                (2,'Northside Food Pantry','North Little Rock area',34.773,-92.274),
                (3,'Westside Food Pantry','West Little Rock area',34.741,-92.333),
            ]:
                con.execute('INSERT INTO banks(id,name,area,latitude,longitude) VALUES(?,?,?,?,?)',row)
            item_names = [
                ('Rice','bags','Dry Goods'),('Canned Beans','cans','Canned Goods'),
                ('Pasta','boxes','Dry Goods'),('Peanut Butter','jars','Pantry'),
                ('Cereal','boxes','Dry Goods'),('Canned Soup','cans','Canned Goods'),
                ('Apples','boxes','Produce'),('Bottled Water','cases','Beverages'),
            ]
            con.executemany('INSERT INTO items(name,unit,category) VALUES(?,?,?)', item_names)
            stocks = [
                (1,1,5,20),(1,2,42,15),(1,3,12,12),(1,4,4,15),
                (1,5,6,12),(1,6,25,15),(1,7,3,10),(1,8,35,15),
                (2,1,40,12),(2,2,4,18),(2,3,9,15),(2,4,18,10),
                (2,5,20,12),(2,6,6,20),(2,7,18,8),(2,8,6,15),
                (3,1,8,16),(3,2,30,10),(3,3,30,12),(3,4,20,10),
                (3,5,4,15),(3,6,30,10),(3,7,8,10),(3,8,35,12)
            ]
            con.executemany('INSERT INTO inventory(bank_id,item_id,quantity,target) VALUES(?,?,?,?)',stocks)
        # Seed stock lots for pre-upgrade inventories exactly once, not every startup.
        # A zero-quantity lot is unnecessary.
        con.execute('''INSERT INTO lots(bank_id,item_id,quantity,expiration_date,source)
            SELECT i.bank_id,i.item_id,i.quantity,i.expiration_date,'legacy'
            FROM inventory i WHERE i.quantity>0
            AND NOT EXISTS(SELECT 1 FROM lots l WHERE l.bank_id=i.bank_id AND l.item_id=i.item_id)''')
        # On old DBs, reuse the old per-bank password for the new staff users.
        # Public demo passwords are only created in explicit classroom demo setups.
        for b in con.execute('SELECT id,password_hash FROM banks'):
            if not con.execute('SELECT 1 FROM users WHERE bank_id=?',(b['id'],)).fetchone():
                saved = b['password_hash'] or (hash_password('demo123') if demo_accounts else None)
                if saved:
                    con.execute('INSERT INTO users(bank_id,username,password_hash,role) VALUES(?,?,?,?)',
                                (b['id'],'staff',saved,'staff'))
        if demo_accounts and not con.execute("SELECT 1 FROM users WHERE role='admin' LIMIT 1").fetchone():
            con.execute('INSERT INTO users(bank_id,username,password_hash,role) VALUES(?,?,?,?)',
                        (1,'admin',hash_password('admin123'),'admin'))
        from phase3_schema import upgrade
        # Any pre-existing Food Bank DB without the final tables gets a backup
        # (including the original prototype); a brand-new DB does not.
        upgrade(con,db_path,needs_backup='banks' in before and 'recalls' not in before)
