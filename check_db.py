"""
Скрипт для сбора информации о структуре и размерах БД PostgreSQL.
Аналог check_db.py для SQLite, но адаптирован под PostgreSQL.
"""
import psycopg2
import psycopg2.extras
from datetime import datetime
from common.config import DB_CONFIG


def get_connection():
    """Подключение к PostgreSQL"""
    return psycopg2.connect(**DB_CONFIG)


def get_db_size(conn):
    """Получает общий размер БД"""
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cursor.execute("SELECT pg_size_pretty(pg_database_size(current_database())) AS size")
    return cursor.fetchone()['size']


def get_tables_info(conn):
    """Получает информацию о всех таблицах"""
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cursor.execute("""
                   SELECT schemaname || '.' || relname                                         AS table_name,
                          n_live_tup                                                           AS row_count,
                          pg_size_pretty(pg_total_relation_size(schemaname || '.' || relname)) AS total_size,
                          pg_size_pretty(pg_relation_size(schemaname || '.' || relname))       AS data_size,
                          pg_size_pretty(pg_total_relation_size(schemaname || '.' || relname) -
                                         pg_relation_size(schemaname || '.' || relname))       AS index_size,
                          n_dead_tup                                                           AS dead_rows,
                          last_vacuum,
                          last_autovacuum,
                          last_analyze,
                          last_autoanalyze
                   FROM pg_stat_user_tables
                   WHERE schemaname = 'public'
                   ORDER BY pg_total_relation_size(schemaname || '.' || relname) DESC
                   """)
    return cursor.fetchall()


def get_indexes_info(conn):
    """Получает информацию об индексах"""
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cursor.execute("""
        SELECT 
            schemaname || '.' || relname AS table_name,
            indexrelname AS index_name,
            pg_size_pretty(pg_relation_size(indexrelid)) AS size,
            idx_scan AS times_used,
            idx_tup_read AS tuples_read,
            idx_tup_fetch AS tuples_fetched
        FROM pg_stat_user_indexes
        WHERE schemaname = 'public'
        ORDER BY pg_relation_size(indexrelid) DESC
    """)
    return cursor.fetchall()


def get_bloat_info(conn):
    """Получает информацию о bloat (разрастании) таблиц"""
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cursor.execute("""
                   SELECT schemaname || '.' || relname                                   AS table_name,
                          n_live_tup                                                     AS live_rows,
                          n_dead_tup                                                     AS dead_rows,
                          CASE
                              WHEN n_live_tup > 0
                                  THEN round(n_dead_tup::numeric / (n_live_tup + n_dead_tup) * 100, 2)
                              ELSE 0
                              END                                                        AS dead_percent,
                          pg_size_pretty(pg_relation_size(schemaname || '.' || relname)) AS table_size
                   FROM pg_stat_user_tables
                   WHERE schemaname = 'public'
                     AND n_dead_tup > 0
                   ORDER BY n_dead_tup DESC
                   """)
    return cursor.fetchall()


def get_unused_indexes(conn):
    """Находит индексы, которые не используются"""
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cursor.execute("""
        SELECT 
            schemaname || '.' || relname AS table_name,
            indexrelname AS index_name,
            pg_size_pretty(pg_relation_size(indexrelid)) AS size,
            idx_scan AS times_used
        FROM pg_stat_user_indexes
        WHERE schemaname = 'public'
          AND idx_scan = 0
          AND indexrelname NOT LIKE '%pkey%'
        ORDER BY pg_relation_size(indexrelid) DESC
    """)
    return cursor.fetchall()


def get_table_columns(conn, table_name):
    """Получает информацию о колонках таблицы"""
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cursor.execute("""
                   SELECT column_name,
                          data_type,
                          character_maximum_length,
                          is_nullable,
                          column_default
                   FROM information_schema.columns
                   WHERE table_schema = 'public'
                     AND table_name = %s
                   ORDER BY ordinal_position
                   """, (table_name,))
    return cursor.fetchall()


def get_primary_keys(conn):
    """Получает информацию о первичных ключах"""
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cursor.execute("""
                   SELECT tc.table_name,
                          kcu.column_name,
                          kcu.ordinal_position
                   FROM information_schema.table_constraints tc
                            JOIN information_schema.key_column_usage kcu
                                 ON tc.constraint_name = kcu.constraint_name
                                     AND tc.table_schema = kcu.table_schema
                   WHERE tc.constraint_type = 'PRIMARY KEY'
                     AND tc.table_schema = 'public'
                   ORDER BY tc.table_name, kcu.ordinal_position
                   """)
    return cursor.fetchall()


