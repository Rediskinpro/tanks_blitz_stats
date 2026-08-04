import time
from bisect import bisect_right
from collections import defaultdict
from common.db import get_db_connection, get_cursor, release_db_connection, db_lock

# Размер батча для удаления
DELETE_BATCH_SIZE = 5000


def longest_non_decreasing_subsequence_indices(values):
    """
    Находит индексы элементов, образующих самую длинную неубывающую подпоследовательность.
    Алгоритм: O(n log n).
    """
    n = len(values)
    if n == 0:
        return []

    tails = []
    tail_indices = []
    parent = [-1] * n

    for i, val in enumerate(values):
        pos = bisect_right(tails, val)

        if pos > 0:
            parent[i] = tail_indices[pos - 1]

        if pos == len(tails):
            tails.append(val)
            tail_indices.append(i)
        else:
            tails[pos] = val
            tail_indices[pos] = i

    result = []
    idx = tail_indices[-1] if tail_indices else -1
    while idx != -1:
        result.append(idx)
        idx = parent[idx]

    result.reverse()
    return result


def get_players_with_anomalies():
    """Находит player_id, у которых есть аномалии в player_tanks_stats"""
    conn = get_db_connection()
    cursor = get_cursor(conn)
    try:
        cursor.execute('''
            SELECT DISTINCT player_id 
            FROM (
                SELECT 
                    player_id,
                    last_battle_time,
                    LAG(last_battle_time) OVER (
                        PARTITION BY player_id, tank_id 
                        ORDER BY collected_date
                    ) as prev_lbt
                FROM player_tanks_stats
                WHERE last_battle_time IS NOT NULL
            ) sub
            WHERE last_battle_time < prev_lbt
        ''')
        player_ids = [row['player_id'] for row in cursor.fetchall()]
    finally:
        release_db_connection(conn)

    print(f"🔍 Найдено {len(player_ids)} игроков с аномалиями в player_tanks_stats")
    return player_ids


def get_player_tank_records(player_id):
    """
    Получает ВСЕ записи по всем танкам игрока одним запросом.
    Возвращает словарь: {tank_id: [(collected_date, last_battle_time), ...]}
    """
    conn = get_db_connection()
    cursor = get_cursor(conn)
    try:
        cursor.execute('''
            SELECT tank_id, collected_date, last_battle_time
            FROM player_tanks_stats
            WHERE player_id = %s AND last_battle_time IS NOT NULL
            ORDER BY tank_id, collected_date
        ''', (player_id,))

        # Группируем по tank_id
        tanks_data = defaultdict(list)
        for row in cursor.fetchall():
            tanks_data[row['tank_id']].append({
                'collected_date': row['collected_date'],
                'last_battle_time': row['last_battle_time']
            })
    finally:
        release_db_connection(conn)

    return tanks_data


def delete_records_batch(records_to_delete):
    """
    Удаляет записи батчами по (player_id, tank_id, collected_date).
    records_to_delete: список кортежей (player_id, tank_id, collected_date)
    """
    if not records_to_delete:
        return 0

    total_deleted = 0

    with db_lock:
        conn = get_db_connection()
        cursor = get_cursor(conn)
        try:
            for i in range(0, len(records_to_delete), DELETE_BATCH_SIZE):
                batch = records_to_delete[i:i + DELETE_BATCH_SIZE]

                # Формируем условие для батча
                conditions = []
                params = []
                for player_id, tank_id, collected_date in batch:
                    conditions.append("(player_id = %s AND tank_id = %s AND collected_date = %s)")
                    params.extend([player_id, tank_id, collected_date])

                sql = f"DELETE FROM player_tanks_stats WHERE {' OR '.join(conditions)}"
                cursor.execute(sql, params)
                total_deleted += cursor.rowcount

            conn.commit()
        except Exception as e:
            conn.rollback()
            print(f"❌ Ошибка удаления батча: {e}")
        finally:
            release_db_connection(conn)

    return total_deleted


