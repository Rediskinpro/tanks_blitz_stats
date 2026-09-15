import time
import json
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from common.config import BATCH_SIZE, BATCH_WORKERS
from common.db import get_db_connection, get_cursor, release_db_connection, db_lock
from common.api import get_api_data
from common.logger import log_error, save_errors_to_db, export_errors_to_excel
from common.memory_monitor import memory_monitor

def get_clan_ids_from_db():#создаём список с id кланов, которые нужно обновить
    conn = get_db_connection() #Получает один из пулов соединения (соединение с БД установлено сразу в несколько пулов)
    cursor = get_cursor(conn) #Создаем указатель на начало временной таблицы, а сама таблица создается по запросу дальше
    try: #Пробует выполнить те операции, что идут до блока finally
        cursor.execute("""
                        SELECT clan_id FROM clans
                        UNION
                        SELECT clan_id FROM clan_members
                        """) #Создает временную таблицу с clan_id из обеих таблиц и записывает всё в cursor
        clan_ids = [row['clan_id'] for row in cursor.fetchall()] #генерируем список с id игроков:
                                                                 #cursor.fetchall - берёт все строки из cursor
                                                                 #row['clan_id'] for row - записывает в список значение каждой строки из cursor.fetchall
    finally: #Выполняем операции независимо от успеха выполнения операций из блока try
        release_db_connection(conn) #возвращаем пул соединения
    print(f"🔍 Найдено {len(clan_ids)} кланов в БД для обновления")
    return clan_ids


def process_clan_batch(batch_clan_ids):#обработка батча с id кланов - запрашивает данные по апи, обрабатываем и записываем в БД свежие данные
    api_data = get_api_data(batch_clan_ids, "wotb/clans/info/", param_name="clan_id")
    if not api_data:
        return 0, 0, 0.0
    # Счётчики для мониторинга
    clans_processed = 0
    clans_with_errors = 0
    batch_start_time = time.time()

    with db_lock: #блочим БД, чтобы другие потоки не изменяли её, пока не закончит текущий поток
        conn = get_db_connection() #Получает один из пулов соединения (соединение с БД установлено сразу в несколько пулов)
        cursor = get_cursor(conn) #Создаем указатель на начало временной таблицы, а сама таблица создается по запросу дальше
        try: #Пробует выполнить те операции, что идут до блока finally
            for cid_str, info in api_data.items():#cid_str - clan_id в ответе от апи, но он приходит в виде строки, info - вся остальная инфа
                if info is None:#если инфы для текущего cid_str нет, то...
                    continue#Пропускает всё, что делается в текущей итерации цикла

                clan_start_time = time.time()
                try:
                    clan_id = int(cid_str)#переводим clan_id в integer
                    members_ids_raw = info.get('members_ids', [])#в словаре info ищет ключ members_ids и присваивает значение в список members_ids_raw, а если не находит, то создает пустой список
                    new_members_set = set(members_ids_raw)#переводит список members_ids_raw в множество

                    cursor.execute('''
                               INSERT INTO clans (clan_id, name, tag, members_count, leader_name, members_ids,
                                                  collected_date)
                               VALUES (%s, %s, %s, %s, %s, %s, %s)
                               ON CONFLICT (clan_id) DO UPDATE SET name           = EXCLUDED.name,
                                                                   tag            = EXCLUDED.tag,
                                                                   members_count  = EXCLUDED.members_count,
                                                                   leader_name    = EXCLUDED.leader_name,
                                                                   members_ids    = EXCLUDED.members_ids,
                                                                   collected_date = EXCLUDED.collected_date
                               ''', (#выполняет SQL код с БД
                                     #%s - подставляет значения, которые перечислены в скобках после '''. Каждый %s - следующее значение
                                   clan_id,
                                   info.get('name'),
                                   info.get('tag'),
                                   info.get('members_count'),
                                   info.get('leader_name'),
                                   json.dumps(members_ids_raw),#преобразовывает список members_ids_raw в одну строку, чтобы записать в БД
                                   int(datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()),#преобразует текущий день в число, чтобы сохранить его в БД. Так используется меньше памяти
                               ))

                    cursor.execute("SELECT player_id FROM clan_members WHERE clan_id = %s", (clan_id,))
                    current_members = {row['player_id'] for row in cursor.fetchall()}

                    members_to_nullify = current_members - new_members_set#список игроков, которым надо обнулить clan_id
                    if members_to_nullify:
                        placeholders = ','.join(['%s'] * len(members_to_nullify))#формирует строковое значение, состоящее из players_id, которым надо занулить clan_id
                                                                             #Это нужно, так как мы не знаем скольким игрокам нужно занулить clan_id, чтобы подставить в sql запрос ниже
                        cursor.execute(
                            f"UPDATE clan_members SET clan_id = NULL WHERE clan_id = %s AND player_id IN ({placeholders})",#после IN подставляет всю строку placeholders
                            (clan_id, *members_to_nullify)#оператор * записывает все значения из members_to_nullify. Clan_id пишется в первый %s, а все значения из members_to_nullify - в то, что в placeholders
                        )

                    if new_members_set:#если new_members_set не пустой
                        records = [(clan_id, pid) for pid in new_members_set]#генерируем список records: каждое значение это кортеж из clan_id и player_id
                        cursor.executemany("""
                                INSERT INTO clan_members (clan_id, player_id)
                                VALUES (%s, %s)
                                ON CONFLICT (player_id) DO UPDATE SET clan_id = EXCLUDED.clan_id""",
                            records#в каждом значении списка records лежит кортеж из clan_id и player_id, поэтому %s, %s найдёт оба значения тут.
                                   #executemany ожидает список кортежей, поэтому делает обновление для каждого значения в списке, то есть для каждого кортежа в списке
                        )
                    conn.commit()#коммитит изменения, записывает в БД. До этого изменения существовали только в рамках потока, который с ними работал
                    clans_processed += 1
                except Exception as e:#при ошибке внутри цикла (работаем с 1 кланом)
                    conn.rollback()#не вносим изменений
                    clans_with_errors += 1
                    print(f"⚠️ Ошибка обработки клана {cid_str}: {e}")
                    log_error("wotb/clans/info/", "DB_ERROR", "PG_ERROR", str(e), cid_str)

                clan_elapsed = time.time() - clan_start_time
                if clan_elapsed > 1.0:  # Если клан обрабатывается дольше 1 секунды
                    print(f"⚠️ Медленная обработка клана {cid_str}: {clan_elapsed:.2f} сек")

        except Exception as e:
            print(f"❌ Ошибка в батче кланов {batch_clan_ids}: {e}")
            log_error("wotb/clans/info/", "DB_ERROR", "PG_ERROR", str(e), str(batch_clan_ids))
        finally:
            release_db_connection(conn)

        batch_elapsed = time.time() - batch_start_time#считаем время обработки батча
        return clans_processed, clans_with_errors, batch_elapsed


