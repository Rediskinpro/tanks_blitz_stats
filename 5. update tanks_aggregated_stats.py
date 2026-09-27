import time
from datetime import datetime, timedelta
from common.db import get_db_connection, get_cursor, release_db_connection
from common.logger import log_error, save_errors_to_db


def update_tanks_stats():
    print(f"\n📊 Обновление cumulative статистики танков...")
    step_start = time.time()

    conn = get_db_connection()
    cursor = get_cursor(conn)
    try:
        today_int = int(datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp())

        print(f"    Шаг 1/2: Сбор последней статистики по игрокам-танкам...")

        cursor.execute("DROP TABLE IF EXISTS temp_latest_player_tank_stats;")
        cursor.execute('''
            CREATE TEMPORARY TABLE temp_latest_player_tank_stats AS
            SELECT DISTINCT ON (pts.player_id, pts.tank_id)
                pts.player_id,
                pts.tank_id,
                pts.battles,
                pts.damage_dealt,
                pts.damage_received,
                pts.frags,
                pts.hits,
                pts.losses,
                pts.shots,
                pts.spotted,
                pts.survived_battles,
                pts.win_and_survived,
                pts.wins
            FROM player_tanks_stats pts
            LEFT JOIN blacklist bl ON pts.player_id = bl.player_id AND pts.tank_id = bl.tank_id
            WHERE bl.player_id IS NULL
            ORDER BY pts.player_id, pts.tank_id, pts.collected_date DESC
        ''')
        cursor.execute("ANALYZE temp_latest_player_tank_stats;")
        cursor.execute("SELECT COUNT(*) FROM temp_latest_player_tank_stats")
        count_records = cursor.fetchone()[0]
        print(f"   ✅ Найдено {count_records:,} записей игроков-танков")

        print(f"   ⏳ Шаг 2/2: Агрегация по танкам и сохранение...")

        cursor.execute('''
            INSERT INTO tanks_stats (
                tank_id
                , calculated_date
                , players_count
                , battles
                , damage_dealt
                , damage_received
                , frags
                , hits
                , losses
                , shots
                , spotted
                , survived_battles
                , win_and_survived
                , wins
            )
            SELECT 
                tank_id,
                %s AS calculated_date,
                COUNT(DISTINCT player_id) AS players_count,
                SUM(battles)           AS battles,
                SUM(damage_dealt)      AS damage_dealt,
                SUM(damage_received)   AS damage_received,
                SUM(frags)             AS frags,
                SUM(hits)              AS hits,
                SUM(losses)            AS losses,
                SUM(shots)             AS shots,
                SUM(spotted)           AS spotted,
                SUM(survived_battles)  AS survived_battles,
                SUM(win_and_survived)  AS win_and_survived,
                SUM(wins)              AS wins
            FROM temp_latest_player_tank_stats
            GROUP BY tank_id
            ON CONFLICT (tank_id, calculated_date) DO UPDATE SET
                players_count    = EXCLUDED.players_count,
                battles          = EXCLUDED.battles,
                damage_dealt     = EXCLUDED.damage_dealt,
                damage_received  = EXCLUDED.damage_received,
                frags            = EXCLUDED.frags,
                hits             = EXCLUDED.hits,
                losses           = EXCLUDED.losses,
                shots            = EXCLUDED.shots,
                spotted          = EXCLUDED.spotted,
                survived_battles = EXCLUDED.survived_battles,
                win_and_survived = EXCLUDED.win_and_survived,
                wins             = EXCLUDED.wins
        ''', (today_int,))

        rows_affected = cursor.rowcount
        conn.commit()

        step_elapsed = time.time() - step_start
        print(f"   ✅ Обновлено/вставлено {rows_affected} танков за {step_elapsed:.1f} сек")

        return rows_affected

    except Exception as e:
        conn.rollback()
        print(f"❌ Ошибка обновления tanks_stats: {e}")
        log_error("tanks_stats", "DB_ERROR", "PG_ERROR", str(e), "update_tanks_stats")
        return 0
    finally:
        cursor.execute("DROP TABLE IF EXISTS temp_latest_player_tank_stats;")
        release_db_connection(conn)


