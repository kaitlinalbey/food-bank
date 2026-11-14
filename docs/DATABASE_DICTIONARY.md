# SQLite database dictionary

This dictionary describes the schema created by `database.initialize()` (which also runs `phase3_schema.py`) on a new database. It has 28 tables. Abbreviations: **PK** = primary key, **FK** = declared foreign key.

The simplified relationship diagram is in `diagrams/core_data_model.png`.

| Table | Purpose / example records | Columns | Declared FK references |
| --- | --- | ---: | --- |
| `activity` | Human-readable operations log | 7 | banks, exchanges, users |
| `audit_events` | Audit records | 7 | banks, users |
| `db_backups` | Register of verified backups made from the admin page (file names only) | 12 | users |
| `auth_tokens` | API bearer authentication tokens | 3 | users |
| `banks` | Participating food banks | 6 | — |
| `deliveries` | Assigned deliveries | 7 | drivers, exchanges, vehicles |
| `discrepancies` | Shipment count/damage issues | 9 | exchanges |
| `distributed_events` | API request correlation traces | 9 | banks |
| `donations` | Received donations | 8 | banks, items, lots |
| `donors` | Donor directory | 5 | banks |
| `drivers` | Drivers | 4 | banks |
| `exchanges` | Transfer requests and state | 12 | banks, items |
| `inventory` | Current bank-by-item quantities and targets | 7 | banks, items |
| `items` | Food product definitions | 6 | — |
| `login_attempts` | Login throttling | 4 | — |
| `lots` | Food batches, expiry, recall flags | 9 | banks, items |
| `notifications` | Bank notifications | 6 | banks, exchanges |
| `offline_receipts` | Deduplicated offline donation receipts | 9 | banks, items |
| `recalls` | Lot recalls | 8 | items, users |
| `reconciliations` | Physical count reviews | 12 | banks, items, users |
| `shipment_lots` | Lot movements by exchange | 5 | exchanges, lots |
| `stock_events` | Stock movement history | 6 | banks, items |
| `storage_zones` | Bank capacity by storage type | 3 | banks |
| `sync_operations` | Offline sync replay protection | 7 | banks, items |
| `user_role_overrides` | Extra user permissions | 2 | users |
| `users` | Bank staff and administrators | 6 | banks |
| `vehicles` | Delivery vehicles | 6 | banks |
| `waste` | Discarded food | 7 | banks, items, lots |

## Columns by table

### `activity`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `INTEGER` | PK |
| `bank_id` | `INTEGER` | FK, required |
| `actor_user_id` | `INTEGER` | FK |
| `action` | `TEXT` | required |
| `details` | `TEXT` | required |
| `exchange_id` | `INTEGER` | FK |
| `created_at` | `TEXT` | required, default CURRENT_TIMESTAMP |

### `audit_events`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `INTEGER` | PK |
| `bank_id` | `INTEGER` | FK |
| `actor_user_id` | `INTEGER` | FK |
| `operation` | `TEXT` | required |
| `result` | `TEXT` | required |
| `detail` | `TEXT` | required |
| `created_at` | `TEXT` | required, default CURRENT_TIMESTAMP |

### `auth_tokens`

| Column | Type | Notes |
| --- | --- | --- |
| `token_hash` | `TEXT` | PK |
| `user_id` | `INTEGER` | FK, required |
| `expires_at` | `INTEGER` | required |

### `banks`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `INTEGER` | PK |
| `name` | `TEXT` | required |
| `area` | `TEXT` | required |
| `latitude` | `REAL` | required |
| `longitude` | `REAL` | required |
| `password_hash` | `TEXT` | required, default '' |

### `db_backups`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `INTEGER` | PK |
| `file_name` | `TEXT` | required, unique |
| `created_at` | `TEXT` | required, default CURRENT_TIMESTAMP |
| `created_by` | `INTEGER` | FK |
| `size_bytes` | `INTEGER` | required |
| `sha256` | `TEXT` | required |
| `integrity` | `TEXT` | required |
| `verified` | `INTEGER` | required, default 0 |
| `table_counts` | `TEXT` | required (JSON) |
| `note` | `TEXT` | required, default '' |
| `restore_checked_at` | `TEXT` | — |
| `restore_result` | `TEXT` | — (JSON) |

### `deliveries`

