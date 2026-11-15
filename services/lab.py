"""Repeatable lab simulations, isolated from production inventory.

No network storm and no changes to a user's actual Food Bank DB. Each test
receives its own seeded temporary SQLite database and a bounded workload.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import tempfile
from time import perf_counter
import sqlite3
from database import initialize,read_db,write_db
from offline_queue import save_snapshot,enqueue,sync_pending,all_queue
from .transfers import create_transfer,change_transfer
from .inventory import stock
from .operations import donation,sync_adjustment
from .advanced import (issue_recall,close_recall,propose_count,decide_count,
                       add_driver,add_vehicle,assign_transport,receipt_with_discrepancy,
                       independent_receipt)
from .validation import Problem

SCENARIOS={
    'multi_bank':'20-bank load simulation with concurrent database operations',
    'outage':'Disconnect, queue, reconnect, sync and conflict detection',
    'concurrent':'Two competing transfers and idempotent request replay',
    'recall':'Recall quarantine and clearance across banks',
    'reconciliation':'Physical count approved by manager',
    'delivery':'Assigned transport, partial delivery and discrepancy',
    'backup_restore':'Consistent SQLite backup with verified restore',
    'offline_receipt':'Independent donation safely merged after other stock edits'
}


def _do(scenario,path,home):
    if scenario=='multi_bank':
        with write_db(path) as c:
            for bank_id in range(4,21):
                c.execute('INSERT INTO banks(id,name,area,latitude,longitude) VALUES(?,?,?,?,?)',
                    (bank_id,f'Lab Pantry {bank_id}','Lab Area',34.73+bank_id/10000,-92.28))
                c.execute('INSERT INTO inventory(bank_id,item_id,quantity,target) VALUES(?,?,0,0)',(bank_id,1))
                c.execute("INSERT INTO storage_zones(bank_id,storage_type,capacity_units) VALUES(?,'ambient',10000)",(bank_id,))
        times=[]
        def work(i):
            t=perf_counter()
            with write_db(path) as c:
                c.execute('UPDATE inventory SET target=? WHERE bank_id=? AND item_id=1',(i%20,i%20+1))
            return (perf_counter()-t)*1000
        with ThreadPoolExecutor(max_workers=6) as pool:
            times=list(pool.map(work,range(60)))
        with read_db(path) as c:
            n=c.execute('SELECT COUNT(*) FROM banks').fetchone()[0]
        assert n==20
        times.sort()
        return {'banks':n,'concurrent_workers':6,'operations':len(times),
                'p95_ms':round(times[int(.95*(len(times)-1))],2)}
    if scenario=='outage':
        queue=home/'offline_node.db'
        with read_db(path) as c:
            row=stock(c,1,1)
        save_snapshot(queue,{'bank':{'id':1,'name':'Lab'},'inventory':[{'item_id':1,'item_name':'Rice','quantity':row['quantity'],'version':row['version']}]})
        enqueue(queue,1,-1)
        class Offline(Exception):
            status=503
        def fail(*_args):raise Offline('Simulated central API outage')
        result=sync_pending(queue,fail)
        assert result['synced']==0 and result['error']
        # A concurrent change invalidates negative stock corrections.
        sync_adjustment(path,1,{'item_id':1,'delta':1,'base_version':row['version'],
                                'op_key':'lab-edit-000001'})
        def send(_method,_route,data):return sync_adjustment(path,1,data)
        result=sync_pending(queue,send)
        assert result['conflicts']==1 and all_queue(queue)[0]['status']=='conflict'
        return {'offline_queue':1,'conflicts_flagged':1,'overwrites_prevented':1}
    if scenario=='concurrent':
        ids=[create_transfer(path,1,{'source_bank_id':2,'item_id':1,'quantity':14,
                                   'request_key':f'lab-key-{i:09d}'})[0] for i in range(2)]
        def approve(ex_id):
            try:change_transfer(path,2,ex_id,'approve');return True
            except Problem as e:
                if e.status!=409:raise
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(approve,ids))
        assert results.count(True)==1 and results.count(False)==1
        assert create_transfer(path,1,{'source_bank_id':2,'item_id':1,'quantity':14,
                    'request_key':'lab-key-000000000'})==(ids[0],False)
        return {'approved':1,'rejected':1,'duplicate_request_safe':True}
    if scenario=='recall':
        x=donation(path,2,{'item_id':1,'quantity':5,'donor':'Lab donor','lot_code':'LAB-RECALL-001'})
        with read_db(path) as c:before=c.execute('SELECT quantity FROM inventory WHERE bank_id=2 AND item_id=1').fetchone()[0]
        result=issue_recall(path,1,{'item_id':1,'lot_code':'LAB-RECALL-001','reason':'Lab test recall'})
        with read_db(path) as c:
            flag=c.execute('SELECT recalled FROM lots WHERE id=?',(x['lot_id'],)).fetchone()[0]
        assert flag==1 and result['affected_banks']==[2]
        close_recall(path,1,result['recall_id'])
        with read_db(path) as c:flag=c.execute('SELECT recalled FROM lots WHERE id=?',(x['lot_id'],)).fetchone()[0]
        assert flag==0
        return {'affected_banks':1,'quarantine_and_release':True,'physical_qty_kept':before}
    if scenario=='reconciliation':
        x=propose_count(path,1,{'item_id':2,'counted_qty':39,'reason':'Lab shelf count'})
        assert decide_count(path,1,x['reconciliation_id'],'apply')['status']=='applied'
        with read_db(path) as c:assert stock(c,1,2)['quantity']==39
        return {'count_applied':True,'new_quantity':39}
    if scenario=='delivery':
        ex=create_transfer(path,1,{'source_bank_id':2,'item_id':1,'quantity':5,'request_key':'lab-delivery-001'})[0]
        change_transfer(path,2,ex,'approve')
        driver=add_driver(path,2,{'display_name':'Volunteer A'})['driver_id']
        vehicle=add_vehicle(path,2,{'label':'Van 1','capacity_units':20})['vehicle_id']
        assign_transport(path,2,ex,{'driver_id':driver,'vehicle_id':vehicle})
        change_transfer(path,2,ex,'ship')
        result=receipt_with_discrepancy(path,1,ex,{'received_qty':3,'damaged_qty':1,'notes':'Lab missing box'})
        assert (result['received_qty'],result['missing_qty'],result['damaged_qty'])==(3,1,1)
        with read_db(path) as c:
            assert c.execute('SELECT status FROM exchanges WHERE id=?',(ex,)).fetchone()[0]=='received'
        return {'shipped':5,'received':3,'damaged':1,'missing':1}
    if scenario=='backup_restore':
        copy=home/'consistent-backup.db';restored=home/'restored.db'
        with closing(sqlite3.connect(str(path))) as source,closing(sqlite3.connect(str(copy))) as destination:
            source.backup(destination)
        with closing(sqlite3.connect(str(copy))) as source,closing(sqlite3.connect(str(restored))) as destination:
            source.backup(destination)
        with closing(sqlite3.connect(str(restored))) as c:
            assert c.execute('PRAGMA quick_check').fetchone()[0]=='ok'
            count=c.execute('SELECT COUNT(*) FROM inventory').fetchone()[0]
            assert count>=24
        return {'restored_rows':count,'integrity':'ok'}
    if scenario=='offline_receipt':
        with read_db(path) as c:initial=stock(c,1,2)
        donor_data={'item_id':2,'quantity':4,'donor':'Independent church collection',
             'lot_code':'LAB-OFFLINE-01','op_key':'lab-receipt-001'}
        first=sync_adjustment(path,1,{'item_id':2,'delta':2,'base_version':initial['version'],
                                       'op_key':'lab-concurrent-001'})
        result=independent_receipt(path,1,donor_data)
        assert not result['duplicate'] and independent_receipt(path,1,donor_data)['duplicate']
        with read_db(path) as c:assert stock(c,1,2)['quantity']==initial['quantity']+6
        return {'safe_addition':4,'other_edit':2,'duplicate_ignored':True}
    raise ValueError('Unknown test scenario.')


def run_lab(scenario='all'):
    if scenario!='all' and scenario not in SCENARIOS:
        raise Problem('Unknown test scenario.')
    names=list(SCENARIOS) if scenario=='all' else [scenario]
    results=[]
    for name in names:
        with tempfile.TemporaryDirectory(prefix='foodbank_lab_') as root:
            home=Path(root);db=home/'isolated.db'
            start=perf_counter()
            try:
                initialize(db,demo_accounts=True)
                detail=_do(name,db,home)
                row={'scenario':name,'passed':True,'details':detail}
            except Exception as exc:
                row={'scenario':name,'passed':False,'error':type(exc).__name__+': '+str(exc)[:200]}
            row['duration_ms']=round((perf_counter()-start)*1000,1)
            results.append(row)
    return {'notice':'Tests use isolated temporary SQLite databases. Live data is unchanged.',
            'passed':sum(r['passed'] for r in results),'total':len(results),'results':results}