def print_report():
    """Выводит полный отчёт о состоянии БД"""
    print("\n" + "=" * 80)
    print(f" ОТЧЁТ О СОСТОЯНИИ БАЗЫ ДАННЫХ POSTGRESQL")
    print(f" Дата проверки: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)

    conn = get_connection()

    try:
        # 1. Общий размер БД
        print("\n📊 ОБЩИЙ РАЗМЕР БД:")
        print("-" * 80)
        db_size = get_db_size(conn)
        print(f"  Размер базы данных: {db_size}")

        # 2. Информация о таблицах
        print("\n\n📋 ИНФОРМАЦИЯ О ТАБЛИЦАХ:")
        print("-" * 80)
        tables = get_tables_info(conn)

        print(f"{'Таблица':<30} {'Записей':>12} {'Всего':>10} {'Данные':>10} {'Индексы':>10}")
        print("-" * 80)

        total_rows = 0
        for table in tables:
            print(f"{table['table_name']:<30} {table['row_count']:>12,} "
                  f"{table['total_size']:>10} {table['data_size']:>10} {table['index_size']:>10}")
            total_rows += table['row_count']

        print("-" * 80)
        print(f"{'ИТОГО':<30} {total_rows:>12,}")

        # 3. Информация об индексах
        print("\n\n🔍 ИНФОРМАЦИЯ ОБ ИНДЕКСАХ:")
        print("-" * 80)
        indexes = get_indexes_info(conn)

        print(f"{'Таблица':<30} {'Индекс':<35} {'Размер':>10} {'Исп.':>8} {'Прочитано':>12}")
        print("-" * 80)

        for idx in indexes:
            print(f"{idx['table_name']:<30} {idx['index_name']:<35} "
                  f"{idx['size']:>10} {idx['times_used']:>8} {idx['tuples_read']:>12}")

        # 4. Неиспользуемые индексы
        print("\n\n⚠️  НЕИСПОЛЬЗУЕМЫЕ ИНДЕКСЫ (idx_scan = 0):")
        print("-" * 80)
        unused_indexes = get_unused_indexes(conn)

        if unused_indexes:
            print(f"{'Таблица':<30} {'Индекс':<35} {'Размер':>10}")
            print("-" * 80)
            total_unused_size = 0
            for idx in unused_indexes:
                print(f"{idx['table_name']:<30} {idx['index_name']:<35} {idx['size']:>10}")
            print("-" * 80)
            print(f"  💡 Рекомендуется удалить эти индексы для освобождения места")
        else:
            print("  ✅ Все индексы используются")

        # 5. Bloat (разрастание таблиц)
        print("\n\n🗑️  РАЗРАСТАНИЕ ТАБЛИЦ (BLOAT):")
        print("-" * 80)
        bloat_info = get_bloat_info(conn)

        if bloat_info:
            print(f"{'Таблица':<30} {'Живых':>12} {'Мёртвых':>12} {'% мёртвых':>12} {'Размер':>10}")
            print("-" * 80)
            for table in bloat_info:
                print(f"{table['table_name']:<30} {table['live_rows']:>12,} "
                      f"{table['dead_rows']:>12,} {table['dead_percent']:>11.2f}% "
                      f"{table['table_size']:>10}")
            print("-" * 80)
            print(f"  💡 Выполните VACUUM для освобождения места от мёртвых строк")
        else:
            print("  ✅ Разрастание не обнаружено")

        # 6. Первичные ключи
        print("\n\n ПЕРВИЧНЫЕ КЛЮЧИ:")
        print("-" * 80)
        primary_keys = get_primary_keys(conn)

        current_table = None
        for pk in primary_keys:
            if pk['table_name'] != current_table:
                current_table = pk['table_name']
                print(f"\n📌 {current_table}:")
            print(f"  - {pk['column_name']} (позиция {pk['ordinal_position']})")

        # 7. Рекомендации
        print("\n\n💡 РЕКОМЕНДАЦИИ:")
        print("-" * 80)
        recommendations = []

        if unused_indexes:
            recommendations.append(f"Удалить {len(unused_indexes)} неиспользуемых индексов")

        if bloat_info:
            high_bloat = [t for t in bloat_info if float(t['dead_percent']) > 10]
            if high_bloat:
                recommendations.append(f"Выполнить VACUUM для {len(high_bloat)} таблиц с высоким bloat")

        tables_needing_vacuum = [t for t in tables if t['last_vacuum'] is None and t['row_count'] > 1000]
        if tables_needing_vacuum:
            recommendations.append(f"Выполнить VACUUM ANALYZE для {len(tables_needing_vacuum)} таблиц")

        if recommendations:
            for i, rec in enumerate(recommendations, 1):
                print(f"  {i}. {rec}")
        else:
            print("  ✅ Состояние БД в норме")

        print("\n" + "=" * 80)

    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
    finally:
        conn.close()


if __name__ == "__main__":
    print_report()