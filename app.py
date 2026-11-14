"""Central API app factory. Run with python server.py or python start_demo.py."""
from flask import Flask,jsonify,g,request
import re
import time
from database import DEFAULT_DB,initialize,write_db
from services.core import Problem
from routes import auth,inventory,transfers,stats,admin,extensions,docs,advanced,stabilization,dbadmin


def create_app(db_path=None):
    app=Flask(__name__)
    app.config['MAX_CONTENT_LENGTH']=2*1024*1024
    @app.before_request
    def begin_trace():
        g.trace_started=time.monotonic()
        raw=request.headers.get('X-Request-ID','')
        g.correlation_id=raw if re.fullmatch(r'[A-Za-z0-9_-]{8,80}',raw) else 'central-'+str(time.monotonic_ns())
    @app.after_request
    def capture_event(response):
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['X-Frame-Options']='DENY'
        response.headers['Cache-Control']='no-store'
        response.headers['X-Request-ID']=getattr(g,'correlation_id','')
        if request.path.startswith('/api/') and request.path not in ('/api/health','/api/nodes'):
            try:
                elapsed=round((time.monotonic()-getattr(g,'trace_started',time.monotonic()))*1000,2)
                with write_db(app.config['DB_PATH']) as con:
                    con.execute('''INSERT INTO distributed_events(bank_id,origin,method,path,status_code,correlation_id,latency_ms)
                        VALUES(?,?,?,?,?,?,?)''',(getattr(g,'bank_id',None),'central_api',request.method,
                        request.path,response.status_code,getattr(g,'correlation_id','none'),elapsed))
                    con.execute('''DELETE FROM distributed_events WHERE id NOT IN
                        (SELECT id FROM distributed_events ORDER BY id DESC LIMIT 5000)''')
            except Exception:
                pass
        return response
    app.config['DB_PATH']=str(db_path or DEFAULT_DB)
    initialize(app.config['DB_PATH'])
    for module in (auth,inventory,transfers,stats,admin,extensions,docs,advanced,stabilization,dbadmin):
        app.register_blueprint(module.bp)

    @app.errorhandler(Problem)
    def business_error(exc):
        if request.path.startswith('/api/'):
            try:
                with write_db(app.config['DB_PATH']) as con:
                    con.execute('''INSERT INTO audit_events(bank_id,actor_user_id,operation,result,detail)
                        VALUES(?,?,?,?,?)''',(getattr(g,'bank_id',None),getattr(g,'user_id',None),
                        request.method+' '+request.path,'failed',f'HTTP {exc.status}: {str(exc)[:200]}'))
            except Exception:
                pass  # Audit failure must not hide original user-facing error.
        return jsonify({'error':str(exc)}),exc.status

    @app.errorhandler(404)
    def missing(_exc):
        return jsonify({'error':'Route not found.'}),404

    @app.errorhandler(405)
    def wrong_method(_exc):
        return jsonify({'error':'HTTP method not allowed.'}),405

    @app.errorhandler(413)
    def too_large(_exc):
        return jsonify({'error':'Request body is too large (2 MB limit).'}),413

    @app.errorhandler(500)
    def server_error(exc):
        # Details go to the server log only; the client gets a request ID to report.
        original=getattr(exc,'original_exception',None) or exc
        app.logger.error('Unhandled error on %s %s (request %s): %r',request.method,request.path,
                         getattr(g,'correlation_id','none'),original)
        return jsonify({'error':'Internal server error. Report request ID '+str(getattr(g,'correlation_id','none'))+'.'}),500

    return app
