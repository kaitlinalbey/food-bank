"""Bounded SQLite multi-bank concurrency benchmark; not an HTTP/production load test."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import json
from pathlib import Path
import tempfile
import time
from database import initialize,read_db,write_db


def case(bank_count=5,rounds=4):
    if bank_count not in (5,10,20) or not (1<=rounds<=20):
        raise ValueError('Supported bank counts: 5, 10, 20. Rounds: 1-20.')
    with tempfile.TemporaryDirectory(prefix='foodbank_perf_') as root:
        db=Path(root)/'bench.db'
        initialize(db,demo_accounts=True)
        with write_db(db) as c:
            for bank_id in range(4,bank_count+1):
                c.execute('INSERT INTO banks(id,name,area,latitude,longitude) VALUES(?,?,?,?,?)',
                    (bank_id,f'Perf bank {bank_id}','Test',34.74,-92.26))
                c.execute('INSERT INTO inventory(bank_id,item_id,quantity,target) VALUES(?,?,0,0)',(bank_id,1))
        with read_db(db) as c:
            base={r['bank_id']:r['target'] for r in c.execute('SELECT bank_id,target FROM inventory WHERE item_id=1')}
        def update(bank_id):
            t=time.perf_counter()
            with write_db(db) as c:
                c.execute('UPDATE inventory SET target=target+1 WHERE bank_id=? AND item_id=1',(bank_id,))
            return round((time.perf_counter()-t)*1000,3)
        tasks=[b for i in range(rounds) for b in range(1,bank_count+1)]
        t0=time.perf_counter()
        errors=[];latencies=[]
        with ThreadPoolExecutor(max_workers=min(8,bank_count)) as pool:
            futures=[pool.submit(update,b) for b in tasks]
            for f in futures:
                try:latencies.append(f.result())
                except Exception as exc:errors.append(str(exc)[:150])
        duration=time.perf_counter()-t0
        with read_db(db) as c:
            final={r['bank_id']:r['target'] for r in c.execute('SELECT bank_id,target FROM inventory WHERE item_id=1')}
            integrity=c.execute('PRAGMA quick_check').fetchone()[0]
        accurate=all(final[b]==base[b]+rounds for b in range(1,bank_count+1))
        sorted_l=sorted(latencies)
        return {'bank_count':bank_count,'workers':min(8,bank_count),'requested_operations':len(tasks),
                'successful_operations':len(latencies),'failed_operations':len(errors),
                'duration_s':round(duration,3),
                'ops_per_second':round(len(latencies)/duration,2) if duration else 0,
                'p50_ms':sorted_l[len(sorted_l)//2] if sorted_l else None,
                'p95_ms':sorted_l[int((len(sorted_l)-1)*.95)] if sorted_l else None,
                'quantity_correct':accurate,'sqlite_integrity':integrity,
                'passed':not errors and accurate and integrity=='ok',
                'errors':errors[:3]}


def run_all():
    return {'benchmark_type':'simulated_local_SQLite_concurrency_NOT_remote_HTTP_load',
            'generated_at_utc':datetime.now(timezone.utc).isoformat(),
            'results':[case(b) for b in (5,10,20)]}


def main():
    p=argparse.ArgumentParser();p.add_argument('--out',default='reports/performance.json');a=p.parse_args()
    result=run_all();dest=Path(a.out);dest.parent.mkdir(parents=True,exist_ok=True)
    dest.write_text(json.dumps(result,indent=2)+'\n',encoding='utf8')
    print(json.dumps(result,indent=2));print('Report:',dest)
    raise SystemExit(0 if all(r['passed'] for r in result['results']) else 1)

if __name__=='__main__':main()