| Column | Type | Notes |
| --- | --- | --- |
| `exchange_id` | `INTEGER` | PK, FK |
| `driver_id` | `INTEGER` | FK, required |
| `vehicle_id` | `INTEGER` | FK, required |
| `assigned_at` | `TEXT` | required, default CURRENT_TIMESTAMP |
| `picked_up_at` | `TEXT` | — |
| `delivered_at` | `TEXT` | — |
| `proof_note` | `TEXT` | required, default '' |

### `discrepancies`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `INTEGER` | PK |
| `exchange_id` | `INTEGER` | FK, required |
| `expected_qty` | `INTEGER` | required |
| `received_qty` | `INTEGER` | required |
| `missing_qty` | `INTEGER` | required |
| `damaged_qty` | `INTEGER` | required |
| `notes` | `TEXT` | required |
| `status` | `TEXT` | required, default 'open' |
| `recorded_at` | `TEXT` | required, default CURRENT_TIMESTAMP |

### `distributed_events`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `INTEGER` | PK |
| `bank_id` | `INTEGER` | FK |
| `origin` | `TEXT` | required |
| `method` | `TEXT` | required |
| `path` | `TEXT` | required |
| `status_code` | `INTEGER` | required |
| `correlation_id` | `TEXT` | required |
| `latency_ms` | `REAL` | required |
| `created_at` | `TEXT` | required, default CURRENT_TIMESTAMP |

### `donations`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `INTEGER` | PK |
| `bank_id` | `INTEGER` | FK, required |
| `item_id` | `INTEGER` | FK, required |
| `donor` | `TEXT` | required |
| `quantity` | `INTEGER` | required |
| `lot_id` | `INTEGER` | FK |
| `created_at` | `TEXT` | required, default CURRENT_TIMESTAMP |
| `donor_id` | `INTEGER` | — |

### `donors`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `INTEGER` | PK |
| `bank_id` | `INTEGER` | FK, required |
| `name` | `TEXT` | required |
| `notes` | `TEXT` | required, default '' |
| `created_at` | `TEXT` | required, default CURRENT_TIMESTAMP |

### `drivers`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `INTEGER` | PK |
| `bank_id` | `INTEGER` | FK, required |
| `display_name` | `TEXT` | required |
| `active` | `INTEGER` | required, default 1 |

### `exchanges`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `INTEGER` | PK |
| `source_bank_id` | `INTEGER` | FK, required |
| `destination_bank_id` | `INTEGER` | FK, required |
| `item_id` | `INTEGER` | FK, required |
| `quantity` | `INTEGER` | required |
| `status` | `TEXT` | required, default 'pending' |
| `request_key` | `TEXT` | — |
| `created_at` | `TEXT` | required, default CURRENT_TIMESTAMP |
| `updated_at` | `TEXT` | required, default CURRENT_TIMESTAMP |
| `pickup_at` | `TEXT` | — |
| `delivery_at` | `TEXT` | — |
| `carrier_notes` | `TEXT` | — |

### `inventory`

| Column | Type | Notes |
| --- | --- | --- |
| `bank_id` | `INTEGER` | PK, FK, required |
| `item_id` | `INTEGER` | PK, FK, required |
| `quantity` | `INTEGER` | required, default 0 |
| `target` | `INTEGER` | required, default 0 |
| `expiration_date` | `TEXT` | — |
| `updated_at` | `TEXT` | required, default CURRENT_TIMESTAMP |
| `version` | `INTEGER` | required, default 0 |

### `items`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `INTEGER` | PK |
| `name` | `TEXT` | required |
| `unit` | `TEXT` | required, default 'units' |
| `category` | `TEXT` | required, default 'Other' |
| `barcode` | `TEXT` | — |
| `storage_type` | `TEXT` | required, default 'ambient' |

### `login_attempts`

| Column | Type | Notes |
| --- | --- | --- |
| `bank_id` | `INTEGER` | PK, required |
| `username` | `TEXT` | PK, required |
| `attempts` | `INTEGER` | required, default 0 |
| `window_start` | `INTEGER` | required |

### `lots`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `INTEGER` | PK |
| `bank_id` | `INTEGER` | FK, required |
| `item_id` | `INTEGER` | FK, required |
| `quantity` | `INTEGER` | required |
| `expiration_date` | `TEXT` | — |
| `source` | `TEXT` | required, default 'manual' |
| `received_at` | `TEXT` | required, default CURRENT_TIMESTAMP |
| `lot_code` | `TEXT` | — |
| `recalled` | `INTEGER` | required, default 0 |

