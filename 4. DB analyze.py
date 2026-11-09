import time
from common.db import get_db_connection, get_cursor, release_db_connection
from common.logger import log_error, save_errors_to_db

ERROR_LOG_RETENTION_SECONDS = 86400  # 1 день


def clean_duplicates_in_players():
    print("\n🔍 Очистка дубликатов в players...")
    print("   Критерии: player_id + last_battle_time")

    conn = get_db_connection()
    cursor = get_cursor(conn)
    try:
        cursor.execute("SET work_mem = '512MB';")
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
        cursor.execute("SET work_mem = '2GB';")
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


def clean_old_error_logs():
    print("\n🗑️ Очистка старых записей в error_log...")

    conn = get_db_connection()
    cursor = get_cursor(conn)

    try:
        one_day_ago = int(time.time()) - ERROR_LOG_RETENTION_SECONDS

        cursor.execute('''
            DELETE FROM error_log
            WHERE timestamp IS NULL
               OR timestamp < %s
        ''', (one_day_ago,))

        deleted = cursor.rowcount
        conn.commit()

        if deleted > 0:
            print(f"   🗑️ Удалено записей: {deleted:,}")
        else:
            print("   ✅ Нечего удалять")

        return deleted

    except Exception as e:
        conn.rollback()
        print(f"   ❌ Ошибка: {e}")
        log_error("clean_error_log", "DB_ERROR", "PG_ERROR", str(e), "error_log")
        return 0

    finally:
        release_db_connection(conn)


def terminate_idle_connections():
    print("\n🔌 Проверка неактивных клиентских соединений...")

    conn = get_db_connection()
    conn.autocommit = True
    cursor = get_cursor(conn)

    try:
        cursor.execute('''
            SELECT 
                pid,
                usename,
                application_name,
                client_addr,
                state,
                state_change,
                LEFT(query, 120) AS query
            FROM pg_stat_activity
            WHERE datname = current_database()
              AND pid != pg_backend_pid()
              AND backend_type = 'client backend'
              AND state IN ('idle', 'idle in transaction')
            ORDER BY state_change
        ''')

        idle_connections = cursor.fetchall()

        if not idle_connections:
            print("   ✅ Нет неактивных соединений")
            return 0

        print(f"   ⚠️ Найдено {len(idle_connections)} неактивных соединений:")

        for row in idle_connections:
            print(
                f"      PID {row['pid']}: "
                f"user={row['usename']}, "
                f"app={row['application_name']}, "
                f"addr={row['client_addr']}, "
                f"state={row['state']}, "
                f"state_change={row['state_change']}"
            )
            if row['query']:
                print(f"         {row['query']}")

        terminated = 0

        for row in idle_connections:
            pid = row['pid']

            try:
                cursor.execute("SELECT pg_terminate_backend(%s) AS terminated;", (pid,))
                result = cursor.fetchone()

                if result and result['terminated']:
                    terminated += 1
                    print(f"   🔌 Разорвано соединение PID {pid}")
                else:
                    print(f"   ⚠️ Соединение PID {pid} уже завершилось или не было разорвано")

            except Exception as e:
                print(f"   ⚠️ Не удалось разорвать PID {pid}: {e}")
                log_error("terminate_connections", "DB_ERROR", "PG_ERROR", str(e), str(pid))

        return terminated

    except Exception as e:
        print(f"   ❌ Ошибка проверки соединений: {e}")
        log_error("terminate_connections", "DB_ERROR", "PG_ERROR", str(e), "idle_connections")
        return 0

    finally:
        conn.autocommit = False
        release_db_connection(conn)


def vacuum_analyze():
    print("\n🧹 Выполняю VACUUM ANALYZE...")
    vacuum_start = time.time()
    conn = get_db_connection()
    conn.autocommit = True
    cursor = get_cursor(conn)
    try:
        cursor.execute("SET maintenance_work_mem = '1GB';")
        cursor.execute('''
            SELECT COALESCE(SUM(n_dead_tup), 0) AS total_dead
            FROM pg_stat_user_tables
        ''')
        dead_before = cursor.fetchone()['total_dead']

        cursor.execute("VACUUM ANALYZE;")
        cursor.execute('''
            SELECT COALESCE(SUM(n_dead_tup), 0) AS total_dead
            FROM pg_stat_user_tables
        ''')
        dead_after = cursor.fetchone()['total_dead']
        vacuum_elapsed = time.time() - vacuum_start
        cleaned = dead_before - dead_after
        print(f"   Мёртвых строк: {dead_before:,} → {dead_after:,} (очищено {cleaned:,})")
        print(f"   ✅ VACUUM ANALYZE завершён за {vacuum_elapsed:.1f} сек")

    except Exception as e:
        print(f"   ❌ Ошибка VACUUM: {e}")
        log_error("vacuum_analyze", "DB_ERROR", "PG_ERROR", str(e), "database")
    finally:
        conn.autocommit = False
        release_db_connection(conn)


def main():
    start_time = time.time()
    print(" ОЧИСТКА ДУБЛИКАТОВ И ОБСЛУЖИВАНИЕ БД")

    deleted_players = clean_duplicates_in_players()
    deleted_tanks = clean_duplicates_in_player_tanks_stats()
    deleted_errors = clean_old_error_logs()
    terminate_idle_connections()
    vacuum_analyze()

    elapsed = time.time() - start_time
    print(" 📊 ИТОГОВЫЙ ОТЧЁТ")
    print(f"   Удалено из players:            {deleted_players:,}")
    print(f"   Удалено из player_tanks_stats: {deleted_tanks:,}")
    print(f"   Удалено из error_log:          {deleted_errors:,}")
    print(f"   ⏱️ Общее время:                 {elapsed:.1f} сек ({elapsed / 60:.1f} мин)")
    print("\n💾 СОХРАНЕНИЕ ЛОГА ОШИБОК")
    save_errors_to_db()


if __name__ == "__main__":
    main()