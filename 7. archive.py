import time
from datetime import datetime, timedelta
from common.db import get_db_connection, get_cursor, release_db_connection
from common.logger import log_error, save_errors_to_db

ARCHIVE_PERIOD_DAYS = 90


def archive_old_records():
    print(f"\n🗂️ АРХИВАЦИЯ СТАРЫХ ЗАПИСЕЙ (старше {ARCHIVE_PERIOD_DAYS} дней)...")
    step_start = time.time()

    conn = get_db_connection()
    conn.autocommit = True
    cursor = get_cursor(conn)

    try:
        period_start_int = int((datetime.now() - timedelta(days=ARCHIVE_PERIOD_DAYS)).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).timestamp())

        print(f"   📅 Граница периода: {datetime.fromtimestamp(period_start_int)}")

        # 🔑 Получаем список НЕгенерируемых столбцов из метаданных таблицы
        cursor.execute('''
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = 'player_tanks_stats' 
              AND is_generated = 'NEVER'
            ORDER BY ordinal_position
        ''')
        columns_list = [row[0] for row in cursor.fetchall()]
        columns = ', '.join(columns_list)
        print(f"   📋 Негенерируемых столбцов: {len(columns_list)}")

        # Считаем общее количество записей до архивации
        cursor.execute("SELECT COUNT(*) FROM player_tanks_stats")
        total_before = cursor.fetchone()[0]
        print(f"   📊 Всего записей в таблице: {total_before:,}")

        # Считаем количество старых записей (включая NULL через COALESCE)
        cursor.execute('''
            SELECT COUNT(*) FROM player_tanks_stats
            WHERE COALESCE(last_battle_time, 0) < %s
        ''', (period_start_int,))
        old_count = cursor.fetchone()[0]
        print(f"   📊 Записей старше {ARCHIVE_PERIOD_DAYS} дней: {old_count:,}")

        if old_count == 0:
            print("   ✅ Нечего архивировать")
            return 0

        # 🔑 Создаём новую таблицу с ТОЧНОЙ структурой оригинала (включая генерируемые столбцы)
        print("   ⏳ Создание новой таблицы...")
        cursor.execute("DROP TABLE IF EXISTS player_tanks_stats_new;")
        cursor.execute("CREATE TABLE player_tanks_stats_new (LIKE player_tanks_stats INCLUDING ALL);")

        # 🔑 Копируем свежие записи (за последние 90 дней)
        # Указываем только негенерируемые столбцы — генерируемые вычислятся автоматически
        print("   ⏳ Копирование свежих записей...")
        cursor.execute(f'''
            INSERT INTO player_tanks_stats_new ({columns})
            SELECT {columns} FROM player_tanks_stats
            WHERE last_battle_time >= %s
        ''', (period_start_int,))
        fresh_count = cursor.rowcount
        print(f"   ✅ Свежих записей: {fresh_count:,}")

        # 🔑 Копируем базовые точки (последняя запись до 90 дней для каждой пары игрок-танк)
        # Указываем только негенерируемые столбцы — генерируемые вычислятся автоматически
        print("   ⏳ Копирование базовых точек...")
        cursor.execute(f'''
            INSERT INTO player_tanks_stats_new ({columns})
            SELECT DISTINCT ON (player_id, tank_id) {columns}
            FROM player_tanks_stats
            WHERE COALESCE(last_battle_time, 0) < %s
            ORDER BY player_id, tank_id, collected_date DESC
        ''', (period_start_int,))
        baseline_count = cursor.rowcount
        print(f"   ✅ Базовых точек: {baseline_count:,}")

        # 🔑 Атомарная подмена таблиц (в транзакции)
        print("   ⏳ Подмена таблиц...")
        conn.autocommit = False
        cursor.execute("BEGIN;")
        cursor.execute("ALTER TABLE player_tanks_stats RENAME TO player_tanks_stats_old;")
        cursor.execute("ALTER TABLE player_tanks_stats_new RENAME TO player_tanks_stats;")
        cursor.execute("COMMIT;")

        # Считаем экономию
        net_savings = total_before - fresh_count - baseline_count

        print(f"   📊 Было: {total_before:,} | Стало: {fresh_count + baseline_count:,} | Экономия: {net_savings:,}")

        # 🔑 Удаляем старую таблицу
        print("   🗑️ Удаление старой таблицы...")
        conn.autocommit = True
        cursor.execute("DROP TABLE player_tanks_stats_old;")

        step_elapsed = time.time() - step_start
        print(f"✅ Архивация завершена за {step_elapsed:.1f} сек ({step_elapsed / 60:.1f} мин)")

        return net_savings

    except Exception as e:
        try:
            conn.rollback()
            conn.autocommit = True
            cursor.execute("DROP TABLE IF EXISTS player_tanks_stats_new;")
            cursor.execute("DROP TABLE IF EXISTS player_tanks_stats_old;")
        except Exception as cleanup_error:
            print(f"   ⚠️ Не удалось выполнить очистку после ошибки: {cleanup_error}")
            log_error("archive", "CLEANUP_ERROR", cleanup_error.__class__.__name__,
                      str(cleanup_error), "archive_old_records")

        print(f"❌ Ошибка архивации: {e}")
        log_error("archive", "DB_ERROR", "PG_ERROR", str(e), "archive_old_records")
        return None
    finally:
        conn.autocommit = False
        release_db_connection(conn)


def main():
    start_time = time.time()
    print("=" * 70)
    print(" АРХИВАЦИЯ СТАРЫХ ЗАПИСЕЙ")
    print("=" * 70)

    try:
        archived_count = archive_old_records()

        if archived_count is None:
            print("❌ Архивация не удалась.")
            return

        if archived_count == 0:
            print("✅ Нечего архивировать, работа завершена.")
            return

        elapsed = time.time() - start_time
        print(f"\n✅ РАБОТА ЗАВЕРШЕНА за {elapsed:.1f} сек ({elapsed / 60:.1f} мин)")

    finally:
        print("💾 СОХРАНЕНИЕ ЛОГА ОШИБОК")
        save_errors_to_db()


if __name__ == "__main__":
    main()