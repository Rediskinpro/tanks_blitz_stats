"""
Скрипт проверки БД PostgreSQL.
Анализирует структуру, дубликаты, размер и предлагает оптимизации.
"""
import psycopg2
import psycopg2.extras
from datetime import datetime
from common.config import DB_CONFIG


def get_db_size(conn):
    """Получает размер БД"""
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cursor.execute("SELECT pg_size_pretty(pg_database_size(current_database())) AS size")
    return cursor.fetchone()['size']


def get_tables_info(conn):
    """Получает информацию о таблицах"""
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cursor.execute("""
                   SELECT schemaname || '.' || relname                                         AS table_name,
                          n_live_tup                                                           AS row_count,
                          pg_size_pretty(pg_total_relation_size(schemaname || '.' || relname)) AS total_size,
                          pg_size_pretty(pg_relation_size(schemaname || '.' || relname))       AS data_size,
                          pg_size_pretty(pg_total_relation_size(schemaname || '.' || relname) -
                                         pg_relation_size(schemaname || '.' || relname))       AS index_size
                   FROM pg_stat_user_tables
                   WHERE schemaname = 'public'
                   ORDER BY pg_total_relation_size(schemaname || '.' || relname) DESC
                   """)
    return cursor.fetchall()


def get_columns_info(conn, table_name):
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


def check_duplicates(conn, table_name, pk_columns):
    """Проверяет дубликаты по PRIMARY KEY"""
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    # Формируем запрос для поиска дубликатов
    pk_str = ', '.join(pk_columns)
    cursor.execute(f"""
        SELECT {pk_str}, COUNT(*) AS cnt
        FROM {table_name}
        GROUP BY {pk_str}
        HAVING COUNT(*) > 1
        LIMIT 10
    """)

    duplicates = cursor.fetchall()
    return len(duplicates)


def check_negative_values(conn, table_name, numeric_columns):
    """Проверяет отрицательные значения в числовых колонках"""
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    conditions = ' OR '.join([f"{col} < 0" for col in numeric_columns])
    cursor.execute(f"""
        SELECT COUNT(*) AS cnt
        FROM {table_name}
        WHERE {conditions}
    """)

    return cursor.fetchone()['cnt']


def check_foreign_keys(conn):
    """Проверяет целостность внешних ключей"""
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    issues = []

    # Игроки в player_tanks_stats без записей в players
    cursor.execute("""
                   SELECT COUNT(DISTINCT pts.player_id) AS orphaned
                   FROM player_tanks_stats pts
                            LEFT JOIN players p ON pts.player_id = p.player_id
                       AND pts.collected_date = p.collected_date
                   WHERE p.player_id IS NULL
                   """)
    orphaned = cursor.fetchone()['orphaned']
    if orphaned > 0:
        issues.append(f"  ❌ {orphaned} игроков в player_tanks_stats без записей в players")

    # Неизвестные танки
    cursor.execute("""
                   SELECT COUNT(DISTINCT pts.tank_id) AS unknown
                   FROM player_tanks_stats pts
                            LEFT JOIN tanks t ON pts.tank_id = t.tank_id
                   WHERE t.tank_id IS NULL
                   """)
    unknown = cursor.fetchone()['unknown']
    if unknown > 0:
        issues.append(f"  ❌ {unknown} неизвестных tank_id в player_tanks_stats")

    return issues


def suggest_optimizations(conn, tables_info):
    """Предлагает способы оптимизации"""
    suggestions = []

    # 1. Проверка необходимости столбцов
    suggestions.append("\n📊 Анализ столбцов для удаления:")
    suggestions.append("   Следующие столбцы можно безопасно удалить:")
    suggestions.append("   • clans.updated_at")
    suggestions.append("   • players.processed_at")
    suggestions.append("   • player_tanks_stats.processed_at")
    suggestions.append("   • tanks.updated_at, processed_at")
    suggestions.append("   ⚠️ players.updated_at — оставить для аудита")

    # 2. Архивация старых данных
    suggestions.append("\n Архивация старых данных:")
    suggestions.append("   Данные старше 90 дней можно перенести в архивную таблицу")
    suggestions.append("   или удалить, если не нужны для анализа")

    # 3. VACUUM
    suggestions.append("\n🧹 Оптимизация хранения:")
    suggestions.append("   Выполнить VACUUM FULL для освобождения места")
    suggestions.append("   (требует блокировки таблиц на время выполнения)")

    # 4. Индексы
    suggestions.append("\n🔍 Анализ индексов:")
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cursor.execute("""
                   SELECT schemaname || '.' || tablename                                   AS table_name,
                          indexname,
                          pg_size_pretty(pg_relation_size(schemaname || '.' || indexname)) AS size
                   FROM pg_indexes
                   WHERE schemaname = 'public'
                   ORDER BY pg_relation_size(schemaname || '.' || indexname) DESC
                   LIMIT 10
                   """)
    large_indexes = cursor.fetchall()
    if large_indexes:
        suggestions.append("   Крупнейшие индексы:")
        for idx in large_indexes[:5]:
            suggestions.append(f"   • {idx['indexname']}: {idx['size']}")

    return suggestions


