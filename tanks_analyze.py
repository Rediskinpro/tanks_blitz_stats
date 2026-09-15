import time
from common.db import get_db_connection, get_cursor, release_db_connection
from common.logger import log_error, save_errors_to_db, export_errors_to_excel

MIN_TANK_TIER = 7  # Минимальный уровень танка


def create_tanks_analytics_table(cursor):
    """Создаёт таблицу tanks_analytics."""
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tanks_analytics (
            tank_id INTEGER PRIMARY KEY,
            tank_name TEXT,
            players_count INTEGER,
            players_avg_winrate REAL,
            players_weighted_winrate REAL,
            tank_battles BIGINT,
            tank_damage_dealt BIGINT,
            players_avg_damage_dealt BIGINT,
            players_weighted_damage_dealt BIGINT,
            tank_battles_30d BIGINT,
            tank_damage_dealt_30d BIGINT,
            tank_avg_winrate REAL,
            tank_weighted_winrate REAL,
            tank_avg_winrate_30d REAL,
            tank_weighted_winrate_30d REAL
        )
    ''')
    print("   ✅ Таблица tanks_analytics готова")


def fill_tanks_analytics(cursor):
    """Заполняет tanks_analytics из player_tanks_analytics."""
    # Очищаем таблицу для идемпотентности (повторный запуск перезапишет данные)
    cursor.execute("TRUNCATE TABLE tanks_analytics;")

    # Заполняем агрегированными данными
    cursor.execute(f'''
        INSERT INTO tanks_analytics (
            tank_id, tank_name, players_count,
            players_avg_winrate, players_weighted_winrate,
            tank_battles, tank_damage_dealt,
            players_avg_damage_dealt, players_weighted_damage_dealt,
            tank_battles_30d, tank_damage_dealt_30d,
            tank_avg_winrate, tank_weighted_winrate,
            tank_avg_winrate_30d, tank_weighted_winrate_30d
        )
        SELECT
            pa.tank_id,
            COALESCE(t.tank_name, 'Unknown') AS tank_name,
            COUNT(DISTINCT pa.player_id) AS players_count,

            -- Профиль игроков (общий winrate, не привязанный к танку)
            AVG(pa.player_winrate) AS players_avg_winrate,
            SUM(pa.player_winrate * pa.player_battles) / SUM(pa.player_battles) AS players_weighted_winrate,

            -- Агрегация по танку (за всё время)
            SUM(pa.player_tank_battles) AS tank_battles,
            SUM(pa.player_tank_damage_dealt) AS tank_damage_dealt,

            -- Профиль игроков (суммарный урон, не привязанный к танку)
            ROUND(AVG(pa.player_damage_dealt))::BIGINT AS players_avg_damage_dealt,
            ROUND(SUM(pa.player_damage_dealt::NUMERIC * pa.player_battles) / SUM(pa.player_battles))::BIGINT AS players_weighted_damage_dealt,

            -- Агрегация по танку (за 30 дней)
            SUM(pa.player_tank_battles_30d) AS tank_battles_30d,
            SUM(pa.player_tank_damage_dealt_30d) AS tank_damage_dealt_30d,

            -- Winrate на танке (за всё время)
            AVG(pa.player_tank_winrate) AS tank_avg_winrate,
            SUM(pa.player_tank_battles * pa.player_tank_winrate) / SUM(pa.player_tank_battles) AS tank_weighted_winrate,

            -- Winrate на танке (за 30 дней)
            AVG(pa.player_tank_winrate_30d) AS tank_avg_winrate_30d,
            SUM(pa.player_tank_battles_30d * pa.player_tank_winrate_30d) / SUM(pa.player_tank_battles_30d) AS tank_weighted_winrate_30d

        FROM player_tanks_analytics pa
        INNER JOIN tanks t ON pa.tank_id = t.tank_id
        WHERE t.tier >= {MIN_TANK_TIER}
          AND t.tier IS NOT NULL
          AND pa.calculated_date = (SELECT MAX(calculated_date) FROM player_tanks_analytics)
        GROUP BY pa.tank_id, t.tank_name
    ''')

    return cursor.rowcount


def verify_results(cursor):
    """Выводит топ-10 танков для проверки."""
    cursor.execute('''
        SELECT tank_id, tank_name, players_count,
               ROUND(players_avg_winrate::NUMERIC * 100, 2) AS avg_wr,
               ROUND(players_weighted_winrate::NUMERIC * 100, 2) AS weighted_wr,
               tank_battles, tank_battles_30d,
               ROUND(tank_weighted_winrate_30d::NUMERIC * 100, 2) AS weighted_wr_30d
        FROM tanks_analytics
        ORDER BY tank_weighted_winrate_30d DESC NULLS LAST
        LIMIT 10
    ''')
    top10 = cursor.fetchall()

    print(f"\n🏆 ТОП-10 ТАНКОВ ПО tank_weighted_winrate_30d:")
    print(f"{'Танк':<25} {'Игроков':<8} {'WR avg%':<9} {'WR вес%':<9} {'Боёв':<12} {'Боёв 30d':<12} {'WR 30d вес%':<12}")
    print("-" * 100)
    for row in top10:
        print(f"{row['tank_name']:<25} {row['players_count']:<8} "
              f"{row['avg_wr']:<9} {row['weighted_wr']:<9} "
              f"{row['tank_battles']:<12,} {row['tank_battles_30d']:<12,} "
              f"{row['weighted_wr_30d']:<12}")


def main():
    start_time = time.time()
    print("\n" + "=" * 70)
    print(" ЗАПОЛНЕНИЕ ТАБЛИЦЫ tanks_analytics")
    print("=" * 70)
    print(f"📊 Фильтр: танки уровня {MIN_TANK_TIER}+")

    conn = get_db_connection()
    cursor = get_cursor(conn)
    try:
        # Шаг 1: Создание таблицы
        print(f"\n⏳ Шаг 1/3: Создание таблицы...")
        create_tanks_analytics_table(cursor)
        conn.commit()

        # Шаг 2: Заполнение
        print(f"\n⏳ Шаг 2/3: Заполнение из player_tanks_analytics...")
        step_start = time.time()
        rows_inserted = fill_tanks_analytics(cursor)
        conn.commit()
        step_elapsed = time.time() - step_start
        print(f"   ✅ Вставлено записей: {rows_inserted} за {step_elapsed:.1f} сек")

        # Шаг 3: Проверка и VACUUM
        print(f"\n⏳ Шаг 3/3: Проверка результата...")
        verify_results(cursor)

        conn.autocommit = True
        cursor.execute("VACUUM ANALYZE tanks_analytics;")
        conn.autocommit = False
        print(f"\n   ✅ VACUUM ANALYZE выполнен")

        elapsed = time.time() - start_time
        print(f"\n{'=' * 70}")
        print(f"✅ Заполнение завершено за {elapsed:.1f} сек")
        print(f"   Записей в tanks_analytics: {rows_inserted}")

    except Exception as e:
        conn.rollback()
        print(f"❌ Ошибка: {e}")
        log_error("tanks_analytics", "DB_ERROR", "PG_ERROR", str(e), "fill_tanks_analytics")
    finally:
        release_db_connection(conn)

    print("\n" + "=" * 70)
    print("💾 СОХРАНЕНИЕ ЛОГА ОШИБОК")
    print("=" * 70)
    save_errors_to_db()
    export_errors_to_excel()


if __name__ == "__main__":
    main()