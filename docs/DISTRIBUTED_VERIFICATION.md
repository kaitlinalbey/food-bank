# Distributed System Verification

This document records the results of `verify_distributed.py`, our live test of the system running as separate processes. Unlike the unit tests, this script starts the **real** central API (`server.py`) and **three real bank nodes** (`node.py`) as separate operating-system processes. It then drives them over HTTP the same way browsers and nodes do.

## How the test works

- It creates a new temporary folder with a fresh fictional database and separate node queue files. `data/food_bank.db` is never used. The processes run in classroom demo mode (`FOOD_BANK_DEMO_MODE=1`), because the test uses the demo accounts and the administrator outage control.
- It refuses to start if any port it needs is already in use. The default ports are 25000-25003. The run below used `--base-port 27000`.
- Every HTTP request has a 15 second timeout, and every wait loop has a deadline.
- Every process it starts is stopped at the end, and the ports are checked again afterward.
- The script exits with code 0 only if every scenario passes.

```powershell
.venv\Scripts\python.exe verify_distributed.py
.venv\Scripts\python.exe verify_distributed.py --base-port 27000 --out reports\distributed_verification.json
```

## Summary of the final run

| Item | Result |
| --- | --- |
| Date (UTC) | 2026-11-10 |
| Environment | Windows 11, Python 3.13.3, Flask 3.1.3, all processes on one computer |
| Scenarios | **18 / 18 passed** |
| Individual checks | **148 / 148 passed** |
| Timeouts | none |
| Processes or ports left running afterward | none |
| Python tracebacks in server logs | 0 |
| HTTP 5xx lines in logs | 16, all intended: 13 HTTP 503 offline pages and 3 HTTP 503 `/node-health` answers from nodes during the disconnect and outage scenarios. The central API logged none. |

## Scenarios and checks

### 1. Launch central API and three separate bank nodes: PASS

- [x] central API /api/health 200
- [x] node 1 /node-health reports bank 1
- [x] node 2 /node-health reports bank 2
- [x] node 3 /node-health reports bank 3
- [x] central API sees all 3 nodes online
- [x] 4 distinct OS processes

### 2. Each node authenticates and reads its own inventory: PASS

- [x] node 1 login + dashboard
- [x] node 2 login + dashboard
- [x] node 3 login + dashboard
- [x] node 1 still signed in after logging into the other nodes (one cookie jar)
- [x] node 2 still signed in after logging into the other nodes (one cookie jar)
- [x] node 3 still signed in after logging into the other nodes (one cookie jar)
- [x] each node uses its own session cookie
- [x] wrong password rejected with a clear message
- [x] API token for bank 1 is scoped to bank 1
- [x] API token for bank 2 is scoped to bank 2
- [x] API token for bank 3 is scoped to bank 3
- [x] another bank's inventory is redacted (no physical quantity)
- [x] bank 1 edit applies only to bank 1

### 3. Update at one bank is visible, accurately, from another: PASS

- [x] node 2 form update accepted
- [x] central DB holds the new quantity
- [x] bank 1 sees bank 2 availability equal to bank 2's own surplus
- [x] node 1 page shows updated Northside Apples availability

### 4. Exchange from request through receipt across nodes: PASS

- [x] node 1 request sent
- [x] approve via node 2
- [x] ship via node 2
- [x] receive via node 1
- [x] transfer status is received
- [x] source decreased by 6
- [x] destination increased by 6
- [x] shipped batches equal transfer quantity
- [x] total Rice across banks conserved
- [x] database invariants pass after the exchange

### 5. One node disconnects: PASS

- [x] node 3 process stopped -> central reports it offline
- [x] nodes 1 and 2 still online
- [x] node 1 keeps working while node 3 is down
- [x] central API still accepts requests addressed to the offline bank
- [x] node 1 simulated disconnect enabled
- [x] node 1 health reports simulated_offline (503)
- [x] node 1 shows its offline page with cached snapshot (503)

### 6. Supported offline actions are queued safely: PASS

- [x] stock adjustment queued locally
- [x] second change to the same item refused (one pending change per item)
- [x] independent receipt queued locally
- [x] queue file holds 1 adjustment + 1 receipt
- [x] no token or password stored in the node queue
- [x] central database unchanged while offline

### 7. Reconnect and synchronize: PASS

- [x] node 1 reconnected
- [x] adjustment synchronized
- [x] receipt synchronized
- [x] central quantities include exactly the offline changes
- [x] re-running sync applies nothing twice
- [x] node 3 restarted and reported online again
- [x] restarted node 3 asks for a fresh login (new process secret)
- [x] node 3 login works after restart

### 8. Stale updates and synchronization conflicts: PASS

- [x] helper item created
- [x] 3 changes queued offline
- [x] concurrent central edit applied
- [x] helper item removed centrally
- [x] stale adjustment detected as a conflict, not applied
- [x] a permanently invalid queued change does not block later ones (M2)
- [x] stale change kept for operator review with a reason
- [x] rejected change kept for review with the API reason
- [x] valid change synced
- [x] second adjustment with the same old version gets 409

### 9. Idempotent additive operations synchronize safely: PASS

- [x] first receipt merged (201), retries report duplicate
- [x] quantity added exactly once
- [x] same key with different data refused (409)
- [x] independent receipt merges across a version change
- [x] replayed adjustment is a no-op duplicate
- [x] 30 concurrent replays of 10 receipts add exactly 10 units

### 10. Simultaneous requests for the same supplies: PASS

