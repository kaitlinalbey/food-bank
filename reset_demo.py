"""Interactive reset. Stop the demo first. Safeguards against accidental resets."""
from database import DEFAULT_DB
from seed_more_demo_data import enrich_demo


def main():
    print('WARNING: Removes ALL local demo inventory, users, and transfer history.')
    if input('Type RESET to confirm: ').strip()!='RESET':
        print('Canceled. No changes made.')
        return
    for suffix in ('','-wal','-shm'):
        path=DEFAULT_DB.with_name(DEFAULT_DB.name+suffix)
        if path.exists():
            path.unlink()
    enrich_demo(DEFAULT_DB)
    print('Reset complete. Fictional demo data recreated.')

if __name__=='__main__':
    main()
