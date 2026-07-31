import sqlite3
import os
import pandas as pd
from datetime import datetime
from common.config import DB_FILE


def get_tables_info(conn):
    """Получает информацию о всех таблицах"""
    cursor = conn.cursor()
    cursor.execute("""
                   SELECT name, sql
                   FROM sqlite_master
                   WHERE type = 'table'
                     AND name NOT LIKE 'sqlite_%'
                   ORDER BY name
                   """)

    tables = []
    for table_name, table_sql in cursor.fetchall():
        cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
        count = cursor.fetchone()[0]
        tables.append({
            'table_name': table_name,
            'record_count': count,
            'create_sql': table_sql
        })

    return tables


def get_columns_info(conn):
    """Получает информацию о всех колонках во всех таблицах"""
    cursor = conn.cursor()
    cursor.execute("""
                   SELECT name
                   FROM sqlite_master
                   WHERE type = 'table'
                     AND name NOT LIKE 'sqlite_%'
                   ORDER BY name
                   """)
    tables = [row[0] for row in cursor.fetchall()]

    columns = []
    for table_name in tables:
        cursor.execute(f"PRAGMA table_info({table_name})")
        for row in cursor.fetchall():
            cid, name, col_type, notnull, default_value, pk = row
            columns.append({
                'table_name': table_name,
                'column_name': name,
                'column_type': col_type,
                'not_null': bool(notnull),
                'default_value': default_value,
                'is_primary_key': bool(pk)
            })

    return columns


def get_indexes_info(conn):
    """Получает информацию о всех индексах"""
    cursor = conn.cursor()
    cursor.execute("""
                   SELECT name, tbl_name, sql
                   FROM sqlite_master
                   WHERE type = 'index'
                     AND sql IS NOT NULL
                   ORDER BY tbl_name, name
                   """)

    indexes = []
    for name, tbl_name, sql in cursor.fetchall():
        indexes.append({
            'index_name': name,
            'table_name': tbl_name,
            'create_sql': sql
        })

    return indexes


