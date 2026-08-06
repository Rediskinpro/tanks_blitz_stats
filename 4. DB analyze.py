import time
from common.db import get_db_connection, get_cursor, release_db_connection
from common.logger import log_error, save_errors_to_db, export_errors_to_excel


def clean_duplicates_in_players():
    print("\n🔍 Очистка дубликатов в players...")
    print("   Критерии: player_id + last_battle_time")

    conn = get_db_connection()
    cursor = get_cursor(conn)
    try:
        cursor.execute('''
            DELETE FROM players a
            USING players b
            WHERE a.player_id = b.player_id
              AND a.last_battle_time IS NOT DISTINCT FROM b.last_battle_time
              AND a.collected_date > b.collected_date
        ''')

        deleted = cursor.rowcount
        conn.commit()
        if deleted > 0:
            print(f"   🗑️ Удалено дубликатов: {deleted:,}")
        else:
            print("   ✅ Дубликатов не найдено")
        return deleted

    except Exception as e:
        conn.rollback()
        print(f"   ❌ Ошибка: {e}")
        log_error("check_duplicates", "DB_ERROR", "PG_ERROR", str(e), "players")
        return 0
    finally:
        release_db_connection(conn)


def clean_duplicates_in_player_tanks_stats():
    print("\n🔍 Очистка дубликатов в player_tanks_stats...")
    print("   Критерии: player_id + tank_id + last_battle_time")

    conn = get_db_connection()
    cursor = get_cursor(conn)
    try:
        cursor.execute('''
            DELETE FROM player_tanks_stats a
            USING player_tanks_stats b
            WHERE a.player_id = b.player_id
              AND a.tank_id = b.tank_id
              AND a.last_battle_time IS NOT DISTINCT FROM b.last_battle_time
              AND a.collected_date > b.collected_date
        ''')

        deleted = cursor.rowcount
        conn.commit()
        if deleted > 0:
            print(f"   🗑️ Удалено дубликатов: {deleted:,}")
        else:
            print("   ✅ Дубликатов не найдено")
        return deleted

    except Exception as e:
        conn.rollback()
        print(f"   ❌ Ошибка: {e}")
        log_error("check_duplicates", "DB_ERROR", "PG_ERROR", str(e), "player_tanks_stats")
        return 0
    finally:
        release_db_connection(conn)


def vacuum_analyze():
    print("\n🧹 Выполняю VACUUM ANALYZE...")
    vacuum_start = time.time()
    conn = get_db_connection()
    conn.autocommit = True
    cursor = conn.cursor()
    try:
        cursor.execute('''
            SELECT COALESCE(SUM(n_dead_tup), 0) as total_dead
            FROM pg_stat_user_tables
        ''')
        dead_before = cursor.fetchone()[0]
        cursor.execute("VACUUM ANALYZE;")
        cursor.execute('''
            SELECT COALESCE(SUM(n_dead_tup), 0) as total_dead
            FROM pg_stat_user_tables
        ''')
        dead_after = cursor.fetchone()[0]
        vacuum_elapsed = time.time() - vacuum_start
        cleaned = dead_before - dead_after
        print(f"   Мёртвых строк: {dead_before:,} → {dead_after:,} (очищено {cleaned:,})")
        print(f"   ✅ VACUUM ANALYZE завершён за {vacuum_elapsed:.1f} сек")

    except Exception as e:
        print(f"   ❌ Ошибка VACUUM: {e}")
        log_error("vacuum_analyze", "DB_ERROR", "PG_ERROR", str(e), "database")
    finally:
        conn.autocommit = False
        cursor.close()
        release_db_connection(conn)


def main():
    start_time = time.time()
    print("\n" + "=" * 70)
    print(" ОЧИСТКА ДУБЛИКАТОВ И ОБСЛУЖИВАНИЕ БД")
    print("=" * 70)

    deleted_players = clean_duplicates_in_players()
    deleted_tanks = clean_duplicates_in_player_tanks_stats()
    vacuum_analyze()

    elapsed = time.time() - start_time
    print("\n" + "=" * 70)
    print("📊 ИТОГОВЫЙ ОТЧЁТ")
    print("=" * 70)
    print(f"   Удалено из players:            {deleted_players:,}")
    print(f"   Удалено из player_tanks_stats: {deleted_tanks:,}")
    print(f"   ⏱️ Общее время:                 {elapsed:.1f} сек ({elapsed / 60:.1f} мин)")

    print("\n" + "=" * 70)
    print("💾 СОХРАНЕНИЕ ЛОГА ОШИБОК")
    print("=" * 70)
    save_errors_to_db()
    export_errors_to_excel()


if __name__ == "__main__":
    main()