- [x] bank 2 surplus set to 10
- [x] both competing requests recorded (201)
- [x] exactly one simultaneous approval succeeds, the other gets 409
- [x] reserved stock equals the one approved transfer
- [x] surplus never goes negative
- [x] 10 parallel approvals of 4 units against 25 available: exactly 6 succeed
- [x] no over-reservation
- [x] invariants pass after the race

### 11. Duplicate API retries do not duplicate transfers: PASS

- [x] 3 retries of one request return one exchange id
- [x] 8 concurrent retries still one exchange
- [x] database has exactly one row for the key
- [x] same key with different details refused
- [x] double-submitted node form creates one transfer
- [x] retried approve/ship are refused, stock moved once

### 12. Central API unavailable: PASS

- [x] central API really unreachable
- [x] node 1 shows offline page (503) with a clear message
- [x] offline page still shows the cached snapshot
- [x] node process itself stays healthy
- [x] change queued during the real outage
- [x] sync during outage fails safely with a reason, nothing lost
- [x] login during outage gives a clear error
- [x] after API restart node 1 session still valid (tokens are stored centrally)
- [x] queued change synchronized after recovery
- [x] central quantity includes the outage change once

### 13. Failed operations give meaningful errors and recover: PASS

- [x] over-request: 4xx with a readable message
- [x] wrong bank approves: 4xx with a readable message
- [x] malformed JSON: 4xx with a readable message
- [x] unknown item: 4xx with a readable message
- [x] expired token: 4xx with a readable message
- [x] staff calls admin API: 4xx with a readable message
- [x] node shows API error as a flash message
- [x] failed operations audited centrally
- [x] no HTTP 5xx recorded by the central API
- [x] valid operation succeeds right after the failures
- [x] database invariants still pass

### 14. Controlled load with 5, 10 and 20 banks: PASS

- [x] 5 banks: 100 HTTP requests, 0 errors, quantities exact
- [x] 10 banks: 200 HTTP requests, 0 errors, quantities exact
- [x] 20 banks: 400 HTTP requests, 0 errors, quantities exact
- [x] integrity and invariants pass after load
- [x] benchmark.py SQLite cases (5/10/20) pass

### 15. Backup and isolated restore checks: PASS

- [x] online backup created and verified while 4 services run
- [x] isolated restore verification passed
- [x] live database not overwritten
- [x] restore_database.py restores to a NEW file and verifies it
- [x] restore_database.py refuses to overwrite the live database
- [x] isolated test lab 8/8 via the running API

### 16. Node health, trace IDs, status endpoints and event logs: PASS

- [x] node 1 health ok
- [x] node 2 health ok
- [x] node 3 health ok
- [x] system status: database healthy
- [x] system status: 3 nodes online (simulated banks 4-20 have no node process)
- [x] X-Request-ID echoed by the API
- [x] trace lookup finds the request
- [x] bank 1 cannot see bank 2 trace (bank-scoped)
- [x] node 3 local event ID found in the central trace log
- [x] event log endpoint returns recent events
- [x] node page lists its own request IDs

### 17. Only administrators can simulate an outage; recovery works: PASS

- [x] node 1 starts online
- [x] staff outage attempt refused with 403
- [x] state unchanged after staff attempt
- [x] viewer outage attempt refused with 403
- [x] logged-out outage attempt refused with 401
- [x] state unchanged after refused attempts
- [x] administrator switches the simulated outage on
- [x] staff see the offline page without a restore button
- [x] staff cannot end the outage either (403, still offline)
- [x] administrator restores the connection
- [x] staff dashboard works again after recovery
- [x] refused attempts are logged on the node
- [x] successful outage changes are logged with the admin name

### 18. Each bank searches its inventory independently; no cross-bank edits: PASS

- [x] bank 1 searches its own inventory
- [x] node 1 search page loads
- [x] bank 2 searches its own inventory
- [x] node 2 search page loads
- [x] bank 3 searches its own inventory
- [x] node 3 search page loads
- [x] bank 1 searches bank 2 separately and sees only public fields
- [x] bank 1 cannot change bank 2's inventory through request parameters

## Load measurements (scenario 14)

One concurrent client per bank, sending requests through the central API:

| Banks | Requests | Errors | Throughput | Median | 95th percentile | Max | Final quantities correct |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 5 | 100 | 0 | 35.9 req/s | 98 ms | 323 ms | 414 ms | yes |
| 10 | 200 | 0 | 35.2 req/s | 187 ms | 643 ms | 2700 ms | yes |
| 20 | 400 | 0 | 36.4 req/s | 280 ms | 1537 ms | 4823 ms | yes |

Throughput stayed about the same as clients were added, while latency grew. All requests go through one API process and one SQLite write lock, so this is what we expected. Banks 4-20 in this scenario exist only in the database. They have no node process, so this measures the central API, not 20 separate machines. The numbers vary between runs on the same machine (earlier runs measured 34-44 req/s).

## What this does and does not show

It shows that, on one computer with real separate processes:

- nodes, the API and the database work together over HTTP and JSON;
- one node failing does not affect the others;
- stopping the central API is handled without crashes or lost queued changes;
- retries, duplicate submissions and simultaneous approvals never move stock twice or over-reserve it;
- stale offline changes are detected and held for review;
- database integrity checks pass after every scenario.

It does **not** show:

- behavior across separate computers or a real network (all processes used 127.0.0.1);
- that the central API is fault tolerant. It is still a single point of failure (see `ARCHITECTURE.md`, section 10);
- the Docker Compose setup running (see `FINAL_TEST_RESULTS.md`).

