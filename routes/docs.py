"""Self-contained OpenAPI outline and simple API playground (no CDN needed)."""
from flask import Blueprint,jsonify,render_template
bp=Blueprint('docs',__name__)

ENDPOINTS=[
 ('get','/api/health','Health check'),('post','/api/login','Log in and get a token'),
 ('post','/api/logout','Log out'),('get','/api/me','Current user'),
 ('get','/api/dashboard','Dashboard'),('get','/api/stats','Summary statistics'),
 ('get','/api/items','Item catalog'),('post','/api/inventory','Create stock item'),
 ('put','/api/inventory/{item_id}','Edit stock item'),('delete','/api/inventory/{item_id}','Remove zero-stock item'),
 ('get','/api/matches','Find nearby supply matches'),('get','/api/banks','List food banks'),
 ('get','/api/banks/{bank_id}/inventory','Available supplies at another bank'),
 ('post','/api/exchanges','Request transfer'),('post','/api/exchanges/{exchange_id}/approve','Approve transfer'),
 ('post','/api/exchanges/{exchange_id}/ship','Ship transfer'),('post','/api/exchanges/{exchange_id}/receive','Receive transfer'),
 ('post','/api/exchanges/{exchange_id}/decline','Decline transfer'),('post','/api/exchanges/{exchange_id}/cancel','Cancel transfer'),
 ('put','/api/exchanges/{exchange_id}/schedule','Schedule pickup and delivery'),
 ('get','/api/notifications','Notifications'),('post','/api/notifications/read','Mark notifications read'),
 ('get','/api/activity','Activity history'),('get','/api/nodes','Node availability'),
 ('get','/api/lots','Stock batches'),('patch','/api/lots/{lot_id}','Update lot expiry'),
 ('get','/api/donations','List donations'),('post','/api/donations','Record donation'),
 ('get','/api/waste','List waste records'),('post','/api/waste','Record discarded stock'),
 ('get','/api/forecast','Usage forecasts and alerts'),
 ('post','/api/inventory/{item_id}/adjust','Conflict-aware offline adjustment'),
 ('get','/api/inventory/export.csv','Export CSV'),('post','/api/inventory/import.csv','Import CSV'),
 ('get','/api/barcodes/{code}','Look up barcode'),
 ('get','/api/admin/audit','Admin audit records'),('post','/api/admin/banks','Create food bank'),
 ('get','/api/admin/users','List users'),('post','/api/admin/users','Create user'),
 ('put','/api/admin/items/{item_id}/barcode','Assign barcode'),
 ('get','/api/donors','List donor profiles'),('post','/api/donors','Create a donor profile'),
 ('get','/api/donations/{donation_id}/receipt','Printable donation acknowledgment'),
 ('get','/api/recalls','Food recalls visible to this bank'),
 ('post','/api/admin/recalls','Issue batch recall (admin)'),
 ('post','/api/admin/recalls/{recall_id}/close','Close recall (admin)'),
 ('get','/api/storage','Storage capacity and current occupancy'),
 ('put','/api/storage/{storage_type}','Update capacity (manager/admin)'),
 ('put','/api/admin/items/{item_id}/storage','Set item cold-chain requirement'),
 ('get','/api/reconciliations','List physical stock counts'),
 ('post','/api/reconciliations','Submit physical stock count'),
 ('post','/api/reconciliations/{recon_id}/apply','Approve stock correction'),
 ('post','/api/reconciliations/{recon_id}/reject','Reject stock correction'),
 ('get','/api/transport','Drivers, vehicles and assignments'),
 ('post','/api/drivers','Create driver label'),('post','/api/vehicles','Create vehicle'),
 ('put','/api/exchanges/{exchange_id}/transport','Assign driver and vehicle'),
 ('post','/api/exchanges/{exchange_id}/receive-detailed','Confirm partial delivery'),
 ('get','/api/discrepancies','Missing or damaged delivery cases'),
 ('post','/api/discrepancies/{discrepancy_id}/review','Mark dispute reviewed'),
 ('post','/api/offline/receipts','Replay idempotent independent donation'),
 ('get','/api/analytics','Real donation, waste and transfer statistics'),
 ('get','/api/events','Distributed event logs'),
 ('get','/api/admin/lab/scenarios','Available isolated test scenarios'),
 ('post','/api/admin/lab/run','Run isolated test scenario'),
 ('get','/api/admin/system/status','Read central database and bank node status (admin)'),
 ('get','/api/traces/{trace_id}','Find central request trace by correlation ID'),
 ('get','/api/admin/overview','Database administration overview (admin)'),
 ('get','/api/admin/banks','Search banks with node connectivity (admin)'),
 ('put','/api/admin/banks/{bank_id}','Update bank name, area or location (admin)'),
 ('delete','/api/admin/banks/{bank_id}','Delete a never-used bank (admin, typed confirmation)'),
 ('put','/api/admin/users/{user_id}','Change user role or bank (admin)'),
 ('post','/api/admin/users/{user_id}/disable','Disable account (admin, typed confirmation)'),
 ('post','/api/admin/users/{user_id}/enable','Re-enable account (admin)'),
 ('get','/api/admin/health','Read-only integrity and invariant checks (admin)'),
 ('get','/api/admin/backups','List recorded backups (admin)'),
 ('post','/api/admin/backups','Create verified online backup (admin)'),
 ('post','/api/admin/backups/{backup_id}/verify-restore','Restore backup into isolated temp DB and verify (admin)')]

@bp.get('/api/openapi.json')
def spec():
    paths={}
    for method,path,summary in ENDPOINTS:
        params=[{'name':s[1:-1],'in':'path','required':True,'schema':{'type':'string' if s in ('{code}','{storage_type}','{trace_id}') else 'integer'}}
                for s in path.split('/') if s.startswith('{') and s.endswith('}')]
        endpoint={'summary':summary,'responses':{'200':{'description':'Success'},'400':{'description':'Invalid request'},
                 '409':{'description':'Conflict'}}}
        if params:endpoint['parameters']=params
        if path not in ('/api/health','/api/login'):
            endpoint['security']=[{'bearerAuth':[]}]
        if method in ('post','put','patch') or path=='/api/admin/banks/{bank_id}' and method=='delete':
            content_type='text/csv' if path=='/api/inventory/import.csv' else 'application/json'
            schema={'type':'string'} if content_type=='text/csv' else {'type':'object'}
            endpoint['requestBody']={'content':{content_type:{'schema':schema}},'required':True}
        paths.setdefault(path,{})[method]=endpoint
    return jsonify({'openapi':'3.0.3','info':{'title':'Food Bank Database classroom API','version':'3.0',
        'description':'Local demonstration. See docs/API.md for full request examples and behavior.'},
        'servers':[{'url':'http://127.0.0.1:5000'}],
        'components':{'securitySchemes':{'bearerAuth':{'type':'http','scheme':'bearer'}}},'paths':paths})

@bp.get('/docs')
def playground():
    return render_template('api_docs.html',endpoints=ENDPOINTS)
