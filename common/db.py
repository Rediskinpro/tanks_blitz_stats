import sqlite3
import threading
from common.config import DB_FILE

db_lock = threading.Lock()

def get_db_connection():
    conn = sqlite3.connect(DB_FILE, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn