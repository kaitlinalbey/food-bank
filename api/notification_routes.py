from flask import Blueprint

bp = Blueprint('notifications', __name__)


@bp.get('/api/notifications')
def list_notifications():
    raise NotImplementedError


@bp.post('/api/notifications/<int:notification_id>/read')
def mark_read(notification_id):
    raise NotImplementedError
