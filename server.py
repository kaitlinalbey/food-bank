"""Compatibility launcher for the original project's server.py command."""
import argparse
import os
from app import create_app

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',type=int,default=5000)
    parser.add_argument('--host',default='127.0.0.1')
    args=parser.parse_args()
    if os.environ.get('FOOD_BANK_SEED_EXPANDED')=='1':
        from database import DEFAULT_DB
        if not DEFAULT_DB.exists():
            from seed_more_demo_data import enrich_demo
            print('Docker demo seed:',enrich_demo(DEFAULT_DB),flush=True)
    create_app().run(host=args.host,port=args.port,debug=False,threaded=True)
