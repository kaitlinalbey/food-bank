"""Audit history and notification writing helpers."""

def record(con, bank_id, action, details, actor=None, exchange_id=None):
    con.execute('INSERT INTO activity(bank_id,actor_user_id,action,details,exchange_id) VALUES(?,?,?,?,?)',
                (bank_id,actor,action,details,exchange_id))


def notify(con, bank_id, message, exchange_id=None):
    con.execute('INSERT INTO notifications(bank_id,message,exchange_id) VALUES(?,?,?)',
                (bank_id,message,exchange_id))


