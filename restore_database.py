"""Safely inspect a SQLite backup and optionally restore to a NEW database file.

Never overwrites a live database. To replace active data, stop servers and
perform a separate manually reviewed migration after verifying the restored copy.
"""
import argparse
from contextlib import closing
from pathlib import Path
import sqlite3


def inspect(path):
    if not Path(path).is_file():raise ValueError('Source backup file does not exist.')
    # Read-only prevents implicit creation or modification.
    from urllib.parse import quote
    uri='file:'+quote(str(Path(path).resolve()),safe='/')+'?mode=ro'
    with closing(sqlite3.connect(uri,uri=True)) as con:
        quick=con.execute('PRAGMA quick_check').fetchone()[0]
        tables={r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        count=con.execute('SELECT COUNT(*) FROM banks').fetchone()[0] if 'banks' in tables else None
    if quick!='ok':raise ValueError('Backup failed SQLite integrity check.')
    if not {'banks','inventory','exchanges','lots'}.issubset(tables):
        raise ValueError('Backup does not contain the required Food Bank tables.')
    return count


def main():
    ap=argparse.ArgumentParser(description='Validate and restore backup to a separate file')
    ap.add_argument('--from-backup',required=True,help='Existing .db backup file')
    ap.add_argument('--destination',required=True,help='New .db path to create (never overwrite)')
    ap.add_argument('--apply',action='store_true',help='Create the destination after safe preview')
    args=ap.parse_args()
    source=Path(args.from_backup).resolve();dest=Path(args.destination).resolve()
    if source==dest or dest.exists():raise SystemExit('Destination must be a NEW, unused path.')
    try:count=inspect(source)
    except (sqlite3.Error,ValueError) as e:raise SystemExit('Backup validation failed: '+str(e))
    print('Validated source:',source,'| bank rows:',count,'| destination:',dest)
    if not args.apply:
        print('Preview only. Add --apply to create a separate restored copy.')
        return
    dest.parent.mkdir(parents=True,exist_ok=True)
    try:
        # closing() so the destination is released and can be removed on failure (Windows).
        with closing(sqlite3.connect(str(source))) as input_db,closing(sqlite3.connect(str(dest))) as output_db:
            input_db.backup(output_db)
        inspect(dest)
        print('Verified restored database:',dest)
    except Exception:
        if dest.exists():dest.unlink()
        raise

if __name__=='__main__':main()