def process_player(player_id):
    """
    Обрабатывает одного игрока: находит аномальные записи по всем его танкам.
    Возвращает список записей к удалению: [(player_id, tank_id, collected_date), ...]
    """
    tanks_data = get_player_tank_records(player_id)
    records_to_delete = []
    records_kept = 0

    for tank_id, records in tanks_data.items():
        if len(records) < 2:
            records_kept += len(records)
            continue

        collected_dates = [r['collected_date'] for r in records]
        lbt_values = [r['last_battle_time'] for r in records]

        # Находим индексы записей, которые нужно ОСТАВИТЬ
        keep_indices = longest_non_decreasing_subsequence_indices(lbt_values)
        keep_set = set(keep_indices)

        # Собираем записи к удалению
        for i in range(len(records)):
            if i not in keep_set:
                records_to_delete.append((player_id, tank_id, collected_dates[i]))

        records_kept += len(keep_indices)

    return records_to_delete, records_kept


def clean_player_tanks_stats():
    start_time = time.time()
    print("\n" + "=" * 70)
    print(" ОЧИСТКА ТАБЛИЦЫ player_tanks_stats ОТ АНОМАЛЬНЫХ ЗАПИСЕЙ")
    print("=" * 70)

    # Шаг 1: Находим игроков с аномалиями
    player_ids = get_players_with_anomalies()

    if not player_ids:
        print("✅ Аномалий не найдено. Завершаем работу.")
        return

    total_deleted = 0
    total_kept = 0
    processed = 0
    buffer = []  # Буфер для накопления записей перед удалением
    BUFFER_FLUSH_SIZE = 10000  # Сбрасываем буфер каждые 10000 записей

    # Шаг 2: Обрабатываем каждого игрока
    for player_id in player_ids:
        try:
            records_to_delete, records_kept = process_player(player_id)

            if records_to_delete:
                buffer.extend(records_to_delete)
                total_kept += records_kept

            processed += 1

            # Сбрасываем буфер при достижении размера
            if len(buffer) >= BUFFER_FLUSH_SIZE:
                deleted = delete_records_batch(buffer)
                total_deleted += deleted
                buffer = []

            # Прогресс каждые 500 игроков
            if processed % 500 == 0 or processed == len(player_ids):
                elapsed = time.time() - start_time
                speed = processed / elapsed if elapsed > 0 else 0
                remaining = (len(player_ids) - processed) / speed if speed > 0 else 0

                print(f"    Прогресс: {processed}/{len(player_ids)} игроков | "
                      f"Удалено: {total_deleted} | В буфере: {len(buffer)} | "
                      f"Оставлено: {total_kept} | "
                      f"Скорость: {speed:.1f} игр/сек | Осталось: ~{remaining:.0f}с")

        except Exception as e:
            print(f"⚠️ Ошибка обработки игрока {player_id}: {e}")
            processed += 1
            continue

    # Финальный сброс буфера
    if buffer:
        deleted = delete_records_batch(buffer)
        total_deleted += deleted
        print(f"💾 Финальный сброс буфера: удалено {deleted} записей")

    # Шаг 3: Финальная статистика
    elapsed = time.time() - start_time
    print("\n" + "=" * 70)
    print("📊 РЕЗУЛЬТАТЫ ОЧИСТКИ")
    print("=" * 70)
    print(f"✅ Обработано игроков: {processed}")
    print(f"🗑️ Удалено записей: {total_deleted}")
    print(f"💾 Оставлено записей: {total_kept}")
    print(f"⏱️ Время выполнения: {elapsed:.1f} сек ({elapsed / 60:.1f} мин)")


def verify_cleanup():
    """Проверяет, что аномалий больше нет"""
    print("\n🔍 Проверка результата...")

    conn = get_db_connection()
    cursor = get_cursor(conn)
    try:
        cursor.execute('''
            SELECT COUNT(*) as anomalies_count
            FROM (
                SELECT 
                    last_battle_time,
                    LAG(last_battle_time) OVER (
                        PARTITION BY player_id, tank_id 
                        ORDER BY collected_date
                    ) as prev_lbt,
                    collected_date,
                    LAG(collected_date) OVER (
                        PARTITION BY player_id, tank_id 
                        ORDER BY collected_date
                    ) as prev_date
                FROM player_tanks_stats
                WHERE last_battle_time IS NOT NULL
            ) sub
            WHERE last_battle_time < prev_lbt
              AND collected_date > prev_date
        ''')
        result = cursor.fetchone()
        anomalies = result['anomalies_count']
    finally:
        release_db_connection(conn)

    if anomalies == 0:
        print("✅ Аномалий больше нет! Таблица очищена успешно.")
    else:
        print(f"⚠️ Осталось {anomalies} аномалий. Возможно, нужен повторный запуск.")

    return anomalies


if __name__ == "__main__":
    clean_player_tanks_stats()
    verify_cleanup()