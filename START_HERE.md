# Start here

A short checklist for running the Food Bank Database for the first time. The full instructions are in [README.md](README.md).

## Run the demo

1. Install Python 3.10 or newer and check **Add Python to PATH**.
2. Double-click **`run_presentation_windows.bat`**. The first run needs internet so it can install Flask.
3. Leave the terminal open and visit:
   - Central Food Pantry: http://127.0.0.1:5001
   - Northside Food Pantry: http://127.0.0.1:5002
   - Westside Food Pantry: http://127.0.0.1:5003
   - API reference: http://127.0.0.1:5000/docs
4. Log in with `staff` / `demo123` on any node, or `admin` / `admin123` on the Central node.
5. Press **Ctrl+C** in the terminal to stop everything.

Each run of `run_presentation_windows.bat` creates a new database under `data/presentation_sessions/`, so you always start from the same fictional data. Use `run_windows.bat` instead if you want your changes saved in `data/food_bank.db`.

## Check that it works

- Double-click `run_tests_windows.bat` to run the automated tests.
- Double-click `run_release_check_windows.bat` for the full check. It writes `reports/release_verification.md`.

## Resetting demo data

- For a clean demo, just run `run_presentation_windows.bat` again. Old session folders are kept until you delete them.
- `reset_demo.bat` **permanently** replaces `data/food_bank.db` with fresh sample data. It asks you to type `RESET` first. Take a backup with `backup_database.bat` before using it.
- `fill_demo_data_windows.bat` adds any missing sample items to an existing `data/food_bank.db` without removing your changes. It makes a backup first.

## Where to read next

- How the system is put together: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- API endpoints: [docs/API.md](docs/API.md)
- Presentation script: [docs/PRESENTATION.md](docs/PRESENTATION.md)
