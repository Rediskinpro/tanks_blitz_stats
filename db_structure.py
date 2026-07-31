"""
Скрипт для сбора полной информации о структуре БД PostgreSQL.
Сохраняет результат в xlsx файл.
"""
import psycopg2
import psycopg2.extras
import pandas as pd
import os
from datetime import datetime
from common.config import DB_CONFIG


def get_db_info(conn):
    """Получает общую информацию о БД"""
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    # Размер БД
    cursor.execute("SELECT pg_size_pretty(pg_database_size(current_database())) AS db_size")
    db_size = cursor.fetchone()['db_size']

    # Версия PostgreSQL
    cursor.execute("SELECT version()")
    pg_version = cursor.fetchone()[0]

    # Количество таблиц
    cursor.execute("""
                   SELECT COUNT(*) AS table_count
                   FROM information_schema.tables
                   WHERE table_schema = 'public'
                     AND table_type = 'BASE TABLE'
                   """)
    table_count = cursor.fetchone()['table_count']

    # Количество индексов
    cursor.execute("""
                   SELECT COUNT(*) AS index_count
                   FROM pg_indexes
                   WHERE schemaname = 'public'
                   """)
    index_count = cursor.fetchone()['index_count']

    # Общий размер всех таблиц
    cursor.execute("""
                   SELECT pg_size_pretty(SUM(pg_total_relation_size(schemaname || '.' || relname))) AS total_size
                   FROM pg_stat_user_tables
                   WHERE schemaname = 'public'
                   """)
    total_size = cursor.fetchone()['total_size']

    return {
        'Параметр': [
            'Версия PostgreSQL',
            'Размер БД',
            'Общий размер таблиц',
            'Количество таблиц',
            'Количество индексов',
            'Дата проверки'
        ],
        'Значение': [
            pg_version.split('\n')[0],
            db_size,
            total_size,
            str(table_count),
            str(index_count),
            datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        ]
    }


def get_tables(conn):
    """Получает список всех таблиц с количеством записей"""
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    cursor.execute("""
                   SELECT t.table_name,
                          (xpath('/row/cnt/text()', xml_count)) [1]::text::int AS row_count
                   FROM information_schema.tables t
                            LEFT JOIN (SELECT table_name,
                                              query_to_xml('SELECT COUNT(*) AS cnt FROM "' || table_name || '"', false,
                                                           true, '') AS xml_count
                                       FROM information_schema.tables
                                       WHERE table_schema = 'public'
                                         AND table_type = 'BASE TABLE') counts ON t.table_name = counts.table_name
                   WHERE t.table_schema = 'public'
                     AND t.table_type = 'BASE TABLE'
                   ORDER BY t.table_name
                   """)

    tables = []
    for row in cursor.fetchall():
        tables.append({
            'table_name': row['table_name'],
            'row_count': row['row_count'] or 0
        })

    return tables


def get_columns(conn):
    """Получает информацию о всех колонках"""
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    cursor.execute("""
                   SELECT table_name,
                          column_name,
                          ordinal_position,
                          data_type,
                          character_maximum_length,
                          is_nullable,
                          column_default
                   FROM information_schema.columns
                   WHERE table_schema = 'public'
                   ORDER BY table_name, ordinal_position
                   """)

    columns = []
    for row in cursor.fetchall():
        # Формируем полный тип данных
        data_type = row['data_type']
        if row['character_maximum_length']:
            data_type = f"{data_type}({row['character_maximum_length']})"

        columns.append({
            'table_name': row['table_name'],
            'column_name': row['column_name'],
            'ordinal_position': row['ordinal_position'],
            'data_type': data_type,
            'is_nullable': row['is_nullable'],
            'column_default': row['column_default'] if row['column_default'] else ''
        })

    return columns


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

    primary_keys = []
    for row in cursor.fetchall():
        primary_keys.append({
            'table_name': row['table_name'],
            'column_name': row['column_name'],
            'key_order': row['ordinal_position']
        })

    return primary_keys


def get_indexes(conn):
    """Получает информацию об индексах"""
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    cursor.execute("""
                   SELECT schemaname,
                          tablename,
                          indexname,
                          indexdef
                   FROM pg_indexes
                   WHERE schemaname = 'public'
                   ORDER BY tablename, indexname
                   """)

    indexes = []
    for row in cursor.fetchall():
        indexes.append({
            'table_name': row['tablename'],
            'index_name': row['indexname'],
            'index_definition': row['indexdef']
        })

    return indexes


