import sys
from auth import hash_password
import db

DEMO_PASSWORD = "password123"

BANKS = [
    (1, "North Food Bank", "Little Rock"),
    (2, "East Food Bank", "Little Rock"),
    (3, "South Food Bank", "Little Rock"),
]

USERS = [
    ("admin", 1, "admin"),
    ("staff1", 1, "staff"),
    ("staff2", 2, "staff"),
    ("staff3", 3, "staff"),
]

ITEMS = [
    (1, "Rice", "kg", "Grains"),
    (2, "Beans", "kg", "Legumes"),
    (3, "Canned Tuna", "can", "Protein"),
    (4, "Canned Corn", "can", "Vegetables"),
    (5, "Canned Tomatoes", "can", "Vegetables"),
]

INVENTORY = [
    # bank_id, item_id, quantity, min_level, max_level
    (1, 1, 210, 20, 200),
    (1, 2, 50, 10, 100),
    (1, 3, 30, 5, 60),
    (2, 1, 14, 15, 150),
    (2, 4, 40, 10, 80),
    (3, 2, 140, 10, 120),
    (3, 5, 2, 5, 50),
]

def seed(db_path):
    db.init_db(db_path)
    conn = db.connect(db_path)

    try:
        if conn.execute("SELECT COUNT(*) FROM banks").fetchone()[0] > 0:
            print(f"Database {db_path} already seeded.")
            return
        
        with conn:
            conn.executemany(
                "INSERT INTO banks (id, name, city) VALUES (?, ?, ?)", BANKS
            )
            conn.executemany(
                "INSERT INTO users (username, bank_id, password_hash, role) VALUES (?, ?, ?, ?)",
                [(u, b, hash_password(DEMO_PASSWORD), role) for u, b, role in USERS],
            )
            conn.executemany("INSERT INTO items (id, name, unit, category) VALUES (?, ?, ?, ?)", ITEMS)
            conn.executemany(
                "INSERT INTO inventory (bank_id, item_id, quantity, min_level, max_level) VALUES (?, ?, ?, ?, ?)",
                INVENTORY,
            )
        print(f"Seeded {db_path}")
    finally:
        conn.close()


if __name__ == '__main__':
    target = sys.argv[1] if len(sys.argv) > 1 else db.DEFAULT_DB
    seed(target)