def calculate_tanks_stats(days: int, table_name: str):
    print(f"\n📊 Расчёт статистики танков за {days} дней...")
    step_start = time.time()

    conn = get_db_connection()
    cursor = get_cursor(conn)
    try:
        today_int = int(datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
        period_start_int = int((datetime.now() - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0).timestamp())

        print(f"    Шаг 1/4: Поиск последних записей за период...")
        cursor.execute("DROP TABLE IF EXISTS temp_latest_in_period;")
        cursor.execute('''
            CREATE TEMPORARY TABLE temp_latest_in_period AS
            SELECT DISTINCT ON (player_id, tank_id)
                player_id,
                tank_id,
                battles,
                damage_dealt,
                damage_received,
                frags,
                hits,
                losses,
                shots,
                spotted,
                survived_battles,
                win_and_survived,
                wins
            FROM player_tanks_stats
            WHERE last_battle_time >= %s
            ORDER BY player_id, tank_id, collected_date DESC
        ''', (period_start_int,))#создаём временную таблицу с данными во время 30/90-дневного периода
        cursor.execute("ANALYZE temp_latest_in_period;")
        cursor.execute("SELECT COUNT(*) FROM temp_latest_in_period")
        count_in_period = cursor.fetchone()[0]
        print(f"   ✅ Найдено {count_in_period:,} записей внутри периода")

        print(f"   ⏳ Шаг 2/4: Поиск последних записей до периода...")
        cursor.execute("DROP TABLE IF EXISTS temp_latest_before_period;")
        cursor.execute('''
            CREATE TEMPORARY TABLE temp_latest_before_period AS
            SELECT DISTINCT ON (player_id, tank_id)
                player_id,
                tank_id,
                battles,
                damage_dealt,
                damage_received,
                frags,
                hits,
                losses,
                shots,
                spotted,
                survived_battles,
                win_and_survived,
                wins
            FROM player_tanks_stats
            WHERE last_battle_time < %s
            ORDER BY player_id, tank_id, collected_date DESC
        ''', (period_start_int,))#создаём временную таблицу с данными до 30/90-дневного периода
        cursor.execute("ANALYZE temp_latest_before_period;")
        cursor.execute("SELECT COUNT(*) FROM temp_latest_before_period")
        count_before = cursor.fetchone()[0]
        print(f"   ✅ Найдено {count_before:,} записей до периода")

        print(f"   ⏳ Шаг 3/4: Расчёт дельт...")
        cursor.execute("DROP TABLE IF EXISTS temp_deltas;")
        cursor.execute('''
            CREATE TEMPORARY TABLE temp_deltas AS
            SELECT a.player_id,
                   a.tank_id,
                   a.battles - b.battles                   AS battles_delta,
                   a.damage_dealt - b.damage_dealt         AS damage_dealt_delta,
                   a.damage_received - b.damage_received   AS damage_received_delta,
                   a.frags - b.frags                       AS frags_delta,
                   a.hits - b.hits                         AS hits_delta,
                   a.losses - b.losses                     AS losses_delta,
                   a.shots - b.shots                       AS shots_delta,
                   a.spotted - b.spotted                   AS spotted_delta,
                   a.survived_battles - b.survived_battles AS survived_battles_delta,
                   a.win_and_survived - b.win_and_survived AS win_and_survived_delta,
                   a.wins - b.wins                         AS wins_delta
            FROM temp_latest_in_period a
            INNER JOIN temp_latest_before_period b
                ON a.player_id = b.player_id AND a.tank_id = b.tank_id
            WHERE (a.battles - b.battles) > 0
        ''')
        cursor.execute("ANALYZE temp_deltas;")
        cursor.execute("CREATE INDEX idx_temp_deltas_tank ON temp_deltas (tank_id);")
        cursor.execute("SELECT COUNT(*) FROM temp_deltas")
        count_deltas = cursor.fetchone()[0]
        print(f"   ✅ Рассчитано {count_deltas:,} дельт")

        print(f"   ⏳ Шаг 4/4: Агрегация и сохранение...")
        cursor.execute(f'''
                INSERT INTO {table_name} (tank_id, calculated_date, players_count, battles, damage_dealt,
                                         damage_received, frags, hits, losses, shots, spotted, survived_battles,
                                         win_and_survived, wins)
                SELECT tank_id,
                       %s                          AS calculated_date,
                       COUNT(*)                    AS players_count,
                       SUM(battles_delta)          AS battles,
                       SUM(damage_dealt_delta)     AS damage_dealt,
                       SUM(damage_received_delta)  AS damage_received,
                       SUM(frags_delta)            AS frags,
                       SUM(hits_delta)             AS hits,
                       SUM(losses_delta)           AS losses,
                       SUM(shots_delta)            AS shots,
                       SUM(spotted_delta)          AS spotted,
                       SUM(survived_battles_delta) AS survived_battles,
                       SUM(win_and_survived_delta) AS win_and_survived,
                       SUM(wins_delta)             AS wins
                FROM temp_deltas
                GROUP BY tank_id
                ON CONFLICT (tank_id, calculated_date) DO UPDATE SET players_count    = EXCLUDED.players_count,
                                                                     battles          = EXCLUDED.battles,
                                                                     damage_dealt     = EXCLUDED.damage_dealt,
                                                                     damage_received  = EXCLUDED.damage_received,
                                                                     frags            = EXCLUDED.frags,
                                                                     hits             = EXCLUDED.hits,
                                                                     losses           = EXCLUDED.losses,
                                                                     shots            = EXCLUDED.shots,
                                                                     spotted          = EXCLUDED.spotted,
                                                                     survived_battles = EXCLUDED.survived_battles,
                                                                     win_and_survived = EXCLUDED.win_and_survived,
                                                                     wins             = EXCLUDED.wins
            ''', (today_int,))

        rows_affected = cursor.rowcount
        print(f"    Обновлено/вставлено записей: {rows_affected}")

        conn.commit()

        step_elapsed = time.time() - step_start
        print(f"✅ Статистика за {days} дней рассчитана за {step_elapsed:.1f} сек ({step_elapsed / 60:.1f} мин):")
        print(f"   Обновлено записей в {table_name}: {rows_affected}")

    except Exception as e:
        conn.rollback()
        print(f"❌ Ошибка расчёта статистики за {days} дней: {e}")
        log_error(table_name, "DB_ERROR", "PG_ERROR", str(e), f"calculate_{days}d")

    finally:
        cursor.execute("DROP TABLE IF EXISTS temp_latest_in_period;")
        cursor.execute("DROP TABLE IF EXISTS temp_latest_before_period;")
        cursor.execute("DROP TABLE IF EXISTS temp_deltas;")
        release_db_connection(conn)


def main():
    start_time = time.time()
    print("РАСЧЁТ АГРЕГИРОВАННОЙ СТАТИСТИКИ ТАНКОВ")

    update_tanks_stats()
    calculate_tanks_stats(30, "tanks_stats_30d")
    calculate_tanks_stats(90, "tanks_stats_90d")

    elapsed = time.time() - start_time
    print(f"✅ Расчёт статистики завершён за {elapsed:.1f} сек ({elapsed / 60:.1f} мин)")
    print("💾 СОХРАНЕНИЕ ЛОГА ОШИБОК")
    save_errors_to_db()


if __name__ == "__main__":
    main()