### `notifications`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `INTEGER` | PK |
| `bank_id` | `INTEGER` | FK, required |
| `message` | `TEXT` | required |
| `exchange_id` | `INTEGER` | FK |
| `is_read` | `INTEGER` | required, default 0 |
| `created_at` | `TEXT` | required, default CURRENT_TIMESTAMP |

### `offline_receipts`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `INTEGER` | PK |
| `bank_id` | `INTEGER` | FK, required |
| `op_key` | `TEXT` | required |
| `item_id` | `INTEGER` | FK, required |
| `quantity` | `INTEGER` | required |
| `donor` | `TEXT` | required |
| `lot_code` | `TEXT` | required |
| `expiration_date` | `TEXT` | — |
| `created_at` | `TEXT` | required, default CURRENT_TIMESTAMP |

### `recalls`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `INTEGER` | PK |
| `item_id` | `INTEGER` | FK, required |
| `lot_code` | `TEXT` | required |
| `reason` | `TEXT` | required |
| `status` | `TEXT` | required, default 'active' |
| `created_by` | `INTEGER` | FK |
| `created_at` | `TEXT` | required, default CURRENT_TIMESTAMP |
| `closed_at` | `TEXT` | — |

### `reconciliations`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `INTEGER` | PK |
| `bank_id` | `INTEGER` | FK, required |
| `item_id` | `INTEGER` | FK, required |
| `expected_qty` | `INTEGER` | required |
| `counted_qty` | `INTEGER` | required |
| `base_version` | `INTEGER` | required |
| `reason` | `TEXT` | required |
| `status` | `TEXT` | required, default 'pending' |
| `requested_by` | `INTEGER` | FK |
| `approved_by` | `INTEGER` | FK |
| `created_at` | `TEXT` | required, default CURRENT_TIMESTAMP |
| `completed_at` | `TEXT` | — |

### `shipment_lots`

| Column | Type | Notes |
| --- | --- | --- |
| `exchange_id` | `INTEGER` | FK, required |
| `expiration_date` | `TEXT` | — |
| `quantity` | `INTEGER` | required |
| `lot_id` | `INTEGER` | FK |
| `lot_code` | `TEXT` | — |

### `stock_events`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `INTEGER` | PK |
| `bank_id` | `INTEGER` | FK, required |
| `item_id` | `INTEGER` | FK, required |
| `delta` | `INTEGER` | required |
| `reason` | `TEXT` | required |
| `created_at` | `TEXT` | required, default CURRENT_TIMESTAMP |

### `storage_zones`

| Column | Type | Notes |
| --- | --- | --- |
| `bank_id` | `INTEGER` | PK, FK, required |
| `storage_type` | `TEXT` | PK, required |
| `capacity_units` | `INTEGER` | required |

### `sync_operations`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `INTEGER` | PK |
| `bank_id` | `INTEGER` | FK, required |
| `op_key` | `TEXT` | required |
| `item_id` | `INTEGER` | FK, required |
| `delta` | `INTEGER` | required |
| `base_version` | `INTEGER` | required |
| `created_at` | `TEXT` | required, default CURRENT_TIMESTAMP |

### `user_role_overrides`

| Column | Type | Notes |
| --- | --- | --- |
| `user_id` | `INTEGER` | PK, FK |
| `role` | `TEXT` | required |

### `users`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `INTEGER` | PK |
| `bank_id` | `INTEGER` | FK, required |
| `username` | `TEXT` | required |
| `password_hash` | `TEXT` | required |
| `role` | `TEXT` | required, default 'staff' |
| `active` | `INTEGER` | required, default 1 |

### `vehicles`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `INTEGER` | PK |
| `bank_id` | `INTEGER` | FK, required |
| `label` | `TEXT` | required |
| `capacity_units` | `INTEGER` | required |
| `refrigerated` | `INTEGER` | required, default 0 |
| `active` | `INTEGER` | required, default 1 |

### `waste`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `INTEGER` | PK |
| `bank_id` | `INTEGER` | FK, required |
| `item_id` | `INTEGER` | FK, required |
| `lot_id` | `INTEGER` | FK |
| `quantity` | `INTEGER` | required |
| `reason` | `TEXT` | required |
| `created_at` | `TEXT` | required, default CURRENT_TIMESTAMP |

## Notes

- The project uses SQLite foreign keys, checks, uniqueness rules, transactions, and application-level validation.
- The readable ER diagram shows only the central tables; see the complete schema in `database.py` and `phase3_schema.py`.
- If a later change adds tables or columns, update this file to match.
