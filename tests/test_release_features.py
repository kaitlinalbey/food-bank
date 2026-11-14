import os; os.environ.setdefault('FOOD_BANK_DEMO_MODE', '1')  # classroom demo accounts (see tests/__init__.py)
"""New presentation/readiness checks without third-party dependencies."""
import tempfile
import unittest
from pathlib import Path
from database import initialize,read_db
from services.readiness import snapshot
from start_presentation import new_session
from benchmark import case
from presentation_story import story

class StabilizationTests(unittest.TestCase):
    def test_new_demo_session_is_isolated_and_does_not_overwrite_old(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            old=root/'ordinary.db';initialize(old)
            original=old.read_bytes()
            a,db1=new_session(root/'sessions')
            b,db2=new_session(root/'sessions')
            self.assertNotEqual(a,b)
            self.assertTrue((a/'queues').is_dir())
            self.assertTrue(db1.is_file() and db2.is_file())
            self.assertEqual(old.read_bytes(),original)

    def test_readiness_snapshot_and_node_checks(self):
        with tempfile.TemporaryDirectory() as temp:
            db=Path(temp)/'db.sqlite3';initialize(db)
            result=snapshot(db,lambda row:{'bank_name':row['name'],'bank_id':row['id'],'online':True})
            self.assertTrue(result['database']['healthy'])
            self.assertEqual(result['online_nodes'],3)
            self.assertEqual(result['food_bank_count'],3)
            self.assertEqual(result['transfers_by_status'],{})
            self.assertEqual(snapshot(Path(temp)/'missing.db')['overall'],'unavailable')
            self.assertFalse((Path(temp)/'missing.db').exists())

    def test_benchmark_five_banks_is_consistent(self):
        result=case(5,2)
        self.assertTrue(result['passed'],result)
        self.assertEqual(result['successful_operations'],10)

    def test_presentation_story_uses_isolated_data_and_passes(self):
        result=story()
        self.assertEqual(result['passed'],result['total'],result)
        self.assertTrue(result['fictional_isolated_data'])

if __name__=='__main__':unittest.main()
