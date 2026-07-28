import pandas as pd
import requests
import time
from datetime import datetime
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

# ============================================================
# КОНСТАНТЫ
# ============================================================
API_KEY = "59fbd1d08ae11577dc9355ad1ab44166"
BASE_URL = "https://papi.tanksblitz.ru"
BATCH_SIZE = 100
RATE_LIMIT = 18
INPUT_FILE = "sample_data.xlsx"
OUTPUT_FILE = "sample_data.xlsx"
DELAY = 0.1
MAX_WORKERS = 18


# ============================================================
# РЕЙТ-ЛИМИТЕР
# ============================================================
class RateLimiter:
    def __init__(self, max_rps):
        self.max_rps = max_rps
        self.times = []

    def wait(self):
        now = time.time()
        self.times = [t for t in self.times if now - t < 1.0]
        if len(self.times) >= self.max_rps:
            sleep_time = 1.0 - (now - self.times[0]) + 0.01
            if sleep_time > 0:
                time.sleep(sleep_time)
        self.times.append(time.time())


rate_limiter = RateLimiter(RATE_LIMIT)


# ============================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================
def timestamp_to_str(ts):
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError, OSError):
        return None


def normalize_last_battle_time(value):
    if value is None or pd.isna(value):
        return None
    if isinstance(value, (int, float)):
        return timestamp_to_str(value)
    return str(value).strip()


# ============================================================
# ЧТЕНИЕ ДАННЫХ ИЗ EXCEL
# ============================================================
def load_data_from_excel(filename):
    print(f"📂 Чтение файла: {filename}")
    print("=" * 70)
    df_clans = pd.read_excel(filename, sheet_name='Кланы')
    df_players = pd.read_excel(filename, sheet_name='Игроки')
    df_tanks = pd.read_excel(filename, sheet_name='Танки')
    clans = df_clans.to_dict(orient='records')
    players = df_players.to_dict(orient='records')
    tanks = df_tanks.to_dict(orient='records')
    print(f"\n КЛАНЫ: {len(clans)} записей")
    print(f"👥 ИГРОКИ: {len(players)} записей")
    print(f"🚗 ТАНКИ: {len(tanks)} записей")
    return clans, players, tanks


