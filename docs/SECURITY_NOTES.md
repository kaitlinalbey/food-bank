# Security Notes

This project is a classroom prototype. It has not had an independent security review, and it must not be run on a public network or used with real personal data. The demo passwords in the README are public on purpose, and they only work in classroom demo mode.

## What the project does

**Accounts and login**
- Passwords are stored as salted PBKDF2-SHA256 hashes (240,000 iterations).
- Login tokens are random, stored only as SHA-256 hashes, and expire after 12 hours. Changing a password with `manage_accounts.py` ends that user's sessions.
- The public demo accounts (`staff`/`demo123`, `admin`/`admin123`) are created only in explicit classroom demo mode (`FOOD_BANK_DEMO_MODE=1`, set by the launchers and Docker Compose). Outside demo mode:
  - a new database gets no accounts,
  - an existing database never gets an `admin` added automatically,
  - a correct demo password is refused at login (HTTP 403, recorded in `audit_events`),
  - admins cannot give a new account a demo password.
- After 8 failed logins for the same account within 15 minutes, further attempts are blocked.

**Authorization**
- Permissions are checked on the server for every request, by role and by bank. Hiding a button is never the only protection. The bank a user acts for comes from their token, never from the request body or query string.
- Other banks' private inventory fields are removed by the API before anything is sent.
- The connection-outage simulation can only be used by an administrator, only in demo mode, and only from the same computer. Every attempt is logged, and a refused attempt never changes the outage state.

**Web interface and requests**
- Node forms are protected with CSRF tokens. Each node uses its own session cookie name (HttpOnly, SameSite=Lax, and `Secure` when `FOOD_BANK_SECURE_COOKIES=1`), and only accepts sessions created for its own bank. The API token is never placed in a page.
- Responses include `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY` and `Cache-Control: no-store`.
- Request bodies are limited to 2 MB. Input is validated, and the database enforces foreign keys and constraints. Unexpected errors return a generic JSON message with a request ID. Details stay in the server log.
- Every outgoing network call has a timeout: node to API 4 s, CSV upload 5 s, node health checks 0.65 s.

**Logging**
- Failed operations are written to `audit_events`. Request traces do not contain request bodies, passwords or tokens.
- Admin pages require typed confirmation for risky actions, such as deleting a bank or disabling a user.

The tests for these behaviors are in `tests/`. `test_security.py` covers the outage control, demo accounts, cross-bank attempts made directly through the API, and error responses.

## What is missing

- There is no HTTPS in the demo. Tokens travel over plain HTTP on localhost, and the node keeps the token in a signed (not encrypted) session cookie.
- Login throttling is per account, not per IP address, so anyone can lock out a known username for 15 minutes.
- There is no password reset, multi-factor login, or password change page in the web interface. Passwords can be changed with `manage_accounts.py`.
- Demo mode is a single on/off setting. Anyone who can start the servers can turn it on.
- There is no dependency scanning, penetration testing or secret management.
- Food safety features (recalls, storage types) are simplified models, not compliance tools.
