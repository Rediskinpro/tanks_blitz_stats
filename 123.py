import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from common.config import BATCH_SIZE, BATCH_WORKERS, API_KEYS
from common.db import get_db_connection, get_cursor, release_db_connection
# Импортируем тестовые версии функций
from common.api_test import get_api_data, start_api_stats_monitor


def get_all_player_ids():
    conn = get_db_connection()
    cursor = get_cursor(conn)
    try:
        cursor.execute('''
                        SELECT player_id FROM players
                        UNION
                        SELECT player_id FROM clan_members
                        ''')
        player_ids = [row['player_id'] for row in cursor.fetchall()]
    finally:
        release_db_connection(conn)
    print(f"🔍 Найдено {len(player_ids)} уникальных player_id для обновления")
    return player_ids


def get_players_last_battle_times():
    conn = get_db_connection()
    cursor = get_cursor(conn)
    try:
        cursor.execute('''
                        SELECT player_id, MAX(last_battle_time) as last_battle_time FROM players
                        GROUP BY player_id
                        ''')
        result = {row['player_id']: row['last_battle_time'] for row in cursor.fetchall()}
    finally:
        release_db_connection(conn)
    print(f"✅ Загружены last_battle_time для {len(result)} игроков из БД")
    return result


def process_players_batch_test(batch, players_lbt, key_number):
    """Упрощенная версия без записи в БД, только имитация обработки"""
    # Делаем реальные запросы к API
    api_data, api_failed_ids = get_api_data(batch, "wotb/account/info/", param_name="account_id", extra="statistics.rating", key_number=key_number)

    failed_ids = list(set(api_failed_ids))

    players_saved = 0
    players_skipped = 0
    players_with_null_response = 0

    # Имитируем логику проверки без реальной записи в БД
    for player_id_str, info in api_data.items():
        player_id = int(player_id_str)
        if info is None:
            players_with_null_response += 1
            continue

        lbt_int = info.get('last_battle_time')
        prev_lbt = players_lbt.get(player_id)

        if prev_lbt is not None and lbt_int is not None and prev_lbt >= lbt_int:
            players_skipped += 1
            continue

        # Если дошли сюда, значит игрок был бы "записан"
        players_saved += 1

    return players_saved, players_skipped, players_with_null_response, failed_ids


def main():
    start_time = time.time()
    print("🚀 ТЕСТОВОЕ ОБНОВЛЕНИЕ ДАННЫХ ИГРОКОВ (БЕЗ ЗАПИСИ В БД)")

    # 🔑 ЗАПУСКАЕМ МОНИТОР СТАТИСТИКИ (вывод каждую минуту)
    start_api_stats_monitor()

    player_ids = get_all_player_ids()
    if not player_ids:
        print("⏭️ Нет игроков для обновления.")
        return

    players_lbt = get_players_last_battle_times()

    batches = [player_ids[i:i + BATCH_SIZE] for i in range(0, len(player_ids), BATCH_SIZE)]
    total_batches = len(batches)

    num_keys = len(API_KEYS)
    key_numbers = [(i % num_keys) + 1 for i in range(total_batches)]

    print(f"📦 Разбито на {total_batches} батчей по {BATCH_SIZE} игроков.")
    print(f"🔑 Используется {num_keys} API ключей")
    print(f"⚙️ Потоков: {BATCH_WORKERS * num_keys}")
    print("⏳ Начало обработки...\n")

    total_players_saved = 0
    total_players_skipped = 0
    total_null_response = 0
    total_failed_ids = 0
    completed_batches = 0
    progress_interval = 1000
    start_time_threading = time.time()

    # Увеличиваем количество потоков пропорционально ключам
    with ThreadPoolExecutor(max_workers=BATCH_WORKERS * num_keys) as executor:
        futures = [executor.submit(process_players_batch_test, batch, players_lbt, key_numbers[i]) for i, batch in
                   enumerate(batches)]

        for future in as_completed(futures):
            try:
                players_saved, players_skipped, null_response, failed_ids = future.result()
                total_players_saved += players_saved
                total_players_skipped += players_skipped
                total_null_response += null_response
                total_failed_ids += len(failed_ids)
                completed_batches += 1

                current_time = time.time()
                if completed_batches % progress_interval == 0 or completed_batches == total_batches:
                    elapsed = current_time - start_time_threading
                    speed_batches = completed_batches / elapsed if elapsed > 0 else 0
                    remaining_batches = total_batches - completed_batches
                    remaining_time = remaining_batches / speed_batches if speed_batches > 0 else 0

                    print(f"    Прогресс: {completed_batches}/{total_batches} батчей | "
                          f"Записано(имит.): {total_players_saved} | Пропущено: {total_players_skipped} | "
                          f"Пустых ответов: {total_null_response} | "
                          f"Ошибок: {total_failed_ids} | "
                          f"Скорость: {speed_batches:.1f} батчей/сек | Осталось: ~{remaining_time / 60:.0f}мин")
            except Exception as e:
                print(f"   ❌ Ошибка в future: {e}")

    elapsed = time.time() - start_time
    print(f"\n✅ Тестовое обновление завершено за {elapsed:.2f} сек ({elapsed / 3600:.1f} ч)")
    print(f"   📊 Итоговая статистика обработки:")
    print(f"      ✅ Успешно обработано (имитация): {total_players_saved}")
    print(f"      ⏭️ Пропущено (lbt не изменился): {total_players_skipped}")
    print(f"      ⚠️ Игроков с пустым ответом API: {total_null_response}")
    print(f"      ❌ Ошибок (API): {total_failed_ids}")

    # Финальная проверка распределения из глобального словаря
    print("\n" + "=" * 75)
    print(" 🏁 ИТОГОВАЯ ПРОВЕРКА РАСПРЕДЕЛЕНИЯ КЛЮЧЕЙ:")
    from common.api_test import api_stats, stats_lock
    with stats_lock:
        for key_num, data in api_stats.items():
            key_preview = API_KEYS[key_num - 1][:8] + "..."
            errors = data["total"] - data["success"]
            print(f"   Ключ #{key_num} ({key_preview}): "
                  f"Всего запросов: {data['total']:4d} | "
                  f"Успешных: {data['success']:4d} | "
                  f"Ошибок/Пустых: {errors:4d}")
    print("=" * 75)


if __name__ == "__main__":
    main()