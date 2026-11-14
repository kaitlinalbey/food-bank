"""Administrator-only database administration API.

Every route requires an admin bearer token (routes.common.require_admin), so a
non-admin from any bank gets 403 and an anonymous caller gets 401. Business
rules live in services/dbadmin.py; failures are audited by app.business_error,
successes by services.dbadmin._audit.

Creating banks and users reuses the existing POST /api/admin/banks and
POST /api/admin/users (routes/admin.py). Listing users is GET /api/admin/users
(also routes/admin.py, now with search filters).
"""
from flask import Blueprint, current_app, g, jsonify, request

from routes.common import json_body, require_admin
from routes.stats import _node_status
from services import dbadmin

bp = Blueprint('dbadmin', __name__)


def db():
    return current_app.config['DB_PATH']


@bp.get('/api/admin/overview')
@require_admin
def overview():
    return jsonify(dbadmin.overview(db()))


@bp.get('/api/admin/banks')
@require_admin
def banks():
    probe = request.args.get('probe', '1') != '0'
    return jsonify({'banks': dbadmin.list_banks(db(), request.args.get('q', ''),
                                                _node_status if probe else None)})


@bp.put('/api/admin/banks/<int:bank_id>')
@require_admin
def update_bank(bank_id):
    return jsonify(dbadmin.update_bank(db(), bank_id, json_body(), g.bank_id, g.user_id))


@bp.delete('/api/admin/banks/<int:bank_id>')
@require_admin
def delete_bank(bank_id):
    return jsonify(dbadmin.delete_bank(db(), bank_id, json_body(), g.bank_id, g.user_id))


@bp.put('/api/admin/users/<int:user_id>')
@require_admin
def update_user(user_id):
    return jsonify(dbadmin.update_user(db(), user_id, json_body(), g.bank_id, g.user_id))


@bp.post('/api/admin/users/<int:user_id>/disable')
@require_admin
def disable_user(user_id):
    return jsonify(dbadmin.set_user_active(db(), user_id, False, json_body(), g.bank_id, g.user_id))


@bp.post('/api/admin/users/<int:user_id>/enable')
@require_admin
def enable_user(user_id):
    return jsonify(dbadmin.set_user_active(db(), user_id, True, json_body(), g.bank_id, g.user_id))


@bp.get('/api/admin/health')
@require_admin
def health():
    return jsonify(dbadmin.health(db()))


@bp.get('/api/admin/backups')
@require_admin
def backups():
    return jsonify({'backups': dbadmin.list_backups(db()),
                    'live_restore': 'Not available through the API or web page. Follow the manual '
                                    'maintenance procedure in docs/DATABASE_MAINTENANCE.md.'})


@bp.post('/api/admin/backups')
@require_admin
def create_backup():
    return jsonify(dbadmin.create_backup(db(), json_body(), g.bank_id, g.user_id)), 201


@bp.post('/api/admin/backups/<int:backup_id>/verify-restore')
@require_admin
def verify_restore(backup_id):
    return jsonify(dbadmin.verify_restore(db(), backup_id, g.bank_id, g.user_id))
