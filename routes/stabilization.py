"""Readiness and request-trace endpoints for classroom demonstrations."""
import re
from flask import Blueprint,current_app,g,jsonify
from database import read_db
from routes.common import require_login,require_admin
from services.readiness import snapshot
from services.validation import Problem
from routes.stats import _node_status

bp=Blueprint('stabilization',__name__)

@bp.get('/api/admin/system/status')
@require_admin
def system_status():
    return jsonify(snapshot(current_app.config['DB_PATH'],_node_status))

@bp.get('/api/traces/<trace_id>')
@require_login
def trace_lookup(trace_id):
    # Correlation ID is an opaque identifier and never contains request payloads.
    if not re.fullmatch(r'[A-Za-z0-9_-]{8,80}',trace_id):
        raise Problem('Invalid trace ID.')
    with read_db(current_app.config['DB_PATH']) as db:
        if g.user['role']=='admin':
            rows=db.execute('''SELECT created_at,bank_id,origin,method,path,status_code,
                correlation_id,latency_ms FROM distributed_events
                WHERE correlation_id=? ORDER BY id DESC LIMIT 100''',(trace_id,))
        else:
            rows=db.execute('''SELECT created_at,bank_id,origin,method,path,status_code,
                correlation_id,latency_ms FROM distributed_events
                WHERE correlation_id=? AND bank_id=? ORDER BY id DESC LIMIT 100''',
                (trace_id,g.bank_id))
        events=[dict(r) for r in rows]
    return jsonify({'correlation_id':trace_id,'events':events,'count':len(events)})
