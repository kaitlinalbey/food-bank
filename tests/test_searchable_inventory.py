import os; os.environ.setdefault('FOOD_BANK_DEMO_MODE', '1')  # classroom demo accounts (see tests/__init__.py)
"""Search/filter tests for the own-bank UI and each remote bank's scoped listing."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

from database import read_db
from inventory_filters import (categories_for, filter_inventory, parse_filters,
                               shared_inventory_view)
from seed_more_demo_data import enrich_demo
from services.dashboard import dashboard
from services.inventory import add_item, inventory_list


class FilterUnitTests(unittest.TestCase):
    def setUp(self):
        self.own = [
            {'item_name':'Canned Beans','category':'Canned Goods','unit':'cans',
             'quantity':22,'surplus':10,'shortage':0,'expired':False},
            {'item_name':'White Rice','category':'Dry Goods','unit':'bags',
             'quantity':2,'surplus':0,'shortage':12,'expired':False},
            {'item_name':'Fresh Milk','category':'Dairy','unit':'cartons',
             'quantity':0,'surplus':0,'shortage':5,'expired':False},
            {'item_name':'Old Milk','category':'Dairy','unit':'cartons',
             'quantity':7,'surplus':0,'shortage':0,'expired':True},
            {'item_name':'Salt','category':'Dry Goods','unit':'boxes',
             'quantity':8,'surplus':0,'shortage':0,'expired':False},
        ]

    def test_search_is_case_insensitive_partial_and_can_match_category(self):
        self.assertEqual([r['item_name'] for r in filter_inventory(self.own,parse_filters('BEaN'))],['Canned Beans'])
        self.assertEqual(len(filter_inventory(self.own,parse_filters('Dairy'))),2)
        self.assertEqual(len(filter_inventory(self.own,parse_filters('Rice','Dairy'))),0)
        self.assertEqual(categories_for(self.own),['Canned Goods','Dairy','Dry Goods'])

    def test_own_status_filters(self):
        counts={'all':5,'in_stock':4,'out_of_stock':1,'shortage':2,
                'surplus':1,'expired':1,'at_target':1}
        for key,count in counts.items():
            with self.subTest(status=key):
                self.assertEqual(len(filter_inventory(self.own,parse_filters(status=key))),count)

    def test_other_bank_filters_never_mix_inventory(self):
        north=[{'item_name':'Beans','category':'Canned','unit':'cans','available':6},
               {'item_name':'Pasta','category':'Dry','unit':'boxes','available':0}]
        west=[{'item_name':'Rice','category':'Dry','unit':'bags','available':3}]
        search=parse_filters('Rice',status='available',shared=True)
        self.assertEqual(filter_inventory(north,search,shared=True),[])
        self.assertEqual([r['item_name'] for r in filter_inventory(west,search,shared=True)],['Rice'])
        self.assertEqual(len(filter_inventory(north,parse_filters(status='unavailable',shared=True),shared=True)),1)
        self.assertEqual(len(filter_inventory(north,parse_filters(status='available',shared=True),shared=True)),1)

    def test_invalid_and_overlong_parameters_are_bounded(self):
        params=parse_filters('  X'*100,' Y'*100,'other')
        self.assertLessEqual(len(params['q']),100)
        self.assertLessEqual(len(params['category']),60)
        self.assertEqual(params['status'],'all')
        self.assertEqual(parse_filters(status='expired',shared=True)['status'],'all')

    def test_remote_rows_exclude_sensitive_stock_fields(self):
        raw=[dict(self.own[0],item_id=1,target=10,reserved=3,lot_count=5,version=9,expiration_date='2026-12-30')]
        sanitized=shared_inventory_view(raw)
        self.assertEqual(sanitized,[{'item_id':1,'item_name':'Canned Beans',
                                     'category':'Canned Goods','unit':'cans','available':10}])
        for key in ('quantity','target','reserved','lot_count','version','expiration_date','shortage'):
            self.assertNotIn(key,sanitized[0])


@unittest.skipUnless(importlib.util.find_spec('jinja2') is not None,'Jinja2 (installed with Flask) required to render templates')
class InventoryTemplateTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=Path(self.tmp.name)/'test.db'
        enrich_demo(self.db)

    def tearDown(self):self.tmp.cleanup()

    def _html(self,bank_id=2,search='',remote_search='',edit=False,role='staff'):
        from jinja2 import Environment,FileSystemLoader
        own=dashboard(self.db,1)
        with read_db(self.db) as con:
            remote_rows=shared_inventory_view(inventory_list(con,bank_id))
            other=[dict(r) for r in con.execute('SELECT id,name,area FROM banks WHERE id != 1 ORDER BY id')]
        own_filters=parse_filters(search)
        remote_filters=parse_filters(remote_search,shared=True)
        selected=next(b for b in other if b['id']==bank_id)
        env=Environment(loader=FileSystemLoader(str(Path(__file__).resolve().parents[1]/'templates')),autoescape=True)
        env.globals.update(url_for=lambda name,**kwargs:'/'+name,session={},get_flashed_messages=lambda:[],
                           bank_name='Central',csrf_token='test-csrf',new_request_key=lambda:'unique-key-1234')
        # The multi-page interface shows our own inventory and other banks on separate pages;
        # render both pages so each test can inspect the section it needs.
        own_page=env.get_template('inventory.html').render(**own,
                  user={'role':role},inventory_edit=edit,
                  visible_inventory=filter_inventory(own['inventory'],own_filters),
                  own_filters=own_filters,own_categories=categories_for(own['inventory']))
        other_page=env.get_template('other_banks.html').render(user={'role':role},
                  other_banks=other,selected_bank=selected,other_inventory=filter_inventory(remote_rows,remote_filters,shared=True),
                  other_total=len(remote_rows),other_filters=remote_filters,other_categories=categories_for(remote_rows),other_error=None)
        return own_page+other_page

    def test_own_view_and_edit_are_filterable(self):
        for edit in (False,True):
            html=self._html(search='whole milk',edit=edit)
            portion=html.split('<section id="inventory">',1)[1].split('</section>',1)[0]
            self.assertIn('name="q" value="whole milk"',portion)
            self.assertIn('name="category"',portion)
            self.assertIn('name="status"',portion)
            self.assertIn('Whole Milk',portion)
            self.assertNotIn('<strong>White Rice</strong>',portion)
            self.assertEqual('name="quantity"' in portion,edit)
        remote=self._html(search='whole milk').split('<section id="other-inventories">',1)[1].split('</section>',1)[0]
        self.assertNotIn('name="quantity"',remote)
        self.assertIn('name="other_q"',remote)

    def test_each_other_bank_has_independent_search(self):
        unique_item='Northside Only Special Rice'
        add_item(self.db,2,{'name':unique_item,'unit':'bags','category':'Dry Goods','quantity':15,'target':2})
        north=self._html(bank_id=2,remote_search='Special Rice')
        west=self._html(bank_id=3,remote_search='Special Rice')
        north_table=north.split('<section id="other-inventories">',1)[1].split('</section>',1)[0]
        west_table=west.split('<section id="other-inventories">',1)[1].split('</section>',1)[0]
        self.assertIn(unique_item,north_table)
        self.assertNotIn(unique_item,west_table)
        self.assertIn('No items match this bank',west_table)
        self.assertIn('Northside Food Pantry',north_table)
        self.assertIn('Westside Food Pantry',west_table)

    def test_viewer_cannot_edit_even_with_edit_url(self):
        html=self._html(edit=True,role='viewer')
        own=html.split('<section id="inventory">',1)[1].split('</section>',1)[0]
        self.assertNotIn('name="quantity"',own)
        self.assertNotIn('Edit Inventory</a>',own)


@unittest.skipUnless(importlib.util.find_spec('flask') is not None,'Flask required for API integration')
class SearchAPIIntegrationTests(unittest.TestCase):
    def setUp(self):
        from app import create_app
        self.temp=tempfile.TemporaryDirectory()
        self.app=create_app(Path(self.temp.name)/'api.db')
        self.app.config['TESTING']=True
        self.client=self.app.test_client()
        login=self.client.post('/api/login',json={'bank_id':1,'username':'staff','password':'demo123'})
        self.assertEqual(login.status_code,200,login.get_json())
        self.headers={'Authorization':'Bearer '+login.json['token']}

    def tearDown(self):self.temp.cleanup()

    def test_other_bank_api_filter_works_and_remains_redacted(self):
        all_rows=self.client.get('/api/banks/2/inventory',headers=self.headers)
        self.assertEqual(all_rows.status_code,200)
        self.assertEqual(all_rows.json['bank']['id'],2)
        self.assertGreater(all_rows.json['total'],0)
        self.assertEqual(all_rows.json['filtered_total'],all_rows.json['total'])
        self.assertTrue(all('quantity' not in r and 'target' not in r for r in all_rows.json['inventory']))
        filtered=self.client.get('/api/banks/2/inventory?q=Rice&status=available',headers=self.headers)
        self.assertEqual(filtered.status_code,200)
        self.assertTrue(all('rice' in r['item_name'].lower() and r['available']>0 for r in filtered.json['inventory']))
        notfound=self.client.get('/api/banks/900/inventory',headers=self.headers)
        self.assertEqual(notfound.status_code,404)
        self.assertEqual(self.client.get('/api/banks/2/inventory').status_code,401)


if __name__=='__main__':unittest.main()
