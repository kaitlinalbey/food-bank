"""Deliberately plain HTML bank node; communicates only with the central REST API."""
import argparse
import json
import os
import secrets
from time import perf_counter
from urllib.error import HTTPError,URLError
from urllib.request import Request,urlopen

from functools import wraps
from flask import Flask,Response,abort,flash,redirect,render_template,request,session,url_for
from pathlib import Pathlib import Path
from werkzeug.exceptions import HTTPException
from inventory_filters import parse_filters,filter_inventory,categories_for
from offline_queue import (save_snapshot,get_snapshot,enqueue,all_queue,sync_pending,discard,
    enqueue_receipt,all_receipts,sync_receipts,discard_receipt,log_local_event,local_events)

BANK_NAMES={1:'Central Food Pantry',2:'Northside Food Pantry',3:'Westside Food Pantry'}


class APIError(Exception):
    def __init__(self,status,message):
        super().__init__(message)
        self.status=status


def api_call(base,method,path,payload=None,token=None,correlation_id=None):
    headers={'Accept':'application/json'}
    if token:
        headers['Authorization']='Bearer '+token
    if correlation_id:headers['X-Request-ID']=correlation_id
    body=None
    if isinstance(payload,bytes):
        headers['Content-Type']='text/csv; charset=utf-8'
        body=payload
    elif payload is not None:
        headers['Content-Type']='application/json'
        body=json.dumps(payload).encode('utf-8')
    req=Request(base+path,data=body,method=method,headers=headers)
    try:
        with urlopen(req,timeout=4) as response:
            return json.loads(response.read())
    except HTTPError as exc:
        try:
            data=json.loads(exc.read())
            message=data.get('error','Request failed.')
        except (ValueError,UnicodeError):
            message='Request failed.'
        raise APIError(exc.code,message) from exc
    except (URLError,TimeoutError,OSError) as exc:
        raise APIError(503,'Central API is offline or not responding.') from exc


LOOPBACK_ADDRESSES=('127.0.0.1','::1','::ffff:127.0.0.1')


def _demo_mode_enabled():
    return os.environ.get('FOOD_BANK_DEMO_MODE','').strip().lower() in ('1','true','yes','on')

def _enrich_own_inventory(rows):
    """Normalize central inventory rows with numeric shortage/surplus fields for UI templates."""
    enriched = []
    for row in rows:
        item = dict(row)
        qty = item.get('quantity', 0)
        min_lvl = item.get('min_level', 0)
        max_lvl = item.get('max_level', 0)
        item['shortage'] = max(0, min_lvl - qty)
        item['surplus'] = max(0, qty - max_lvl)
        item.setdefault('expiration_date', None)
        enriched.append(item)
    return enriched

