"""Fresh, isolated and repeatable classroom demo; never resets normal project data."""
import os
from pathlib import Path
from datetime import datetime,timezone
import secrets
from database import initialize
from seed_more_demo_data import enrich_demo
from start_demo import main


def new_session(root=None):
    root=Path(root or Path(__file__).resolve().parent/'data'/'presentation_sessions')
    root.mkdir(parents=True,exist_ok=True)
    # A new directory per run makes reset safe even if last session is needed for review.
    name=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'_'+secrets.token_hex(3)
    session_dir=root/name
    session_dir.mkdir(exist_ok=False)
    (session_dir/'queues').mkdir()
    db=session_dir/'food_bank.db'
    enrich_demo(db)
    return session_dir,db


def launch():
    session_dir,db=new_session()
    os.environ['FOOD_BANK_DB_PATH']=str(db)
    os.environ['FOOD_BANK_QUEUE_DIR']=str(session_dir/'queues')
    print('Fresh demo data:',db,flush=True)
    print('Normal data/food_bank.db and normal node queues are untouched.',flush=True)
    print('Run this launcher again for a clean session. Old sessions are not automatically deleted.',flush=True)
    main()

if __name__=='__main__':
    launch()
