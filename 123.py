import time
import os
from datetime import datetime
import pandas as pd
from common.db import get_db_connection, get_cursor, release_db_connection
from common.logger import log_error, save_errors_to_db


def find_anomalies():
    print("=" * 70)
    print(" 🔍 ПОИСК АНОМАЛИЙ В player_tanks_stats")
    print(" (Уменьшение last_battle_time или battles по сравнению с ПРЕДЫДУЩЕЙ записью)")
    print("=" * 70)

    start_time = time.time()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = f"anomalies_report_{timestamp}.csv"

    conn = get_db_connection()
    conn.autocommit = True
    cursor = get_cursor(conn)

    try:
        # 🔑 1. МАКСИМАЛЬНАЯ ОПТИМИЗАЦИЯ ПАМЯТИ ДЛЯ ЭТОЙ СЕССИИ
        # Это заставит PostgreSQL выполнить сортировку 227 млн строк в RAM, а не на диске
        print("⚙️ Настройка параметров памяти сессии (work_mem = 2GB)...")
        cursor.execute("SET work_mem = '2GB';")
        cursor.execute("SET hash_mem_multiplier = 2.0;")
        cursor.execute("SET max_parallel_workers_per_gather = 4;")
        print("✅ Параметры памяти установлены.")

        # 🔑 2. SQL-ЗАПРОС С ОКОННОЙ ФУНКЦИЕЙ LAG()
        # LAG() берет значения из строго ПРЕДЫДУЩЕЙ строки для того же player_id и tank_id
        print("⏳ Выполнение анализа данных (это может занять 2-5 минут)...")

        query = """
            WITH RankedStats AS (
                SELECT 
                    player_id,
                    tank_id,
                    collected_date,
                    COALESCE(last_battle_time, 0) AS last_battle_time,
                    battles,
                    LAG(collected_date) OVER w AS prev_collected_date,
                    LAG(COALESCE(last_battle_time, 0)) OVER w AS prev_last_battle_time,
                    LAG(battles) OVER w AS prev_battles
                FROM player_tanks_stats
                WINDOW w AS (
                    PARTITION BY player_id, tank_id 
                    ORDER BY collected_date ASC
                )
            )
            SELECT 
                player_id,
                tank_id,
                prev_collected_date AS old_collected_date,
                prev_last_battle_time AS old_last_battle_time,
                prev_battles AS old_battles,
                collected_date AS new_collected_date,
                last_battle_time AS new_last_battle_time,
                battles AS new_battles,
                CASE 
                    WHEN last_battle_time < prev_last_battle_time AND battles < prev_battles THEN 'BOTH'
                    WHEN last_battle_time < prev_last_battle_time THEN 'LBT_ONLY'
                    WHEN battles < prev_battles THEN 'BATTLES_ONLY'
                END AS anomaly_type
            FROM RankedStats
            WHERE (last_battle_time < prev_last_battle_time)
               OR (battles < prev_battles)
            ORDER BY player_id, tank_id, collected_date;
        """

        cursor.execute(query)
        rows = cursor.fetchall()

        if not rows:
            print("\n✅ АНОМАЛИЙ НЕ НАЙДЕНО. Данные в полном порядке!")
            return

        # 🔑 3. СОХРАНЕНИЕ В CSV ФАЙЛ
        print(f"⏳ Найдено аномалий: {len(rows):,}. Сохранение в {output_file}...")

        columns = [
            'player_id', 'tank_id',
            'old_collected_date', 'old_last_battle_time', 'old_battles',
            'new_collected_date', 'new_last_battle_time', 'new_battles',
            'anomaly_type'
        ]

        # Преобразуем кортежи в DataFrame для удобного экспорта
        df = pd.DataFrame(rows, columns=columns)

        # Конвертируем Unix-timestamp в читаемый формат дат (опционально, но очень полезно для анализа)
        for col in ['old_collected_date', 'new_collected_date']:
            df[col] = pd.to_datetime(df[col], unit='s', errors='coerce').dt.strftime('%Y-%m-%d %H:%M:%S')

        # utf-8-sig гарантирует, что Excel откроет файл с корректной кириллицей в заголовках
        df.to_csv(output_file, index=False, encoding='utf-8-sig')

        # 🔑 4. СТАТИСТИКА ПО ТИПАМ АНОМАЛИЙ
        anomaly_counts = df['anomaly_type'].value_counts()

        print("\n" + "=" * 70)
        print(" 📊 РЕЗУЛЬТАТЫ ПРОВЕРКИ")
        print("=" * 70)
        print(f"✅ Файл сохранен: {os.path.abspath(output_file)}")
        print(f"📉 Всего аномальных пар (игрок+танк): {len(df):,}")
        print("\n🔍 Детализация по типу аномалии:")

        for atype, count in anomaly_counts.items():
            desc = {
                'BOTH': 'Уменьшились И бои, И last_battle_time',
                'LBT_ONLY': 'Уменьшился только last_battle_time',
                'BATTLES_ONLY': 'Уменьшилось только количество боёв'
            }.get(atype, atype)
            print(f"   - {atype}: {count:,} случаев ({desc})")

        print(f"\n⏱️ Общее время выполнения: {time.time() - start_time:.1f} сек")
        print("=" * 70)

    except Exception as e:
        print(f"\n❌ Ошибка при поиске аномалий: {e}")
        log_error("anomaly_check", "DB_ERROR", "PG_ERROR", str(e), "find_anomalies")
    finally:
        release_db_connection(conn)


def main():
    try:
        find_anomalies()
    finally:
        print("\n💾 СОХРАНЕНИЕ ЛОГА ОШИБОК (если были)")
        save_errors_to_db()


if __name__ == "__main__":
    main()