def main():
    start_time = time.time() #записываем стартовое время
    memory_monitor.start()  # начинаем постоянный мониторинг памяти
    memory_monitor.print_memory_status()
    print("🚀 Начало обновления таблиц clans и clan_members")

    clan_ids = get_clan_ids_from_db() #получаем список id кланов для обновления

    if not clan_ids: #если список кланов пустой...
        print("⚠️ Таблицы clans и clan_members пусты. Завершаем работу.")
        return

    print("\n--- Обновление clans и clan_members по батчам ---")
    batches = [clan_ids[i:i + BATCH_SIZE] for i in range(0, len(clan_ids), BATCH_SIZE)]#разбиваем весь список кланов на батчи

    # Собираем статистику ошибок при обработке батча кланов
    total_processed = 0
    total_errors = 0
    total_time_in_batches = 0.0

    with ThreadPoolExecutor(max_workers=BATCH_WORKERS) as executor:#создаёт BATCH_WORKERS одновременных потоков
        results = list(executor.map(process_clan_batch, batches))#каждому потоку даёт задачу выполнить функцию process_clan_batch с переменной batches
                                                                #list нужен только для того, чтобы дождаться обработки всех batches
        # Агрегируем результаты
        for processed, errors, elapsed in results:
            total_processed += processed
            total_errors += errors
            total_time_in_batches += elapsed

    print("\n" + "=" * 70)
    print("📊 СТАТИСТИКА ОБНОВЛЕНИЯ КЛАНОВ")
    print("=" * 70)
    print(f"✅ Успешно обработано кланов: {total_processed}")
    print(f"❌ Ошибок при обработке: {total_errors}")
    print(f"⏱️ Время в функциях обработки: {total_time_in_batches:.2f} сек ({total_time_in_batches / 3600:.1f} ч)")
    print(f"⏱️ Общее время скрипта: {time.time() - start_time:.2f} сек ({(time.time() - start_time) / 3600:.1f} ч)")

    if total_errors > 0:
        error_rate = (total_errors / (total_processed + total_errors)) * 100
        print(f"⚠️ Процент ошибок: {error_rate:.2f}%")

    print("\n" + "=" * 70)
    print(" СОХРАНЕНИЕ ОШИБОК")
    print("=" * 70)
    save_errors_to_db() #сохраняем ошибки в БД
    export_errors_to_excel() #сохраняем ошибки в xlsx
    memory_monitor.stop()  # начинаем постоянный мониторинг памяти
    memory_monitor.print_memory_status()
    elapsed = time.time() - start_time #рассчитываем общее время выполнения
    print(f"\n✅ Обновление таблиц clans и clan_members завершено за {elapsed:.2f} сек ({elapsed / 3600:.1f} ч)")


if __name__ == "__main__":
    main()