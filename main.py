import subprocess
import sys
import time
from datetime import datetime
from common.memory_monitor import memory_monitor
from common.db import close_all_connections, get_db_connection, get_cursor, release_db_connection

SCRIPTS = [
    "1. update clans.py",
    "2. update players.py",
    "3. update players_tanks_stats.py",
    "4. DB analyze.py",
    "5. update tanks_aggregated_stats.py",
    "6. update players_aggregated_stats.py"
    ]

def main():
    start_time = time.time()
    today_int = int(datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    conn = get_db_connection()
    cursor = get_cursor(conn)
    try:
        cursor.execute('''
            INSERT INTO date_reference (date_int, date_readable)
            VALUES (%s, TO_TIMESTAMP(%s)::DATE)
            ON CONFLICT (date_int) DO NOTHING
        ''', (today_int, today_int))
        conn.commit()
    finally:
        release_db_connection(conn)
    print("\n" + "=" * 70)
    print(" ПОЛНЫЙ ЦИКЛ ОБНОВЛЕНИЯ ДАННЫХ")
    print(f" Запуск: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    memory_monitor.start()

    try:
        for i, script in enumerate(SCRIPTS, 1):
            print(f"\n{'=' * 70}")
            print(f" ШАГ {i}/{len(SCRIPTS)}: {script}")
            print(f"{'=' * 70}")

            memory_monitor.print_memory_status()

            result = subprocess.run([sys.executable, script])

            if result.returncode != 0:
                print(f"\n❌ Ошибка в скрипте {script} (код: {result.returncode})")
                print("⛔ Прерываем выполнение.")
                break

    finally:
        memory_monitor.stop()
        memory_monitor.print_memory_status()
        close_all_connections()

    elapsed = time.time() - start_time
    print(f"\n{'=' * 70}")
    print(" ✅ ПОЛНЫЙ ЦИКЛ ЗАВЕРШЁН!")
    print(f"{'=' * 70}")
    print(f"⏱️  Общее время: {elapsed:.0f} сек ({elapsed / 3600:.1f} ч)")


if __name__ == "__main__":
    main()