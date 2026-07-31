import time
import json
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from common.config import BATCH_SIZE, BATCH_WORKERS
from common.db import get_db_connection, get_cursor, release_db_connection, db_lock
from common.api import get_api_data
from common.logger import log_error, save_errors_to_db, export_errors_to_excel


def get_clan_ids_from_db():
    with db_lock:
        conn = get_db_connection()
        cursor = get_cursor(conn)
        try:
            cursor.execute("""
                           SELECT clan_id FROM clans
                           UNION
                           SELECT clan_id FROM clan_members
                           """)
            clan_ids = [row['clan_id'] for row in cursor.fetchall()]
        finally:
            release_db_connection(conn)
    print(f"🔍 Найдено {len(clan_ids)} кланов в БД для обновления")
    return clan_ids


def process_clan_batch(batch_clan_ids):
    api_data = get_api_data(batch_clan_ids, "wotb/clans/info/", param_name="clan_id")
    if not api_data:
        return

    with db_lock:
        conn = get_db_connection()
        cursor = get_cursor(conn)
        try:
            for cid_str, info in api_data.items():
                if info is None:
                    continue
                clan_id = int(cid_str)
                members_ids_raw = info.get('members_ids', [])
                new_members_set = set(members_ids_raw)

                cursor.execute('''
                               INSERT INTO clans (clan_id, name, tag, members_count, leader_name, members_ids,
                                                  collected_date, updated_at)
                               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                               ON CONFLICT (clan_id) DO UPDATE SET name           = EXCLUDED.name,
                                                                   tag            = EXCLUDED.tag,
                                                                   members_count  = EXCLUDED.members_count,
                                                                   leader_name    = EXCLUDED.leader_name,
                                                                   members_ids    = EXCLUDED.members_ids,
                                                                   collected_date = EXCLUDED.collected_date,
                                                                   updated_at     = EXCLUDED.updated_at
                               ''', (
                                   clan_id,
                                   info.get('name'),
                                   info.get('tag'),
                                   info.get('members_count'),
                                   info.get('leader_name'),
                                   json.dumps(members_ids_raw),
                                   datetime.now().strftime("%Y-%m-%d"),
                                   datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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
                    cursor.executemany(
                        """INSERT INTO clan_members (clan_id, player_id)
                           VALUES (%s, %s)
                           ON CONFLICT (player_id) DO UPDATE SET clan_id = EXCLUDED.clan_id""",
                        records
                    )

            conn.commit()
        except Exception as e:
            conn.rollback()
            print(f"❌ Ошибка в батче кланов {batch_clan_ids}: {e}")
            log_error("wotb/clans/info/", "DB_ERROR", "PG_ERROR", str(e), str(batch_clan_ids))
        finally:
            release_db_connection(conn)


def process_null_clan_members():
    print("\n Обработка игроков с NULL clan_id в clan_members...")
    with db_lock:
        conn = get_db_connection()
        cursor = get_cursor(conn)
        try:
            cursor.execute("SELECT player_id FROM clan_members WHERE clan_id IS NULL")
            null_player_ids = [row['player_id'] for row in cursor.fetchall()]
        finally:
            release_db_connection(conn)

    if not null_player_ids:
        print("✅ Нет игроков с NULL clan_id для проверки.")
        return

    print(f"🔍 Найдено {len(null_player_ids)} игроков с NULL clan_id. Запрашиваем их данные через API...")

    for i in range(0, len(null_player_ids), BATCH_SIZE):
        batch = null_player_ids[i:i + BATCH_SIZE]
        api_data = get_api_data(batch, "wotb/clans/accountinfo/", param_name="account_id")

        if api_data:
            with db_lock:
                conn = get_db_connection()
                cursor = get_cursor(conn)
                try:
                    for pid_str, info in api_data.items():
                        if info is None:
                            continue
                        player_id = int(pid_str)
                        new_clan_id = info.get('clan_id')
                        if new_clan_id:
                            cursor.execute(
                                "UPDATE clan_members SET clan_id = %s WHERE player_id = %s", (new_clan_id, player_id)
                            )
                    conn.commit()
                except Exception as e:
                    conn.rollback()
                    print(f"❌ Ошибка обновления clan_members для батча: {e}")
                    log_error("wotb/clans/accountinfo/", "DB_ERROR", "PG_ERROR", str(e), str(batch))
                finally:
                    release_db_connection(conn)

    print("✅ Обработка игроков с NULL clan_id завершена.")


def main():
    start_time = time.time()
    print("🚀 Начало обновления таблиц clans и clan_members")

    clan_ids = get_clan_ids_from_db()

    if not clan_ids:
        print("⚠️ Таблицы clans и clan_members пусты. Завершаем работу.")
        return

    print("\n--- Обновление clans и clan_members по батчам ---")
    batches = [clan_ids[i:i + BATCH_SIZE] for i in range(0, len(clan_ids), BATCH_SIZE)]

    with ThreadPoolExecutor(max_workers=BATCH_WORKERS) as executor:
        list(executor.map(process_clan_batch, batches))

    process_null_clan_members()

    print("\n" + "=" * 70)
    print(" СОХРАНЕНИЕ ОШИБОК")
    print("=" * 70)
    save_errors_to_db()
    export_errors_to_excel()

    elapsed = time.time() - start_time
    print(f"\n✅ Обновление таблиц clans и clan_members завершено за {elapsed:.2f} сек.")


if __name__ == "__main__":
    main()