import pandas as pd
import requests
import time
import json
import sqlite3
import threading
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

# ============================================================
# КОНСТАНТЫ
# ============================================================
API_KEY = "59fbd1d08ae11577dc9355ad1ab44166"
BASE_URL = "https://papi.tanksblitz.ru"
BATCH_SIZE = 100
RATE_LIMIT = 15
MAX_WORKERS = 15
BATCH_WORKERS = 10
DELAY = 0.1
DB_FILE = "test_tanks_blitz.db"
ERRORS_FILE = "errors_log.xlsx"
TANKS_SAVE_INTERVAL = 100

# Глобальный лог ошибок
error_log = []

# ============================================================
# БЛОКИРОВКА ДЛЯ БАЗЫ ДАННЫХ
# ============================================================
db_lock = threading.Lock()

# ============================================================
# РЕЙТ-ЛИМИТЕР (потокобезопасный)
# ============================================================
class RateLimiter:
    def __init__(self, max_rps):
        self.max_rps = max_rps
        self.times = []
        self.lock = threading.Lock()

    def wait(self):
        with self.lock:
            now = time.time()
            self.times = [t for t in self.times if now - t < 1.0]
            if len(self.times) >= self.max_rps:
                sleep_time = 1.0 - (now - self.times[0]) + 0.01
                if sleep_time > 0:
                    time.sleep(sleep_time)
            self.times.append(time.time())

rate_limiter = RateLimiter(RATE_LIMIT)

# ============================================================
# ЛОГИРОВАНИЕ ОШИБОК
# ============================================================
def log_error(endpoint, error_field, error_code, error_message, ids):
    error_log.append({
        'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        'endpoint': endpoint,
        'error_field': str(error_field),
        'error_code': str(error_code),
        'error_message': str(error_message)[:200],
        'ids': str(ids)[:100]
    })

def export_errors_to_excel(filename=ERRORS_FILE):
    if not error_log:
        print("✅ Ошибок не обнаружено, файл не создан")
        return

    df_errors = pd.DataFrame(error_log)
    cols = ['timestamp', 'endpoint', 'error_field', 'error_code', 'error_message', 'ids']
    df_errors = df_errors[cols]

    with pd.ExcelWriter(filename, engine='openpyxl') as writer:
        df_errors.to_excel(writer, sheet_name='Ошибки', index=False)
    print(f"💾 Лог ошибок сохранён в {filename} ({len(df_errors)} записей)")

def save_errors_to_db():
    if not error_log:
        return

    with db_lock:
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.executemany('''
                INSERT INTO error_log (timestamp, endpoint, error_field, error_code, error_message, ids)
                VALUES (:timestamp, :endpoint, :error_field, :error_code, :error_message, :ids)
            ''', error_log)
            conn.commit()
            print(f"💾 Сохранено {len(error_log)} записей об ошибках в БД")
        except Exception as e:
            conn.rollback()
            print(f"❌ Ошибка сохранения ошибок: {e}")
        finally:
            conn.close()

# ============================================================
# ПОДКЛЮЧЕНИЕ К БД
# ============================================================
def get_db_connection():
    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn

# ============================================================
# УНИВЕРСАЛЬНЫЙ ЗАПРОС К API
# ============================================================
def get_api_data(ids, endpoint, param_name="account_id", depth=0, max_depth=4):
    if not ids:
        return {}

    rate_limiter.wait()
    ids_str = ",".join(map(str, ids))
    params = {"application_id": API_KEY, param_name: ids_str}

    try:
        response = requests.get(f"{BASE_URL}/{endpoint}", params=params, timeout=15)
        data = response.json()

        if data.get("status") == "ok":
            return data.get("data", {})
        else:
            error_info = data.get("error", {})
            if depth == 0:
                log_error(endpoint, error_info.get("field", "unknown"),
                          error_info.get("code", "unknown"),
                          error_info.get("message", "unknown"), ids_str)
            return {}
    except Exception as e:
        if depth == 0:
            log_error(endpoint, "EXCEPTION", "EXCEPTION", str(e), ids_str)
        return {}

    if depth < max_depth and len(ids) > 1:
        time.sleep(DELAY)
        mid = len(ids) // 2
        left = get_api_data(ids[:mid], endpoint, param_name, depth + 1, max_depth)
        right = get_api_data(ids[mid:], endpoint, param_name, depth + 1, max_depth)
        left.update(right)
        return left
    return {}

