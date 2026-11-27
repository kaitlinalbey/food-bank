from flask import Blueprint, g, jsonify

from auth import login_required
from db import get_db, transaction
from errors import APIError

bp = Blueprint('notifications', __name__)


def notify(db, bank_id, message):
    db.execute('INSERT INTO notifications (bank_id, message) VALUES (?, ?)', (bank_id, message))


@bp.get('/api/notifications')
@login_required
def list_notifications():
    rows = get_db().execute(
        'SELECT id, message, is_read, created_at FROM notifications WHERE bank_id = ? ORDER BY id DESC',
        (g.bank_id,),
    ).fetchall()
    notifications = [dict(row, is_read=bool(row['is_read'])) for row in rows]
    unread = sum(not n['is_read'] for n in notifications)
    return jsonify(notifications=notifications, unread_count=unread)


@bp.post('/api/notifications/<int:notification_id>/read')
@login_required
def mark_read(notification_id):
    with transaction() as db:
        cur = db.execute(
            'UPDATE notifications SET is_read = 1 WHERE id = ? AND bank_id = ?',
            (notification_id, g.bank_id),
        )
    if cur.rowcount == 0:
        raise APIError('notification not found', 404)
    return jsonify(status='ok')


@bp.post('/api/notifications/read-all')
@login_required
def mark_all_read():
    with transaction() as db:
        cur = db.execute('UPDATE notifications SET is_read = 1 WHERE bank_id = ? AND is_read = 0', (g.bank_id,))
    return jsonify(marked=cur.rowcount)
