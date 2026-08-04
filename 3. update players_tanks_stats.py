import time
import gc
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from common.config import MAX_WORKERS, PLAYERS_SAVE_INTERVAL
from common.db import get_db_connection, get_cursor, release_db_connection, db_lock
from common.api import get_api_data
from common.logger import log_error, save_errors_to_db, export_errors_to_excel
from common.memory_monitor import memory_monitor


def get_active_players(today_int):
    conn = get_db_connection()
    cursor = get_cursor(conn)
    try:
        cursor.execute('''
                       SELECT DISTINCT player_id FROM players
                       WHERE collected_date = %s
                       ''', (today_int,))

        player_ids = [row['player_id'] for row in cursor.fetchall()]
    finally:
        release_db_connection(conn)
    print(f"🔍 Найдено {len(player_ids)} активных игроков (играли сегодня или вчера)")
    return player_ids


def get_already_processed_today(player_ids, today_int):
    processed = set()
    if not player_ids:
        return processed
    conn = get_db_connection()
    cursor = get_cursor(conn)
    try:
        chunk_size = 5000
        for i in range(0, len(player_ids), chunk_size):#проверяем все player_id из переданного списка по 5к
            chunk = player_ids[i:i + chunk_size]
            placeholders = ','.join(['%s'] * len(chunk))
            cursor.execute(f'''
                SELECT DISTINCT player_id FROM player_tanks_stats
                WHERE player_id IN ({placeholders})
                    AND collected_date = %s
                ''', chunk + [today_int])
            for row in cursor.fetchall():
                processed.add(row['player_id'])#добавляем во множество processed те player_id из 5к, которые нашлись в sql запросе
    finally:
         release_db_connection(conn)

    return processed


def get_tanks_last_battle_times_batched(player_ids, batch_size=5000):
    total_players = len(player_ids)

    for i in range(0, total_players, batch_size):
        batch_ids = player_ids[i:i + batch_size]
        tanks_lbt = {}
        conn = get_db_connection()
        cursor = get_cursor(conn)
        try:
            chunk_size = 5000
            for j in range(0, len(batch_ids), chunk_size):
                chunk = batch_ids[j:j + chunk_size]
                placeholders = ','.join(['%s'] * len(chunk))

                cursor.execute(f'''
                    SELECT DISTINCT ON (player_id, tank_id) player_id, tank_id, last_battle_time FROM player_tanks_stats
                    WHERE player_id IN ({placeholders})
                    ORDER BY player_id, tank_id, collected_date DESC
                ''', chunk)
                for row in cursor.fetchall():
                    key = (row['player_id'], row['tank_id'])
                    tanks_lbt[key] = row['last_battle_time']
        finally:
            release_db_connection(conn)

        yield batch_ids, tanks_lbt
        print(f"📊 Обработано {min(i + batch_size, total_players)}/{total_players} игроков")


def fetch_player_tanks_stats(player_id, tanks_lbt, today_int):
    api_data = get_api_data([player_id], "wotb/tanks/stats/", param_name="account_id")
    if not api_data:
        return [], 0

    tanks_list = api_data.get(str(player_id), [])#список словарей, в которых вложены ещё списки словарей и т.д.
    if not isinstance(tanks_list, list):
        return [], 0

    records_to_save = []
    skipped_count = 0

    for tank in tanks_list:
        if not isinstance(tank, dict):
            continue
        tank_stats = tank.get("all", {}) or {}
        if tank_stats.get("battles", 0) == 0:
            continue
        tank_id = tank.get("tank_id")
        raw_lbt = tank.get("last_battle_time")
        lbt_int = raw_lbt if raw_lbt else None
        key = (player_id, tank_id)
        prev_lbt = tanks_lbt.get(key)
        if prev_lbt is not None and lbt_int is not None and prev_lbt >= lbt_int:
            skipped_count += 1
            continue

        records_to_save.append({
            'player_id': player_id,
            'tank_id': tank_id,
            'battle_life_time': tank.get("battle_life_time"),
            'last_battle_time': lbt_int,
            'mark_of_mastery': tank.get("mark_of_mastery"),
            'battles': tank_stats.get("battles", 0),
            'damage_dealt': tank_stats.get("damage_dealt", 0),
            'damage_received': tank_stats.get("damage_received", 0),
            'frags': tank_stats.get("frags", 0),
            'hits': tank_stats.get("hits", 0),
            'losses': tank_stats.get("losses", 0),
            'shots': tank_stats.get("shots", 0),
            'spotted': tank_stats.get("spotted", 0),
            'survived_battles': tank_stats.get("survived_battles", 0),
            'win_and_survived': tank_stats.get("win_and_survived", 0),
            'wins': tank_stats.get("wins", 0),
            'collected_date': today_int,
        })

    return records_to_save, skipped_count


