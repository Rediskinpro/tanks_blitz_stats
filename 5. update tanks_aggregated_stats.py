import time
from datetime import datetime, timedelta
from common.db import get_db_connection, get_cursor, release_db_connection
from common.logger import log_error, save_errors_to_db


def get_missing_calculation_days():
    print("\n🔍 Поиск пропущенных дней для расчёта статистики танков...")

    conn = get_db_connection()
    cursor = get_cursor(conn)
    try:
        cursor.execute('''
            SELECT DISTINCT collected_date
            FROM player_tanks_stats
            ORDER BY collected_date DESC
        ''')
        collected_dates = {row['collected_date'] for row in cursor.fetchall()}
        print(f"   ✅ Найдено {len(collected_dates)} дней сбора статистики игроков")
        cursor.execute('''
            SELECT DISTINCT calculated_date
            FROM tanks_stats_30d
            ORDER BY calculated_date DESC
        ''')
        calculated_dates = {row['calculated_date'] for row in cursor.fetchall()}
        print(f"   ✅ Найдено {len(calculated_dates)} дней расчёта статистики танков")
        missing_dates = sorted(collected_dates - calculated_dates, reverse=True)
        if not missing_dates:
            print("   ✅ Пропущенных дней не найдено")
            return []
        missing_dates = missing_dates[:7]
        return missing_dates

    except Exception as e:
        print(f"   ❌ Ошибка поиска пропущенных дней: {e}")
        log_error("tanks_stats_30d", "DB_ERROR", "PG_ERROR", str(e), "get_missing_days")
        return []

    finally:
        release_db_connection(conn)


def calculate_tanks_stats_for_date(target_date_int):
    date_readable = datetime.fromtimestamp(target_date_int).strftime('%Y-%m-%d')
    print(f"\n📊 Расчёт статистики танков за 30 дней для {date_readable}...")
    step_start = time.time()

    conn = get_db_connection()
    cursor = get_cursor(conn)
    try:
        cursor.execute("SET work_mem = '512MB';")
        cursor.execute("SET maintenance_work_mem = '2GB';")
        cursor.execute("SET max_parallel_workers_per_gather = 4;")
        cursor.execute("SET hash_mem_multiplier = 2.0;")
        period_start_int = target_date_int - 30 * 86400

        period_start_readable = datetime.fromtimestamp(period_start_int).strftime('%Y-%m-%d')
        print(f"   📅 Период: {period_start_readable} — {date_readable}")
        print(f"   ⏳ Шаг 1/4: Сбор последних записей...")
        cursor.execute("DROP TABLE IF EXISTS temp_pairs;")
        cursor.execute('''
            CREATE TEMPORARY TABLE temp_pairs AS
            SELECT DISTINCT ON (player_id, tank_id, (last_battle_time >= %s))
                player_id,
                tank_id,
                (last_battle_time >= %s) AS in_period,
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
            WHERE collected_date <= %s
            ORDER BY player_id, tank_id, (last_battle_time >= %s), collected_date DESC
        ''', (period_start_int, period_start_int, target_date_int, period_start_int))
        cursor.execute("ANALYZE temp_pairs;")
        cursor.execute("SELECT COUNT(*) FROM temp_pairs WHERE in_period = true")
        count_in_period = cursor.fetchone()[0]
        print(f"   ✅ Найдено {count_in_period:,} записей внутри периода")

        cursor.execute("SELECT COUNT(*) FROM temp_pairs WHERE in_period = false")
        count_before = cursor.fetchone()[0]
        print(f"   ✅ Найдено {count_before:,} записей до периода")

        print(f"   ⏳ Шаг 2/4: Расчёт дельт...")
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
            FROM temp_pairs a
            INNER JOIN temp_pairs b
                ON a.player_id = b.player_id AND a.tank_id = b.tank_id
            WHERE a.in_period = true
              AND b.in_period = false
              AND (a.battles - b.battles) > 0
        ''')
        cursor.execute("ANALYZE temp_deltas;")
        cursor.execute("CREATE INDEX idx_temp_deltas_tank ON temp_deltas (tank_id);")
        cursor.execute("SELECT COUNT(*) FROM temp_deltas")
        count_deltas = cursor.fetchone()[0]
        print(f"   ✅ Рассчитано {count_deltas:,} дельт")

        print(f"   ⏳ Шаг 3/4: Агрегация и сохранение...")
        cursor.execute('''
            INSERT INTO tanks_stats_30d (
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
        ''', (target_date_int,))

        rows_affected = cursor.rowcount
        conn.commit()

        step_elapsed = time.time() - step_start
        print(f"   ✅ Обновлено/вставлено {rows_affected} танков за {step_elapsed:.1f} сек")
        return rows_affected

    except Exception as e:
        conn.rollback()
        print(f"   ❌ Ошибка расчёта для {date_readable}: {e}")
        log_error("tanks_stats_30d", "DB_ERROR", "PG_ERROR", str(e), f"calculate_{target_date_int}")
        return None

    finally:
        cursor.execute("DROP TABLE IF EXISTS temp_pairs;")
        cursor.execute("DROP TABLE IF EXISTS temp_deltas;")
        release_db_connection(conn)


def main():
    start_time = time.time()
    print(" РАСЧЁТ АГРЕГИРОВАННОЙ СТАТИСТИКИ ТАНКОВ ЗА 30 ДНЕЙ")
    missing_dates = get_missing_calculation_days()
    if not missing_dates:
        print("\n✅ Нечего пересчитывать. Завершаем работу.")
        return

    total_updated = 0
    successful_days = 0
    failed_days = 0

    for target_date_int in missing_dates:
        date_readable = datetime.fromtimestamp(target_date_int).strftime('%Y-%m-%d')
        result = calculate_tanks_stats_for_date(target_date_int)
        if result is None:
            failed_days += 1
            print(f"   ⚠️ Пропускаем {date_readable} из-за ошибки")
        else:
            successful_days += 1
            total_updated += result

    elapsed = time.time() - start_time
    print(" 📊 ИТОГОВЫЙ ОТЧЁТ")
    print(f"   ✅ Успешно пересчитано дней: {successful_days}")
    print(f"   ❌ Дней с ошибками: {failed_days}")
    print(f"   💾 Всего обновлено записей: {total_updated:,}")
    print(f"   ⏱️ Общее время: {elapsed:.1f} сек ({elapsed / 60:.1f} мин)")
    print("\n💾 СОХРАНЕНИЕ ЛОГА ОШИБОК")
    save_errors_to_db()


if __name__ == "__main__":
    main()