import os
import sqlite3

DB_FILE = "test_tanks_blitz.db"

# 1. Размеры файлов
print("=" * 50)
print("РАЗМЕРЫ ФАЙЛОВ:")
print("=" * 50)
for f in os.listdir('.'):
    if f.startswith(DB_FILE):
        size_mb = os.path.getsize(f) / 1024 / 1024
        print(f"  {f}: {size_mb:.2f} MB")

# 2. Информация о БД
conn = sqlite3.connect(DB_FILE)
print("\n" + "=" * 50)
print("ИНФОРМАЦИЯ О БД:")
print("=" * 50)

page_size = conn.execute("PRAGMA page_size").fetchone()[0]
page_count = conn.execute("PRAGMA page_count").fetchone()[0]
freelist = conn.execute("PRAGMA freelist_count").fetchone()[0]
journal = conn.execute("PRAGMA journal_mode").fetchone()[0]

used_mb = (page_count - freelist) * page_size / 1024 / 1024
free_mb = freelist * page_size / 1024 / 1024

print(f"  Режим журнала: {journal}")
print(f"  Размер страницы: {page_size} байт")
print(f"  Всего страниц: {page_count}")
print(f"  Свободных страниц: {freelist}")
print(f"  Использовано: {used_mb:.2f} MB")
print(f"  Свободно (дыры): {free_mb:.2f} MB")

# 3. Количество записей
print("\n" + "=" * 50)
print("КОЛИЧЕСТВО ЗАПИСЕЙ:")
print("=" * 50)
for table in ['players', 'clans', 'clan_members', 'player_tanks_stats']:
    try:
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  {table}: {count:,}")
    except:
        print(f"  {table}: таблица не существует")

conn.close()