import time
from datetime import datetime, timedelta
from common.db import get_db_connection, get_cursor, release_db_connection, db_lock
from common.logger import log_error, save_errors_to_db, export_errors_to_excel


def calculate_tanks_stats(days: int, table_name: str):

    print(f"\n Расчёт статистики танков за {days} дней...")
    step_start = time.time()

    with db_lock:
        conn = get_db_connection()
        cursor = get_cursor(conn)

        try:
            today = datetime.now().strftime("%Y-%m-%d")
            period_start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")

            print(f"   ⏳ Шаг 1/5: Поиск последних записей за период...")
            cursor.execute("DROP TABLE IF EXISTS temp_latest_in_period;")
            cursor.execute(f'''
                CREATE TEMPORARY TABLE temp_latest_in_period AS
                SELECT player_id, tank_id, battles, damage_dealt, damage_received, frags, hits, losses, shots, spotted,
                survived_battles, win_and_survived, wins, battle_life_time
                FROM (
                    SELECT *, ROW_NUMBER() OVER (
                        PARTITION BY player_id, tank_id 
                        ORDER BY last_battle_time DESC
                    ) as rn
                    FROM player_tanks_stats
                    WHERE last_battle_time >= %s
                )
                WHERE rn = 1
            ''', (period_start,))
            cursor.execute("SELECT COUNT(*) FROM temp_latest_in_period")
            count_in_period = cursor.fetchone()[0]
            print(f"   ✅ Найдено {count_in_period:,} записей внутри периода")

            print(f"   ⏳ Шаг 2/5: Поиск последних записей до периода...")
            cursor.execute("DROP TABLE IF EXISTS temp_latest_before_period;")
            cursor.execute(f'''
                CREATE TEMPORARY TABLE temp_latest_before_period AS
                SELECT player_id, tank_id, battles, damage_dealt, damage_received, frags, hits, losses, shots, spotted,
                survived_battles, win_and_survived, wins, battle_life_time
                FROM (
                    SELECT *, ROW_NUMBER() OVER (
                        PARTITION BY player_id, tank_id
                        ORDER BY last_battle_time DESC
                    ) as rn
                    FROM player_tanks_stats
                    WHERE last_battle_time < %s
                )
                WHERE rn = 1
            ''', (period_start,))
            cursor.execute("SELECT COUNT(*) FROM temp_latest_before_period")
            count_before = cursor.fetchone()[0]
            print(f"   ✅ Найдено {count_before:,} записей до периода")

            print(f"   ⏳ Шаг 3/5: Фильтрация игроков с записями в обоих периодах...")
            cursor.execute("DROP TABLE IF EXISTS temp_valid_players;")
            cursor.execute('''
                CREATE TEMPORARY TABLE temp_valid_players AS
                SELECT DISTINCT a.player_id, a.tank_id FROM temp_latest_in_period a
                INNER JOIN temp_latest_before_period b 
                    ON a.player_id = b.player_id AND a.tank_id = b.tank_id
            ''')
            cursor.execute("SELECT COUNT(*) FROM temp_valid_players")
            count_valid = cursor.fetchone()[0]
            print(f"   ✅ Найдено {count_valid:,} пар (игрок, танк) для расчёта")

            print(f"   ⏳ Шаг 4/5: Расчёт дельт...")
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
                                  a.wins - b.wins                         AS wins_delta,
                                  a.battle_life_time - b.battle_life_time AS battle_life_time_delta
                           FROM temp_latest_in_period a
                                    INNER JOIN temp_latest_before_period b
                                               ON a.player_id = b.player_id AND a.tank_id = b.tank_id
                                    INNER JOIN temp_valid_players v
                                               ON a.player_id = v.player_id AND a.tank_id = v.tank_id
                           WHERE (a.battles - b.battles) > 0
                           ''')
            cursor.execute("SELECT COUNT(*) FROM temp_deltas")
            count_deltas = cursor.fetchone()[0]
            print(f"   ✅ Рассчитано {count_deltas:,} дельт")

            print(f"   ⏳ Шаг 5/5: Агрегация и сохранение...")
            cursor.execute(f'''
                INSERT INTO {table_name} (
                    tank_id, calculated_date, players_count, battles, damage_dealt, damage_received, frags, hits,
                    losses, shots, spotted, survived_battles, win_and_survived, wins, battle_life_time
                )
                SELECT 
                    tank_id,
                    %s AS calculated_date,
                    COUNT(DISTINCT player_id) AS players_count,
                    SUM(battles_delta) AS battles,
                    SUM(damage_dealt_delta) AS damage_dealt,
                    SUM(damage_received_delta) AS damage_received,
                    SUM(frags_delta) AS frags,
                    SUM(hits_delta) AS hits,
                    SUM(losses_delta) AS losses,
                    SUM(shots_delta) AS shots,
                    SUM(spotted_delta) AS spotted,
                    SUM(survived_battles_delta) AS survived_battles,
                    SUM(win_and_survived_delta) AS win_and_survived,
                    SUM(wins_delta) AS wins,
                    SUM(battle_life_time_delta) AS battle_life_time
                FROM temp_deltas
                GROUP BY tank_id
                ON CONFLICT (tank_id, calculated_date) DO UPDATE SET
                    players_count = EXCLUDED.players_count,
                    battles = EXCLUDED.battles,
                    damage_dealt = EXCLUDED.damage_dealt,
                    damage_received = EXCLUDED.damage_received,
                    frags = EXCLUDED.frags,
                    hits = EXCLUDED.hits,
                    losses = EXCLUDED.losses,
                    shots = EXCLUDED.shots,
                    spotted = EXCLUDED.spotted,
                    survived_battles = EXCLUDED.survived_battles,
                    win_and_survived = EXCLUDED.win_and_survived,
                    wins = EXCLUDED.wins,
                    battle_life_time = EXCLUDED.battle_life_time
            ''', (today,))

            rows_affected = cursor.rowcount
            print(f"    Обновлено/вставлено записей: {rows_affected}")

            cursor.execute("DROP TABLE IF EXISTS temp_latest_in_period;")
            cursor.execute("DROP TABLE IF EXISTS temp_latest_before_period;")
            cursor.execute("DROP TABLE IF EXISTS temp_valid_players;")
            cursor.execute("DROP TABLE IF EXISTS temp_deltas;")

            conn.commit()

            step_elapsed = time.time() - step_start
            print(f"✅ Статистика за {days} дней рассчитана за {step_elapsed:.1f} сек:")
            print(f"   Обновлено записей в {table_name}: {rows_affected}")

        except Exception as e:
            conn.rollback()
            print(f"❌ Ошибка расчёта статистики за {days} дней: {e}")
            log_error(table_name, "DB_ERROR", "PG_ERROR", str(e), f"calculate_{days}d")

            for temp_table in ['temp_latest_in_period', 'temp_latest_before_period', 'temp_valid_players',
                               'temp_deltas']:
                try:
                    cursor.execute(f"DROP TABLE IF EXISTS {temp_table};")
                except:
                    pass
        finally:
            release_db_connection(conn)


def main():
    start_time = time.time()
    print("\n" + "=" * 70)
    print(" РАСЧЁТ АГРЕГИРОВАННОЙ СТАТИСТИКИ ТАНКОВ")
    print("=" * 70)

    calculate_tanks_stats(30, "tanks_stats_30d")
    calculate_tanks_stats(90, "tanks_stats_90d")

    elapsed = time.time() - start_time
    print(f"\n✅ Расчёт статистики завершён за {elapsed:.1f} сек ({elapsed / 60:.1f} мин)")

    print("\n" + "=" * 70)
    print(" СОХРАНЕНИЕ ЛОГА ОШИБОК")
    print("=" * 70)
    save_errors_to_db()
    export_errors_to_excel()


if __name__ == "__main__":
    main()