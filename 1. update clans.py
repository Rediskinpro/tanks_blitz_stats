import time
import json
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from common.config import BATCH_SIZE, BATCH_WORKERS
from common.db import get_db_connection, get_cursor, release_db_connection, db_lock
from common.api import get_api_data
from common.logger import log_error, save_errors_to_db


def get_clan_ids_from_db():
    conn = get_db_connection()
    cursor = get_cursor(conn)
    try:
        cursor.execute("SELECT clan_id FROM clans UNION SELECT clan_id FROM clan_members")
        clan_ids = [row['clan_id'] for row in cursor.fetchall()]
    finally:
        release_db_connection(conn)
    print(f"🔍 Найдено {len(clan_ids)} кланов в БД для обновления")
    return clan_ids


def process_clan_batch(batch_clan_ids):
    api_data, failed_ids = get_api_data(batch_clan_ids, "wotb/clans/info/", param_name="clan_id")
    clans_processed = 0
    clans_with_errors = 0
    clans_with_null_response = 0

    with db_lock:
        conn = get_db_connection()
        cursor = get_cursor(conn)
        try:
            for clan_id_str, info in api_data.items():
                if info is None:
                    clans_with_null_response += 1
                    continue

                try:
                    clan_id = int(clan_id_str)
                    members_ids_raw = info.get('members_ids', [])
                    new_members_set = set(members_ids_raw)

                    cursor.execute('''
                               INSERT INTO clans (
                                    clan_id
                                    , name
                                    , tag
                                    , members_count
                                    , leader_name
                                    , members_ids
                                    , collected_date
                                    )
                               VALUES (%s, %s, %s, %s, %s, %s, %s)
                               ON CONFLICT (clan_id) DO UPDATE SET name           = EXCLUDED.name,
                                                                   tag            = EXCLUDED.tag,
                                                                   members_count  = EXCLUDED.members_count,
                                                                   leader_name    = EXCLUDED.leader_name,
                                                                   members_ids    = EXCLUDED.members_ids,
                                                                   collected_date = EXCLUDED.collected_date
                               ''', (
                        clan_id,
                        info.get('name'),
                        info.get('tag'),
                        info.get('members_count'),
                        info.get('leader_name'),
                        json.dumps(members_ids_raw),
                        int(datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
                    ))

                    cursor.execute("SELECT player_id FROM clan_members WHERE clan_id = %s", (clan_id,))
                    current_members = {row['player_id'] for row in cursor.fetchall()}

                    members_to_nullify = current_members - new_members_set
                    if members_to_nullify:
                        placeholders = ','.join(['%s'] * len(members_to_nullify))
                        cursor.execute(
                            f"UPDATE clan_members SET clan_id = NULL WHERE clan_id = %s AND player_id IN ({placeholders})",
                            (clan_id, *members_to_nullify)
                        )

                    if new_members_set:
                        records = [(clan_id, pid) for pid in new_members_set]
                        cursor.executemany("""
                                INSERT INTO clan_members (clan_id, player_id)
                                VALUES (%s, %s)
                                ON CONFLICT (player_id) DO UPDATE SET clan_id = EXCLUDED.clan_id""", records)

                    conn.commit()
                    clans_processed += 1

                except Exception as e:
                    conn.rollback()
                    clans_with_errors += 1
                    print(f"⚠️ Ошибка обработки клана {clan_id_str}: {e}")
                    log_error("wotb/clans/info/", "DB_ERROR", "PG_ERROR", str(e), clan_id_str)
                    failed_ids.append(int(clan_id_str))

        except Exception as e:
            conn.rollback()
            print(f"❌ Ошибка в батче кланов {batch_clan_ids}: {e}")
            log_error("wotb/clans/info/", "DB_ERROR", "PG_ERROR", str(e), str(batch_clan_ids))
            failed_ids.extend([int(clan_id) for clan_id in batch_clan_ids])
        finally:
            release_db_connection(conn)

    return clans_processed, clans_with_errors, clans_with_null_response, failed_ids


def main():
    start_time = time.time()
    print("🚀 Начало обновления таблиц clans и clan_members")

    clan_ids = get_clan_ids_from_db()
    if not clan_ids:
        print("⚠️ Таблицы clans и clan_members пусты. Завершаем работу.")
        return

    batches = [clan_ids[i:i + BATCH_SIZE] for i in range(0, len(clan_ids), BATCH_SIZE)]
    print(f"📦 Разбито на {len(batches)} батчей по {BATCH_SIZE} кланов.")

    total_processed = 0
    total_errors = 0
    total_null_response = 0
    total_failed = 0

    with ThreadPoolExecutor(max_workers=BATCH_WORKERS) as executor:
        results = list(executor.map(process_clan_batch, batches))
        for processed, errors, null_response, failed_ids in results:
            total_processed += processed
            total_errors += errors
            total_null_response += null_response
            total_failed += len(failed_ids)

    elapsed = time.time() - start_time
    print(f"\n📊 СТАТИСТИКА ОБНОВЛЕНИЯ КЛАНОВ")
    print(f"✅ Успешно обработано: {total_processed}")
    print(f"⚠️ Кланов с пустым ответом API: {total_null_response}")
    print(f"❌ Ошибок: {total_errors}")
    print(f"⏭️ Не удалось получить от API: {total_failed}")
    print(f"⏱️ Время: {elapsed:.2f} сек ({elapsed / 3600:.1f} ч)")
    print("СОХРАНЕНИЕ ОШИБОК")
    save_errors_to_db()


if __name__ == "__main__":
    main()