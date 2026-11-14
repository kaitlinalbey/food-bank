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
from pathlib import Path
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
        """Main bank data from the API. Every successful load refreshes the offline snapshot."""
        data=call('GET','/api/dashboard')
        save_snapshot(app.config['OFFLINE_DB'],data)
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

    @app.get('/other-banks')
    @page
    def other_banks_page():
        # The bank chooser never combines stock from several banks. The API removes
        # other banks' private totals before this node receives any data.
        other_banks=[b for b in call('GET','/api/banks')['banks'] if b['id']!=bank_id]
        requested=request.args.get('other_bank',type=int)
        selected=next((b for b in other_banks if b['id']==requested),other_banks[0] if other_banks else None)
        other_filters=parse_filters(request.args.get('other_q'),request.args.get('other_category'),
                                    request.args.get('other_status'),shared=True)
        rows,other_error=[],None
        if selected:
            try:
                rows=call('GET',f'/api/banks/{selected["id"]}/inventory')['inventory']
            except APIError as exc:
                if exc.status==401:raise
                other_error='This food bank\'s inventory is temporarily unavailable: '+str(exc)
        visible,sort=sort_rows(filter_inventory(rows,other_filters,shared=True),request.args.get('other_sort'),OTHER_SORTS)
        return render_template('other_banks.html',other_banks=other_banks,selected_bank=selected,
                               other_inventory=visible,other_total=len(rows),other_filters=other_filters,
                               other_categories=categories_for(rows),other_error=other_error,sort=sort,user=current_user())

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

    @app.get('/donations')
    @page
    def donations_page():
        data=dashboard_data()
        return render_template('donations.html',inventory=data['inventory'],
                               donations=optional('/api/donations',{}).get('donations',data.get('recent_donations',[])),
                               waste=optional('/api/waste',{}).get('waste',data.get('recent_waste',[])),
                               donors=optional('/api/donors',{}).get('donors',[]),user=current_user())

    LOT_VIEWS=('all','expiring','expired','recalled')

    @app.get('/batches')
    @page
    def batches_page():
        from datetime import date
        data=dashboard_data()
        today=date.today()
        lots=[]
        for lot in optional('/api/lots',{}).get('lots',[]):
            days=None
            if lot.get('expiration_date'):
                try:days=(date.fromisoformat(lot['expiration_date'])-today).days
                except ValueError:days=None
            state='recalled' if lot.get('recalled') else 'expired' if days is not None and days<0 else \
                  'expiring' if days is not None and days<=7 else 'ok'
            lots.append(dict(lot,days_left=days,state=state))
        view=request.args.get('lots') if request.args.get('lots') in LOT_VIEWS else 'all'
        shown=[l for l in lots if view=='all' or l['state']==view]
        counts={v:(len(lots) if v=='all' else sum(l['state']==v for l in lots)) for v in LOT_VIEWS}
        return render_template('batches.html',lots=shown,lot_view=view,lot_counts=counts,lot_views=LOT_VIEWS,
                               alerts=data.get('alerts',[]),recalls=optional('/api/recalls',{}).get('recalls',[]),
                               storage=optional('/api/storage',{}).get('storage',[]),user=current_user())

    @app.get('/transport')
    @page
    def transport_page():
        data=dashboard_data()
        return render_template('transport.html',
                               to_schedule=[e for e in data['incoming'] if e['status']=='approved'],
                               scheduled=[e for e in data['incoming']+data['outgoing'] if e.get('pickup_at') or e.get('delivery_at')],
                               arriving=[e for e in data['outgoing'] if e['status']=='in_transit'],
                               fleet=optional('/api/transport',{}),
                               discrepancies=optional('/api/discrepancies',{}).get('discrepancies',[]),user=current_user())

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

    @app.get('/more')
    @page
    def more_page():
        data=dashboard_data()
        is_admin=session.get('role')=='admin'
        return render_template('more.html',inventory=data['inventory'],forecast=data.get('forecast',[]),
                               reconciliations=optional('/api/reconciliations',{}).get('reconciliations',[]),
                               analytics=optional('/api/analytics',{}).get('metrics',{}),
                               lab_scenarios=optional('/api/admin/lab/scenarios',{}).get('scenarios',{}) if is_admin else {},
                               last_lab=session.pop('last_lab',None),user=current_user())

    @app.get('/advanced')
    def advanced_page():
        # The former "Additional features" page was split into dedicated pages. Old links
        # (and their #section bookmarks) land here and are forwarded by a fixed map.
        if not session.get('token'):
            return redirect(url_for('login_page'))
        return render_template('moved.html',user=current_user())

    # After a form is submitted, return to the page (and section) the form belongs to.
    # Targets come only from these fixed tables, never from user input.
    RETURN_PAGES={'request_transfer':('transfers_page',{'tab':'outgoing'},None),
                  'read_notifications':('home',{},'notifications'),
                  'record_donation':('donations_page',{},'records'),'record_waste':('donations_page',{},'waste'),
                  'set_lot_expiry':('batches_page',{},'batches'),'set_schedule':('transport_page',{},'schedules'),
                  'update_barcode':('more_page',{},'barcodes'),'lookup_barcode':('more_page',{},'barcodes'),
                  'add_bank':('admin_page',{},'banks'),'add_user':('admin_page',{},'users'),
                  'csv_import':('more_page',{},'csv'),'csv_export':('more_page',{},'csv'),
                  'queue_offline':('offline_page',{},'queue'),'synchronize_offline':('offline_page',{},'queue'),
                  'discard_offline':('offline_page',{},'queue'),'simulate_offline':('offline_page',{},None),
                  'advanced_close_recall':('batches_page',{},'recalls'),'advanced_count_action':('more_page',{},'counts'),
                  'advanced_discrepancy':('transport_page',{},'discrepancies'),
                  'advanced_queue_receipt':('offline_page',{},'receipts'),'advanced_sync_receipts':('offline_page',{},'receipts'),
                  'advanced_discard_receipt':('offline_page',{},'receipts'),'advanced_run_lab':('more_page',{},'lab')}
    ADVANCED_ACTION_PAGES={'donor':('donations_page','donors'),'recall':('batches_page','recalls'),
                           'storage':('batches_page','storage'),'item_storage':('batches_page','storage'),
                           'count':('more_page','counts'),'driver':('transport_page','fleet'),
                           'vehicle':('transport_page','fleet'),'transport':('transport_page','fleet'),
                           'receipt':('transport_page','discrepancies')}

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
            data={k:request.form.get(k,'') for k in ('quantity','target','expiration_date')}
        except (KeyError,ValueError):
            flash('Item ID is invalid.')
            return redirect(url_for('my_inventory_page',inventory='edit'))
        return perform('PUT',f'/api/inventory/{item_id}',data,return_inventory_edit=True)

    @app.post('/inventory/add')
    def add_inventory():
        data={k:request.form.get(k,'') for k in
            ('name','unit','category','quantity','target','expiration_date')}
        return perform('POST','/api/inventory',data,return_inventory_edit=True)

    @app.post('/inventory/<int:item_id>/delete')
    def remove_inventory(item_id):
        return perform('DELETE',f'/api/inventory/{item_id}',return_inventory_edit=True)

    @app.post('/request')
    def request_transfer():
        data={k:request.form.get(k,'') for k in
            ('source_bank_id','item_id','quantity','request_key')}
        return perform('POST','/api/exchanges',data)

    @app.post('/exchange/<int:exchange_id>/<action>')
    def change_exchange(exchange_id,action):
        if action not in ('approve','decline','ship','receive','cancel'):
            abort(404)
        return perform('POST',f'/api/exchanges/{exchange_id}/{action}')

    @app.post('/notifications/read')
    def read_notifications():
        return perform('POST','/api/notifications/read')

    @app.post('/admin/bank')
    def add_bank():
        payload={k:request.form.get(k,'') for k in
            ('name','area','latitude','longitude','staff_password')}
        return perform('POST','/api/admin/banks',payload,
                       admin_anchor='banks' if request.form.get('return_to')=='admin' else None)

    @app.post('/admin/user')
    def add_user():
        payload={k:request.form.get(k,'') for k in
            ('bank_id','username','password','role')}
        return perform('POST','/api/admin/users',payload,
                       admin_anchor='users' if request.form.get('return_to')=='admin' else None)

    @app.post('/donation')
    def record_donation():
        fields=('item_id','quantity','donor','donor_id','lot_code','expiration_date')
        return perform('POST','/api/donations',{k:request.form.get(k,'') for k in fields})

    @app.post('/waste')
    def record_waste():
        fields=('item_id','quantity','reason','lot_id')
        return perform('POST','/api/waste',{k:request.form.get(k,'') for k in fields})

    @app.post('/lot/<int:lot_id>/expiry')
    def set_lot_expiry(lot_id):
        # The dashboard form posts to /lot/0/expiry and sends the batch ID as lot_id_entry;
        # a small script rewrites the URL, but this keeps the form working without JavaScript.
        if lot_id==0:
            entry=request.form.get('lot_id_entry','').strip()
            if not entry.isdigit() or int(entry)<1:
                flash('Error: Enter a valid batch ID.')
                return back()
            lot_id=int(entry)
        return perform('PATCH',f'/api/lots/{lot_id}',{'expiration_date':request.form.get('expiration_date','')})

    @app.post('/exchange/<int:exchange_id>/schedule')
    def set_schedule(exchange_id):
        # The basic HTML form asks for UTC explicitly.
        payload={}
        for key in ('pickup_at','delivery_at'):
            value=request.form.get(key,'')
            payload[key]=value+'+00:00' if value else ''
        payload['carrier_notes']=request.form.get('carrier_notes','')
        return perform('PUT',f'/api/exchanges/{exchange_id}/schedule',payload)

    @app.post('/barcode')
    def update_barcode():
        try:item_id=int(request.form.get('item_id',''))
        except ValueError:
            flash('Invalid item ID.');return back()
        return perform('PUT',f'/api/admin/items/{item_id}/barcode',{'barcode':request.form.get('barcode','')})

    @app.get('/barcode/lookup')
    def lookup_barcode():
        if not session.get('token'):return redirect(url_for('login_page'))
        import urllib.parse
        barcode=request.args.get('barcode','').strip()
        try:
            result=call('GET','/api/barcodes/'+urllib.parse.quote(barcode,safe=''))
            item=result['item']
            flash(f'Barcode {barcode}: {item["name"]} ({item["unit"]}).')
        except APIError as exc:flash('Barcode lookup: '+str(exc))
        return back()

    @app.get('/csv/export')
    def csv_export():
        if not session.get('token'):return redirect(url_for('login_page'))
        if app.config['SIMULATED_API_OFFLINE']:
            flash('Export is unavailable offline.');return back()
        req=Request(app.config['API_URL']+'/api/inventory/export.csv',
                    headers={'Authorization':'Bearer '+session['token']})
        try:
            with urlopen(req,timeout=5) as response:
                content=response.read()
            return Response(content,mimetype='text/csv',headers={
                'Content-Disposition':'attachment; filename="food_bank_inventory.csv"',
                'Cache-Control':'no-store'})
        except (HTTPError,URLError,TimeoutError) as exc:
            flash('Could not export CSV: '+str(exc));return back()

    @app.post('/csv/import')
    def csv_import():
        if not session.get('token'):return redirect(url_for('login_page'))
        file=request.files.get('inventory_csv')
        if not file:
            flash('Choose a CSV file first.');return back()
        data=file.stream.read(131073)
        if len(data)>131072:
            flash('CSV is too large.');return back()
        return perform('POST','/api/inventory/import.csv',data)

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

    def advanced_perform(method,route,payload):
        if not session.get('token'):return redirect(url_for('login_page'))
        try:
            result=call(method,route,payload)
            flash(result.get('message','Saved.'))
        except APIError as exc:
            flash('Error: '+str(exc))
        return back('advanced_page')

    @app.post('/advanced/action/<action>')
    def advanced_action(action):
        # Fixed allowlist prevents arbitrary backend requests from the browser.
        actions={
          'donor':('POST','/api/donors',('name','notes')),
          'recall':('POST','/api/admin/recalls',('item_id','lot_code','reason')),
          'storage':('PUT','/api/storage/'+request.form.get('storage_type',''),('capacity_units',)),
          'item_storage':('PUT','/api/admin/items/'+request.form.get('item_id','')+'/storage',('storage_type',)),
          'count':('POST','/api/reconciliations',('item_id','counted_qty','reason')),
          'driver':('POST','/api/drivers',('display_name',)),
          'vehicle':('POST','/api/vehicles',('label','capacity_units','refrigerated')),
          'transport':('PUT','/api/exchanges/'+request.form.get('exchange_id','')+'/transport',('driver_id','vehicle_id')),
          'receipt':('POST','/api/exchanges/'+request.form.get('exchange_id','')+'/receive-detailed',('received_qty','damaged_qty','notes')),
        }
        if action not in actions:abort(404)
        if action=='storage' and request.form.get('storage_type','') not in ('ambient','refrigerated','frozen'):
            flash('Error: Choose ambient, refrigerated or frozen storage.');return back('advanced_page')
        method,route,fields=actions[action]
        if action in ('item_storage','transport','receipt'):
            import re
            if not re.fullmatch(r'[1-9][0-9]{0,8}',request.form.get('item_id' if action=='item_storage' else 'exchange_id','')):
                flash('Invalid ID.');return back('advanced_page')
        return advanced_perform(method,route,{k:request.form.get(k,'') for k in fields})

    @app.post('/advanced/recall/<int:recall_id>/close')
    def advanced_close_recall(recall_id):
        return advanced_perform('POST',f'/api/admin/recalls/{recall_id}/close',{})

    @app.post('/advanced/count/<int:recon_id>/<action>')
    def advanced_count_action(recon_id,action):
        if action not in ('apply','reject'):abort(404)
        return advanced_perform('POST',f'/api/reconciliations/{recon_id}/{action}',{})

    @app.post('/advanced/discrepancy/<int:issue_id>/review')
    def advanced_discrepancy(issue_id):
        return advanced_perform('POST',f'/api/discrepancies/{issue_id}/review',{})

    @app.post('/advanced/receipt/queue')
    def advanced_queue_receipt():
        if not session.get('token'):return redirect(url_for('login_page'))
        try:
            enqueue_receipt(app.config['OFFLINE_DB'],request.form.get('item_id'),
                request.form.get('quantity'),request.form.get('donor'),request.form.get('expiration_date'))
            flash('Independent receipt queued safely on this node.')
        except ValueError as exc:flash('Queue error: '+str(exc))
        return back('advanced_page')

    @app.post('/advanced/receipt/sync')
    def advanced_sync_receipts():
        if not session.get('token'):return redirect(url_for('login_page'))
        result=sync_receipts(app.config['OFFLINE_DB'],call)
        flash('Receipt sync: {} synchronized; {} conflicts. {}'.format(
              result['synced'],result['conflicts'],result['error'] or ''))
        return back('advanced_page')

    @app.post('/advanced/receipt/<int:receipt_id>/discard')
    def advanced_discard_receipt(receipt_id):
        if not session.get('token'):return redirect(url_for('login_page'))
        discard_receipt(app.config['OFFLINE_DB'],receipt_id)
        flash('Local receipt discarded.');return back('advanced_page')

    def status_context(trace=None,trace_error=None):
        """Data for the System status page. Health checks are shown to everyone; the
        system-wide summary is admin only (the API enforces this too)."""
        data,error=None,None
        if session.get('role')=='admin':
            try:data=call('GET','/api/admin/system/status')
            except APIError as exc:error=str(exc)
        try:
            api_health=call('GET','/api/health').get('status')
        except APIError as exc:
            api_health='unreachable: '+str(exc)
        nodes=[]
        if not str(api_health).startswith('unreachable'):
            try:nodes=call('GET','/api/nodes').get('nodes',[])
            except APIError:nodes=[]
        events=[]
        if not str(api_health).startswith('unreachable'):
            try:events=call('GET','/api/events').get('events',[])[:25]
            except APIError:events=[]
        return dict(status=data,error=error,api_health=api_health,nodes=nodes,central_events=events,
            simulated=app.config['SIMULATED_API_OFFLINE'],
            node_events=local_events(app.config['OFFLINE_DB'])[:25],
            queue_count=sum(x['status'] in ('pending','conflict') for x in all_queue(app.config['OFFLINE_DB'])),
            receipt_count=sum(x['status'] in ('pending','conflict') for x in all_receipts(app.config['OFFLINE_DB'])),
            trace=trace,trace_error=trace_error,user=current_user())

    @app.get('/project-status')
    def project_status():
        if not session.get('token'):
            return redirect(url_for('login_page'))
        return render_template('project_status.html',**status_context())

    @app.get('/project-trace')
    def project_trace():
        if not session.get('token'):
            return redirect(url_for('login_page'))
        import re
        trace_id=request.args.get('id','')
        trace=None;trace_error=None
        if not re.fullmatch(r'[A-Za-z0-9_-]{8,80}',trace_id):
            trace_error='Enter a valid request ID (8-80 letters, numbers, hyphens or underscores).'
        else:
            try:trace=call('GET','/api/traces/'+trace_id)
            except APIError as exc:trace_error=str(exc)
        return render_template('project_status.html',**status_context(trace,trace_error))

    @app.post('/advanced/lab')
    def advanced_run_lab():
        if not session.get('token'):return redirect(url_for('login_page'))
        try:
            result=call('POST','/api/admin/lab/run',{'scenario':request.form.get('scenario','all')})
            session['last_lab']=result
            flash('Isolated test lab: {}/{} passed.'.format(result['passed'],result['total']))
        except APIError as exc:flash('Lab error: '+str(exc))
        return back('advanced_page')

    # ------------------------------------------------------------------ Database Administration page
    # Plain HTML front end for /api/admin/*. The central API enforces
    # admin-only access; the node check below only avoids showing an empty page.
    def admin_guard():
        if not session.get('token'):
            return redirect(url_for('login_page'))
        if session.get('role')!='admin':
            return render_template('admin.html',denied=True),403
        return None

    @app.get('/admin')
    def admin_page():
        blocked=admin_guard()
        if blocked:return blocked
        import urllib.parse
        args=request.args
        bank_q=args.get('bank_q','').strip()[:80]
        user_filters={k:args.get(k,'').strip()[:80] for k in ('q','bank_id','role','active')}
        data={};errors={}
        fetch=[('overview','/api/admin/overview'),
               ('banks','/api/admin/banks?'+urllib.parse.urlencode({'q':bank_q})),
               ('users','/api/admin/users?'+urllib.parse.urlencode({k:v for k,v in user_filters.items() if v})),
               ('backups','/api/admin/backups'),
               ('audit','/api/admin/audit')]
        if args.get('health')=='1':
            fetch.append(('health','/api/admin/health'))
        for key,route in fetch:
            try:
                data[key]=call('GET',route)
            except APIError as exc:
                if exc.status==401:
                    session.clear();flash('Session expired. Log in again.')
                    return redirect(url_for('login_page'))
                if exc.status==403:
                    return render_template('admin.html',denied=True),403
                errors[key]=str(exc)
        return render_template('admin.html',denied=False,data=data,errors=errors,
                               bank_q=bank_q,user_filters=user_filters,
                               roles=('staff','viewer','driver','dispatcher','manager','admin'),
                               restore=session.pop('last_restore',None),
                               this_user=session.get('username'))

    def admin_perform(method,route,payload,anchor):
        blocked=admin_guard()
        if blocked:return blocked
        return perform(method,route,payload,admin_anchor=anchor)

    @app.post('/admin/banks/<int:target_id>/update')
    def admin_update_bank(target_id):
        form=request.form
        payload={k:form.get(k,'').strip() for k in ('name','area') if form.get(k,'').strip()}
        if form.get('latitude','').strip() or form.get('longitude','').strip():
            payload.update(latitude=form.get('latitude',''),longitude=form.get('longitude',''))
        return admin_perform('PUT',f'/api/admin/banks/{target_id}',payload,'banks')

    @app.post('/admin/banks/<int:target_id>/delete')
    def admin_delete_bank(target_id):
        return admin_perform('DELETE',f'/api/admin/banks/{target_id}',
                             {'confirm_name':request.form.get('confirm_name','')},'banks')

    @app.post('/admin/users/<int:user_id>/update')
    def admin_update_user(user_id):
        form=request.form
        payload={k:form.get(k,'') for k in ('role','bank_id','confirm_username') if form.get(k,'')}
        return admin_perform('PUT',f'/api/admin/users/{user_id}',payload,'users')

    @app.post('/admin/users/<int:user_id>/<action>')
    def admin_user_active(user_id,action):
        if action not in ('disable','enable'):abort(404)
        payload={k:request.form.get(k,'') for k in ('confirm_username','reason')}
        return admin_perform('POST',f'/api/admin/users/{user_id}/{action}',payload,'users')

    @app.post('/admin/backups')
    def admin_create_backup():
        return admin_perform('POST','/api/admin/backups',{'note':request.form.get('note','')},'backups')

    @app.post('/admin/backups/<int:backup_id>/verify-restore')
    def admin_verify_restore(backup_id):
        blocked=admin_guard()
        if blocked:return blocked
        try:
            result=call('POST',f'/api/admin/backups/{backup_id}/verify-restore',{})
            flash(result.get('message','Verification finished.'))
            # Small summary only (session cookie size limit).
            session['last_restore']={'backup_id':backup_id,'passed':result['passed'],
                'steps':[{'step':s['step'],'passed':s['passed']} for s in result['steps']]}
        except APIError as exc:
            if exc.status==401:
                session.clear();flash('Session expired.');return redirect(url_for('login_page'))
            flash('Error: '+str(exc))
        return redirect(url_for('admin_page',_anchor='backups'))

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
