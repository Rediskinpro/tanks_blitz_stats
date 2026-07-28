import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from common.config import BATCH_SIZE, BATCH_WORKERS
from common.db import get_db_connection, db_lock
from common.api import get_api_data
from common.logger import log_error, save_errors_to_db, export_errors_to_excel


def get_all_player_ids():
    with db_lock:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT player_id FROM players
            UNION
            SELECT player_id FROM clan_members
        ''')
        player_ids = [row['player_id'] for row in cursor.fetchall()]
        conn.close()
    print(f"🔍 Найдено {len(player_ids)} уникальных player_id для обновления")
    return player_ids


def get_players_last_battle_times():
    with db_lock:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT player_id, last_battle_time
            FROM (
                SELECT player_id, last_battle_time,
                       ROW_NUMBER() OVER (PARTITION BY player_id ORDER BY collected_date DESC) as rn
                FROM players
            )
            WHERE rn = 1
        ''')
        result = {row['player_id']: row['last_battle_time'] for row in cursor.fetchall()}
        conn.close()
    print(f"📋 Загружены last_battle_time для {len(result)} игроков из БД")
    return result


def process_players_batch(batch, players_lbt):
    api_data = get_api_data(batch, "wotb/account/info/", param_name="account_id", extra="statistics.rating")
    if not api_data:
        return 0, 0, 0

    clan_api_data = get_api_data(batch, "wotb/clans/accountinfo/", param_name="account_id", extra="clan")

    players_saved = 0
    players_skipped = 0
    members_updated = 0

    with db_lock:
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            current_date = datetime.now().strftime("%Y-%m-%d")
            current_datetime = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            for pid_str, info in api_data.items():
                if info is None:
                    continue
                player_id = int(pid_str)
                nickname = info.get('nickname')
                raw_lbt = info.get('last_battle_time')
                lbt_str = None
                if raw_lbt:
                    try:
                        lbt_str = datetime.fromtimestamp(raw_lbt).strftime("%Y-%m-%d %H:%M:%S")
                    except Exception:
                        pass

                prev_lbt = players_lbt.get(player_id)
                if prev_lbt is not None and prev_lbt == lbt_str:
                    players_skipped += 1
                    continue

                clan_info = clan_api_data.get(pid_str, {}) if clan_api_data else {}
                clan_id = clan_info.get('clan_id') if clan_info else None
                clan_name = None
                clan_tag = None
                if clan_info and 'clan' in clan_info:
                    clan_data = clan_info['clan']
                    clan_name = clan_data.get('name')
                    clan_tag = clan_data.get('tag')

                stats = info.get('statistics', {}) or {}
                stats_all = stats.get('all', {}) or {}
                stats_rating = stats.get('rating', {}) or {}

                cursor.execute('''
                    INSERT INTO players (
                        player_id, nickname, clan_id, clan_tag, clan_name, last_battle_time, collected_date, updated_at,
                        processed_at, stat_all_battles, stat_all_damage_dealt, stat_all_damage_received, stat_all_frags,
                        stat_all_hits, stat_all_losses, stat_all_shots, stat_all_spotted, stat_all_survived_battles,
                        stat_all_win_and_survived, stat_all_wins, stat_rating_battles, stat_rating_capture_points,
                        stat_rating_damage_dealt, stat_rating_damage_received, stat_rating_frags, stat_rating_hits,
                        stat_rating_losses, stat_rating_mm_rating, stat_rating_shots, stat_rating_spotted,
                        stat_rating_survived_battles, stat_rating_win_and_survived, stat_rating_wins
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(player_id, collected_date) DO UPDATE SET
                        nickname = excluded.nickname,
                        clan_id = excluded.clan_id,
                        clan_tag = excluded.clan_tag,
                        clan_name = excluded.clan_name,
                        last_battle_time = excluded.last_battle_time,
                        updated_at = excluded.updated_at,
                        processed_at = excluded.processed_at,
                        stat_all_battles = excluded.stat_all_battles,
                        stat_all_damage_dealt = excluded.stat_all_damage_dealt,
                        stat_all_damage_received = excluded.stat_all_damage_received,
                        stat_all_frags = excluded.stat_all_frags,
                        stat_all_hits = excluded.stat_all_hits,
                        stat_all_losses = excluded.stat_all_losses,
                        stat_all_shots = excluded.stat_all_shots,
                        stat_all_spotted = excluded.stat_all_spotted,
                        stat_all_survived_battles = excluded.stat_all_survived_battles,
                        stat_all_win_and_survived = excluded.stat_all_win_and_survived,
                        stat_all_wins = excluded.stat_all_wins,
                        stat_rating_battles = excluded.stat_rating_battles,
                        stat_rating_capture_points = excluded.stat_rating_capture_points,
                        stat_rating_damage_dealt = excluded.stat_rating_damage_dealt,
                        stat_rating_damage_received = excluded.stat_rating_damage_received,
                        stat_rating_frags = excluded.stat_rating_frags,
                        stat_rating_hits = excluded.stat_rating_hits,
                        stat_rating_losses = excluded.stat_rating_losses,
                        stat_rating_mm_rating = excluded.stat_rating_mm_rating,
                        stat_rating_shots = excluded.stat_rating_shots,
                        stat_rating_spotted = excluded.stat_rating_spotted,
                        stat_rating_survived_battles = excluded.stat_rating_survived_battles,
                        stat_rating_win_and_survived = excluded.stat_rating_win_and_survived,
                        stat_rating_wins = excluded.stat_rating_wins
                ''', (
                    player_id,
                    nickname,
                    clan_id,
                    clan_tag,
                    clan_name,
                    lbt_str,
                    current_date,
                    current_datetime,
                    current_datetime,
                    stats_all.get('battles'),
                    stats_all.get('damage_dealt'),
                    stats_all.get('damage_received'),
                    stats_all.get('frags'),
                    stats_all.get('hits'),
                    stats_all.get('losses'),
                    stats_all.get('shots'),
                    stats_all.get('spotted'),
                    stats_all.get('survived_battles'),
                    stats_all.get('win_and_survived'),
                    stats_all.get('wins'),
                    stats_rating.get('battles'),
                    stats_rating.get('capture_points'),
                    stats_rating.get('damage_dealt'),
                    stats_rating.get('damage_received'),
                    stats_rating.get('frags'),
                    stats_rating.get('hits'),
                    stats_rating.get('losses'),
                    stats_rating.get('mm_rating'),
                    stats_rating.get('shots'),
                    stats_rating.get('spotted'),
                    stats_rating.get('survived_battles'),
                    stats_rating.get('win_and_survived'),
                    stats_rating.get('wins')
                ))
                players_saved += 1

                try:
                    cursor.execute('DELETE FROM clan_members WHERE player_id = ?', (player_id,))
                    cursor.execute('''
                        INSERT INTO clan_members (clan_id, player_id)
                        VALUES (?, ?)
                    ''', (clan_id, player_id))
                    members_updated += 1
                except Exception as e:
                    print(f"⚠️ Ошибка обновления clan_members для игрока {player_id}: {e}")
                    log_error("wotb/clans/accountinfo/", "DB_ERROR", "SQLITE_ERROR", str(e), str(player_id))

            conn.commit()
        except Exception as e:
            conn.rollback()
            print(f"❌ Ошибка при обработке батча: {e}")
            log_error("wotb/account/info/", "DB_ERROR", "SQLITE_ERROR", str(e), str(batch))
        finally:
            conn.close()

    return players_saved, players_skipped, members_updated


