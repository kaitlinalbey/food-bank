"""Command-line account tools for running the project outside classroom demo mode.

Outside demo mode (FOOD_BANK_DEMO_MODE not set), a new database gets no accounts, and the
public demo passwords are refused at login. Use this tool to create the first administrator
or to give an existing account (for example the old demo 'admin') a new password.

    python manage_accounts.py create-admin --bank 1 --username coordinator
    python manage_accounts.py set-password --bank 1 --username admin
    python manage_accounts.py list

The database is the one the API uses: data/food_bank.db, or FOOD_BANK_DB_PATH if set.
Passwords are typed at a hidden prompt and are never printed or logged.
"""
import argparse
import getpass
import sys

from database import DEFAULT_DB, DEMO_PASSWORDS, hash_password, initialize, read_db, write_db

MIN_LENGTH = 10


def ask_password(prompt=getpass.getpass):
    first = prompt('New password: ')
    second = prompt('Repeat password: ')
    if first != second:
        raise SystemExit('Passwords do not match. Nothing was changed.')
    if not MIN_LENGTH <= len(first) <= 128:
        raise SystemExit(f'Password must be {MIN_LENGTH}-128 characters. Nothing was changed.')
    if first in DEMO_PASSWORDS:
        raise SystemExit('That is a public demo password. Choose another. Nothing was changed.')
    return first


def create_admin(db, bank_id, username, password):
    with write_db(db) as con:
        if not con.execute('SELECT 1 FROM banks WHERE id=?', (bank_id,)).fetchone():
            raise SystemExit(f'Bank {bank_id} does not exist. Nothing was changed.')
        if con.execute('SELECT 1 FROM users WHERE bank_id=? AND username=? COLLATE NOCASE', (bank_id, username)).fetchone():
            raise SystemExit('That username already exists at this bank. Use set-password instead.')
        con.execute('INSERT INTO users(bank_id,username,password_hash,role) VALUES(?,?,?,?)',
                    (bank_id, username, hash_password(password), 'admin'))
        con.execute("INSERT INTO audit_events(bank_id,operation,result,detail) VALUES(?,?,?,?)",
                    (bank_id, 'cli: create-admin', 'success', f'Administrator {username} created from the command line.'))
    return f'Administrator {username} created at bank {bank_id}.'


def set_password(db, bank_id, username, password):
    with write_db(db) as con:
        user = con.execute('SELECT id FROM users WHERE bank_id=? AND username=? COLLATE NOCASE', (bank_id, username)).fetchone()
        if not user:
            raise SystemExit('No such account. Nothing was changed.')
        con.execute('UPDATE users SET password_hash=? WHERE id=?', (hash_password(password), user['id']))
        con.execute('DELETE FROM auth_tokens WHERE user_id=?', (user['id'],))  # sign out existing sessions
        con.execute("INSERT INTO audit_events(bank_id,operation,result,detail) VALUES(?,?,?,?)",
                    (bank_id, 'cli: set-password', 'success', f'Password changed for {username}; sessions ended.'))
    return f'Password changed for {username} at bank {bank_id}. Existing sessions were ended.'


def list_accounts(db):
    with read_db(db) as con:
        rows = con.execute('''SELECT u.bank_id,b.name,u.username,COALESCE(o.role,u.role) role,u.active
            FROM users u JOIN banks b ON b.id=u.bank_id LEFT JOIN user_role_overrides o ON o.user_id=u.id
            ORDER BY u.bank_id,u.username''').fetchall()
    return [f'bank {r["bank_id"]} ({r["name"]}): {r["username"]} [{r["role"]}]{"" if r["active"] else " (disabled)"}' for r in rows]


def main(argv=None):
    parser = argparse.ArgumentParser(description='Food Bank Database account tools')
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('create-admin', 'set-password'):
        p = sub.add_parser(name)
        p.add_argument('--bank', type=int, required=True)
        p.add_argument('--username', required=True)
    sub.add_parser('list')
    args = parser.parse_args(argv)
    db = str(DEFAULT_DB)
    initialize(db)  # schema only; never creates demo accounts unless demo mode is on
    if args.command == 'list':
        print('\n'.join(list_accounts(db)) or 'No accounts.')
        return 0
    username = args.username.strip()
    if not 1 <= len(username) <= 40:
        raise SystemExit('Username must be 1-40 characters.')
    password = ask_password()
    action = create_admin if args.command == 'create-admin' else set_password
    print(action(db, args.bank, username, password))
    return 0


if __name__ == '__main__':
    sys.exit(main())
