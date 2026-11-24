import sqlite3
from contextlib import contextmanager
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parent / 'data' / 'food_bank.db'
SCHEMA_FILE = Path(__file__).resolve().parent / 'schema.sql'


def connect(db_path):
    raise NotImplementedError


def get_db():
    raise NotImplementedError


def close_db(exc=None):
    raise NotImplementedError


def init_db(db_path):
    raise NotImplementedError


@contextmanager
def transaction():
    raise NotImplementedError
    yield
