
import sqlite3, json
try:
    conn = sqlite3.connect("/app/local_assistant_db/chroma.sqlite3")
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type=\x27table\x27")
    tables = [t[0] for t in cursor.fetchall()]
    user_ids = set()
    if "embedding_metadata" in tables:
        cursor.execute("PRAGMA table_info(embedding_metadata)")
        cols = [c[1] for c in cursor.fetchall()]
        meta_col = "metadata" if "metadata" in cols else (cols[1] if len(cols) > 1 else cols[0])
        cursor.execute(f"SELECT {meta_col} FROM embedding_metadata")
        for r in cursor.fetchall():
            if r and r[0]:
                try:
                    data = json.loads(r[0])
                    if "user_id" in data: user_ids.add(data["user_id"])
                except: pass
    if not user_ids:
        for table in tables:
            try:
                cursor.execute(f"PRAGMA table_info({table})")
                cols = [c[1] for c in cursor.fetchall()]
                for col in cols:
                    cursor.execute(f"SELECT {col} FROM {table} WHERE {col} LIKE \x27%user_id%\x27")
                    for r in cursor.fetchall():
                        if r and r[0]:
                            try:
                                data = json.loads(r[0])
                                if "user_id" in data: user_ids.add(data["user_id"])
                            except: pass
            except: pass
    print("\n=== MIA BOT STATISTICS ===")
    print(f"TOTAL UNIQUE USERS: {len(user_ids)}")
    print(f"TELEGRAM ID LIST: {list(user_ids)}\n")
    conn.close()
except Exception as e:
    print("Error:", e)