# ============================================================
# СБОР ID ИГРОКОВ ИЗ КЛАНОВ
# ============================================================
def collect_players_data_from_clans(clans_list):
    players_data = {}
    clan_ids = [c['clan_id'] for c in clans_list if c.get('clan_id') is not None]
    print(f"\n🔍 Найдено {len(clan_ids)} кланов для запроса из {len(clans_list)} записей")
    total_batches = (len(clan_ids) + BATCH_SIZE - 1) // BATCH_SIZE
    for i in range(0, len(clan_ids), BATCH_SIZE):
        batch = clan_ids[i:i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        print(f"\n📦 Батч {batch_num}/{total_batches} (Кланы: {batch[0]} ... {batch[-1]})")
        rate_limiter.wait()
        ids_str = ",".join(map(str, batch))
        params = {"application_id": API_KEY, "clan_id": ids_str}
        try:
            response = requests.get(f"{BASE_URL}/wotb/clans/info/", params=params, timeout=15)
            data = response.json()
            if data.get("status") == "ok":
                clans_data = data.get("data", {})
                batch_count = 0
                for cid, c_info in clans_data.items():
                    if not c_info:
                        continue
                    clan_id = int(cid)
                    clan_tag = c_info.get("tag")
                    clan_name = c_info.get("name")
                    if "members_ids" in c_info:
                        members = c_info["members_ids"]
                        if isinstance(members, list):
                            for player_id in members:
                                players_data[player_id] = {
                                    'player_id': player_id,
                                    'clan_id': clan_id,
                                    'clan_tag': clan_tag,
                                    'clan_name': clan_name
                                }
                                batch_count += 1
                print(f"   ✓ Получено {batch_count} записей игроков")
            else:
                error_msg = data.get("error", {}).get("message", "unknown")
                print(f"   ⚠️ Ошибка API: {error_msg}")
        except Exception as e:
            print(f"   ❌ Ошибка запроса: {e}")
    print(f"\n✅ Сбор завершен!")
    print(f"   Уникальных ID игроков: {len(players_data)}")
    return players_data


def update_players_list(existing_players, players_data):
    print(f"\n🔄 Обновление списка игроков...")
    print(f"   Игроков в Excel: {len(existing_players)}")
    print(f"   Игроков из API: {len(players_data)}")
    players_dict = {}
    for player in existing_players:
        player_id = player.get('player_id')
        if player_id is not None:
            players_dict[player_id] = player.copy()
    print(f"   Создан словарь из {len(players_dict)} игроков")
    updated_count = 0
    new_count = 0
    unchanged_count = 0
    for player_id, api_data in players_data.items():
        if player_id in players_dict:
            existing = players_dict[player_id]
            old_clan_id = existing.get('clan_id')
            old_clan_tag = existing.get('clan_tag')
            old_clan_name = existing.get('clan_name')
            if (old_clan_id != api_data['clan_id'] or
                    old_clan_tag != api_data['clan_tag'] or
                    old_clan_name != api_data['clan_name']):
                existing['clan_id'] = api_data['clan_id']
                existing['clan_tag'] = api_data['clan_tag']
                existing['clan_name'] = api_data['clan_name']
                existing['updated_at'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                updated_count += 1
            else:
                unchanged_count += 1
        else:
            new_player = {
                'player_id': player_id,
                'nickname': None,
                'clan_id': api_data['clan_id'],
                'clan_tag': api_data['clan_tag'],
                'clan_name': api_data['clan_name'],
                'last_battle_time': None,
                'collected_date': datetime.now().strftime("%Y-%m-%d"),
                'updated_at': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                'processed_at': None
            }
            players_dict[player_id] = new_player
            new_count += 1
    updated_players = list(players_dict.values())
    print(f"\n📊 Результат обновления:")
    print(f"   ✏️  Обновлено записей: {updated_count}")
    print(f"   ➕ Добавлено новых: {new_count}")
    print(f"   ⏭️  Без изменений: {unchanged_count}")
    print(f"   📋 Итого игроков: {len(updated_players)}")
    return updated_players


# ============================================================
# СБОР ИНФОРМАЦИИ ОБ ИГРОКАХ ИЗ API
# ============================================================
def fetch_players_info_recursive(player_ids, depth=0, max_depth=4):
    if not player_ids:
        return {}
    rate_limiter.wait()
    ids_str = ",".join(map(str, player_ids))
    params = {"application_id": API_KEY, "account_id": ids_str}
    try:
        response = requests.get(f"{BASE_URL}/wotb/account/info/", params=params, timeout=15)
        data = response.json()
        if data.get("status") == "ok":
            return data.get("data", {})
        else:
            error_msg = data.get("error", {}).get("message", "unknown")
    except Exception as e:
        error_msg = str(e)
    if depth < max_depth and len(player_ids) > 1:
        if depth == 0:
            print(f"   ⚠️ Ошибка батча ({len(player_ids)} игроков), дробим на части...")
        time.sleep(DELAY)
        mid = len(player_ids) // 2
        left_data = fetch_players_info_recursive(player_ids[:mid], depth + 1, max_depth)
        right_data = fetch_players_info_recursive(player_ids[mid:], depth + 1, max_depth)
        left_data.update(right_data)
        return left_data
    else:
        if depth == 0:
            print(f"   ❌ Не удалось получить данные: {error_msg}")
        return {}


def collect_players_info(player_ids):
    print(f"\n📊 Сбор информации об игроках из API...")
    print(f"   Всего игроков: {len(player_ids)}")
    players_info = {}
    total_batches = (len(player_ids) + BATCH_SIZE - 1) // BATCH_SIZE
    for i in range(0, len(player_ids), BATCH_SIZE):
        batch = player_ids[i:i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        print(f"\n📦 Батч {batch_num}/{total_batches} (Игроки: {batch[0]} ... {batch[-1]})")
        api_data = fetch_players_info_recursive(batch)
        if api_data:
            batch_count = 0
            current_date = datetime.now().strftime("%Y-%m-%d")
            for player_id_str, player_info in api_data.items():
                player_id = int(player_id_str)
                raw_last_battle = player_info.get('last_battle_time')
                last_battle_str = timestamp_to_str(raw_last_battle)
                players_info[player_id] = {
                    'player_id': player_id,
                    'nickname': player_info.get('nickname'),
                    'last_battle_time': last_battle_str,
                    'collected_date': current_date,
                    'updated_at': current_date,
                    'processed_at': current_date
                }
                batch_count += 1
            print(f"   ✓ Получено {batch_count} записей")
        else:
            print(f"   ⚠️ Нет данных для этого батча")
    print(f"\n✅ Сбор информации об игроках завершен!")
    print(f"   Получено данных: {len(players_info)}")
    return players_info


# ============================================================
# СБОР ИНФОРМАЦИИ О ТАНКАХ ИЗ API
# ============================================================
def fetch_player_tanks_single(player_id):
    rate_limiter.wait()
    params = {"application_id": API_KEY, "account_id": str(player_id)}
    try:
        response = requests.get(f"{BASE_URL}/wotb/tanks/stats/", params=params, timeout=30)
        data = response.json()
        if data.get("status") == "ok":
            player_data = data.get("data", {}).get(str(player_id))
            if player_data and isinstance(player_data, list):
                return player_id, player_data, None
            else:
                return player_id, [], None
        else:
            error_msg = data.get("error", {}).get("message", "unknown")
            return player_id, [], error_msg
    except Exception as e:
        return player_id, [], str(e)


def process_single_player_tanks(player_id, stats, stats_lock, today, now_str):
    pid, tanks_list, error = fetch_player_tanks_single(player_id)
    if error:
        with stats_lock:
            stats['errors'] += 1
        return []
    with stats_lock:
        stats['success'] += 1
    tanks_data = []
    for tank in tanks_list:
        if not isinstance(tank, dict):
            continue
        tank_stats = tank.get("all", {}) or {}
        tank_battles = tank_stats.get("battles", 0)
        if tank_battles > 0:
            raw_tank_lbt = tank.get("last_battle_time")
            tank_lbt_str = timestamp_to_str(raw_tank_lbt)
            tanks_data.append({
                'player_id': pid,
                'tank_id': tank.get("tank_id"),
                'battle_life_time': tank.get("battle_life_time"),
                'last_battle_time': tank_lbt_str,
                'mark_of_mastery': tank.get("mark_of_mastery"),
                'battles': tank_battles,
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
                'collected_date': today,
                'updated_at': now_str,
                'processed_at': now_str
            })
    return tanks_data


def collect_tanks_stats(player_ids):
    print(f"\n🚗 Сбор статистики по танкам из API...")
    print(f"   Всего игроков: {len(player_ids)}")
    print(f"   Потоков: {MAX_WORKERS}")
    all_tanks_stats = []
    stats = {'success': 0, 'errors': 0}
    stats_lock = threading.Lock()
    today = datetime.now().strftime("%Y-%m-%d")
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)
    futures = {
        executor.submit(process_single_player_tanks, player_id, stats, stats_lock, today, now_str): player_id
        for player_id in player_ids
    }
    processed_count = 0
    last_progress_time = time.time()
    for future in as_completed(futures):
        try:
            tanks_data = future.result()
            all_tanks_stats.extend(tanks_data)
            processed_count += 1
        except Exception as e:
            print(f"  ❌ Исключение в задаче: {e}")
            processed_count += 1
        now = time.time()
        if now - last_progress_time > 10:
            elapsed_total = now - last_progress_time + 10
            speed = processed_count / elapsed_total if elapsed_total > 0 else 0
            with stats_lock:
                success = stats['success']
                errors = stats['errors']
            remaining = (len(player_ids) - processed_count) / speed if speed > 0 else 0
            print(f"\n{'=' * 70}")
            print(
                f"📊 ПРОГРЕСС: {processed_count}/{len(player_ids)} игроков ({processed_count / len(player_ids) * 100:.1f}%)")
            print(f"⚡ Скорость: {speed:.1f} игроков/сек")
            print(f"⏱️  Осталось: ~{remaining:.0f}с ({remaining / 60:.1f} мин)")
            print(f"✅ Успешных: {success} | ❌ Ошибок: {errors}")
            print(f"📦 Собрано записей: {len(all_tanks_stats)}")
            print(f"{'=' * 70}\n")
            last_progress_time = now
    executor.shutdown(wait=True)
    print(f"\n✅ Сбор статистики по танкам завершен!")
    print(f"   Всего записей о танках: {len(all_tanks_stats)}")
    print(f"   Успешных запросов: {stats['success']}")
    print(f"   Ошибок: {stats['errors']}")
    return all_tanks_stats


# ============================================================
# СБОР ИНФОРМАЦИИ О ТАНКАХ ИЗ ТАНКОПЕДИИ
# ============================================================
def fetch_tanks_info_batch(tank_ids):
    rate_limiter.wait()
    ids_str = ",".join(map(str, tank_ids))
    params = {"application_id": API_KEY, "tank_id": ids_str}
    try:
        response = requests.get(f"{BASE_URL}/wotb/encyclopedia/vehicles/", params=params, timeout=15)
        data = response.json()
        if data.get("status") == "ok":
            return data.get("data", {}), None
        else:
            error_msg = data.get("error", {}).get("message", "unknown")
            return None, error_msg
    except Exception as e:
        return None, str(e)


def collect_tanks_info(tank_ids):
    print(f"\n📖 Сбор информации о танках из API...")
    print(f"   Всего уникальных танков: {len(tank_ids)}")
    tanks_info = {}
    total_batches = (len(tank_ids) + BATCH_SIZE - 1) // BATCH_SIZE
    for i in range(0, len(tank_ids), BATCH_SIZE):
        batch = tank_ids[i:i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        print(f"\n Батч {batch_num}/{total_batches} (Танки: {batch[0]} ... {batch[-1]})")
        api_data, error = fetch_tanks_info_batch(batch)
        if api_data and not error:
            batch_count = 0
            skipped_count = 0
            for tank_id_str, tank_info in api_data.items():
                if tank_info is None:
                    skipped_count += 1
                    continue
                tank_id = int(tank_id_str)
                tanks_info[tank_id] = {
                    'tank_id': tank_id,
                    'tank_name': tank_info.get('name'),
                    'tier': tank_info.get('tier'),
                    'type': tank_info.get('type'),
                    'nation': tank_info.get('nation'),
                    'is_premium': 1 if tank_info.get('is_premium') else 0,
                    'is_collectible': 1 if tank_info.get('is_collectible') else 0
                }
                batch_count += 1
            print(f"   ✓ Получено {batch_count} записей")
            if skipped_count > 0:
                print(f"   ⚠️ Пропущено {skipped_count} (данные отсутствуют)")
        else:
            print(f"   ⚠️ Ошибка API: {error}")
    print(f"\n✅ Сбор информации о танках завершен!")
    print(f"   Получено данных: {len(tanks_info)}")
    return tanks_info


# ============================================================
# АГРЕГАЦИЯ СТАТИСТИКИ ПО ТАНКАМ (ИСПРАВЛЕНО)
# ============================================================
def aggregate_tanks_stats(tanks_stats):
    """
    Агрегирует статистику по танкам из списка tanks_stats.
    Суммирует числовые поля для каждого tank_id.
    Возвращает словарь {tank_id: {battles, damage_dealt, ...}}
    """
    print(f"\n Агрегация статистики по танкам...")

    tanks_aggregated = {}

    numeric_fields = [
        'battles', 'damage_dealt', 'damage_received', 'frags',
        'hits', 'losses', 'shots', 'spotted', 'survived_battles',
        'win_and_survived', 'wins'
    ]

    for record in tanks_stats:
        tank_id = record.get('tank_id')
        if tank_id is None:
            continue

        if tank_id not in tanks_aggregated:
            tanks_aggregated[tank_id] = {'tank_id': tank_id}
            for field in numeric_fields:
                tanks_aggregated[tank_id][field] = 0
            tanks_aggregated[tank_id]['last_battle_time'] = None

        agg = tanks_aggregated[tank_id]

        # Суммируем числовые поля
        for field in numeric_fields:
            agg[field] += record.get(field, 0)

        # Находим максимальное last_battle_time
        tank_last_battle = record.get('last_battle_time')
        if tank_last_battle:
            if agg['last_battle_time'] is None or tank_last_battle > agg['last_battle_time']:
                agg['last_battle_time'] = tank_last_battle

    # Рассчитываем производные поля
    for tank_id, agg in tanks_aggregated.items():
        battles = agg.get('battles', 0)

        # winrate = wins / battles * 100
        if battles > 0:
            agg['winrate'] = round(agg.get('wins', 0) / battles * 100, 2)
            agg['avg_dmg'] = round(agg.get('damage_dealt', 0) / battles, 0)
        else:
            agg['winrate'] = 0
            agg['avg_dmg'] = 0

    print(f"   Уникальных танков: {len(tanks_aggregated)}")
    return tanks_aggregated


# ============================================================
# ОБНОВЛЕНИЕ ЛИСТА ТАНКИ (ИСПРАВЛЕНО)
# ============================================================
def update_tanks_sheet(existing_tanks, tanks_info, tanks_aggregated):
    """
    Обновляет лист "Танки":
    - Добавляет новые танки из API и агрегированной статистики
    - Обновляет информацию о танке (название, уровень, тип, нация, премиум)
    - Записывает агрегированные значения (battles, damage_dealt и т.д.)
    - Проставляет даты collected_date, updated_at, processed_at
    """
    print(f"\n🔄 Обновление листа 'Танки'...")
    tanks_dict = {}
    for tank in existing_tanks:
        tank_id = tank.get('tank_id')
        if tank_id is not None:
            tanks_dict[tank_id] = tank.copy()
    all_tank_ids = set(tanks_info.keys()) | set(tanks_aggregated.keys())
    today = datetime.now().strftime("%Y-%m-%d")
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    updated_count = 0
    new_count = 0
    for tank_id in all_tank_ids:
        if tank_id not in tanks_dict:
            tanks_dict[tank_id] = {'tank_id': tank_id}
            new_count += 1
        else:
            updated_count += 1
        tank = tanks_dict[tank_id]
        if tank_id in tanks_info:
            info = tanks_info[tank_id]
            tank['tank_name'] = info.get('tank_name')
            tank['tier'] = info.get('tier')
            tank['type'] = info.get('type')
            tank['nation'] = info.get('nation')
            tank['is_premium'] = info.get('is_premium')
            tank['is_collectible'] = info.get('is_collectible')
        if tank_id in tanks_aggregated:
            agg = tanks_aggregated[tank_id]
            for key, value in agg.items():
                if key != 'tank_id':
                    tank[key] = value
        tank['collected_date'] = today
        tank['updated_at'] = now_str
        tank['processed_at'] = now_str
    updated_tanks = list(tanks_dict.values())
    print(f"   ✏️  Обновлено записей: {updated_count}")
    print(f"   ➕ Добавлено новых: {new_count}")
    print(f"   📋 Итого танков: {len(updated_tanks)}")
    return updated_tanks


# ============================================================
# СОХРАНЕНИЕ В EXCEL (ИСПРАВЛЕНО)
# ============================================================
def save_to_excel(clans, players, tanks, player_tanks_stats, filename):
    print(f"\n💾 Сохранение в файл: {filename}")
    try:
        df_clans = pd.DataFrame(clans)
        df_players = pd.DataFrame(players)
        df_tanks = pd.DataFrame(tanks)
        df_player_tanks_stats = pd.DataFrame(player_tanks_stats)

        with pd.ExcelWriter(filename, engine='openpyxl') as writer:
            df_clans.to_excel(writer, sheet_name='Кланы', index=False)
            df_players.to_excel(writer, sheet_name='Игроки', index=False)
            df_tanks.to_excel(writer, sheet_name='Танки', index=False)
            df_player_tanks_stats.to_excel(writer, sheet_name='Статистика_танков', index=False)

        print(f"   ✓ Файл сохранён успешно")
        print(f"   • Лист 'Кланы': {len(df_clans)} записей")
        print(f"   • Лист 'Игроки': {len(df_players)} записей")
        print(f"   • Лист 'Танки': {len(df_tanks)} записей")
        print(f"   • Лист 'Статистика_танков': {len(df_player_tanks_stats)} записей")
        if not df_tanks.empty:
            print(f"\n   📋 Колонки 'Танки': {list(df_tanks.columns)}")
    except Exception as e:
        print(f"   ❌ Ошибка сохранения: {e}")
        import traceback
        traceback.print_exc()


# ============================================================
# ГЛАВНАЯ ФУНКЦИЯ (ИСПРАВЛЕНО)
# ============================================================
def main():
    start_time = time.time()
    print("\n" + "=" * 70)
    print(" ПОЛНЫЙ СБОР И ОБНОВЛЕНИЕ ДАННЫХ")
    print(f" Дата сбора: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    clans, players, tanks = load_data_from_excel(INPUT_FILE)
    players_data = collect_players_data_from_clans(clans)
    updated_players = update_players_list(players, players_data)
    player_ids = [p['player_id'] for p in updated_players if p.get('player_id') is not None]

    # 3. Сбор инфо об игроках (nickname, last_battle_time)
    players_info = collect_players_info(player_ids)
    for player in updated_players:
        player_id = player.get('player_id')
        if player_id in players_info:
            api_info = players_info[player_id]
            player['nickname'] = api_info['nickname']
            player['last_battle_time'] = api_info['last_battle_time']
            player['collected_date'] = api_info['collected_date']
            player['updated_at'] = api_info['updated_at']
            player['processed_at'] = api_info['processed_at']

    # 4. Сбор статистики по танкам игроков (индивидуальные данные)
    player_tanks_stats = collect_tanks_stats(player_ids)

    # 5. Получаем уникальные ID танков
    unique_tank_ids = list(set([t['tank_id'] for t in player_tanks_stats if 'tank_id' in t]))
    print(f"\n🔍 Найдено {len(unique_tank_ids)} уникальных танков в статистике")

    # 6. Сбор информации о танках из Танкопедии
    tanks_info = collect_tanks_info(unique_tank_ids)

    # 7. Агрегация статистики (суммарные данные по танкам)
    tanks_aggregated = aggregate_tanks_stats(player_tanks_stats)

    # 8. Обновление листа "Танки" (суммарная информация)
    updated_tanks = update_tanks_sheet(tanks, tanks_info, tanks_aggregated)

    # 9. Сохранение в Excel
    save_to_excel(clans, updated_players, updated_tanks, player_tanks_stats, OUTPUT_FILE)

    elapsed = time.time() - start_time
    print(f"\n{'=' * 70}")
    print(" ✅ ПОЛНЫЙ СБОР ЗАВЕРШЁН!")
    print("=" * 70)
    print(f"⏱️  Общее время: {elapsed:.0f} сек ({elapsed / 60:.1f} мин)")
    print(f"\n📁 Файл сохранён: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()