import sqlite3
import os
from datetime import datetime
from common.db import DB_FILE


def get_db_size_info():
    """Получает информацию о размере БД"""
    conn = sqlite3.connect(DB_FILE)

    # Размер файла на диске
    file_size = os.path.getsize(DB_FILE)

    # Информация о страницах
    page_size = conn.execute("PRAGMA page_size").fetchone()[0]
    page_count = conn.execute("PRAGMA page_count").fetchone()[0]
    freelist_count = conn.execute("PRAGMA freelist_count").fetchone()[0]

    # Расчёт
    total_size = page_count * page_size
    used_size = (page_count - freelist_count) * page_size
    free_size = freelist_count * page_size

    conn.close()

    return {
        'file_size_mb': file_size / 1024 / 1024,
        'total_size_mb': total_size / 1024 / 1024,
        'used_size_mb': used_size / 1024 / 1024,
        'free_size_mb': free_size / 1024 / 1024,
        'free_percent': (free_size / total_size * 100) if total_size > 0 else 0,
        'page_size': page_size,
        'page_count': page_count,
        'freelist_count': freelist_count
    }


def get_tables_info():
    """Получает информацию о всех таблицах"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    # Получаем список всех таблиц
    cursor.execute("""
                   SELECT name, sql
                   FROM sqlite_master
                   WHERE type = 'table'
                     AND name NOT LIKE 'sqlite_%'
                   ORDER BY name
                   """)

    tables = []
    for table_name, table_sql in cursor.fetchall():
        # Количество записей
        cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
        count = cursor.fetchone()[0]

        # Примерная оценка размера (через длину данных)
        cursor.execute(f"""
            SELECT SUM(LENGTH(CAST(rowid AS TEXT)) + 
                   {' + '.join([f"LENGTH(COALESCE(CAST({col} AS TEXT), ''))"
                                for col in get_table_columns(conn, table_name)])}) as size
            FROM {table_name}
        """)
        size_bytes = cursor.fetchone()[0] or 0

        tables.append({
            'name': table_name,
            'count': count,
            'size_mb': size_bytes / 1024 / 1024,
            'structure': table_sql
        })

    conn.close()
    return tables


def get_table_columns(conn, table_name):
    """Получает список колонок таблицы"""
    cursor = conn.cursor()
    cursor.execute(f"PRAGMA table_info({table_name})")
    return [row[1] for row in cursor.fetchall()]


def check_duplicates():
    """Проверяет дубликаты в таблицах"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    duplicates = {}

    # Проверка player_tanks_stats по (player_id, tank_id, last_battle_time)
    try:
        cursor.execute("""
                       SELECT COUNT(*)                                                                          as total,
                              COUNT(*) -
                              COUNT(DISTINCT player_id || '-' || tank_id || '-' || last_battle_time)            as duplicates
                       FROM player_tanks_stats
                       """)
        result = cursor.fetchone()
        duplicates['player_tanks_stats'] = {
            'total': result[0],
            'duplicates': result[1],
            'fields': 'player_id, tank_id, last_battle_time'
        }
    except Exception as e:
        duplicates['player_tanks_stats'] = {'error': str(e)}

    # Проверка players по (player_id, last_battle_time)
    try:
        cursor.execute("""
                       SELECT COUNT(*)                                                        as total,
                              COUNT(*) - COUNT(DISTINCT player_id || '-' || last_battle_time) as duplicates
                       FROM players
                       """)
        result = cursor.fetchone()
        duplicates['players'] = {
            'total': result[0],
            'duplicates': result[1],
            'fields': 'player_id, last_battle_time'
        }
    except Exception as e:
        duplicates['players'] = {'error': str(e)}

    conn.close()
    return duplicates