def get_db_size_info(conn):
    """Получает информацию о размере БД"""
    file_size = os.path.getsize(DB_FILE)

    page_size = conn.execute("PRAGMA page_size").fetchone()[0]
    page_count = conn.execute("PRAGMA page_count").fetchone()[0]
    freelist_count = conn.execute("PRAGMA freelist_count").fetchone()[0]

    total_size = page_count * page_size
    used_size = (page_count - freelist_count) * page_size
    free_size = freelist_count * page_size

    return {
        'parameter': [
            'Размер файла на диске (MB)',
            'Общий размер (MB)',
            'Использовано данных (MB)',
            'Свободно / пустые страницы (MB)',
            'Процент пустых страниц',
            'Размер страницы (bytes)',
            'Всего страниц',
            'Пустых страниц',
            'Режим журнала',
            'Всего таблиц',
            'Всего индексов'
        ],
        'value': [
            f"{file_size / 1024 / 1024:.2f}",
            f"{total_size / 1024 / 1024:.2f}",
            f"{used_size / 1024 / 1024:.2f}",
            f"{free_size / 1024 / 1024:.2f}",
            f"{(free_size / total_size * 100) if total_size > 0 else 0:.1f}%",
            str(page_size),
            f"{page_count:,}",
            f"{freelist_count:,}",
            conn.execute("PRAGMA journal_mode").fetchone()[0],
            str(conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchone()[0]),
            str(conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND sql IS NOT NULL").fetchone()[0])
        ]
    }


def get_duplicates_info(conn):
    """Проверяет дубликаты в ключевых таблицах"""
    duplicates = []

    # player_tanks_stats по (player_id, tank_id, last_battle_time)
    try:
        cursor = conn.cursor()
        cursor.execute("""
                       SELECT COUNT(*)                                                                          as total,
                              COUNT(*) -
                              COUNT(DISTINCT player_id || '-' || tank_id || '-' || last_battle_time)            as duplicates
                       FROM player_tanks_stats
                       """)
        total, dups = cursor.fetchone()
        duplicates.append({
            'table_name': 'player_tanks_stats',
            'fields': 'player_id, tank_id, last_battle_time',
            'total_records': total,
            'duplicates': dups,
            'percent': f"{(dups / total * 100) if total > 0 else 0:.2f}%"
        })
    except Exception as e:
        duplicates.append({
            'table_name': 'player_tanks_stats',
            'fields': 'player_id, tank_id, last_battle_time',
            'total_records': 'ERROR',
            'duplicates': str(e)[:100],
            'percent': '-'
        })

    # players по (player_id, last_battle_time)
    try:
        cursor = conn.cursor()
        cursor.execute("""
                       SELECT COUNT(*)                                                        as total,
                              COUNT(*) - COUNT(DISTINCT player_id || '-' || last_battle_time) as duplicates
                       FROM players
                       """)
        total, dups = cursor.fetchone()
        duplicates.append({
            'table_name': 'players',
            'fields': 'player_id, last_battle_time',
            'total_records': total,
            'duplicates': dups,
            'percent': f"{(dups / total * 100) if total > 0 else 0:.2f}%"
        })
    except Exception as e:
        duplicates.append({
            'table_name': 'players',
            'fields': 'player_id, last_battle_time',
            'total_records': 'ERROR',
            'duplicates': str(e)[:100],
            'percent': '-'
        })

    return duplicates


def main():
    output_file = f"db_structure_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"

    print("=" * 70)
    print(" СБОР ИНФОРМАЦИИ О СТРУКТУРЕ БД")
    print(f" База данных: {DB_FILE}")
    print("=" * 70)

    conn = sqlite3.connect(DB_FILE)

    print("\n📋 Сбор информации о таблицах...")
    tables = get_tables_info(conn)
    print(f"   ✅ Найдено {len(tables)} таблиц")

    print("📋 Сбор информации о колонках...")
    columns = get_columns_info(conn)
    print(f"   ✅ Найдено {len(columns)} колонок")

    print("📋 Сбор информации об индексах...")
    indexes = get_indexes_info(conn)
    print(f"   ✅ Найдено {len(indexes)} индексов")

    print("📋 Сбор информации о размере БД...")
    size_info = get_db_size_info(conn)

    print("📋 Проверка дубликатов...")
    duplicates = get_duplicates_info(conn)

    conn.close()

    # Сохраняем в xlsx
    print(f"\n💾 Сохранение в {output_file}...")

    with pd.ExcelWriter(output_file, engine='openpyxl') as writer:
        # Лист 1: Общая информация о БД
        df_size = pd.DataFrame(size_info)
        df_size.to_excel(writer, sheet_name='DB_Size', index=False)

        # Лист 2: Таблицы
        df_tables = pd.DataFrame(tables)
        df_tables.to_excel(writer, sheet_name='Tables', index=False)

        # Лист 3: Колонки
        df_columns = pd.DataFrame(columns)
        df_columns.to_excel(writer, sheet_name='Columns', index=False)

        # Лист 4: Индексы
        df_indexes = pd.DataFrame(indexes)
        df_indexes.to_excel(writer, sheet_name='Indexes', index=False)

        # Лист 5: Дубликаты
        df_duplicates = pd.DataFrame(duplicates)
        df_duplicates.to_excel(writer, sheet_name='Duplicates', index=False)

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
    print("\n📊 Сводка:")
    print(f"   Таблиц: {len(tables)}")
    print(f"   Колонок: {len(columns)}")
    print(f"   Индексов: {len(indexes)}")

    total_records = sum(t['record_count'] for t in tables)
    print(f"   Всего записей: {total_records:,}")

    print(f"\n📁 Файл: {os.path.abspath(output_file)}")


if __name__ == "__main__":
    main()