def get_foreign_keys(conn):
    """Получает информацию о внешних ключах (если есть)"""
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    cursor.execute("""
                   SELECT tc.table_name,
                          kcu.column_name,
                          ccu.table_name  AS foreign_table_name,
                          ccu.column_name AS foreign_column_name
                   FROM information_schema.table_constraints AS tc
                            JOIN information_schema.key_column_usage AS kcu
                                 ON tc.constraint_name = kcu.constraint_name
                                     AND tc.table_schema = kcu.table_schema
                            JOIN information_schema.constraint_column_usage AS ccu
                                 ON ccu.constraint_name = tc.constraint_name
                                     AND ccu.table_schema = tc.table_schema
                   WHERE tc.constraint_type = 'FOREIGN KEY'
                     AND tc.table_schema = 'public'
                   ORDER BY tc.table_name
                   """)

    foreign_keys = []
    for row in cursor.fetchall():
        foreign_keys.append({
            'table_name': row['table_name'],
            'column_name': row['column_name'],
            'foreign_table': row['foreign_table_name'],
            'foreign_column': row['foreign_column_name']
        })

    return foreign_keys


def save_to_excel(output_file):
    """Сохраняет всю информацию в xlsx файл"""
    print("=" * 70)
    print(" СБОР ИНФОРМАЦИИ О СТРУКТУРЕ БД")
    print("=" * 70)

    # Подключение к БД
    print(f"\n🔌 Подключение к PostgreSQL...")
    conn = psycopg2.connect(**DB_CONFIG)

    try:
        # Собираем данные
        print("📋 Сбор информации о БД...")
        db_info = get_db_info(conn)

        print("📋 Сбор списка таблиц...")
        tables = get_tables(conn)
        print(f"   ✅ Найдено {len(tables)} таблиц")

        print("📋 Сбор информации о колонках...")
        columns = get_columns(conn)
        print(f"   ✅ Найдено {len(columns)} колонок")

        print("📋 Сбор информации о первичных ключах...")
        primary_keys = get_primary_keys(conn)
        print(f"   ✅ Найдено {len(primary_keys)} ключевых колонок")

        print("📋 Сбор информации об индексах...")
        indexes = get_indexes(conn)
        print(f"   ✅ Найдено {len(indexes)} индексов")

        print("📋 Сбор информации о внешних ключах...")
        foreign_keys = get_foreign_keys(conn)
        print(f"   ✅ Найдено {len(foreign_keys)} внешних ключей")

        # Сохраняем в xlsx
        print(f"\n💾 Сохранение в {output_file}...")

        with pd.ExcelWriter(output_file, engine='openpyxl') as writer:
            # Лист 1: Общая информация
            df_db_info = pd.DataFrame(db_info)
            df_db_info.to_excel(writer, sheet_name='DB_Info', index=False)

            # Лист 2: Таблицы
            df_tables = pd.DataFrame(tables)
            df_tables.to_excel(writer, sheet_name='Tables', index=False)

            # Лист 3: Колонки
            df_columns = pd.DataFrame(columns)
            df_columns.to_excel(writer, sheet_name='Columns', index=False)

            # Лист 4: Первичные ключи
            df_pk = pd.DataFrame(primary_keys)
            df_pk.to_excel(writer, sheet_name='Primary_Keys', index=False)

            # Лист 5: Индексы
            df_indexes = pd.DataFrame(indexes)
            df_indexes.to_excel(writer, sheet_name='Indexes', index=False)

            # Лист 6: Внешние ключи
            if foreign_keys:
                df_fk = pd.DataFrame(foreign_keys)
                df_fk.to_excel(writer, sheet_name='Foreign_Keys', index=False)

            # Автоподбор ширины колонок
            for sheet_name in writer.sheets:
                worksheet = writer.sheets[sheet_name]
                for column in worksheet.columns:
                    max_length = 0
                    column_letter = column[0].column_letter
                    for cell in column:
                        try:
                            if len(str(cell.value)) > max_length:
                                max_length = len(str(cell.value))
                        except:
                            pass
                    adjusted_width = min(max_length + 2, 100)
                    worksheet.column_dimensions[column_letter].width = adjusted_width

        print(f"\n✅ Отчёт сохранён: {output_file}")
        print(f"📁 Путь: {os.path.abspath(output_file)}")

    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
        raise
    finally:
        conn.close()

    print("\n" + "=" * 70)
    print(" СБОР ЗАВЕРШЁН")
    print("=" * 70)


def main():
    output_file = f"db_structure_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    save_to_excel(output_file)


if __name__ == "__main__":
    main()