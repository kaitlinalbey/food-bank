"""Make a consistent, verified SQLite backup (command-line version).

Uses the same routine as the administrator page (services.dbadmin.safe_backup):
SQLite's online backup API, then a read-only integrity / foreign-key / table check.
Never overwrites an existing file and never modifies the source database.
"""
from datetime import datetime
from pathlib import Path
import secrets
import sys
from database import DEFAULT_DB


def main():
    if not DEFAULT_DB.exists():
        print('No database exists yet. Run the demo to create one.')
        return
    from services.dbadmin import safe_backup
    folder=Path(__file__).resolve().parent/'backups'
    filename=folder/f'food_bank_backup_{datetime.now().strftime("%Y%m%d_%H%M%S")}_{secrets.token_hex(3)}.db'
    result=safe_backup(DEFAULT_DB,filename)
    print('Backup saved:',filename)
    print('Verified:',result['verified'],'| integrity:',result['integrity'],
          '| foreign key problems:',result['foreign_key_problems'],'| sha256:',result['sha256'])
    if not result['verified']:
        print('WARNING: the backup failed verification. Do not rely on it.')
        sys.exit(1)

if __name__=='__main__':
    main()