def save_tanks_stats_batch(tanks_stats_list):
    if not tanks_stats_list:
        return 0

    with db_lock:
        conn = get_db_connection()
        cursor = get_cursor(conn)
        try:
            cursor.executemany('''
                               INSERT INTO player_tanks_stats (
                               player_id,
                               tank_id,
                               battle_life_time,
                               last_battle_time,
                               mark_of_mastery,
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
                               wins,
                               collected_date
                               )
                               VALUES (
                               %(player_id)s,
                               %(tank_id)s,
                               %(battle_life_time)s,
                               %(last_battle_time)s,
                               %(mark_of_mastery)s,
                               %(battles)s,
                               %(damage_dealt)s,
                               %(damage_received)s,
                               %(frags)s,
                               %(hits)s,
                               %(losses)s,
                               %(shots)s,
                               %(spotted)s,
                               %(survived_battles)s,
                               %(win_and_survived)s,
                               %(wins)s,
                               %(collected_date)s
                               )
                               ON CONFLICT(
                               player_id,
                               tank_id,
                               collected_date
                               )
                               DO UPDATE SET battle_life_time = excluded.battle_life_time,
                                             last_battle_time = excluded.last_battle_time,
                                             mark_of_mastery  = excluded.mark_of_mastery,
                                             battles          = excluded.battles,
                                             damage_dealt     = excluded.damage_dealt,
                                             damage_received  = excluded.damage_received,
                                             frags            = excluded.frags,
                                             hits             = excluded.hits,
                                             losses           = excluded.losses,
                                             shots            = excluded.shots,
                                             spotted          = excluded.spotted,
                                             survived_battles = excluded.survived_battles,
                                             win_and_survived = excluded.win_and_survived,
                                             wins             = excluded.wins
                               ''', tanks_stats_list)
            conn.commit()
            return len(tanks_stats_list)
        except Exception as e:
            conn.rollback()
            print(f"❌ Ошибка сохранения батча статистики танков: {e}")
            log_error("wotb/tanks/stats/", "DB_ERROR", "PG_ERROR", str(e), "batch_save")
            return 0
        finally:
            release_db_connection(conn)


def main():
    print("\n" + "=" * 70)
    print(" СБОР И ОБНОВЛЕНИЕ СТАТИСТИКИ ИГРОКОВ НА ТАНКАХ")
    print("=" * 70)
    memory_monitor.start()
    memory_monitor.print_memory_status()
    today_int = int(datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp())

    player_ids = get_active_players(today_int)#собираем игроков, которых надо обновить
    if not player_ids:
        print("⏭️ Нет активных игроков для обновления статистики танков.")
        return

    print(f"\n🔍 Проверяем, какие игроки уже обработаны сегодня...")
    already_processed = get_already_processed_today(player_ids, today_int)#поулчаем множество игроков, по которым данные в player_tanks_stats уже собраны

    if already_processed:
        print(f"⏭️ Пропускаем {len(already_processed)} игроков (данные уже собраны сегодня)")
        player_ids = [pid for pid in player_ids if pid not in already_processed]#генерирует список player_id: перебирает все из player_ids и проверяет их наличие в already_processed
        print(f"📋 Осталось обработать: {len(player_ids)} игроков")

    if not player_ids:
        print("✅ Все активные игроки уже обработаны сегодня. Завершаем работу.")
        return

    print(f"\n🚀 Начинаем сбор статистики танков для {len(player_ids)} игроков...")
    print(f"⚙️ Потоков: {MAX_WORKERS} | Промежуточное сохранение каждые {PLAYERS_SAVE_INTERVAL} игроков")

    total_records_saved = 0
    total_tanks_skipped = 0
    processed_players = 0
    buffer = []
    stats = {'success': 0, 'errors': 0}
    batch_size = 5000
    start_time = time.time()

    for batch_ids, tanks_lbt in get_tanks_last_battle_times_batched(player_ids, batch_size):#get_tanks_last_battle_times_batched работает как генератор, цикл выполняется пока генератор не закончит генерировать
        print(f"\n📦 Обработка батча из {len(batch_ids)} игроков...")

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {
                executor.submit(fetch_player_tanks_stats, pid, tanks_lbt, today_int): pid
                for pid in batch_ids
            }

            for future in as_completed(futures):
                player_id = futures[future]
                try:
                    records, skipped = future.result()

                    if records:
                        buffer.extend(records)

                    total_tanks_skipped += skipped
                    stats['success'] += 1
                    processed_players += 1

                    if processed_players % PLAYERS_SAVE_INTERVAL == 0:
                        if buffer:
                            saved = save_tanks_stats_batch(buffer)
                            total_records_saved += saved
                            buffer = []

                        elapsed = time.time() - start_time
                        speed = processed_players / elapsed if elapsed > 0 else 0
                        remaining = (len(player_ids) - processed_players) / speed if speed > 0 else 0

                        print(f"    📊 Прогресс: {processed_players}/{len(player_ids)} игроков | "
                              f"Сохранено: {total_records_saved} | Пропущено танков: {total_tanks_skipped} | "
                              f"Скорость: {speed:.1f} игр/сек | Осталось: ~{remaining:.0f}с")

                except Exception as e:
                    stats['errors'] += 1
                    print(f"   ❌ Критическая ошибка при обработке игрока {player_id}: {e}")
                    log_error("wotb/tanks/stats/", "EXCEPTION", "THREAD_ERROR", str(e), str(player_id))

        del tanks_lbt#удаляет значения в списке tanks_lbt, но не сам список
        gc.collect()#очищает память, занимаемую списком tanks_lbt

    if buffer:
        saved = save_tanks_stats_batch(buffer)
        total_records_saved += saved
        print(f"💾 Финальное сохранение: {saved} записей танков")

    elapsed = time.time() - start_time
    print(f"\n✅ Сбор статистики танков завершён!")
    print(f"   Обработано игроков: {processed_players} (Успешно: {stats['success']}, Ошибок: {stats['errors']})")
    print(f"   Сохранено записей в player_tanks_stats: {total_records_saved}")
    print(f"   Пропущено танков (last_battle_time не изменился): {total_tanks_skipped}")
    print(f"   ⏱️ Время выполнения: {elapsed:.1f} сек")
    memory_monitor.stop()
    memory_monitor.print_memory_status()

    print("\n" + "=" * 70)
    print(" СОХРАНЕНИЕ ЛОГА ОШИБОК")
    print("=" * 70)
    save_errors_to_db()
    export_errors_to_excel()


if __name__ == "__main__":
    main()