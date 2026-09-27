import time
from datetime import datetime, timedelta
from common.db import get_db_connection, get_cursor, release_db_connection
from common.logger import log_error, save_errors_to_db


def calculate_players_stats(days: int, table_name: str):
    print(f"\n📊 Расчёт статистики игроков за {days} дней...")
    step_start = time.time()

    conn = get_db_connection()
    cursor = get_cursor(conn)
    try:
        today_int = int(datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
        period_start_int = int((datetime.now() - timedelta(days=days)).replace(
            hour=0, minute=0, second=0, microsecond=0).timestamp())

        print(f"    Шаг 1/4: Поиск последних записей игроков за период...")
        cursor.execute("DROP TABLE IF EXISTS temp_latest_in_period;")
        cursor.execute('''
            CREATE TEMPORARY TABLE temp_latest_in_period AS
            SELECT DISTINCT ON (player_id)
                player_id,
                nickname,
                stat_all_battles,
                stat_all_damage_dealt,
                stat_all_damage_received,
                stat_all_frags,
                stat_all_hits,
                stat_all_losses,
                stat_all_shots,
                stat_all_spotted,
                stat_all_survived_battles,
                stat_all_win_and_survived,
                stat_all_wins,
                stat_rating_battles,
                stat_rating_damage_dealt,
                stat_rating_damage_received,
                stat_rating_frags,
                stat_rating_hits,
                stat_rating_losses,
                stat_rating_shots,
                stat_rating_spotted,
                stat_rating_survived_battles,
                stat_rating_win_and_survived,
                stat_rating_wins
            FROM players
            WHERE last_battle_time >= %s
            ORDER BY player_id, collected_date DESC
        ''', (period_start_int,))
        cursor.execute("ANALYZE temp_latest_in_period;")
        cursor.execute("SELECT COUNT(*) FROM temp_latest_in_period")
        count_in_period = cursor.fetchone()[0]
        print(f"   ✅ Найдено {count_in_period:,} игроков внутри периода")

        print(f"   ⏳ Шаг 2/4: Поиск последних записей игроков до периода...")
        cursor.execute("DROP TABLE IF EXISTS temp_latest_before_period;")
        cursor.execute('''
            CREATE TEMPORARY TABLE temp_latest_before_period AS
            SELECT DISTINCT ON (player_id)
                player_id,
                stat_all_battles,
                stat_all_damage_dealt,
                stat_all_damage_received,
                stat_all_frags,
                stat_all_hits,
                stat_all_losses,
                stat_all_shots,
                stat_all_spotted,
                stat_all_survived_battles,
                stat_all_win_and_survived,
                stat_all_wins,
                stat_rating_battles,
                stat_rating_damage_dealt,
                stat_rating_damage_received,
                stat_rating_frags,
                stat_rating_hits,
                stat_rating_losses,
                stat_rating_shots,
                stat_rating_spotted,
                stat_rating_survived_battles,
                stat_rating_win_and_survived,
                stat_rating_wins
            FROM players
            WHERE last_battle_time < %s
            ORDER BY player_id, collected_date DESC
        ''', (period_start_int,))
        cursor.execute("ANALYZE temp_latest_before_period;")
        cursor.execute("SELECT COUNT(*) FROM temp_latest_before_period")
        count_before = cursor.fetchone()[0]
        print(f"   ✅ Найдено {count_before:,} игроков до периода")

        print(f"   ⏳ Шаг 3/4: Расчёт дельт...")
        cursor.execute("DROP TABLE IF EXISTS temp_deltas;")
        cursor.execute('''
            CREATE TEMPORARY TABLE temp_deltas AS
            SELECT a.player_id,
                   a.nickname,
                   COALESCE(a.stat_all_battles, 0) - COALESCE(b.stat_all_battles, 0)                       AS stat_all_battles,
                   COALESCE(a.stat_all_damage_dealt, 0) - COALESCE(b.stat_all_damage_dealt, 0)             AS stat_all_damage_dealt,
                   COALESCE(a.stat_all_damage_received, 0) - COALESCE(b.stat_all_damage_received, 0)       AS stat_all_damage_received,
                   COALESCE(a.stat_all_frags, 0) - COALESCE(b.stat_all_frags, 0)                           AS stat_all_frags,
                   COALESCE(a.stat_all_hits, 0) - COALESCE(b.stat_all_hits, 0)                             AS stat_all_hits,
                   COALESCE(a.stat_all_losses, 0) - COALESCE(b.stat_all_losses, 0)                         AS stat_all_losses,
                   COALESCE(a.stat_all_shots, 0) - COALESCE(b.stat_all_shots, 0)                           AS stat_all_shots,
                   COALESCE(a.stat_all_spotted, 0) - COALESCE(b.stat_all_spotted, 0)                       AS stat_all_spotted,
                   COALESCE(a.stat_all_survived_battles, 0) - COALESCE(b.stat_all_survived_battles, 0)     AS stat_all_survived_battles,
                   COALESCE(a.stat_all_win_and_survived, 0) - COALESCE(b.stat_all_win_and_survived, 0)     AS stat_all_win_and_survived,
                   COALESCE(a.stat_all_wins, 0) - COALESCE(b.stat_all_wins, 0)                             AS stat_all_wins,
                   COALESCE(a.stat_rating_battles, 0) - COALESCE(b.stat_rating_battles, 0)                 AS stat_rating_battles,
                   COALESCE(a.stat_rating_damage_dealt, 0) - COALESCE(b.stat_rating_damage_dealt, 0)       AS stat_rating_damage_dealt,
                   COALESCE(a.stat_rating_damage_received, 0) - COALESCE(b.stat_rating_damage_received, 0) AS stat_rating_damage_received,
                   COALESCE(a.stat_rating_frags, 0) - COALESCE(b.stat_rating_frags, 0)                     AS stat_rating_frags,
                   COALESCE(a.stat_rating_hits, 0) - COALESCE(b.stat_rating_hits, 0)                       AS stat_rating_hits,
                   COALESCE(a.stat_rating_losses, 0) - COALESCE(b.stat_rating_losses, 0)                   AS stat_rating_losses,
                   COALESCE(a.stat_rating_shots, 0) - COALESCE(b.stat_rating_shots, 0)                     AS stat_rating_shots,
                   COALESCE(a.stat_rating_spotted, 0) - COALESCE(b.stat_rating_spotted, 0)                 AS stat_rating_spotted,
                   COALESCE(a.stat_rating_survived_battles, 0) - COALESCE(b.stat_rating_survived_battles, 0) AS stat_rating_survived_battles,
                   COALESCE(a.stat_rating_win_and_survived, 0) - COALESCE(b.stat_rating_win_and_survived, 0) AS stat_rating_win_and_survived,
                   COALESCE(a.stat_rating_wins, 0) - COALESCE(b.stat_rating_wins, 0)                       AS stat_rating_wins
            FROM temp_latest_in_period a
            INNER JOIN temp_latest_before_period b
                ON a.player_id = b.player_id
            WHERE (COALESCE(a.stat_all_battles, 0) - COALESCE(b.stat_all_battles, 0)) > 0
               OR (COALESCE(a.stat_rating_battles, 0) - COALESCE(b.stat_rating_battles, 0)) > 0
        ''')
        cursor.execute("ANALYZE temp_deltas;")
        cursor.execute("SELECT COUNT(*) FROM temp_deltas")
        count_deltas = cursor.fetchone()[0]
        print(f"   ✅ Рассчитано {count_deltas:,} дельт")

        print(f"   ⏳ Шаг 4/4: Сохранение...")
        cursor.execute(f'''
            INSERT INTO {table_name} (
                player_id,
                calculated_date,
                nickname,
                stat_all_battles,
                stat_all_damage_dealt,
                stat_all_damage_received,
                stat_all_frags,
                stat_all_hits,
                stat_all_losses,
                stat_all_shots,
                stat_all_spotted,
                stat_all_survived_battles,
                stat_all_win_and_survived,
                stat_all_wins,
                stat_rating_battles,
                stat_rating_damage_dealt,
                stat_rating_damage_received,
                stat_rating_frags,
                stat_rating_hits,
                stat_rating_losses,
                stat_rating_shots,
                stat_rating_spotted,
                stat_rating_survived_battles,
                stat_rating_win_and_survived,
                stat_rating_wins
                )
            SELECT player_id,
                   %s AS calculated_date,
                   nickname,
                   stat_all_battles,
                   stat_all_damage_dealt,
                   stat_all_damage_received,
                   stat_all_frags,
                   stat_all_hits,
                   stat_all_losses,
                   stat_all_shots,
                   stat_all_spotted,
                   stat_all_survived_battles,
                   stat_all_win_and_survived,
                   stat_all_wins,
                   stat_rating_battles,
                   stat_rating_damage_dealt,
                   stat_rating_damage_received,
                   stat_rating_frags,
                   stat_rating_hits,
                   stat_rating_losses,
                   stat_rating_shots,
                   stat_rating_spotted,
                   stat_rating_survived_battles,
                   stat_rating_win_and_survived,
                   stat_rating_wins
            FROM temp_deltas
            ON CONFLICT (player_id, calculated_date) DO UPDATE SET
                nickname                     = EXCLUDED.nickname,
                stat_all_battles             = EXCLUDED.stat_all_battles,
                stat_all_damage_dealt        = EXCLUDED.stat_all_damage_dealt,
                stat_all_damage_received     = EXCLUDED.stat_all_damage_received,
                stat_all_frags               = EXCLUDED.stat_all_frags,
                stat_all_hits                = EXCLUDED.stat_all_hits,
                stat_all_losses              = EXCLUDED.stat_all_losses,
                stat_all_shots               = EXCLUDED.stat_all_shots,
                stat_all_spotted             = EXCLUDED.stat_all_spotted,
                stat_all_survived_battles    = EXCLUDED.stat_all_survived_battles,
                stat_all_win_and_survived    = EXCLUDED.stat_all_win_and_survived,
                stat_all_wins                = EXCLUDED.stat_all_wins,
                stat_rating_battles          = EXCLUDED.stat_rating_battles,
                stat_rating_damage_dealt     = EXCLUDED.stat_rating_damage_dealt,
                stat_rating_damage_received  = EXCLUDED.stat_rating_damage_received,
                stat_rating_frags            = EXCLUDED.stat_rating_frags,
                stat_rating_hits             = EXCLUDED.stat_rating_hits,
                stat_rating_losses           = EXCLUDED.stat_rating_losses,
                stat_rating_shots            = EXCLUDED.stat_rating_shots,
                stat_rating_spotted          = EXCLUDED.stat_rating_spotted,
                stat_rating_survived_battles = EXCLUDED.stat_rating_survived_battles,
                stat_rating_win_and_survived = EXCLUDED.stat_rating_win_and_survived,
                stat_rating_wins             = EXCLUDED.stat_rating_wins
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
    print(" РАСЧЁТ АГРЕГИРОВАННОЙ СТАТИСТИКИ ИГРОКОВ")

    calculate_players_stats(30, "players_stats_30d")
    calculate_players_stats(90, "players_stats_90d")

    elapsed = time.time() - start_time
    print(f"\n✅ Расчёт статистики завершён за {elapsed:.1f} сек ({elapsed / 60:.1f} мин)")

    print("💾 СОХРАНЕНИЕ ЛОГА ОШИБОК")
    save_errors_to_db()


if __name__ == "__main__":
    main()