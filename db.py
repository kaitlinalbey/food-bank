import sqlite3
from contextlib import contextmanager
from pathlib import Path
from flask import current_app, g

DEFAULT_DB = Path(__file__).resolve().parent / 'data' / 'food_bank.db'
SCHEMA_FILE = Path(__file__).resolve().parent / 'schema.sql'


def connect(db_path):
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    return conn


def get_db():
    if "db" not in g:
        g.db = connect(current_app.config["DB_PATH"])
    return g.db

def close_db(exc=None):
    if "db" in g:
        g.db.close()
        del g.db


def init_db(db_path):
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA_FILE.read_text())
    finally:
        conn.close()


@contextmanager
def transaction():
    db = get_db()
    if db.in_transaction:
        raise RuntimeError("Nested transactions are not supported.")
    db.execute('BEGIN IMMEDIATE')
    try:
        yield db
    except BaseException:
        db.rollback()
        raise
    else:
        db.commit()


def init_app(app):
    init_db(app.config["DB_PATH"])
    app.teardown_appcontext(close_db)