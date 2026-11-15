"""Five-step example exchange on an isolated database; never touches the main demo."""
import argparse
from pathlib import Path
import tempfile
import json
from database import initialize,read_db
from services.transfers import create_transfer,change_transfer
from services.operations import donation
from services.lab import run_lab


def story():
    steps=[]
    with tempfile.TemporaryDirectory(prefix='foodbank_story_') as home:
        db=Path(home)/'story.db';initialize(db,demo_accounts=True)
        def qty(bank,item=1):
            with read_db(db) as c:
                return c.execute('SELECT quantity FROM inventory WHERE bank_id=? AND item_id=?',(bank,item)).fetchone()[0]
        before_source=qty(2);before_destination=qty(1)
        steps.append({'step':'1. View supplies','source_rice':before_source,'destination_rice':before_destination,'passed':True})
        gift=donation(db,2,{'item_id':1,'quantity':5,'donor':'Fictional Community Drive','lot_code':'DEMO-LOT-01'})
        steps.append({'step':'2. Receive donation','donation_id':gift['donation_id'],
                      'source_rice':qty(2),'passed':qty(2)==before_source+5})
        eid,created=create_transfer(db,1,{'source_bank_id':2,'item_id':1,'quantity':10,
                                           'request_key':'presentation-story-00001'})
        with read_db(db) as c:
            status=c.execute('SELECT status FROM exchanges WHERE id=?',(eid,)).fetchone()[0]
        steps.append({'step':'3. Request 10 bags','exchange_id':eid,'status':status,
                      'passed':created and status=='pending'})
        change_transfer(db,2,eid,'approve')
        steps.append({'step':'4. Approve and reserve','source_rice':qty(2),
                      'passed':qty(2)==before_source+5})
        change_transfer(db,2,eid,'ship')
        ship_source=qty(2)
        steps.append({'step':'5. Ship','source_rice':ship_source,
                      'passed':ship_source==before_source+5-10})
        change_transfer(db,1,eid,'receive')
        with read_db(db) as c:
            status=c.execute('SELECT status FROM exchanges WHERE id=?',(eid,)).fetchone()[0]
        steps.append({'step':'6. Confirm receipt','destination_rice':qty(1),
                      'status':status,'passed':qty(1)==before_destination+10 and status=='received'})
    fault=run_lab('outage')
    steps.append({'step':'7. Demonstrate outage and safe conflict',
                  'passed':fault['passed']==1,'lab_result':fault['results'][0]})
    return {'fictional_isolated_data':True,'total':len(steps),
            'passed':sum(s['passed'] for s in steps),'steps':steps}


def main():
    p=argparse.ArgumentParser();p.add_argument('--out',default='reports/presentation_story.json');args=p.parse_args()
    result=story();path=Path(args.out);path.parent.mkdir(exist_ok=True,parents=True)
    path.write_text(json.dumps(result,indent=2)+'\n',encoding='utf8')
    print(json.dumps(result,indent=2));print('Report:',path)
    raise SystemExit(0 if result['passed']==result['total'] else 1)

if __name__=='__main__':main()
