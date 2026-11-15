# Five-Minute Presentation: Outline and Demo Script

This is the plan for our five-minute Distributed Computing presentation. It has eight short parts. Slides cover the background, and a live demo covers the middle. The **Speaker** column is left blank for the team to fill in.

## Before class (about 10 minutes)

1. Double-click `run_presentation_windows.bat`. It creates a fresh database with fictional data and does not change `data/food_bank.db`.
2. Open two browser windows side by side:
   - Window A: http://127.0.0.1:5001 (Central Food Pantry), logged in as `admin` / `admin123`. The admin can do everything Central's staff can, and only an admin can start the outage simulation in step 7.
   - Window B: http://127.0.0.1:5002 (Northside Food Pantry), logged in as `staff` / `demo123`
3. Open a third tab with http://127.0.0.1:5000/api/health so you can show that the API is running.
4. Open the slides, which use the PNG images in `docs/diagrams/`.
5. Do one practice run, then stop the launcher and start it again so the data is fresh.

In a fresh session, Central needs **15 bags of Rice** (it has 5, target 20) and Northside has **28 bags** available, 2.0 miles away. The script below uses that example.

## Outline and script

| # | Time | Speaker | Show | What to say |
| --- | --- | --- | --- | --- |
| 1 | 0:00-0:30 | | Slide: "The food distribution problem" | Food banks in the same city often have uneven stock. One has too much rice and another has almost none. Finding a partner today means phone calls and spreadsheets. |
| 2 | 0:30-1:00 | | Slide: project purpose and goals | We built a system where each food bank manages its own inventory, sees what other banks can share, and moves food through a tracked transfer. Our focus for this course was keeping the data correct when the network or servers fail. |
| 3 | 1:00-1:40 | | Slide: `system_architecture.png`, then the API health tab | There are three food-bank nodes. Each is a separate Flask process on its own port. They only talk to a central Flask API, using HTTP and JSON, and the API is the only process that touches the SQLite database. Each node also has a small local file for offline work. |
| 4 | 1:40-2:10 | | Window A: sidebar **My inventory**. Search "rice", filter by Shortage. Then sidebar **Other food banks**, Northside tab | Each bank can search and filter its own inventory. It can also browse other banks, but the API only sends what they can share, not their private stock numbers. |
| 5 | 2:10-2:40 | | Window A: sidebar **Supply matching**, the Rice row from Northside | The matching service compares our shortages with other banks' extra stock. Extra means usable, unexpired stock above their target that is not already promised. It ranks the results by distance, how soon the food expires, and how much of our need they can cover. Request 10 bags. |
| 6 | 2:40-3:40 | | Window B: **Transfers**, "Requests to us" tab: approve, then ship. Window A: **Transfers**, "Our requests" tab: receive. Show both **My inventory** pages and the Dashboard notifications. | Approving reserves the 10 bags at Northside. Shipping removes them, oldest expiration first. Receiving adds them at Central with the same expiration dates. Each step runs in one database transaction, and each step only works from the correct state. Clicking receive twice does not add stock twice. |
| 7 | 3:40-4:30 | | Slide: `distributed_communication.png`. Optional: in Window A, sidebar **Offline sync**, click **Simulate connection outage**. On the same page, queue a +1 adjustment, click **Restore simulated connection**, then **Attempt sync**. | When the API cannot be reached, the node shows its last snapshot and lets staff queue changes. When the API is back, the node replays them. Each change has an operation key, so it is never applied twice. If the item changed in the meantime, the version check returns a conflict for a person to review instead of overwriting newer data. Two approvals for the same stock cannot both succeed. The outage button is a demo tool, so only an administrator on the same computer can use it. |
| 8 | 4:30-5:00 | | Slide: results and limitations | Results: 156 of 156 automated tests and 18 of 18 live multi-process scenarios passed (148 checks), including stopping and restarting the API and checking that only an admin can start an outage. Limitation: the central API is a single point of failure, and everything ran on one machine. Next step: a replicated database with failover. |

## Slide checklist

1. Title: project name, course, team names
2. The problem: uneven stock between nearby food banks
3. Goals: the five or six goals from section 3 of `FINAL_TECHNICAL_REPORT.md`
4. Architecture: `docs/diagrams/system_architecture.png`
5. Exchange lifecycle: `docs/diagrams/transfer_lifecycle.png` (backup if the live demo fails)
6. Failure handling: `docs/diagrams/distributed_communication.png`
7. Results and limitations: numbers from section 10 of `FINAL_TECHNICAL_REPORT.md`. Run the tests again before class and update the numbers if they change.

Use real screenshots from your own run. Do not use mock-ups.

## If something goes wrong during the demo

| Problem | What to do |
| --- | --- |
| A node page will not load | Check the terminal for errors. If only one page fails, use the sidebar to open another page and come back. If a port is in use, close the other copy, then restart the launcher. |
| The Rice transfer was already done in practice | Stop and restart `run_presentation_windows.bat` to get fresh data. |
| You are logged out | Log in again. Tokens last 12 hours, and restarting a node ends its sessions. |
| The live demo fails completely | Present the lifecycle diagram, and run `python presentation_story.py` in a terminal. It runs the same transfer story on a temporary database and prints a JSON report of each step. |

## Likely questions

**Is the database distributed?**
No. The application is distributed: separate processes talk over a network API. The data is centralized in one SQLite database behind the API. We chose that so every transfer can be checked in one transaction.

**What stops two banks from getting the same food?**
Approval re-checks the available amount inside a `BEGIN IMMEDIATE` transaction, so approvals happen one at a time. The second one sees the first reservation and is rejected with HTTP 409.

**What if a request times out and the user clicks again?**
Transfer requests carry a unique `request_key`. A repeat with the same key returns the original transfer instead of creating a new one. Actions like ship or receive only work from one state, so repeating them gets a 409.

**What happens if the central server goes down?**
Nodes keep running and show their last snapshot. Staff can queue stock adjustments and donation receipts. Nothing that involves another bank can happen until the API is back. That is the main limitation of the design.

**How did you test it?**
Unit tests, Flask API tests, eight built-in failure scenarios, and a script that starts the real API and three real nodes and runs eighteen scenarios over HTTP, including actually stopping the API.

**Is it ready for real food banks?**
No. It is a class prototype with fictional data, public demo passwords, and no security review.
