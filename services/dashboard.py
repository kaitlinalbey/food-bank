"""Reporting and user-visible notification read state."""
from database import read_db,write_db
from .inventory import inventory_list
from .transfers import exchange_list
from .matching import matches
from .validation import Problem
from .operations import forecast_and_alerts

def dashboard(path,bank_id):
    with read_db(path) as con:
        bank=con.execute('SELECT id,name,area,latitude,longitude FROM banks WHERE id=?',(bank_id,)).fetchone()
        if not bank:
            raise Problem('Food bank not found.',404)
        bank=dict(bank)
        exchanges=exchange_list(con,bank_id)
        inventory=inventory_list(con,bank_id)
        activity=[dict(x) for x in con.execute('SELECT id,action,details,created_at FROM activity WHERE bank_id=? ORDER BY id DESC LIMIT 15',(bank_id,))]
        notifications=[dict(x) for x in con.execute('SELECT id,message,is_read,created_at FROM notifications WHERE bank_id=? ORDER BY id DESC LIMIT 20',(bank_id,))]
        forecasts,alerts=forecast_and_alerts(con,bank_id)
        recent_donations=[dict(r) for r in con.execute('''SELECT d.id,d.donor,d.quantity,i.name AS item_name FROM donations d
            JOIN items i ON i.id=d.item_id WHERE d.bank_id=? ORDER BY d.id DESC LIMIT 10''',(bank_id,))]
        recent_waste=[dict(r) for r in con.execute('''SELECT w.id,w.quantity,w.reason,i.name AS item_name FROM waste w
            JOIN items i ON i.id=w.item_id WHERE w.bank_id=? ORDER BY w.id DESC LIMIT 10''',(bank_id,))]
        incoming=[x for x in exchanges if x['source_bank_id']==bank_id]
        outgoing=[x for x in exchanges if x['destination_bank_id']==bank_id]
        return {'bank':bank,'inventory':inventory,'matches':matches(con,bank),
                'incoming':incoming,'outgoing':outgoing,
                'pending_count':sum(x['status']=='pending' for x in incoming),
                'unread_count':sum(not n['is_read'] for n in notifications),
                'notifications':notifications,'activity':activity,
                'forecast':forecasts,'alerts':alerts,
                'recent_donations':recent_donations,'recent_waste':recent_waste,
                'stats':{'inventory_items':len(inventory),
                         'shortage_items':sum(x['shortage']>0 for x in inventory),
                         'surplus_items':sum(x['surplus']>0 for x in inventory),
                         'pending_requests':sum(x['status']=='pending' for x in incoming),
                         'completed_transfers':sum(x['status']=='received' for x in exchanges)}}


def mark_notifications(path,bank_id):
    with write_db(path) as con:
        count=con.execute('UPDATE notifications SET is_read=1 WHERE bank_id=? AND is_read=0',(bank_id,)).rowcount
    return count


