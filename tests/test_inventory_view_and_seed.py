import os; os.environ.setdefault('FOOD_BANK_DEMO_MODE', '1')  # classroom demo accounts (see tests/__init__.py)
"""New fictional data and view/edit UI smoke tests; no live Flask server needed."""
import tempfile
import unittest
from datetime import date
from pathlib import Path

from database import initialize, read_db, write_db
from seed_more_demo_data import SAMPLE_PRODUCTS, enrich_demo
from services.dashboard import dashboard


class ExpandedSampleDataTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/'sample.db'
    def tearDown(self):
        self.tmp.cleanup()

    def test_enriched_demo_is_idempotent_and_has_realistic_data(self):
        first=enrich_demo(self.path)
        self.assertEqual(first['banks'],3)
        self.assertEqual(first['items'],len(SAMPLE_PRODUCTS)+8)
        self.assertEqual(first['inventory_rows'],first['items']*3)
        self.assertEqual(first['new_donations'],9)
        self.assertEqual(first['new_transfers'],4)
        self.assertGreater(first['lots'],first['inventory_rows'])
        with read_db(self.path) as con:
            self.assertEqual([row[0] for row in con.execute('SELECT status FROM exchanges ORDER BY id')],
                             ['approved','received','in_transit','pending'])
            self.assertEqual(con.execute('SELECT COUNT(*) FROM lots WHERE expiration_date<?',
                                         (date.today().isoformat(),)).fetchone()[0],0)
            self.assertEqual(con.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertEqual(con.execute('PRAGMA foreign_key_check').fetchall(),[])
        second=enrich_demo(self.path)
        self.assertEqual(second['new_products'],0)
        self.assertEqual(second['new_inventory_rows'],0)
        self.assertEqual(second['new_lots'],0)
        self.assertEqual(second['new_donations'],0)
        self.assertEqual(second['new_transfers'],0)
        self.assertEqual(first['inventory_rows'],second['inventory_rows'])
        self.assertEqual(first['transfers'],second['transfers'])

    def test_existing_inventory_is_not_overwritten(self):
        initialize(self.path)
        with write_db(self.path) as con:
            con.execute('UPDATE inventory SET target=77 WHERE bank_id=1 AND item_id=1')
            con.execute('INSERT INTO items(name,unit,category) VALUES(?,?,?)',
                        ('Volunteer Custom Food','crates','Other'))
            custom_id=con.execute('SELECT id FROM items WHERE name="Volunteer Custom Food"').fetchone()[0]
            con.execute('INSERT INTO inventory(bank_id,item_id,quantity,target) VALUES(?,?,0,5)',
                        (1,custom_id))
        enriched=enrich_demo(self.path)
        self.assertEqual(enriched['new_inventory_rows'],len(SAMPLE_PRODUCTS)*3)
        with read_db(self.path) as con:
            self.assertEqual(con.execute('SELECT target FROM inventory WHERE bank_id=1 AND item_id=1').fetchone()[0],77)
            self.assertEqual(con.execute('SELECT target FROM inventory WHERE bank_id=1 AND item_id=?',(custom_id,)).fetchone()[0],5)
            self.assertEqual(con.execute('SELECT quantity FROM inventory WHERE bank_id=2 AND item_id=1').fetchone()[0],40)

    def test_sample_data_works_in_inventory_dashboard(self):
        enrich_demo(self.path)
        info=dashboard(self.path,1)
        self.assertEqual(len(info['inventory']),len(SAMPLE_PRODUCTS)+8)
        self.assertGreater(len(info['matches']),5)
        self.assertGreaterEqual(len(info['recent_donations']),3)
        self.assertTrue(any(item['item_name']=='Whole Milk' for item in info['inventory']))


class InventoryTabMarkupTests(unittest.TestCase):
    def test_view_mode_has_no_edit_forms_and_edit_mode_does(self):
        try:
            from jinja2 import Environment,FileSystemLoader
        except ImportError:
            self.skipTest('Jinja2 is needed to render templates')
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'sample.db';enrich_demo(path)
            info=dashboard(path,1)
            env=Environment(loader=FileSystemLoader(str(Path(__file__).resolve().parents[1]/'templates')),
                            autoescape=True)
            env.globals.update(url_for=lambda name,**kw:'/'+name,session={},
                               get_flashed_messages=lambda:[],bank_name='Central',
                               csrf_token='abc',new_request_key=lambda:'sample-request-unique')
            extra={'user':{'role':'staff'},'nodes':[],'offline_queue':[],'simulated':False,
                   'audit':{'failed_operations':[],'activity':[]}}
            template=env.get_template('inventory.html')  # My inventory page of the multi-page interface
            for is_edit in (False,True):
                result=template.render(**info,**extra,inventory_edit=is_edit)
                inventory=result.split('<section id="inventory">')[1].split('</section>')[0]
                self.assertIn('View Inventory',inventory)
                self.assertIn('Edit Inventory',inventory)
                self.assertEqual('name="quantity"' in inventory,is_edit)
                self.assertEqual('Add item' in inventory,is_edit)
                self.assertEqual('Set quantity to 0 first' in inventory,is_edit)
                self.assertEqual('<th>Usable stock</th>' in inventory,not is_edit)
            viewer=template.render(**{**info,**extra,'user':{'role':'viewer'}},inventory_edit=True)
            inventory=viewer.split('<section id="inventory">')[1].split('</section>')[0]
            self.assertNotIn('Edit Inventory</a>',inventory)
            self.assertNotIn('name="quantity"',inventory)


if __name__=='__main__':unittest.main()