def main():
    print("=" * 70)
    print(" ПРОВЕРКА БД POSTGRESQL")
    print(f" Время: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    # Подключение
    print("\n🔌 Подключение к PostgreSQL...")
    conn = psycopg2.connect(**DB_CONFIG)

    try:
        # 1. Размер БД
        print("\n📊 Размер БД:")
        db_size = get_db_size(conn)
        print(f"   Общий размер: {db_size}")

        # 2. Информация о таблицах
        print("\n📋 Таблицы:")
        tables_info = get_tables_info(conn)
        print(f"{'Таблица':<30} {'Записей':>12} {'Всего':>10} {'Данные':>10} {'Индексы':>10}")
        print("-" * 80)

        total_rows = 0
        for table in tables_info:
            print(f"{table['table_name']:<30} {table['row_count']:>12,} "
                  f"{table['total_size']:>10} {table['data_size']:>10} {table['index_size']:>10}")
            total_rows += table['row_count']

        print("-" * 80)
        print(f"{'ИТОГО':<30} {total_rows:>12,}")

        # 3. Проверка дубликатов
        print("\n🔍 Проверка дубликатов:")

        tables_to_check = {
            'players': ['player_id', 'collected_date'],
            'player_tanks_stats': ['player_id', 'tank_id', 'collected_date'],
            'clans': ['clan_id'],
            'tanks': ['tank_id'],
            'clan_members': ['player_id']
        }

        total_duplicates = 0
        for table_name, pk_columns in tables_to_check.items():
            dup_count = check_duplicates(conn, table_name, pk_columns)
            status = "✅" if dup_count == 0 else "❌"
            print(f"   {status} {table_name}: {dup_count} дубликатов")
            total_duplicates += dup_count

        if total_duplicates == 0:
            print("   ✅ Дубликатов не обнаружено")
        else:
            print(f"   ⚠️ Найдено {total_duplicates} дубликатов")

        # 4. Проверка отрицательных значений
        print("\n🔍 Проверка отрицательных значений:")

        numeric_tables = {
            'player_tanks_stats': ['battles', 'damage_dealt', 'damage_received',
                                   'frags', 'hits', 'losses', 'shots', 'spotted',
                                   'survived_battles', 'win_and_survived', 'wins'],
            'players': ['stat_all_battles', 'stat_all_damage_dealt', 'stat_all_wins']
        }

        for table_name, columns in numeric_tables.items():
            neg_count = check_negative_values(conn, table_name, columns)
            status = "✅" if neg_count == 0 else "❌"
            print(f"   {status} {table_name}: {neg_count} записей с отрицательными значениями")

        # 5. Проверка внешних ключей
        print("\n🔍 Проверка целостности связей:")
        fk_issues = check_foreign_keys(conn)
        if fk_issues:
            for issue in fk_issues:
                print(issue)
        else:
            print("   ✅ Все связи корректны")

        # 6. Предложения по оптимизации
        print("\n Предложения по оптимизации:")
        suggestions = suggest_optimizations(conn, tables_info)
        for suggestion in suggestions:
            print(suggestion)

        # # 7. SQL для удаления столбцов
        # print("\n" + "=" * 70)
        # print(" SQL ДЛЯ УДАЛЕНИЯ СТОЛБЦОВ:")
        # print("=" * 70)
        # print("""
        #       -- Удаление неиспользуемых столбцов
        #       ALTER TABLE clans
        #           DROP COLUMN IF EXISTS updated_at;
        #       ALTER TABLE players
        #           DROP COLUMN IF EXISTS processed_at;
        #       ALTER TABLE player_tanks_stats
        #           DROP COLUMN IF EXISTS processed_at;
        #       ALTER TABLE tanks
        #           DROP COLUMN IF EXISTS updated_at;
        #       ALTER TABLE tanks
        #           DROP COLUMN IF EXISTS processed_at;

# # -- Оптимизация таблиц после удаления столбцов
#               VACUUM FULL clans;
#               VACUUM FULL players;
#               VACUUM FULL player_tanks_stats;
#               VACUUM FULL tanks;
#               """)

    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
    finally:
        conn.close()

    print("\n" + "=" * 70)
    print(" ПРОВЕРКА ЗАВЕРШЕНА")
    print("=" * 70)


if __name__ == "__main__":
    main()