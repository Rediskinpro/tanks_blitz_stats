import time
from common.db import get_db_connection, get_cursor, release_db_connection


def count_duplicates():
    """
    Подсчитывает дубликаты в player_tanks_stats по связке player_id + tank_id + battles.
    """
    conn = get_db_connection()
    cursor = get_cursor(conn)

    try:
        print("🔍 Подсчёт дублей в player_tanks_stats...")
        print("   Критерий: player_id + tank_id + battles\n")

        start_time = time.time()

        # Запрос 1: Общая статистика по дублям
        stats_query = """
        SELECT 
            COUNT(*) as duplicate_groups,
            SUM(duplicate_count - 1) as total_extra_rows,
            SUM(duplicate_count) as total_rows_in_duplicates
        FROM (
            SELECT 
                player_id, 
                tank_id, 
                battles, 
                COUNT(*) as duplicate_count
            FROM player_tanks_stats
            GROUP BY player_id, tank_id, battles
            HAVING COUNT(*) > 1
        ) duplicates
        """

        cursor.execute(stats_query)
        stats = cursor.fetchone()

        duplicate_groups = stats['duplicate_groups'] or 0
        total_extra_rows = stats['total_extra_rows'] or 0
        total_rows_in_duplicates = stats['total_rows_in_duplicates'] or 0

        print("📊 Общая статистика:")
        print(f"   Групп с дублями: {duplicate_groups:,}")
        print(f"   Всего строк в дублях: {total_rows_in_duplicates:,}")
        print(f"   Лишних строк (можно удалить): {total_extra_rows:,}")

        if duplicate_groups > 0:
            print(f"\n📋 Примеры дублей (первые 10 групп):")

            # Запрос 2: Примеры дублей с деталями
            examples_query = """
            SELECT 
                player_id,
                tank_id,
                battles,
                COUNT(*) as count,
                MIN(collected_date) as first_date,
                MAX(collected_date) as last_date
            FROM player_tanks_stats
            GROUP BY player_id, tank_id, battles
            HAVING COUNT(*) > 1
            ORDER BY COUNT(*) DESC
            LIMIT 10
            """

            cursor.execute(examples_query)
            examples = cursor.fetchall()

            for i, row in enumerate(examples):
                print(f"\n  {i + 1}. Игрок {row['player_id']}, Танк {row['tank_id']}")
                print(f"     Бои: {row['battles']}")
                print(f"     Дублей: {row['count']}")
                print(f"     Период: {row['first_date']} - {row['last_date']}")

        elapsed = time.time() - start_time
        print(f"\n⏱️ Время выполнения: {elapsed:.2f} сек")

    finally:
        release_db_connection(conn)


def main():
    print("🔍 Анализ дублей в player_tanks_stats\n")
    count_duplicates()


if __name__ == "__main__":
    main()