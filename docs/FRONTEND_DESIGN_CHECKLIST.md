# Frontend Design Checklist

A short checklist for redesigning the interface. Details are in `FRONTEND_HANDOFF.md`, and the page list is in `MULTIPAGE_NAVIGATION.md`.

## Before you start

- [ ] Work in a copy of the project, and run it with `run_presentation_windows.bat` (fresh fictional data every time).
- [ ] Run `.venv\Scripts\python.exe verify_ui.py` once, so you know it passes before your changes.
- [ ] Log in as `staff` / `demo123` and as `admin` / `admin123` on Central (http://127.0.0.1:5001) and open every page in the sidebar.

## You can change freely

- [ ] Everything in `static/style.css` (or replace it, and add more CSS files in `base.html`)
- [ ] Layout, colors, fonts, spacing, headings, ordering of sections
- [ ] Tables shown as cards, sections shown as tabs, or sections moved to a different page (then update `RETURN_PAGES` in `node.py` and the old-link maps)
- [ ] Button and link text, as long as the meaning stays the same (and update `verify_ui.py` if a checked label changes)

## Do not change

- [ ] Form `action` URLs, `method`s and field `name`s (`FRONTEND_HANDOFF.md`, section 4)
- [ ] The hidden `_csrf` field in every POST form
- [ ] `{{ new_request_key() }}` in the transfer request form
- [ ] Section ids such as `inventory`, `other-inventories`, `matches`, `transfers`, `queue`, `receipts`, `lab` (full list per page in `FRONTEND_HANDOFF.md`, section 1). Keep `<section id="inventory">` and `<section id="other-inventories">` exactly as written.
- [ ] The role checks (`can_edit`, `can_transfer`, `can_fleet`, `is_admin` / `role=='admin'`, `outage_controls`)
- [ ] The page endpoints used by the sidebar (`home`, `my_inventory_page`, `transfers_page`, ...) and the old-link redirects from `/` and `/advanced`
- [ ] `visible_inventory` for the inventory table and `inventory` for the item drop-down lists
- [ ] The three cases on the offline pages (admin restore button, message for others, plain "Retry central API connection" link)
- [ ] No API tokens, database paths or other banks' private totals in any page

## Check every page has

- [ ] A clear title saying what the page is for, and its sidebar link highlighted
- [ ] A helpful message when a list or search result is empty
- [ ] Error and success messages visible near the top (the `messages` box)
- [ ] Wide tables that scroll sideways on a phone instead of breaking the layout
- [ ] Labels on every input, and buttons you can reach with the keyboard (Tab and Enter)
- [ ] Good contrast between text and background

## Check by role

- [ ] **staff:** can edit inventory, request and process transfers, and record donations. No admin links, no outage button.
- [ ] **viewer** (create one on the Database administration page): read-only pages, with no Edit tab, Request, Approve, donation or queue buttons
- [ ] **admin:** Administration in the sidebar, Test lab on More tools, outage button on Offline sync
- [ ] **dispatcher** and **driver** (create them the same way): dispatchers can request and process transfers and add drivers and vehicles but not edit inventory; drivers are read-only

## After your changes

- [ ] `run_tests_windows.bat` ends with `OK` and 0 skipped
- [ ] `.venv\Scripts\python.exe verify_ui.py` ends with every step passed
- [ ] Every sidebar link works at 375 px, 768 px and 1280 px wide, and the Menu button opens the sidebar on a phone
- [ ] Refreshing any page shows the same thing (no form is resubmitted)
- [ ] The five-minute demo in `PRESENTATION.md` still works from start to finish
