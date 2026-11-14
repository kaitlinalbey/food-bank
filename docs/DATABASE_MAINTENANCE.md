# Database backup, restore verification and live restore

This document is for administrators. It describes how backups are made, how a backup is proven restorable, and the **manual** procedure for replacing the live database.

The web page and API deliberately cannot overwrite the active database. Restoring live data is a planned maintenance task, done with the servers stopped.

## 1. Making a backup

Use either of these:

- **Web page:** Database administration, section 5, **Create verified backup**. This calls `POST /api/admin/backups`.
- **Command line:** `python backup_database.py`, or `backup_database.bat`.

Both use the same routine, `services.dbadmin.safe_backup`. It works as follows:

1. Copies the database with SQLite's **online backup API** (`sqlite3.Connection.backup`), which produces a consistent snapshot even while the servers are running. The `.db` file is never copied blindly.
2. Refuses to overwrite an existing file.
3. Opens the new file **read-only** and runs `PRAGMA integrity_check` and `PRAGMA foreign_key_check`. It also confirms the required Food Bank tables exist.
4. Records the file's size, a SHA-256 checksum and row counts.

| Backup made from | Location | Recorded in |
| --- | --- | --- |
| Web page | `admin_backups/` next to the active database (for example `data/admin_backups/`) | The `db_backups` table (file name only, never a full path), plus an `activity` row and an `audit_events` row |
| Command line | `backups/` in the project folder | Printed to the console only |

Nothing in the application ever deletes a backup file.

## 2. Proving a backup can be restored (safe, any time)

On the web page, section 5, select **Test restore (isolated)** on a backup row. This calls `POST /api/admin/backups/<id>/verify-restore`. The test:

1. Checks that the file's SHA-256 still matches the value recorded at backup time, which detects damage or tampering.
2. Restores the backup into a **new temporary database** in the operating system's temp folder, never next to the live data.
3. Runs the integrity, foreign key and required-table checks on the restored copy.
4. Compares the row counts with those recorded at backup time.
5. Runs the same inventory consistency checks as the health page.
6. Deletes the temporary copy and stores the pass/fail result in `db_backups`.

The active database is only written to record the result. Its data is never replaced.

## 3. Restoring the live database (manual maintenance procedure)

Use this only when the live database is damaged or must be rolled back. **Restoring loses every change made after the backup was taken.**

1. **Agree on it.** The team should agree on the restore and choose the backup. Prefer one whose isolated restore test shows *Passed*.
2. **Stop everything.** Stop the launcher (Ctrl+C), the central API (`server.py`, port 5000) and all bank nodes (ports 5001 and up). Then confirm nothing is still listening on those ports.
3. **Preserve the current state.** Run `python backup_database.py`, even if the database is damaged. If that fails, copy `data/food_bank.db` to a safe folder **while the servers are stopped**. Never delete it.
4. **Restore to a NEW file.** First preview, then apply:
   ```
   python restore_database.py --from-backup data\admin_backups\<file>.db --destination data\restore_candidate.db
   python restore_database.py --from-backup data\admin_backups\<file>.db --destination data\restore_candidate.db --apply
   ```
   The script refuses to write to an existing file and validates both the backup and the restored copy.
5. **Inspect the candidate.** Start the system against it by setting `FOOD_BANK_DB_PATH` to the candidate file. Log in as an administrator, then run **Database health** on the administration page and check the overview counts.
6. **Switch over.** With the servers stopped again, rename the old `data/food_bank.db` (do not delete it), for example to `food_bank_replaced_<date>.db`. Rename `restore_candidate.db` to `food_bank.db`. If `food_bank.db-wal` or `food_bank.db-shm` files exist next to the old database, move them with it.
7. **Restart and confirm.** Start normally, then run the health check and take a fresh verified backup.
8. **Record it.** Write down who restored which backup, when and why. The application does not log manual file operations.

Node offline queues (`data/node_N_queue.db`) are separate files. Changes queued on a node after the backup time may still sync after the restore. Review them on each node before syncing.

## 4. Presentation and demo data

Classroom presentations (`run_presentation_windows.bat` or `start_presentation.py`) create a new folder under `data/presentation_sessions/` for each run and never touch `data/food_bank.db`. Backups taken during a presentation go to that session's own `admin_backups/` folder.

`reset_demo.py` is a separate, command-line-only tool for demo data. It is intentionally not offered on the administration page.
