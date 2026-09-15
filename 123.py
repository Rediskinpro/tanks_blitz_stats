import time
import gc
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from common.config import MAX_WORKERS, PLAYERS_SAVE_INTERVAL
from common.db import get_db_connection, get_cursor, release_db_connection, db_lock
from common.api import get_api_data
from common.logger import log_error, save_errors_to_db, export_errors_to_excel
from common.memory_monitor import memory_monitor

RETRY_DELAY = 0.1

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
        for i in range(0, len(player_ids), chunk_size):
            chunk = player_ids[i:i + chunk_size]
            placeholders = ','.join(['%s'] * len(chunk))
            cursor.execute(f'''
                SELECT DISTINCT player_id FROM player_tanks_stats
                WHERE player_id IN ({placeholders})
                    AND collected_date = %s
                ''', chunk + [today_int])
            for row in cursor.fetchall():
                processed.add(row['player_id'])
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
        return [], 0, True

    tanks_list = api_data.get(str(player_id), [])
    if not isinstance(tanks_list, list):
        return [], 0, True

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

    return records_to_save, skipped_count, False


def save_tanks_stats_batch(tanks_stats_list):
    if not tanks_stats_list:
        return 0

    with db_lock:
        conn = get_db_connection()
        cursor = get_cursor(conn)
        try:
            cursor.executemany('''
                               INSERT INTO player_tanks_stats (
                               player_id, tank_id, last_battle_time, mark_of_mastery,
                               battles, damage_dealt, damage_received, frags, hits,
                               losses, shots, spotted, survived_battles, win_and_survived,
                               wins, collected_date
                               )
                               VALUES (
                               %(player_id)s, %(tank_id)s, %(last_battle_time)s, %(mark_of_mastery)s,
                               %(battles)s, %(damage_dealt)s, %(damage_received)s, %(frags)s, %(hits)s,
                               %(losses)s, %(shots)s, %(spotted)s, %(survived_battles)s, %(win_and_survived)s,
                               %(wins)s, %(collected_date)s
                               )
                               ON CONFLICT(player_id, tank_id, collected_date)
                               DO UPDATE SET last_battle_time = excluded.last_battle_time,
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


def retry_failed_players(failed_player_ids, tanks_lbt, today_int, buffer):
    print(f"\n🔄 Повторный запрос для {len(failed_player_ids)} игроков...")
    print(f"   ⚙️ Rate limit: {1 / RETRY_DELAY} запросов/сек")

    retry_success = 0
    records_saved = 0
    last_request_time = 0
    attempt_start = time.time()

    for i, player_id in enumerate(failed_player_ids):
        elapsed_since_last = time.time() - last_request_time
        if elapsed_since_last < RETRY_DELAY:
            time.sleep(RETRY_DELAY - elapsed_since_last)

        last_request_time = time.time()

        records, skipped, has_error = fetch_player_tanks_stats(player_id, tanks_lbt, today_int)

        if has_error:
            log_error("wotb/tanks/stats/", "API_ERROR", "RETRY_FAILED","Не удалось получить данные при повторном запросе", str(player_id))
        else:
            retry_success += 1
            if records:
                buffer.extend(records)
                records_saved += len(records)

        if (i + 1) % 100 == 0 or (i + 1) == len(failed_player_ids):
            elapsed = time.time() - attempt_start
            print(f"Прогресс: {i + 1}/{len(failed_player_ids)} | "f"Успешно: {retry_success} | Ошибок: {i + 1 - retry_success} | "f"Время: {elapsed:.1f}с")

    retry_failed = len(failed_player_ids) - retry_success

    if retry_failed > 0:
        print(f"\n   ⚠️ Осталось {retry_failed} игроков с ошибками после повторного запроса")
    else:
        print(f"\n   ✅ Все игроки успешно обработаны при повторном запросе")

    return retry_success, retry_failed, records_saved


def main():
    print("\n" + "=" * 70)
    print(" СБОР И ОБНОВЛЕНИЕ СТАТИСТИКИ ИГРОКОВ НА ТАНКАХ")
    print("=" * 70)
    memory_monitor.start()
    memory_monitor.print_memory_status()
    today_int = int(datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp())

    player_ids = get_active_players(today_int)
    if not player_ids:
        print("⏭️ Нет активных игроков для обновления статистики танков.")
        return

    print(f"\n🔍 Проверяем, какие игроки уже обработаны сегодня...")
    already_processed = get_already_processed_today(player_ids, today_int)

    if already_processed:
        print(f"⏭️ Пропускаем {len(already_processed)} игроков (данные уже собраны сегодня)")
        player_ids = [pid for pid in player_ids if pid not in already_processed]
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

    failed_player_ids = []

    for batch_ids, tanks_lbt in get_tanks_last_battle_times_batched(player_ids, batch_size):
        print(f"\n📦 Обработка батча из {len(batch_ids)} игроков...")

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {
                executor.submit(fetch_player_tanks_stats, pid, tanks_lbt, today_int): pid
                for pid in batch_ids
            }

            for future in as_completed(futures):
                player_id = futures[future]
                try:
                    records, skipped, has_error = future.result()

                    if has_error:
                        failed_player_ids.append(player_id)
                        stats['errors'] += 1
                    else:
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
                              f"Ошибок API: {stats['errors']} | "
                              f"Скорость: {speed:.1f} игр/сек | Осталось: ~{remaining:.0f}с")

                except Exception as e:
                    stats['errors'] += 1
                    failed_player_ids.append(player_id)
                    print(f"   ❌ Критическая ошибка при обработке игрока {player_id}: {e}")
                    log_error("wotb/tanks/stats/", "EXCEPTION", "THREAD_ERROR", str(e), str(player_id))

        del tanks_lbt
        gc.collect()

    if buffer:
        saved = save_tanks_stats_batch(buffer)
        total_records_saved += saved
        print(f"💾 Финальное сохранение: {saved} записей танков")

    retry_stats = {'success': 0, 'failed': 0, 'records': 0}
    if failed_player_ids:
        print(f"\n{'=' * 70}")
        print(f"🔄 ПОВТОРНЫЕ ЗАПРОСЫ ДЛЯ ИГРОКОВ С ОШИБКАМИ")
        print(f"{'=' * 70}")
        print(f"📋 Игроков с ошибками API: {len(failed_player_ids)}")
        print(f"\n⏳ Загрузка last_battle_time для {len(failed_player_ids)} игроков с ошибками...")
        failed_tanks_lbt = {}
        for batch_ids, tanks_lbt in get_tanks_last_battle_times_batched(failed_player_ids, batch_size):
            failed_tanks_lbt.update(tanks_lbt)
        print(f"   ✅ Загружено {len(failed_tanks_lbt)} записей")

        retry_success, retry_failed, retry_records = retry_failed_players(failed_player_ids, failed_tanks_lbt, today_int, buffer)

        retry_stats['success'] = retry_success
        retry_stats['failed'] = retry_failed
        retry_stats['records'] = retry_records

        if buffer:
            saved = save_tanks_stats_batch(buffer)
            total_records_saved += saved
            print(f"💾 Сохранено записей из повторных запросов: {saved}")

    elapsed = time.time() - start_time
    print(f"\n{'=' * 70}")
    print(f"✅ Сбор статистики танков завершён!")
    print(f"{'=' * 70}")
    print(f"   📊 Основная обработка:")
    print(f"      Обработано игроков: {processed_players}")
    print(f"      Успешно: {stats['success']}")
    print(f"      С ошибками API: {stats['errors']}")
    print(f"      Сохранено записей: {total_records_saved - retry_stats['records']}")
    print(f"      Пропущено танков: {total_tanks_skipped}")

    if failed_player_ids:
        print(f"\n   🔄 Повторные запросы:")
        print(f"      Всего игроков с ошибками: {len(failed_player_ids)}")
        print(f"      Успешно обработано: {retry_stats['success']}")
        print(f"      Осталось с ошибками: {retry_stats['failed']}")
        print(f"      Сохранено записей: {retry_stats['records']}")

    print(f"\n   ⏱️ Общее время: {elapsed:.1f} сек ({elapsed / 3600:.1f} ч)")
    memory_monitor.stop()
    memory_monitor.print_memory_status()

    print("\n" + "=" * 70)
    print(" СОХРАНЕНИЕ ЛОГА ОШИБОК")
    print("=" * 70)
    save_errors_to_db()
    export_errors_to_excel()


if __name__ == "__main__":
    main()