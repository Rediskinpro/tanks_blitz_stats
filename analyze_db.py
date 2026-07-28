import sqlite3
import os
from datetime import datetime

DB_FILE = "test_tanks_blitz.db"


def format_size(size_bytes):
    """Форматирует размер в читаемый вид"""
    for unit in ['Б', 'КБ', 'МБ', 'ГБ']:
        if size_bytes < 1024:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.2f} ТБ"


def analyze_database():
    """Полный анализ базы данных"""

    print("=" * 80)
    print(f" ДИАГНОСТИКА БАЗЫ ДАННЫХ: {DB_FILE}")
    print(f" Дата анализа: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)

    # ============================================================
    # 1. РАЗМЕР ФАЙЛОВ
    # ============================================================
    print("\n" + "=" * 80)
    print(" 1. РАЗМЕР ФАЙЛОВ")
    print("=" * 80)

    files_to_check = [
        DB_FILE,
        f"{DB_FILE}-wal",
        f"{DB_FILE}-shm",
        f"{DB_FILE}-journal"
    ]

    total_size = 0
    for file_path in files_to_check:
        if os.path.exists(file_path):
            size = os.path.getsize(file_path)
            total_size += size
            print(f"   📁 {file_path}: {format_size(size)}")
        else:
            print(f"   ⚪ {file_path}: не существует")

    print(f"\n   📊 Общий размер всех файлов: {format_size(total_size)}")

    # ============================================================
    # 2. ПОДКЛЮЧЕНИЕ И БАЗОВАЯ ИНФОРМАЦИЯ
    # ============================================================
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    print("\n" + "=" * 80)
    print(" 2. ПАРАМЕТРЫ БД")
    print("=" * 80)

    # Размер страницы
    cursor.execute("PRAGMA page_size")
    page_size = cursor.fetchone()[0]
    print(f"   📏 Размер страницы: {page_size} байт")

    # Количество страниц
    cursor.execute("PRAGMA page_count")
    page_count = cursor.fetchone()[0]
    print(f"   📄 Количество страниц: {page_count:,}")
    print(f"   📊 Расчётный размер БД: {format_size(page_size * page_count)}")

    # Свободные страницы (фрагментация)
    cursor.execute("PRAGMA freelist_count")
    freelist_count = cursor.fetchone()[0]
    print(f"   🗑️  Свободных страниц (фрагментация): {freelist_count:,}")
    print(f"   📊 Можно освободить через VACUUM: {format_size(freelist_count * page_size)}")

    # Режим журнала
    cursor.execute("PRAGMA journal_mode")
    journal_mode = cursor.fetchone()[0]
    print(f"   📝 Режим журнала: {journal_mode}")

    # Проверка целостности
    print("\n   🔍 Проверка целостности БД...")
    cursor.execute("PRAGMA integrity_check")
    integrity = cursor.fetchone()[0]
    if integrity == "ok":
        print(f"   ✅ Целостность: OK")
    else:
        print(f"   ❌ Целостность: {integrity}")

    # ============================================================
    # 3. АНАЛИЗ ТАБЛИЦ
    # ============================================================
    print("\n" + "=" * 80)
    print(" 3. АНАЛИЗ ТАБЛИЦ")
    print("=" * 80)

    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    tables = [row[0] for row in cursor.fetchall()]

    table_stats = []

    for table in tables:
        # Количество записей
        cursor.execute(f"SELECT COUNT(*) FROM {table}")
        row_count = cursor.fetchone()[0]

        # Получаем структуру таблицы
        cursor.execute(f"PRAGMA table_info({table})")
        columns = cursor.fetchall()

        # Получаем индексы
        cursor.execute(f"PRAGMA index_list({table})")
        indexes = cursor.fetchall()

        # Прикидываем размер таблицы через анализ страниц
        # Используем dbstat для точного размера (если доступно)
        try:
            cursor.execute(f'''
                SELECT SUM(pgsize) 
                FROM dbstat 
                WHERE name = ?
            ''', (table,))
            table_size = cursor.fetchone()[0] or 0
        except:
            # Если dbstat недоступен, прикидываем вручную
            avg_row_size = sum(len(str(col[2])) for col in columns) * 2 + 50  # грубая оценка
            table_size = row_count * avg_row_size

        table_stats.append({
            'name': table,
            'rows': row_count,
            'columns': len(columns),
            'indexes': len(indexes),
            'size': table_size,
            'column_names': [col[1] for col in columns]
        })

    # Сортируем по размеру
    table_stats.sort(key=lambda x: x['size'], reverse=True)

    print(f"\n   {'Таблица':<30} {'Записей':>12} {'Колонок':>8} {'Индексов':>9} {'Размер':>12}")
    print("   " + "-" * 75)

    total_rows = 0
    for stat in table_stats:
        print(
            f"   {stat['name']:<30} {stat['rows']:>12,} {stat['columns']:>8} {stat['indexes']:>9} {format_size(stat['size']):>12}")
        total_rows += stat['rows']

    print("   " + "-" * 75)
    print(f"   {'ИТОГО':<30} {total_rows:>12,}")

    # ============================================================
    # 4. АНАЛИЗ ИНДЕКСОВ
    # ============================================================
    print("\n" + "=" * 80)
    print(" 4. АНАЛИЗ ИНДЕКСОВ")
    print("=" * 80)

    cursor.execute("SELECT name, tbl_name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'")
    indexes = cursor.fetchall()

    index_stats = []
    for idx_name, tbl_name in indexes:
        try:
            cursor.execute(f'''
                SELECT SUM(pgsize) 
                FROM dbstat 
                WHERE name = ?
            ''', (idx_name,))
            idx_size = cursor.fetchone()[0] or 0
        except:
            idx_size = 0

        cursor.execute(f"PRAGMA index_info({idx_name})")
        idx_cols = [row[2] for row in cursor.fetchall()]

        index_stats.append({
            'name': idx_name,
            'table': tbl_name,
            'columns': idx_cols,
            'size': idx_size
        })

    index_stats.sort(key=lambda x: x['size'], reverse=True)

    print(f"\n   {'Индекс':<40} {'Таблица':<20} {'Колонки':<25} {'Размер':>10}")
    print("   " + "-" * 98)

    for stat in index_stats:
        cols_str = ', '.join(stat['columns'])
        print(f"   {stat['name']:<40} {stat['table']:<20} {cols_str:<25} {format_size(stat['size']):>10}")

    # ============================================================
    # 5. ПРОВЕРКА НА ДУБЛИКАТЫ В player_tanks_stats
    # ============================================================
    print("\n" + "=" * 80)
    print(" 5. ПРОВЕРКА ДУБЛИКАТОВ В player_tanks_stats")
    print("=" * 80)

    try:
        # Общее количество записей
        cursor.execute("SELECT COUNT(*) FROM player_tanks_stats")
        total_records = cursor.fetchone()[0]

        # Количество уникальных пар (player_id, tank_id)
        cursor.execute("SELECT COUNT(DISTINCT player_id || '-' || tank_id) FROM player_tanks_stats")
        unique_pairs = cursor.fetchone()[0]

        duplicates_count = total_records - unique_pairs

        print(f"   📊 Всего записей: {total_records:,}")
        print(f"   📊 Уникальных пар (player_id, tank_id): {unique_pairs:,}")
        print(f"   📊 Дубликатов: {duplicates_count:,}")

        if duplicates_count > 0:
            dup_percent = (duplicates_count / total_records) * 100
            print(f"   ⚠️  Процент дубликатов: {dup_percent:.2f}%")

            # Показываем топ-10 самых дублируемых пар
            print(f"\n   🔍 Топ-10 самых дублируемых пар (player_id, tank_id):")
            cursor.execute('''
                           SELECT player_id, tank_id, COUNT(*) as cnt
                           FROM player_tanks_stats
                           GROUP BY player_id, tank_id
                           HAVING cnt > 1
                           ORDER BY cnt DESC
                           LIMIT 10
                           ''')
            for row in cursor.fetchall():
                print(f"      • Игрок {row[0]}, Танк {row[1]}: {row[2]} копий")

        # Проверяем, есть ли автоинкрементный id
        cursor.execute("PRAGMA table_info(player_tanks_stats)")
        columns = cursor.fetchall()
        has_id_column = any(col[1] == 'id' for col in columns)
        print(f"\n   🔑 Есть ли колонка 'id' (автоинкремент): {'ДА' if has_id_column else 'НЕТ'}")

        # Проверяем PRIMARY KEY
        pk_columns = [col[1] for col in columns if col[5] > 0]
        print(f"   🔑 Первичный ключ: {pk_columns if pk_columns else 'отсутствует'}")

    except Exception as e:
        print(f"   ❌ Ошибка проверки: {e}")

    # ============================================================
    # 6. АНАЛИЗ РАСПРЕДЕЛЕНИЯ ДАННЫХ
    # ============================================================
    print("\n" + "=" * 80)
    print(" 6. РАСПРЕДЕЛЕНИЕ ДАННЫХ")
    print("=" * 80)

    # Топ-10 игроков с наибольшим количеством танков
    print("\n   🏆 Топ-10 игроков с наибольшим количеством танков:")
    cursor.execute('''
                   SELECT player_id, COUNT(*) as tank_count
                   FROM player_tanks_stats
                   GROUP BY player_id
                   ORDER BY tank_count DESC
                   LIMIT 10
                   ''')
    for row in cursor.fetchall():
        print(f"      • Игрок {row[0]}: {row[1]} танков")

    # Топ-10 танков с наибольшим количеством игроков
    print("\n   🏆 Топ-10 танков с наибольшим количеством игроков:")
    cursor.execute('''
                   SELECT tank_id, COUNT(DISTINCT player_id) as player_count
                   FROM player_tanks_stats
                   GROUP BY tank_id
                   ORDER BY player_count DESC
                   LIMIT 10
                   ''')
    for row in cursor.fetchall():
        print(f"      • Танк {row[0]}: {row[1]} игроков")

    # Распределение записей по датам
    print("\n   📅 Распределение записей по датам сбора:")
    cursor.execute('''
                   SELECT collected_date, COUNT(*) as cnt
                   FROM player_tanks_stats
                   GROUP BY collected_date
                   ORDER BY collected_date
                   ''')
    for row in cursor.fetchall():
        print(f"      • {row[0]}: {row[1]:,} записей")

    # ============================================================
    # 7. АНАЛИЗ ТАБЛИЦЫ players
    # ============================================================
    print("\n" + "=" * 80)
    print(" 7. АНАЛИЗ ТАБЛИЦЫ players")
    print("=" * 80)

    cursor.execute("SELECT COUNT(*) FROM players")
    total_players = cursor.fetchone()[0]
    print(f"   📊 Всего игроков: {total_players:,}")

    # Игроки с кланом / без клана
    cursor.execute("SELECT COUNT(*) FROM players WHERE clan_id IS NOT NULL")
    players_with_clan = cursor.fetchone()[0]
    print(f"   👥 Игроков с кланом: {players_with_clan:,}")
    print(f"   👥 Игроков без клана: {total_players - players_with_clan:,}")

    # Распределение по last_battle_time
    print("\n   📅 Распределение игроков по дате последнего боя:")
    cursor.execute('''
                   SELECT CASE
                              WHEN last_battle_time IS NULL THEN 'NULL'
                              WHEN last_battle_time < '2020-01-01' THEN 'До 2020'
                              WHEN last_battle_time < '2023-01-01' THEN '2020-2022'
                              WHEN last_battle_time < '2025-01-01' THEN '2023-2024'
                              ELSE '2025+'
                              END  as period,
                          COUNT(*) as cnt
                   FROM players
                   GROUP BY period
                   ORDER BY period
                   ''')
    for row in cursor.fetchall():
        print(f"      • {row[0]}: {row[1]:,} игроков")

    # ============================================================
    # 8. АНАЛИЗ ТАБЛИЦЫ clans
    # ============================================================
    print("\n" + "=" * 80)
    print(" 8. АНАЛИЗ ТАБЛИЦЫ clans")
    print("=" * 80)

    cursor.execute("SELECT COUNT(*) FROM clans")
    total_clans = cursor.fetchone()[0]
    print(f"   📊 Всего кланов: {total_clans:,}")

    cursor.execute("SELECT COUNT(*) FROM clan_members")
    total_members = cursor.fetchone()[0]
    print(f"   📊 Всего связей клан-игрок: {total_members:,}")

    if total_clans > 0:
        avg_members = total_members / total_clans
        print(f"   📊 Среднее количество участников на клан: {avg_members:.1f}")

    # Проверяем размер поля members_ids
    print("\n   📏 Анализ размера поля members_ids:")
    cursor.execute('''
                   SELECT AVG(LENGTH(members_ids)) as avg_len,
                          MAX(LENGTH(members_ids)) as max_len,
                          MIN(LENGTH(members_ids)) as min_len
                   FROM clans
                   WHERE members_ids IS NOT NULL
                   ''')
    row = cursor.fetchone()
    if row[0]:
        print(f"      • Средняя длина: {row[0]:.0f} символов")
        print(f"      • Максимальная длина: {row[1]} символов")
        print(f"      • Минимальная длина: {row[2]} символов")

    # ============================================================
    # 9. РЕКОМЕНДАЦИИ
    # ============================================================
    print("\n" + "=" * 80)
    print(" 9. АВТОМАТИЧЕСКИЕ РЕКОМЕНДАЦИИ")
    print("=" * 80)

    recommendations = []

    # Проверка WAL
    if journal_mode.upper() == 'WAL':
        wal_size = os.path.getsize(f"{DB_FILE}-wal") if os.path.exists(f"{DB_FILE}-wal") else 0
        if wal_size > 100 * 1024 * 1024:  # > 100 МБ
            recommendations.append("⚠️  WAL-файл слишком большой. Выполните VACUUM или переключитесь на DELETE режим.")

    # Проверка фрагментации
    if freelist_count > page_count * 0.1:  # > 10% фрагментация
        recommendations.append(f"⚠️  Высокая фрагментация ({freelist_count} свободных страниц). Выполните VACUUM.")

    # Проверка дубликатов
    if 'duplicates_count' in locals() and duplicates_count > 0:
        recommendations.append(
            f"⚠️  Обнаружено {duplicates_count:,} дубликатов в player_tanks_stats. Рекомендуется очистка.")

    # Проверка размера БД
    if total_size > 5 * 1024 * 1024 * 1024:  # > 5 ГБ
        recommendations.append("⚠️  БД очень большая (>5 ГБ). Рассмотрите архивацию старых данных.")

    # Проверка наличия составного PK
    if has_id_column and 'duplicates_count' in locals() and duplicates_count > 0:
        recommendations.append("⚠️  Используется автоинкрементный id вместо составного PK. Рекомендуется миграция.")

    if recommendations:
        for rec in recommendations:
            print(f"   {rec}")
    else:
        print("   ✅ Критических проблем не обнаружено")

    conn.close()

    print("\n" + "=" * 80)
    print(" ✅ АНАЛИЗ ЗАВЕРШЁН")
    print("=" * 80)


if __name__ == "__main__":
    analyze_database()