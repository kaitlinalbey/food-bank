"""Near-bank shortage/surplus matching, using straight-line miles."""
from .inventory import need,available
from datetime import date
from .validation import haversine

def matches(con, bank, max_miles=None):
    mine=con.execute('SELECT item_id FROM inventory WHERE bank_id=? ORDER BY item_id',(bank['id'],)).fetchall()
    result=[]
    for item in mine:
        item_id=item['item_id']
        required=need(con,bank['id'],item_id)
        if required<=0:
            continue
        others=con.execute('''SELECT banks.id,banks.name,banks.area,banks.latitude,banks.longitude,
          inv.expiration_date,items.name AS item_name,items.category,items.unit
          FROM inventory inv JOIN banks ON banks.id=inv.bank_id
          JOIN items ON items.id=inv.item_id
          WHERE inv.item_id=? AND inv.bank_id!=?''',(item_id,bank['id'])).fetchall()
        for other in others:
            free=available(con,other['id'],item_id)
            if free<=0:
                continue
            miles=haversine(bank['latitude'],bank['longitude'],other['latitude'],other['longitude'])
            if max_miles is not None and miles>max_miles:
                continue
            expiry_days=(date.fromisoformat(other['expiration_date'])-date.today()).days if other['expiration_date'] else None
            urgency=max(0,14-expiry_days) if expiry_days is not None and expiry_days>=0 else 0
            score=round(max(0,100-miles*2)+min(15,urgency)+min(required,free)/max(1,required)*10,1)
            result.append({'item_id':item_id,'item_name':other['item_name'],
                'category':other['category'],'unit':other['unit'],
                'source_bank_id':other['id'],'source_bank_name':other['name'],
                'area':other['area'],'needed':required,'available':free,
                'suggested_quantity':min(required,free),'distance_miles':miles,
                'expiration_date':other['expiration_date'],
                'expiry_days':expiry_days,'recommendation_score':score})
    return sorted(result,key=lambda m:(m['item_name'],-m['recommendation_score'],m['distance_miles'],m['source_bank_name']))


