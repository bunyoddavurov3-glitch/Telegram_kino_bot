import json
import sqlite3
import os

DB_FILE = "database.db"
JSON_FILE = "kino_db.json"

def restore_if_needed():
    if os.path.exists(DB_FILE):
        print("DB mavjud, restore kerak emas")
        return

    if not os.path.exists(JSON_FILE):
        print("JSON topilmadi, restore imkonsiz")
        return

    print("JSON dan DB tiklanmoqda...")

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS movies (
        id INTEGER PRIMARY KEY,
        code TEXT,
        title TEXT,
        file_id TEXT
    )
    """)

    with open(JSON_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    for movie in data:
        cursor.execute(
            "INSERT INTO movies (code, title, file_id) VALUES (?, ?, ?)",
            (movie["code"], movie["title"], movie["file_id"])
        )

    conn.commit()
    conn.close()

    print("Restore yakunlandi")