# ============================================================
# ШАГ 1: Получение всех player_id из таблицы players
# ============================================================
def get_all_player_ids():
    """Получает все player_id из таблицы players"""
    with db_lock:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT player_id FROM players")
        player_ids = [row['player_id'] for row in cursor.fetchall()]
        conn.close()
    print(f"🔍 Шаг 1: Найдено {len(player_ids)} player_id в таблице players")
    return player_ids

# ============================================================
# ШАГ 2: Обновление всей таблицы players для ВСЕХ игроков
# ============================================================
def update_all_players(player_ids):
    """Обновляет всю таблицу players для ВСЕХ игроков через API"""
    print(f"\n📊 Шаг 2: Обновление {len(player_ids)} игроков в таблице players...")

    total_batches = (len(player_ids) + BATCH_SIZE - 1) // BATCH_SIZE
    updated_count = 0

    for i in range(0, len(player_ids), BATCH_SIZE):
        batch = player_ids[i:i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        print(f"   📦 Батч {batch_num}/{total_batches} (Игроки: {batch[0]} ... {batch[-1]})")

        api_data = get_api_data(batch, "wotb/account/info/", param_name="account_id")

        if api_data:
            current_date = datetime.now().strftime("%Y-%m-%d")
            current_datetime = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            batch_players = []
            for pid_str, info in api_data.items():
                if info is None:
                    continue
                player_id = int(pid_str)
                clan_id = info.get('clan_id')

                raw_lbt = info.get('last_battle_time')
                lbt_str = None
                if raw_lbt:
                    try:
                        lbt_str = datetime.fromtimestamp(raw_lbt).strftime("%Y-%m-%d %H:%M:%S")
                    except:
                        pass

                batch_players.append({
                    'player_id': player_id,
                    'nickname': info.get('nickname'),
                    'clan_id': clan_id,
                    'last_battle_time': lbt_str,
                    'collected_date': current_date,
                    'updated_at': current_datetime,
                    'processed_at': current_datetime
                })

            # Сохраняем батч в БД
            with db_lock:
                conn = get_db_connection()
                cursor = conn.cursor()
                try:
                    cursor.executemany('''
                        INSERT OR REPLACE INTO players (player_id, nickname, clan_id, last_battle_time, collected_date, updated_at, processed_at)
                        VALUES (:player_id, :nickname, :clan_id, :last_battle_time, :collected_date, :updated_at, :processed_at)
                    ''', batch_players)
                    conn.commit()
                    updated_count += len(batch_players)
                except Exception as e:
                    conn.rollback()
                    print(f"   ❌ Ошибка сохранения батча игроков: {e}")
                finally:
                    conn.close()

            print(f"   ✓ Обновлено {len(batch_players)} записей")
        else:
            print(f"   ⚠️ Нет данных для этого батча")

    print(f"\n✅ Шаг 2: Обновлено {updated_count} записей в таблице players")
    return updated_count

# ============================================================
# ШАГ 3: Получение всех clan_id из clans + дополнение из players
# ============================================================
def get_all_clan_ids():
    """Получает все clan_id из clans и дополняет теми, что есть в players но нет в clans"""
    with db_lock:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Получаем все clan_id из clans
        cursor.execute("SELECT clan_id FROM clans")
        clans_clan_ids = set(row['clan_id'] for row in cursor.fetchall())

        # Получаем все clan_id из players
        cursor.execute("SELECT DISTINCT clan_id FROM players WHERE clan_id IS NOT NULL")
        players_clan_ids = set(row['clan_id'] for row in cursor.fetchall())

        conn.close()

    # Объединяем: все clan_id из clans + те, что есть в players но нет в clans
    all_clan_ids = clans_clan_ids | players_clan_ids

    print(f"🔍 Шаг 3: Найдено {len(clans_clan_ids)} clan_id в clans, {len(players_clan_ids)} clan_id в players")
    print(f"🔍 Шаг 3: Итого {len(all_clan_ids)} уникальных clan_id для обновления")

    return list(all_clan_ids)

# ============================================================
# ШАГ 4: Обновление всей таблицы clans для ВСЕХ clan_id
# ============================================================
def update_all_clans(clan_ids):
    """Обновляет всю таблицу clans для ВСЕХ clan_id через API"""
    print(f"\n📊 Шаг 4: Обновление {len(clan_ids)} кланов в таблице clans...")

    total_batches = (len(clan_ids) + BATCH_SIZE - 1) // BATCH_SIZE
    updated_count = 0

    for i in range(0, len(clan_ids), BATCH_SIZE):
        batch = clan_ids[i:i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        print(f"   📦 Батч {batch_num}/{total_batches} (Кланы: {batch[0]} ... {batch[-1]})")

        api_data = get_api_data(batch, "wotb/clans/info/", param_name="clan_id")

        if api_data:
            current_date = datetime.now().strftime("%Y-%m-%d")
            current_datetime = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            batch_clans = []
            for cid_str, info in api_data.items():
                if info is None:
                    continue
                clan_id = int(cid_str)
                members_ids_raw = info.get('members_ids', [])

                batch_clans.append({
                    'clan_id': clan_id,
                    'name': info.get('name'),
                    'tag': info.get('tag'),
                    'members_count': info.get('members_count'),
                    'leader_name': info.get('leader_name'),
                    'members_ids': json.dumps(members_ids_raw) if isinstance(members_ids_raw, list) else "[]",
                    'collected_date': current_date,
                    'updated_at': current_datetime
                })

            # Сохраняем батч в БД
            with db_lock:
                conn = get_db_connection()
                cursor = conn.cursor()
                try:
                    cursor.executemany('''
                        INSERT OR REPLACE INTO clans (clan_id, name, tag, members_count, leader_name, members_ids, collected_date, updated_at)
                        VALUES (:clan_id, :name, :tag, :members_count, :leader_name, :members_ids, :collected_date, :updated_at)
                    ''', batch_clans)
                    conn.commit()
                    updated_count += len(batch_clans)
                except Exception as e:
                    conn.rollback()
                    print(f"   ❌ Ошибка сохранения батча кланов: {e}")
                finally:
                    conn.close()

            print(f"   ✓ Обновлено {len(batch_clans)} записей")
        else:
            print(f"   ⚠️ Нет данных для этого батча")

    print(f"\n✅ Шаг 4: Обновлено {updated_count} записей в таблице clans")
    return updated_count

# ============================================================
# ШАГ 5: Обновление таблицы clan_members для всех кланов
# ============================================================
def update_all_clan_members(clan_ids):
    """Обновляет таблицу clan_members для всех кланов"""
    print(f"\n📊 Шаг 5: Обновление clan_members для {len(clan_ids)} кланов...")

    total_batches = (len(clan_ids) + BATCH_SIZE - 1) // BATCH_SIZE
    updated_count = 0

    for i in range(0, len(clan_ids), BATCH_SIZE):
        batch = clan_ids[i:i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        print(f"   📦 Батч {batch_num}/{total_batches} (Кланы: {batch[0]} ... {batch[-1]})")

        # Получаем данные кланов из БД
        with db_lock:
            conn = get_db_connection()
            cursor = conn.cursor()

            placeholders = ','.join(['?' for _ in batch])
            cursor.execute(f"SELECT clan_id, members_ids FROM clans WHERE clan_id IN ({placeholders})", batch)
            clans_data = cursor.fetchall()
            conn.close()

        # Обновляем clan_members для каждого клана
        with db_lock:
            conn = get_db_connection()
            cursor = conn.cursor()

            try:
                for clan_row in clans_data:
                    clan_id = clan_row['clan_id']
                    members_ids = json.loads(clan_row['members_ids'])

                    # Удаляем старые записи этого клана
                    cursor.execute("DELETE FROM clan_members WHERE clan_id = ?", (clan_id,))

                    # Вставляем новые записи
                    members_records = [(clan_id, pid) for pid in members_ids]
                    if members_records:
                        cursor.executemany('''
                            INSERT OR IGNORE INTO clan_members (clan_id, player_id)
                            VALUES (?, ?)
                        ''', members_records)
                        updated_count += len(members_records)

                conn.commit()
            except Exception as e:
                conn.rollback()
                print(f"   ❌ Ошибка обновления clan_members: {e}")
            finally:
                conn.close()

        print(f"   ✓ Обновлено {len(clans_data)} кланов")

    print(f"\n✅ Шаг 5: Обновлено {updated_count} записей в таблице clan_members")
    return updated_count

# ============================================================
# ШАГ 6: Получение player_id из clan_members, исключая тех, что есть в players
# ============================================================
def get_missing_player_ids():
    """Получает player_id из clan_members, которых нет в players"""
    with db_lock:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Получаем все player_id из clan_members, которых нет в players
        cursor.execute('''
            SELECT cm.player_id
            FROM clan_members cm
            LEFT JOIN players p ON cm.player_id = p.player_id
            WHERE p.player_id IS NULL
        ''')
        missing_player_ids = [row['player_id'] for row in cursor.fetchall()]

        conn.close()

    print(f"🔍 Шаг 6: Найдено {len(missing_player_ids)} player_id в clan_members, которых нет в players")
    return missing_player_ids

# ============================================================
# ШАГ 7: Сбор информации по оставшимся player_id в players
# ============================================================
def collect_missing_players(missing_player_ids):
    """Собирает информацию по player_id, которых нет в players"""
    if not missing_player_ids:
        print("\n⏭️ Шаг 7: Нет player_id для сбора")
        return 0

    print(f"\n📊 Шаг 7: Сбор информации о {len(missing_player_ids)} игроках...")

    total_batches = (len(missing_player_ids) + BATCH_SIZE - 1) // BATCH_SIZE
    collected_count = 0

    for i in range(0, len(missing_player_ids), BATCH_SIZE):
        batch = missing_player_ids[i:i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        print(f"   📦 Батч {batch_num}/{total_batches} (Игроки: {batch[0]} ... {batch[-1]})")

        api_data = get_api_data(batch, "wotb/account/info/", param_name="account_id")

        if api_data:
            current_date = datetime.now().strftime("%Y-%m-%d")
            current_datetime = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            batch_players = []
            for pid_str, info in api_data.items():
                if info is None:
                    continue
                player_id = int(pid_str)
                clan_id = info.get('clan_id')

                raw_lbt = info.get('last_battle_time')
                lbt_str = None
                if raw_lbt:
                    try:
                        lbt_str = datetime.fromtimestamp(raw_lbt).strftime("%Y-%m-%d %H:%M:%S")
                    except:
                        pass

                batch_players.append({
                    'player_id': player_id,
                    'nickname': info.get('nickname'),
                    'clan_id': clan_id,
                    'last_battle_time': lbt_str,
                    'collected_date': current_date,
                    'updated_at': current_datetime,
                    'processed_at': current_datetime
                })

            # Сохраняем батч в БД
            with db_lock:
                conn = get_db_connection()
                cursor = conn.cursor()
                try:
                    cursor.executemany('''
                        INSERT OR REPLACE INTO players (player_id, nickname, clan_id, last_battle_time, collected_date, updated_at, processed_at)
                        VALUES (:player_id, :nickname, :clan_id, :last_battle_time, :collected_date, :updated_at, :processed_at)
                    ''', batch_players)
                    conn.commit()
                    collected_count += len(batch_players)
                except Exception as e:
                    conn.rollback()
                    print(f"   ❌ Ошибка сохранения батча игроков: {e}")
                finally:
                    conn.close()

            print(f"   ✓ Собрано {len(batch_players)} записей")
        else:
            print(f"   ⚠️ Нет данных для этого батча")

    print(f"\n✅ Шаг 7: Собрано {collected_count} записей в таблице players")
    return collected_count

# ============================================================
# ШАГ 8: Фильтрация игроков по last_battle_time
# ============================================================
def filter_players_by_last_battle_time():
    """Фильтрует игроков по last_battle_time (сегодня/вчера/NULL)"""
    with db_lock:
        conn = get_db_connection()
        cursor = conn.cursor()

        today = datetime.now().strftime("%Y-%m-%d")
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

        cursor.execute('''
            SELECT player_id
            FROM players
            WHERE last_battle_time LIKE ?
               OR last_battle_time LIKE ?
               OR last_battle_time IS NULL
        ''', (f"{today}%", f"{yesterday}%"))

        filtered_player_ids = [row['player_id'] for row in cursor.fetchall()]

        conn.close()

    print(f"🔍 Шаг 8: Отфильтровано {len(filtered_player_ids)} игроков (last_battle_time: сегодня/вчера/NULL)")
    return filtered_player_ids

# ============================================================
# ШАГ 9: Сбор информации по игрокам в player_tanks_stats
# ============================================================
def collect_player_tanks_stats(filtered_player_ids):
    """Собирает информацию по игрокам в player_tanks_stats с добавлением/перезаписью на текущую дату"""
    if not filtered_player_ids:
        print("\n⏭️ Шаг 9: Нет игроков для сбора")
        return 0

    print(f"\n📊 Шаг 9: Сбор статистики танков для {len(filtered_player_ids)} игроков...")

    total_batches = (len(filtered_player_ids) + BATCH_SIZE - 1) // BATCH_SIZE
    collected_count = 0

    for i in range(0, len(filtered_player_ids), BATCH_SIZE):
        batch = filtered_player_ids[i:i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        print(f"   📦 Батч {batch_num}/{total_batches} (Игроки: {batch[0]} ... {batch[-1]})")

        # Получаем статистику танков для каждого игрока в батче
        batch_stats = []
        for player_id in batch:
            rate_limiter.wait()
            params = {"application_id": API_KEY, "account_id": str(player_id)}

            try:
                response = requests.get(f"{BASE_URL}/wotb/tanks/stats/", params=params, timeout=30)
                data = response.json()

                if data.get("status") == "ok":
                    p_data = data.get("data", {}).get(str(player_id))

                    if isinstance(p_data, list):
                        current_date = datetime.now().strftime("%Y-%m-%d")
                        current_datetime = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                        for tank in p_data:
                            if not isinstance(tank, dict):
                                continue

                            tank_stats = tank.get("all", {}) or {}
                            if tank_stats.get("battles", 0) > 0:
                                raw_lbt = tank.get("last_battle_time")
                                lbt_str = None
                                if raw_lbt:
                                    try:
                                        lbt_str = datetime.fromtimestamp(raw_lbt).strftime("%Y-%m-%d %H:%M:%S")
                                    except:
                                        pass

                                batch_stats.append({
                                    'player_id': player_id,
                                    'tank_id': tank.get("tank_id"),
                                    'battle_life_time': tank.get("battle_life_time"),
                                    'last_battle_time': lbt_str,
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
                                    'collected_date': current_date,
                                    'updated_at': current_datetime,
                                    'processed_at': current_datetime
                                })
            except Exception as e:
                log_error("wotb/tanks/stats/", "EXCEPTION", "EXCEPTION", str(e), str(player_id))

        # Сохраняем батч в БД с INSERT OR REPLACE
        if batch_stats:
            with db_lock:
                conn = get_db_connection()
                cursor = conn.cursor()
                try:
                    cursor.executemany('''
                        INSERT OR REPLACE INTO player_tanks_stats (
                            player_id, tank_id, battle_life_time, last_battle_time, mark_of_mastery,
                            battles, damage_dealt, damage_received, frags, hits, losses, shots,
                            spotted, survived_battles, win_and_survived, wins, collected_date, updated_at, processed_at
                        ) VALUES (
                            :player_id, :tank_id, :battle_life_time, :last_battle_time, :mark_of_mastery,
                            :battles, :damage_dealt, :damage_received, :frags, :hits, :losses, :shots,
                            :spotted, :survived_battles, :win_and_survived, :wins, :collected_date, :updated_at, :processed_at
                        )
                    ''', batch_stats)
                    conn.commit()
                    collected_count += len(batch_stats)
                except Exception as e:
                    conn.rollback()
                    print(f"   ❌ Ошибка сохранения батча статистики танков: {e}")
                finally:
                    conn.close()

            print(f"   ✓ Собрано {len(batch_stats)} записей")

    print(f"\n✅ Шаг 9: Собрано {collected_count} записей в таблице player_tanks_stats")
    return collected_count

# ============================================================
# ГЛАВНАЯ ФУНКЦИЯ
# ============================================================
def main():
    start_time = time.time()

    print("\n" + "=" * 70)
    print(" ПОЛНЫЙ СБОР И ОБНОВЛЕНИЕ ДАННЫХ (БД)")
    print(f" Дата сбора: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    # ШАГ 1: Получение всех player_id из таблицы players
    print("\n" + "=" * 70)
    print(" ШАГ 1: Получение всех player_id из таблицы players")
    print("=" * 70)
    all_player_ids = get_all_player_ids()

    # ШАГ 2: Обновление всей таблицы players для ВСЕХ игроков
    print("\n" + "=" * 70)
    print(" ШАГ 2: Обновление всей таблицы players для ВСЕХ игроков")
    print("=" * 70)
    update_all_players(all_player_ids)

    # ШАГ 3: Получение всех clan_id из clans + дополнение из players
    print("\n" + "=" * 70)
    print(" ШАГ 3: Получение всех clan_id из clans + дополнение из players")
    print("=" * 70)
    all_clan_ids = get_all_clan_ids()

    # ШАГ 4: Обновление всей таблицы clans для ВСЕХ clan_id
    print("\n" + "=" * 70)
    print(" ШАГ 4: Обновление всей таблицы clans для ВСЕХ clan_id")
    print("=" * 70)
    update_all_clans(all_clan_ids)

    # ШАГ 5: Обновление таблицы clan_members для всех кланов
    print("\n" + "=" * 70)
    print(" ШАГ 5: Обновление таблицы clan_members для всех кланов")
    print("=" * 70)
    update_all_clan_members(all_clan_ids)

    # ШАГ 6: Получение player_id из clan_members, исключая тех, что есть в players
    print("\n" + "=" * 70)
    print(" ШАГ 6: Получение player_id из clan_members, исключая тех, что есть в players")
    print("=" * 70)
    missing_player_ids = get_missing_player_ids()

    # ШАГ 7: Сбор информации по оставшимся player_id в players
    print("\n" + "=" * 70)
    print(" ШАГ 7: Сбор информации по оставшимся player_id в players")
    print("=" * 70)
    collect_missing_players(missing_player_ids)

    # ШАГ 8: Фильтрация игроков по last_battle_time
    print("\n" + "=" * 70)
    print(" ШАГ 8: Фильтрация игроков по last_battle_time")
    print("=" * 70)
    filtered_player_ids = filter_players_by_last_battle_time()

    # ШАГ 9: Сбор информации по игрокам в player_tanks_stats
    print("\n" + "=" * 70)
    print(" ШАГ 9: Сбор информации по игрокам в player_tanks_stats")
    print("=" * 70)
    collect_player_tanks_stats(filtered_player_ids)

    # Сохранение ошибок
    print("\n" + "=" * 70)
    print(" СОХРАНЕНИЕ ОШИБОК")
    print("=" * 70)
    save_errors_to_db()
    export_errors_to_excel()

    elapsed = time.time() - start_time
    print(f"\n{'=' * 70}")
    print(" ✅ ПОЛНЫЙ СБОР ЗАВЕРШЁН!")
    print("=" * 70)
    print(f"⏱️  Общее время: {elapsed:.0f} сек ({elapsed / 60:.1f} мин)")
    print(f"📊 Всего ошибок: {len(error_log)}")
    print(f"\n💾 Данные сохранены в БД: {DB_FILE}")

if __name__ == "__main__":
    main()