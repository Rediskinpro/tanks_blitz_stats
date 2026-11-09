import re
import time
import hashlib
from datetime import datetime, timedelta
from common.db import get_db_connection, get_cursor, release_db_connection
from common.logger import log_error, save_errors_to_db

ARCHIVE_PERIOD_DAYS = 90


def q_ident(name: str):
    """Безопасное экранирование SQL-имени."""
    return '"' + name.replace('"', '""') + '"'


def make_temp_name(name: str, suffix: str):
    """
    Делает временное имя constraint/index с суффиксом.
    PostgreSQL ограничивает имена 63 символами.
    """
    if len(name) + len(suffix) <= 63:
        return name + suffix

    name_hash = hashlib.md5(name.encode('utf-8')).hexdigest()[:8]
    max_base_len = 63 - len(suffix) - 9
    return name[:max_base_len] + "_" + name_hash + suffix


def archive_old_records():
    print(f"\n🗂️ АРХИВАЦИЯ СТАРЫХ ЗАПИСЕЙ (старше {ARCHIVE_PERIOD_DAYS} дней)...")
    step_start = time.time()

    conn = get_db_connection()
    conn.autocommit = True
    cursor = get_cursor(conn)

    try:
        print("   🧹 Проверка наличия старых временных таблиц...")
        cursor.execute("DROP TABLE IF EXISTS player_tanks_stats_new;")
        cursor.execute("DROP TABLE IF EXISTS player_tanks_stats_old;")

        period_start_int = int((datetime.now() - timedelta(days=ARCHIVE_PERIOD_DAYS)).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).timestamp())

        print(f"   📅 Граница периода: {datetime.fromtimestamp(period_start_int)}")

        # Настройки памяти именно для этой сессии
        cursor.execute("SET work_mem = '512MB';")
        cursor.execute("SET maintenance_work_mem = '2GB';")
        cursor.execute("SET hash_mem_multiplier = 2.0;")
        cursor.execute("SET max_parallel_workers_per_gather = 4;")

        # Получаем список НЕгенерируемых столбцов
        cursor.execute('''
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'player_tanks_stats'
              AND is_generated = 'NEVER'
            ORDER BY ordinal_position
        ''')
        columns_list = [row[0] for row in cursor.fetchall()]
        columns = ', '.join(q_ident(col) for col in columns_list)
        print(f"   📋 Негенерируемых столбцов: {len(columns_list)}")

        # Получаем только те constraints, которые надо создавать вручную.
        # NOT NULL сюда НЕ включаем — он уже копируется через LIKE.
        cursor.execute('''
            SELECT conname, contype, pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE conrelid = 'public.player_tanks_stats'::regclass
              AND contype IN ('p', 'u', 'f', 'c', 'x')
            ORDER BY conname
        ''')
        constraints = cursor.fetchall()
        print(f"   🔗 Пересоздаваемых constraints: {len(constraints)}")

        # Получаем обычные индексы, не принадлежащие constraints
        cursor.execute('''
            SELECT i.relname AS indexname, pg_get_indexdef(i.oid) AS indexdef
            FROM pg_index ix
            JOIN pg_class i ON i.oid = ix.indexrelid
            JOIN pg_class t ON t.oid = ix.indrelid
            JOIN pg_namespace n ON n.oid = t.relnamespace
            WHERE n.nspname = 'public'
              AND t.relname = 'player_tanks_stats'
              AND NOT EXISTS (
                  SELECT 1
                  FROM pg_constraint c
                  WHERE c.conindid = ix.indexrelid
              )
            ORDER BY i.relname
        ''')
        indexes = cursor.fetchall()
        print(f"   📇 Обычных индексов: {len(indexes)}")

        cursor.execute("SELECT COUNT(*) FROM player_tanks_stats")
        total_before = cursor.fetchone()[0]
        print(f"   📊 Всего записей в таблице: {total_before:,}")

        cursor.execute('''
            SELECT COUNT(*) FROM player_tanks_stats
            WHERE last_battle_time >= %s
        ''', (period_start_int,))
        fresh_before = cursor.fetchone()[0]
        print(f"   📊 Свежих записей: {fresh_before:,}")

        cursor.execute('''
            SELECT COUNT(*) FROM player_tanks_stats
            WHERE COALESCE(last_battle_time, 0) < %s
        ''', (period_start_int,))
        old_count = cursor.fetchone()[0]
        print(f"   📊 Старых записей: {old_count:,}")

        if old_count == 0:
            print("   ✅ Нечего архивировать")
            return 0

        print("   ⏳ Создание новой таблицы без индексов и constraints...")
        cursor.execute("""
            CREATE TABLE player_tanks_stats_new (
                LIKE player_tanks_stats
                INCLUDING ALL
                EXCLUDING INDEXES
                EXCLUDING CONSTRAINTS
            )
        """)

        print("   ⚡ Переключение новой таблицы в режим UNLOGGED...")
        cursor.execute("ALTER TABLE player_tanks_stats_new SET UNLOGGED;")

        print("   ⏳ Копирование свежих записей...")
        step_copy_start = time.time()
        cursor.execute(f'''
            INSERT INTO player_tanks_stats_new ({columns})
            SELECT {columns}
            FROM player_tanks_stats
            WHERE last_battle_time >= %s
        ''', (period_start_int,))
        fresh_count = cursor.rowcount
        print(f"   ✅ Свежих записей: {fresh_count:,} ({time.time() - step_copy_start:.1f} сек)")

        print("   ⏳ Копирование базовых точек...")
        step_copy_start = time.time()
        cursor.execute(f'''
            INSERT INTO player_tanks_stats_new ({columns})
            SELECT DISTINCT ON (player_id, tank_id) {columns}
            FROM player_tanks_stats
            WHERE COALESCE(last_battle_time, 0) < %s
            ORDER BY player_id, tank_id, collected_date DESC
        ''', (period_start_int,))
        baseline_count = cursor.rowcount
        print(f"   ✅ Базовых точек: {baseline_count:,} ({time.time() - step_copy_start:.1f} сек)")

        # Создаём constraints с временными именами
        constraint_name_map = []

        if constraints:
            print(f"   ⏳ Создание {len(constraints)} constraints...")
            step_idx_start = time.time()

            for conname, contype, condef in constraints:
                temp_conname = make_temp_name(conname, "_new")
                constraint_name_map.append((conname, temp_conname))

                cursor.execute(f'''
                    ALTER TABLE player_tanks_stats_new
                    ADD CONSTRAINT {q_ident(temp_conname)} {condef}
                ''')

            print(f"   ✅ Constraints созданы ({time.time() - step_idx_start:.1f} сек)")

        # Создаём обычные индексы с временными именами
        index_name_map = []

        if indexes:
            print(f"   ⏳ Создание {len(indexes)} индексов...")
            step_idx_start = time.time()

            for indexname, indexdef in indexes:
                temp_indexname = make_temp_name(indexname, "_new")
                index_name_map.append((indexname, temp_indexname))

                # Меняем имя индекса, сохраняя UNIQUE, если он есть
                indexdef_new = re.sub(
                    r'^(CREATE\s+(?:UNIQUE\s+)?INDEX\s+)("?[^"\s]+"?)',
                    lambda m: m.group(1) + q_ident(temp_indexname),
                    indexdef,
                    count=1,
                    flags=re.IGNORECASE
                )

                # Меняем таблицу, на которой создаётся индекс
                indexdef_new = re.sub(
                    r'\s+ON\s+(?:"?public"?\.)?"?player_tanks_stats"?',
                    ' ON public.player_tanks_stats_new',
                    indexdef_new,
                    count=1,
                    flags=re.IGNORECASE
                )

                cursor.execute(indexdef_new)

            print(f"   ✅ Индексы созданы ({time.time() - step_idx_start:.1f} сек)")

        print("   ⚡ Переключение новой таблицы в режим LOGGED...")
        cursor.execute("ALTER TABLE player_tanks_stats_new SET LOGGED;")

        print("   ⏳ Подмена таблиц и переименование constraints/indexes...")
        conn.autocommit = False

        cursor.execute("BEGIN;")

        # Переименовываем таблицы
        cursor.execute("ALTER TABLE player_tanks_stats RENAME TO player_tanks_stats_old;")
        cursor.execute("ALTER TABLE player_tanks_stats_new RENAME TO player_tanks_stats;")

        # Старые constraints сначала уводим на _old-имена,
        # чтобы освободить оригинальные имена
        for original_name, temp_name in constraint_name_map:
            old_name = make_temp_name(original_name, "_old")

            cursor.execute(f'''
                ALTER TABLE player_tanks_stats_old
                RENAME CONSTRAINT {q_ident(original_name)} TO {q_ident(old_name)}
            ''')

        # Новые constraints переименовываем в оригинальные имена
        for original_name, temp_name in constraint_name_map:
            cursor.execute(f'''
                ALTER TABLE player_tanks_stats
                RENAME CONSTRAINT {q_ident(temp_name)} TO {q_ident(original_name)}
            ''')

        # Старые обычные индексы уводим на _old-имена
        for original_name, temp_name in index_name_map:
            old_name = make_temp_name(original_name, "_old")

            cursor.execute(f'''
                ALTER INDEX IF EXISTS public.{q_ident(original_name)}
                RENAME TO {q_ident(old_name)}
            ''')

        # Новые обычные индексы переименовываем в оригинальные имена
        for original_name, temp_name in index_name_map:
            cursor.execute(f'''
                ALTER INDEX IF EXISTS public.{q_ident(temp_name)}
                RENAME TO {q_ident(original_name)}
            ''')

        cursor.execute("COMMIT;")

        net_savings = total_before - fresh_count - baseline_count

        print(f"   📊 Было: {total_before:,}")
        print(f"   📊 Стало: {fresh_count + baseline_count:,}")
        print(f"   📊 Экономия: {net_savings:,}")

        print("   🗑️ Удаление старой таблицы...")
        conn.autocommit = True
        cursor.execute("DROP TABLE player_tanks_stats_old;")

        cursor.execute("ANALYZE player_tanks_stats;")

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
            log_error(
                "archive",
                "CLEANUP_ERROR",
                cleanup_error.__class__.__name__,
                str(cleanup_error),
                "archive_old_records"
            )

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