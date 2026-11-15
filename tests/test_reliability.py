import os; os.environ.setdefault('FOOD_BANK_DEMO_MODE', '1')  # classroom demo accounts (see tests/__init__.py)
"""Reliability regression tests: no leaked SQLite handles, no spurious upgrade backups.

Each test uses a plain tempfile.TemporaryDirectory(), whose cleanup raises if any
file is still open. On Windows that is exactly the failure these tests guard against.
"""
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from database import initialize, read_db


def _old_prototype_db(path):
    """Minimal schema of the original prototype (before lots/recalls existed)."""
    with closing(sqlite3.connect(path)) as con:
        con.executescript('''
        CREATE TABLE banks(id INTEGER PRIMARY KEY,name TEXT NOT NULL,area TEXT NOT NULL,
          latitude REAL NOT NULL,longitude REAL NOT NULL,password_hash TEXT NOT NULL);
        CREATE TABLE items(id INTEGER PRIMARY KEY,name TEXT NOT NULL UNIQUE,unit TEXT NOT NULL);
        CREATE TABLE inventory(bank_id INTEGER NOT NULL,item_id INTEGER NOT NULL,
          quantity INTEGER NOT NULL,target INTEGER NOT NULL,PRIMARY KEY(bank_id,item_id));
        INSERT INTO banks VALUES(1,'Fictional Old Pantry','Downtown',34.7,-92.2,'');
        INSERT INTO items VALUES(1,'Rice','bags');
        INSERT INTO inventory VALUES(1,1,9,20);
        ''')
        con.commit()


class ReliabilityTests(unittest.TestCase):
    def test_fresh_database_creates_no_upgrade_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'fresh.db'
            initialize(db)
            initialize(db)
            self.assertFalse((Path(tmp) / 'backups_before_upgrade').exists())

    def test_old_database_backed_up_once_and_files_released(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'old.db'
            _old_prototype_db(db)
            initialize(db)
            backups = sorted(p.name for p in (Path(tmp) / 'backups_before_upgrade').iterdir())
            self.assertEqual(len(backups), 2, backups)
            self.assertTrue(any(b.startswith('food_bank_pre_expansion_') for b in backups))
            self.assertTrue(any(b.startswith('food_bank_pre_final_') for b in backups))
            initialize(db)  # already upgraded: no further backups
            self.assertEqual(len(list((Path(tmp) / 'backups_before_upgrade').iterdir())), 2)
            with read_db(db) as con:
                self.assertEqual(con.execute('SELECT quantity FROM inventory WHERE bank_id=1').fetchone()[0], 9)
        # Leaving the with-block deletes every file; an open handle would raise here on Windows.

    def test_offline_queue_releases_its_file(self):
        from offline_queue import (save_snapshot, enqueue, all_queue, discard, enqueue_receipt,
                                   all_receipts, log_local_event, local_events)
        with tempfile.TemporaryDirectory() as tmp:
            q = Path(tmp) / 'queue.db'
            save_snapshot(q, {'bank': {'id': 1}, 'inventory': [
                {'item_id': 1, 'item_name': 'Rice', 'quantity': 5, 'version': 0}]})
            enqueue(q, 1, 2)
            enqueue_receipt(q, 1, 3, 'Fictional drive')
            log_local_event(q, 'GET', '/api/x', 'success', 'trace-0001', 1.0)
            self.assertEqual(len(all_queue(q)), 1)
            self.assertEqual(len(all_receipts(q)), 1)
            self.assertEqual(len(local_events(q)), 1)
            self.assertEqual(discard(q, all_queue(q)[0]['id']), 1)

    def test_offline_queue_rolls_back_failed_write(self):
        from offline_queue import _db, save_snapshot, all_queue
        with tempfile.TemporaryDirectory() as tmp:
            q = Path(tmp) / 'queue.db'
            save_snapshot(q, {'bank': {'id': 1}, 'inventory': []})
            with self.assertRaises(RuntimeError):
                with _db(q) as con:
                    con.execute("INSERT INTO queued(op_key,item_id,delta,base_version,status) VALUES('k-0000001',1,1,0,'pending')")
                    raise RuntimeError('simulated failure')
            self.assertEqual(all_queue(q), [])

    def test_isolated_lab_and_benchmark_complete(self):
        from services.lab import run_lab
        from benchmark import case
        result = run_lab('all')
        self.assertEqual(result['passed'], result['total'], [r.get('error') for r in result['results']])
        self.assertTrue(case(5, 2)['passed'])


if __name__ == '__main__':
    unittest.main()