def print_report():
    """Выводит полный отчёт о состоянии БД"""
    print("\n" + "=" * 80)
    print(f" ОТЧЁТ О СОСТОЯНИИ БАЗЫ ДАННЫХ")
    print(f" Файл: {DB_FILE}")
    print(f" Дата проверки: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)

    # 1. Информация о размере БД
    print("\n📊 ИНФОРМАЦИЯ О РАЗМЕРЕ БД:")
    print("-" * 80)
    size_info = get_db_size_info()
    print(f"  Размер файла на диске:     {size_info['file_size_mb']:>10.2f} MB")
    print(f"  Общий размер (страницы):   {size_info['total_size_mb']:>10.2f} MB")
    print(f"  Использовано данных:       {size_info['used_size_mb']:>10.2f} MB")
    print(f"  Свободно (пустые страницы): {size_info['free_size_mb']:>10.2f} MB ({size_info['free_percent']:.1f}%)")
    print(f"  Размер страницы:           {size_info['page_size']:>10} bytes")
    print(f"  Всего страниц:             {size_info['page_count']:>10}")
    print(f"  Пустых страниц:            {size_info['freelist_count']:>10}")

    if size_info['free_percent'] > 10:
        print(f"\n  ⚠️  ВНИМАНИЕ: {size_info['free_percent']:.1f}% БД - пустые страницы!")
        print(f"  💡 Рекомендуется выполнить: VACUUM;")

    # 2. Информация о таблицах
    print("\n\n📋 ИНФОРМАЦИЯ О ТАБЛИЦАХ:")
    print("-" * 80)
    tables = get_tables_info()

    print(f"{'Название таблицы':<30} {'Записей':>12} {'Размер (MB)':>12}")
    print("-" * 80)

    total_records = 0
    total_size = 0

    for table in sorted(tables, key=lambda x: x['count'], reverse=True):
        print(f"{table['name']:<30} {table['count']:>12,} {table['size_mb']:>12.2f}")
        total_records += table['count']
        total_size += table['size_mb']

    print("-" * 80)
    print(f"{'ИТОГО':<30} {total_records:>12,} {total_size:>12.2f}")

    # 3. Структура таблиц
    print("\n\n🏗️  СТРУКТУРА ТАБЛИЦ:")
    print("-" * 80)

    for table in tables:
        print(f"\n📌 {table['name']} ({table['count']:,} записей):")
        # Форматируем SQL для читаемости
        structure = table['structure'].replace(',', ',\n  ')
        print(f"  {structure}")

    # 4. Проверка дубликатов
    print("\n\n🔍 ПРОВЕРКА ДУБЛИКАТОВ:")
    print("-" * 80)
    duplicates = check_duplicates()

    for table_name, info in duplicates.items():
        print(f"\n📌 {table_name}:")
        if 'error' in info:
            print(f"  ❌ Ошибка: {info['error']}")
        else:
            print(f"  Всего записей: {info['total']:,}")
            print(f"  Дубликатов по ({info['fields']}): {info['duplicates']:,}")

            if info['duplicates'] > 0:
                percent = (info['duplicates'] / info['total'] * 100) if info['total'] > 0 else 0
                print(f"  ⚠️  Процент дубликатов: {percent:.2f}%")
                print(f"  💡 Рекомендуется очистить дубликаты")
            else:
                print(f"  ✅ Дубликатов не обнаружено")

    # 5. Рекомендации
    print("\n\n💡 РЕКОМЕНДАЦИИ:")
    print("-" * 80)

    recommendations = []

    if size_info['free_percent'] > 10:
        recommendations.append("Выполнить VACUUM для освобождения пустых страниц")

    for table_name, info in duplicates.items():
        if 'error' not in info and info['duplicates'] > 0:
            recommendations.append(f"Очистить дубликаты в таблице {table_name}")

    if total_size > 10000:  # больше 10 GB
        recommendations.append("Рассмотреть архивацию старых данных")

    if recommendations:
        for i, rec in enumerate(recommendations, 1):
            print(f"  {i}. {rec}")
    else:
        print("  ✅ Состояние БД в норме")

    print("\n" + "=" * 80)


if __name__ == "__main__":
    print_report()