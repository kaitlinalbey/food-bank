"""Read-only readiness snapshot. Never perform mutations or claim failover."""
from concurrent.futures import ThreadPoolExecutor
import sqlite3
from pathlib import Path
from database import read_db


def snapshot(db_path, node_check=None, check_nodes=True):
    """Summarize real SQLite state and optionally check reachable node processes."""
    db_path=Path(db_path)
    if not db_path.is_file():
        return {'database':{'healthy':False,'reason':'Database file does not exist'},'nodes':[],
                'overall':'unavailable'}
    with read_db(db_path) as db:
        check=db.execute('PRAGMA quick_check').fetchone()[0]
        bank_rows=[dict(r) for r in db.execute('SELECT id,name FROM banks ORDER BY id LIMIT 30')]
        exchange_counts={r['status']:r['count'] for r in db.execute(
            'SELECT status,COUNT(*) AS count FROM exchanges GROUP BY status')}
        pending_reconciliations=db.execute("SELECT COUNT(*) FROM reconciliations WHERE status='pending'").fetchone()[0]
        active_recalls=db.execute("SELECT COUNT(*) FROM recalls WHERE status='active'").fetchone()[0]
        error_events=db.execute("SELECT COUNT(*) FROM distributed_events WHERE status_code>=400").fetchone()[0]
        event_count=db.execute('SELECT COUNT(*) FROM distributed_events').fetchone()[0]
        latest=db.execute('SELECT created_at FROM distributed_events ORDER BY id DESC LIMIT 1').fetchone()
    nodes=[]
    if check_nodes and node_check:
        with ThreadPoolExecutor(max_workers=10) as executor:
            nodes=list(executor.map(node_check,bank_rows))
    ready=check=='ok'
    return {
        'database':{'healthy':ready,'integrity_check':check,'bytes':db_path.stat().st_size},
        'food_bank_count':len(bank_rows),
        'transfers_by_status':exchange_counts,
        'pending_reconciliations':pending_reconciliations,'active_recalls':active_recalls,
        'http_events_recorded':event_count,'http_errors_recorded':error_events,
        'latest_http_event':latest['created_at'] if latest else None,
        'nodes':nodes,
        'online_nodes':sum(bool(n.get('online')) for n in nodes),
        'overall':'database_ok' if ready else 'database_problem',
        'notice':('Live node checks are point-in-time HTTP checks. Offline local queues are held by each '
                  'node and cannot be counted centrally. Database checks do not prove production readiness.')
    }