def main():
    start_time = time.time()
    print("\n" + "=" * 70)
    print(" ОБНОВЛЕНИЕ ТАБЛИЦ players И clan_members")
    print("=" * 70)

    player_ids = get_all_player_ids()
    if not player_ids:
        print("⏭️ Нет игроков для обновления.")
        return

    players_lbt = get_players_last_battle_times()

    batches = [player_ids[i:i + BATCH_SIZE] for i in range(0, len(player_ids), BATCH_SIZE)]
    total_batches = len(batches)
    print(f"📦 Разбито на {total_batches} батчей по {BATCH_SIZE} игроков.")

    total_players_saved = 0
    total_players_skipped = 0
    total_members_updated = 0
    completed_batches = 0
    progress_interval = 100

    with ThreadPoolExecutor(max_workers=BATCH_WORKERS) as executor:
        futures = [executor.submit(process_players_batch, batch, players_lbt) for batch in batches]

        for future in as_completed(futures):
            try:
                players_saved, players_skipped, members_updated = future.result()
                total_players_saved += players_saved
                total_players_skipped += players_skipped
                total_members_updated += members_updated
                completed_batches += 1

                current_time = time.time()
                if completed_batches % progress_interval == 0 or completed_batches == total_batches:
                    elapsed = current_time - start_time
                    speed_batches = completed_batches / elapsed if elapsed > 0 else 0
                    remaining_batches = total_batches - completed_batches
                    remaining_time = remaining_batches / speed_batches if speed_batches > 0 else 0
                    progress_percent = (completed_batches / total_batches) * 100

                    print(f"   📊 Прогресс: {completed_batches}/{total_batches} батчей ({progress_percent:.1f}%) | "
                          f"Записано: {total_players_saved} | Пропущено: {total_players_skipped} | "
                          f"Скорость: {speed_batches:.1f} батчей/сек | "
                          f"Осталось: ~{remaining_time:.0f}с")
            except Exception as e:
                print(f"   ❌ Ошибка в батче: {e}")
                log_error("wotb/account/info/", "EXCEPTION", "EXCEPTION", str(e), "batch_processing")

    elapsed = time.time() - start_time
    print(f"\n✅ Обновление таблиц players и clan_members завершено за {elapsed:.2f} сек.")
    print(f"   📈 Записано игроков: {total_players_saved}")
    print(f"   ⏭️ Пропущено (lbt не изменился): {total_players_skipped}")
    print(f"   📈 Обновлено связей в clan_members: {total_members_updated}")

    print("\n" + "=" * 70)
    print(" СОХРАНЕНИЕ ОШИБОК")
    print("=" * 70)
    save_errors_to_db()
    export_errors_to_excel()


if __name__ == "__main__":
    main()