def create_node_app(bank_id,api_url='http://127.0.0.1:5000',queue_path=None,outage_simulation=None):
    if not 1<=bank_id<=100:
        raise ValueError('Bank ID must be between 1 and 100.')
    app=Flask(__name__)
    app.secret_key=os.environ.get('FOOD_BANK_SESSION_SECRET') or secrets.token_hex(32)
    app.config.update(BANK_ID=bank_id,API_URL=api_url.rstrip('/'),
                      SIMULATED_API_OFFLINE=False,
                      # The connection-outage simulation is a classroom demo tool. It is off
                      # unless demo mode is switched on explicitly (FOOD_BANK_DEMO_MODE=1).
                      OUTAGE_SIMULATION_ENABLED=_demo_mode_enabled() if outage_simulation is None else bool(outage_simulation),
                      # Set FOOD_BANK_SECURE_COOKIES=1 when the nodes are served over HTTPS.
                      SESSION_COOKIE_SECURE=os.environ.get('FOOD_BANK_SECURE_COOKIES','')=='1',
                      OFFLINE_DB=str(queue_path or (Path(os.environ.get('FOOD_BANK_QUEUE_DIR') or Path(__file__).resolve().parent/'data')/f'node_{bank_id}_queue.db')),
                      SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SAMESITE='Lax',
                      # Browsers do not separate cookies by port, so every node on 127.0.0.1
                      # needs its own cookie name or logging into one node logs out the others.
                      SESSION_COOKIE_NAME=f'foodbank_node_{bank_id}')
    bank_name=BANK_NAMES.get(bank_id,f'Food Bank #{bank_id}')

    def call(method,path,payload=None):
        started=perf_counter();correlation_id=secrets.token_hex(12)
        status='success'
        try:
            if app.config['SIMULATED_API_OFFLINE']:
                raise APIError(503,'Simulated connection failure (switch connection back on to synchronize).')
            return api_call(app.config['API_URL'],method,path,payload,session.get('token'),correlation_id)
        except APIError as exc:
            status='HTTP '+str(exc.status)
            raise
        finally:
            try:
                log_local_event(app.config['OFFLINE_DB'],method,path,status,correlation_id,
                                (perf_counter()-started)*1000)
            except Exception:
                pass  # Local audit must not hide original API response.


    def outage_permission():
        """(allowed, http_status, reason) for the outage-simulation control."""
        if not app.config['OUTAGE_SIMULATION_ENABLED']:
            return False,404,'Outage simulation is disabled. It is only available in classroom demo mode.'
        if request.remote_addr not in LOOPBACK_ADDRESSES:
            return False,403,'Outage simulation can only be used from this computer.'
        if not session.get('token'):
            return False,401,'Log in as an administrator to simulate an outage.'
        if session.get('role')!='admin':
            return False,403,'Only an administrator can simulate an outage.'
        return True,200,''

    def log_outage_attempt(result):
        who=session.get('username') or 'anonymous'
        try:
            log_local_event(app.config['OFFLINE_DB'],'POST','/offline/simulate',
                            f'{result} (user {who}, role {session.get("role") or "none"}, from {request.remote_addr})',
                            secrets.token_hex(12),0)
        except Exception:
            pass  # A logging failure must not change the outcome.
        app.logger.warning('Outage simulation %s by %s from %s',result,who,request.remote_addr)

    @app.context_processor
    def html_helpers():
        if '_csrf' not in session:
            session['_csrf']=secrets.token_hex(24)
        role=session.get('role')
        return {'csrf_token':session['_csrf'],'bank_name':bank_name,
                'new_request_key':lambda:secrets.token_urlsafe(18),
                'outage_controls':outage_permission()[0],
                # Used by base.html for the sidebar: which page is active, and which links to show.
                'active_page':request.endpoint,'is_admin':role=='admin',
                'link_offline':app.config['SIMULATED_API_OFFLINE']}

    @app.before_request
    def bind_session_to_bank():
        # A session is only valid on the node (bank) that created it. This stops a
        # copied cookie, or a shared FOOD_BANK_SESSION_SECRET, from showing or changing
        # another bank's data through this node. Registered before the CSRF check.
        if session.get('token') and session.get('bank_id')!=bank_id:
            session.clear()

    @app.before_request
    def protect_forms():
        if request.method=='POST':
            key=session.get('_csrf')
            if not key or not secrets.compare_digest(request.form.get('_csrf',''),key):
                abort(400,'Invalid form token. Reload the page and try again.')


    @app.get('/node-health')
    def node_health():
        if app.config['SIMULATED_API_OFFLINE']:
            return {'status':'simulated_offline','bank_id':bank_id},503
        return {'status':'ok','bank_id':bank_id}

    @app.get('/login')
    def login_page():
        return render_template('login.html')

    @app.post('/login')
    def login_submit():
        try:
            result=call('POST','/api/login',{'bank_id':bank_id,
                'username':request.form.get('username','staff'),
                'password':request.form.get('password','')})
            if result['bank_id']!=bank_id:
                # Revoke the minted token before rejecting the wrong-bank login.
                try:
                    api_call(app.config['API_URL'], 'POST', '/api/logout', token=result['token'])
                except APIError:
                    pass
                raise APIError(403,'This account belongs to a different bank.')
            session.clear()
            session.update({'token':result['token'],'username':result['username'],'role':result['role'],
                            'bank_id':bank_id})
            return back()
        except APIError as exc:
            flash(str(exc))
            return redirect(url_for('login_page'))

    @app.post('/logout')
    def logout():
        if session.get('token'):
            try:
                call('POST','/api/logout')
            except APIError:
                pass
        session.clear()
        return redirect(url_for('login_page'))

    # ------------------------------------------------------------------ multi-page interface
    # Every page needs a login. If the central API cannot be reached, a page shows the
    # offline screen (HTTP 503) with the saved snapshot; a 401 sends the user to log in again.
    def offline_response(message):
        return render_template('offline.html',message=message,
                               snapshot=get_snapshot(app.config['OFFLINE_DB']),
                               queue=all_queue(app.config['OFFLINE_DB']),
                               receipt_queue=all_receipts(app.config['OFFLINE_DB']),
                               simulated=app.config['SIMULATED_API_OFFLINE']),503

    def page(fn):
        @wraps(fn)
        def wrapper(*args,**kwargs):
            if not session.get('token'):
                return redirect(url_for('login_page'))
            try:
                return fn(*args,**kwargs)
            except APIError as exc:
                if exc.status==401:
                    session.clear()
                    flash('Session expired. Log in again.')
                    return redirect(url_for('login_page'))
                return offline_response(str(exc))
        return wrapper

    def dashboard_data():
        """Assemble main bank data from the central REST endpoints and refresh offline snapshot."""
        inv_resp = call('GET', f'/api/banks/{bank_id}/inventory')
        inventory = _enrich_own_inventory(inv_resp.get('inventory', []))
        exchanges = call('GET', '/api/exchanges').get('exchanges', [])
        notif_resp = optional('/api/notifications', {'notifications': [], 'unread_count': 0})
        matches_resp = optional('/api/matches', {'matches': []})
        banks = optional('/api/banks', {'banks': []}).get('banks', [])

        bank_info = next((b for b in banks if b['id'] == bank_id), {'id': bank_id, 'name': bank_name})
        incoming = [e for e in exchanges if e['supplier_bank_id'] == bank_id]
        outgoing = [e for e in exchanges if e['requester_bank_id'] == bank_id]

        data = {
            'bank': bank_info,
            'inventory': inventory,
            'incoming': incoming,
            'outgoing': outgoing,
            'notifications': notif_resp.get('notifications', []),
            'unread_notifications': notif_resp.get('unread_count', 0),
            'matches': matches_resp.get('matches', []),
            'banks': banks,
        }
        save_snapshot(app.config['OFFLINE_DB'], data)
        return datadef dashboard_data():
        """Assemble main bank data from the central REST endpoints and refresh offline snapshot."""
        inv_resp = call('GET', f'/api/banks/{bank_id}/inventory')
        inventory = _enrich_own_inventory(inv_resp.get('inventory', []))
        exchanges = call('GET', '/api/exchanges').get('exchanges', [])
        notif_resp = optional('/api/notifications', {'notifications': [], 'unread_count': 0})
        matches_resp = optional('/api/matches', {'matches': []})
        banks = optional('/api/banks', {'banks': []}).get('banks', [])

        bank_info = next((b for b in banks if b['id'] == bank_id), {'id': bank_id, 'name': bank_name})
        incoming = [e for e in exchanges if e['supplier_bank_id'] == bank_id]
        outgoing = [e for e in exchanges if e['requester_bank_id'] == bank_id]

        data = {
            'bank': bank_info,
            'inventory': inventory,
            'incoming': incoming,
            'outgoing': outgoing,
            'notifications': notif_resp.get('notifications', []),
            'unread_notifications': notif_resp.get('unread_count', 0),
            'matches': matches_resp.get('matches', []),
            'banks': banks,
        }
        save_snapshot(app.config['OFFLINE_DB'], data)
        return data

    def optional(route,default):
        """Secondary data: a failure shows an empty section instead of the offline screen."""
        try:
            return call('GET',route)
        except APIError as exc:
            if exc.status==401:
                raise
            return default

    def current_user():
        return {'role':session.get('role','staff'),'username':session.get('username','')}

    INVENTORY_SORTS={'name':(lambda r:(r['item_name'].lower(),),False),
                     'category':(lambda r:(r['category'].lower(),r['item_name'].lower()),False),
                     'quantity':(lambda r:(r['quantity'],r['item_name'].lower()),False),
                     'shortage':(lambda r:(r['shortage'],r['item_name'].lower()),True),
                     'surplus':(lambda r:(r['surplus'],r['item_name'].lower()),True),
                     'expiration':(lambda r:(r['expiration_date'] is None,r['expiration_date'] or '',r['item_name'].lower()),False)}
    OTHER_SORTS={'name':(lambda r:(r['item_name'].lower(),),False),
                 'category':(lambda r:(r['category'].lower(),r['item_name'].lower()),False),
                 'available':(lambda r:(r['available'],r['item_name'].lower()),True)}

    def sort_rows(rows,choice,table):
        key=choice if choice in table else 'name'
        keyfn,reverse=table[key]
        return sorted(rows,key=keyfn,reverse=reverse),key

    def own_params(source):
        """Validated inventory filters (from query string or form) for building URLs."""
        params=parse_filters(source.get('q'),source.get('category'),source.get('status'))
        if source.get('sort') in INVENTORY_SORTS:params['sort']=source.get('sort')
        return {k:v for k,v in params.items() if v not in ('','all','name')}

    @app.get('/')
    @page
    def home():
        args=request.args
        # Bookmarks from the old single-page dashboard keep working: filters and bank
        # choices are validated and sent to the page that now owns them.
        other_requested=args.get('other_bank') or args.get('other_q') or args.get('other_category')
        own_requested=args.get('inventory')=='edit' or any(args.get(k) for k in ('q','category')) or args.get('status') not in (None,'','all')
        if other_requested and not own_requested:
            other=parse_filters(args.get('other_q'),args.get('other_category'),args.get('other_status'),shared=True)
            params={'other_q':other['q'],'other_category':other['category'],'other_status':other['status']}
            if args.get('other_bank',type=int):params['other_bank']=args.get('other_bank',type=int)
            return redirect(url_for('other_banks_page',**{k:v for k,v in params.items() if v not in ('','all')}))
        if own_requested:
            params=own_params(args)
            if args.get('inventory')=='edit':params['inventory']='edit'
            return redirect(url_for('my_inventory_page',**params))
        data=dashboard_data()
        nodes=optional('/api/nodes',{}).get('nodes',[])
        incoming=data.get('incoming',[]);outgoing=data.get('outgoing',[])
        summary={'awaiting_our_approval':sum(e['status']=='pending' for e in incoming),
                 'ready_to_ship':sum(e['status']=='approved' for e in incoming),
                 'waiting_for_others':sum(e['status'] in ('pending','approved') for e in outgoing),
                 'to_receive':sum(e['status']=='in_transit' for e in outgoing),
                 'offline_pending':sum(x['status'] in ('pending','conflict') for x in all_queue(app.config['OFFLINE_DB'])),
                 'nodes_online':sum(1 for n in nodes if n.get('online')),'nodes_total':len(nodes)}
        return render_template('dashboard.html',summary=summary,nodes=nodes,
                               simulated=app.config['SIMULATED_API_OFFLINE'],**dict(data,user=current_user()))

    @app.get('/my-inventory')
    @page
    def my_inventory_page():
        data=dashboard_data()
        own_filters=parse_filters(request.args.get('q'),request.args.get('category'),request.args.get('status'))
        visible,sort=sort_rows(filter_inventory(data['inventory'],own_filters),request.args.get('sort'),INVENTORY_SORTS)
        return render_template('inventory.html',inventory=data['inventory'],visible_inventory=visible,
                               own_filters=own_filters,own_categories=categories_for(data['inventory']),sort=sort,
                               inventory_edit=request.args.get('inventory')=='edit',bank=data['bank'],user=current_user())


    @app.get('/matching')
    @page
    def matching_page():
        data=dashboard_data()
        shortages=[r for r in data['inventory'] if r['shortage']>0]
        return render_template('matching.html',matches=data['matches'],shortages=shortages,
                               bank=data['bank'],user=current_user())

    TRANSFER_TABS=('all','incoming','outgoing','pending','approved','in_transit','completed')

    @app.get('/transfers')
    @page
    def transfers_page():
        data=dashboard_data()
        tab=request.args.get('tab') if request.args.get('tab') in TRANSFER_TABS else 'all'
        rows=[dict(e,direction='incoming') for e in data['incoming']]+[dict(e,direction='outgoing') for e in data['outgoing']]
        rows.sort(key=lambda e:e['id'],reverse=True)
        def match(e,t):
            if t=='all':return True
            if t in ('incoming','outgoing'):return e['direction']==t
            if t=='completed':return e['status'] in ('received','declined','canceled')
            return e['status']==t
        counts={t:sum(1 for e in rows if match(e,t)) for t in TRANSFER_TABS}
        return render_template('transfers.html',transfers=[e for e in rows if match(e,tab)],tab=tab,counts=counts,
                               tabs=TRANSFER_TABS,user=current_user())


    @app.get('/offline')
    def offline_page():
        # This page must keep working while the central API is unreachable.
        if not session.get('token'):
            return redirect(url_for('login_page'))
        online,message,items=True,None,[]
        try:
            items=dashboard_data()['inventory']
        except APIError as exc:
            if exc.status==401:
                session.clear();flash('Session expired. Log in again.');return redirect(url_for('login_page'))
            online,message=False,str(exc)
            items=(get_snapshot(app.config['OFFLINE_DB']) or {}).get('inventory',[])
        html=render_template('offline_sync.html',online=online,message=message,items=items,
                             queue=all_queue(app.config['OFFLINE_DB']),receipts=all_receipts(app.config['OFFLINE_DB']),
                             snapshot=get_snapshot(app.config['OFFLINE_DB']),simulated=app.config['SIMULATED_API_OFFLINE'],
                             user=current_user())
        return (html,200) if online else (html,503)

    
    def back(page_name=None):
        endpoint=request.endpoint
        action=(request.view_args or {}).get('action')
        if endpoint=='change_exchange':
            target=('transfers_page',{'tab':'incoming' if action in ('approve','decline','ship') else 'outgoing'},None)
        elif endpoint=='advanced_action' and action in ADVANCED_ACTION_PAGES:
            name,anchor=ADVANCED_ACTION_PAGES[action]
            target=(name,{},anchor)
        else:
            target=RETURN_PAGES.get(endpoint,(page_name or 'home',{},None))
        name,params,anchor=target
        return redirect(url_for(name,_anchor=anchor,**params) if anchor else url_for(name,**params))

    def perform(method,path,payload=None,*,return_inventory_edit=False,admin_anchor=None):
        if not session.get('token'):
            return redirect(url_for('login_page'))
        try:
            result=call(method,path,payload)
            flash(result.get('message','Saved.'))
        except APIError as exc:
            if exc.status==401:
                session.clear()
                flash('Session expired.')
                return redirect(url_for('login_page'))
            flash('Error: '+str(exc))
        if admin_anchor:
            return redirect(url_for('admin_page',_anchor=admin_anchor))
        if return_inventory_edit:
            params=own_params(request.form)
            return redirect(url_for('my_inventory_page',inventory='edit',**params))
        return back()

    @app.post('/inventory')
    def save_inventory():
        try:
            item_id=int(request.form['item_id'])
            data={}
            for field in ('quantity', 'min_level', 'max_level'):
            raw = request.form.get(field, '').strip()
                if raw != '':
                    data[field] = int(raw)
            # Support legacy 'target' field name if template still submits it.
            if 'target' in request.form and request.form.get('target', '').strip() != '' and 'max_level' not in data:
                data['max_level'] = int(request.form['target'])    
        except (KeyError,ValueError):
            flash('Item ID is invalid.')
            return redirect(url_for('my_inventory_page',inventory='edit'))
        return perform('PUT',f'/api/inventory/{item_id}',data,return_inventory_edit=True)

    @app.post('/inventory/add')
    def add_inventory():
        try:
            item_id = int(request.form['item_id'])
            quantity = int(request.form.get('quantity', '0'))
            min_level = int(request.form.get('min_level', '0'))
            max_raw = request.form.get('max_level') or request.form.get('target') or '0'
            max_level = int(max_raw)
        except (KeyError, ValueError):
            flash('Error: Item ID, quantity, min level, and max level must be whole numbers.')
            return redirect(url_for('my_inventory_page', inventory='edit'))
        payload = {
            'item_id': item_id,
            'quantity': quantity,
            'min_level': min_level,
            'max_level': max_level,
        }
        return perform('POST','/api/inventory',data,return_inventory_edit=True)

    @app.post('/inventory/<int:item_id>/delete')
    def remove_inventory(item_id):
        if not session.get('token'):
            return redirect(url_for('login_page'))
        flash('Error: Removing items is not supported by the central API; set quantity to 0 instead.')
        params = own_params(request.form)
        return redirect(url_for('my_inventory_page', inventory='edit', **params))

    @app.post('/request')
    def request_transfer():
        try:
            supplier_raw = request.form.get('supplier_bank_id') or request.form['source_bank_id']
            req_id = (request.form.get('request_id') or request.form.get('request_key') or '').strip()
            data = {
                'supplier_bank_id': int(supplier_raw),
                'item_id': int(request.form['item_id']),
                'quantity': int(request.form['quantity']),
                'request_id': req_id,
            }
        except (KeyError, ValueError):
            flash('Error: Supplier bank, item, and quantity must be valid whole numbers.')
            return back()
        return perform('POST','/api/exchanges',data, success_message='Transfer request submitted.'))

    @app.post('/exchange/<int:exchange_id>/<action>')
    def change_exchange(exchange_id,action):
        if action not in ('approve','decline','ship','receive','cancel'):
            abort(404)
        return perform('POST',f'/api/exchanges/{exchange_id}/{action}',, success_message=f'Exchange {action}ed.'))

    @app.post('/notifications/read')
    def read_notifications():
        if not session.get('token'):
            return redirect(url_for('login_page'))
        try:
            result = call('POST', '/api/notifications/read-all')
            flash(f"Marked {result.get('marked', 0)} notifications as read.")
        except APIError as exc:
            if exc.status == 401:
                session.clear()
                flash('Session expired.')
                return redirect(url_for('login_page'))
            flash('Error: ' + str(exc))
        return back()
   
    @app.post('/offline/simulate')
    def simulate_offline():
        # Classroom demo control only: admin, demo mode, and a request from this computer.
        # The state is changed only after every check has passed.
        allowed,status,reason=outage_permission()
        if not allowed:
            log_outage_attempt('DENIED: '+reason)
            abort(status,reason)
        app.config['SIMULATED_API_OFFLINE']=not app.config['SIMULATED_API_OFFLINE']
        state='OFFLINE' if app.config['SIMULATED_API_OFFLINE'] else 'ONLINE'
        log_outage_attempt('simulated connection set '+state)
        flash('Simulated API connection is now '+state+'.')
        return back()

    @app.post('/offline/queue')
    def queue_offline():
        if not session.get('token'):return redirect(url_for('login_page'))
        try:
            enqueue(app.config['OFFLINE_DB'],request.form.get('item_id'),request.form.get('delta'))
            flash('Adjustment saved locally. Sync it when the central API is online.')
        except ValueError as exc:flash('Could not queue: '+str(exc))
        return back()

    @app.post('/offline/sync')
    def synchronize_offline():
        if not session.get('token'):return redirect(url_for('login_page'))
        result=sync_pending(app.config['OFFLINE_DB'],call)
        flash(f'Synchronized {result["synced"]} changes; {result["conflicts"]} conflicts.'+
              ((' Error: '+result['error']) if result['error'] else ''))
        return back()

    @app.post('/offline/discard/<int:row_id>')
    def discard_offline(row_id):
        if not session.get('token'):return redirect(url_for('login_page'))
        deleted=discard(app.config['OFFLINE_DB'],row_id)
        flash('Queued adjustment discarded.' if deleted else 'Pending change not found.')
        return back()

    @app.after_request
    def security_headers(response):
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['X-Frame-Options']='DENY'
        response.headers['Cache-Control']='no-store'
        return response

    return app


if __name__=='__main__':
    parser=argparse.ArgumentParser(description='Individual food bank UI node')
    parser.add_argument('--bank',type=int,required=True)
    parser.add_argument('--port',type=int,default=None)
    parser.add_argument('--api',default='http://127.0.0.1:5000')
    parser.add_argument('--host',default='127.0.0.1')
    options=parser.parse_args()
    create_node_app(options.bank,options.api).run(
        host=options.host,port=options.port or 5000+options.bank,debug=False,threaded=True)
