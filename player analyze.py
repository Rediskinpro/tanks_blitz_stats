import time
import pandas as pd
from datetime import datetime, timedelta
from common.db import get_db_connection, get_cursor, release_db_connection
from common.logger import log_error, save_errors_to_db, export_errors_to_excel

# ============================================================
# НАСТРОЙКИ
# ============================================================
TARGET_PLAYER_ID = 62119145  # ← Замени на нужный player_id
DAYS_PERIOD = 30
TANK_TIERS = [7, 8, 9, 10]

# Абсолютные показатели
ABS_METRICS = ['wins', 'battles', 'shots', 'hits', 'spotted',
               'survived_battles', 'win_and_survived', 'damage_dealt', 'damage_received']

# Производные показатели, выводимые как проценты
PERCENT_METRICS = ['winrate', 'accuracy', 'avg_survived', 'avg_win_and_survive']

# Производные показатели, выводимые как числа
NUMBER_METRICS = ['avg_spotted', 'avg_damage_dealt', 'avg_damage_received']

# Все производные
DERIVED_METRICS = ['winrate', 'accuracy', 'avg_spotted', 'avg_survived',
                   'avg_win_and_survive', 'avg_damage_dealt', 'avg_damage_received']

# Все показатели
ALL_METRICS = ABS_METRICS + DERIVED_METRICS


def safe_num(value, default=0):
    """Безопасное преобразование в число."""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def calculate_derived_metrics(stats):
    """Считает производные показатели из абсолютных значений."""
    result = dict(stats)
    battles = safe_num(stats.get('battles', 0))
    shots = safe_num(stats.get('shots', 0))

    if battles > 0:
        result['winrate'] = safe_num(stats.get('wins')) / battles * 100
        result['avg_spotted'] = safe_num(stats.get('spotted')) / battles
        result['avg_survived'] = safe_num(stats.get('survived_battles')) / battles * 100
        result['avg_win_and_survive'] = safe_num(stats.get('win_and_survived')) / battles * 100
        result['avg_damage_dealt'] = safe_num(stats.get('damage_dealt')) / battles
        result['avg_damage_received'] = safe_num(stats.get('damage_received')) / battles
    else:
        for m in ['winrate', 'avg_spotted', 'avg_survived', 'avg_win_and_survive',
                  'avg_damage_dealt', 'avg_damage_received']:
            result[m] = 0

    if shots > 0:
        result['accuracy'] = safe_num(stats.get('hits')) / shots * 100
    else:
        result['accuracy'] = 0

    return result


def calculate_delta(player_stats, all_stats):
    """Считает дельту между показателями игрока и всех игроков."""
    if player_stats is None or all_stats is None:
        return None
    delta = {}
    for metric in ALL_METRICS:
        delta[metric] = safe_num(player_stats.get(metric)) - safe_num(all_stats.get(metric))
    return delta


def prepare_temp_tables(cursor, period_start_int):
    """Создаёт все временные таблицы."""
    tiers_str = ','.join(map(str, TANK_TIERS))

    # ===== Шаг 1: Общие данные за всё время из players =====
    print("\n⏳ Шаг 1/4: Подготовка данных из 'players' (всё время)...")
    step_start = time.time()
    cursor.execute("DROP TABLE IF EXISTS temp_players_alltime;")
    cursor.execute('''
        CREATE TEMPORARY TABLE temp_players_alltime AS
        SELECT DISTINCT ON (player_id)
            player_id,
            stat_all_wins AS wins,
            stat_all_battles AS battles,
            stat_all_shots AS shots,
            stat_all_hits AS hits,
            stat_all_spotted AS spotted,
            stat_all_survived_battles AS survived_battles,
            stat_all_win_and_survived AS win_and_survived,
            stat_all_damage_dealt AS damage_dealt,
            stat_all_damage_received AS damage_received
        FROM players
        ORDER BY player_id, collected_date DESC
    ''')
    cursor.execute("CREATE INDEX idx_tpa_player ON temp_players_alltime (player_id);")
    cursor.execute("ANALYZE temp_players_alltime;")
    cursor.execute("SELECT COUNT(*) FROM temp_players_alltime;")
    count1 = cursor.fetchone()[0]
    print(f"   ✅ Подготовлено {count1:,} игроков за {time.time() - step_start:.1f} сек")

    # ===== Шаг 2: Общие данные за 30 дней из players_stats_30d =====
    print("\n⏳ Шаг 2/4: Подготовка данных из 'players_stats_30d' (30 дней)...")
    step_start = time.time()
    cursor.execute("DROP TABLE IF EXISTS temp_players_30d;")
    cursor.execute('''
        CREATE TEMPORARY TABLE temp_players_30d AS
        SELECT DISTINCT ON (player_id)
            player_id,
            stat_all_wins AS wins,
            stat_all_battles AS battles,
            stat_all_shots AS shots,
            stat_all_hits AS hits,
            stat_all_spotted AS spotted,
            stat_all_survived_battles AS survived_battles,
            stat_all_win_and_survived AS win_and_survived,
            stat_all_damage_dealt AS damage_dealt,
            stat_all_damage_received AS damage_received
        FROM players_stats_30d
        ORDER BY player_id, calculated_date DESC
    ''')
    cursor.execute("CREATE INDEX idx_tp30_player ON temp_players_30d (player_id);")
    cursor.execute("ANALYZE temp_players_30d;")
    cursor.execute("SELECT COUNT(*) FROM temp_players_30d;")
    count2 = cursor.fetchone()[0]
    print(f"   ✅ Подготовлено {count2:,} игроков за {time.time() - step_start:.1f} сек")

    # ===== Шаг 3: Данные по танкам 7+ за всё время =====
    print("\n⏳ Шаг 3/4: Подготовка данных по танкам (всё время)...")
    print(f"   📊 Фильтр: уровни {TANK_TIERS}")
    step_start = time.time()
    cursor.execute("DROP TABLE IF EXISTS temp_tanks_alltime;")
    cursor.execute(f'''
        CREATE TEMPORARY TABLE temp_tanks_alltime AS
        SELECT DISTINCT ON (s.player_id, s.tank_id)
            s.player_id, s.tank_id, t.tier,
            s.battles, s.wins, s.shots, s.hits,
            s.spotted, s.survived_battles, s.win_and_survived,
            s.damage_dealt, s.damage_received
        FROM player_tanks_stats s
        INNER JOIN tanks t ON s.tank_id = t.tank_id
        WHERE t.tier IN ({tiers_str})
        ORDER BY s.player_id, s.tank_id, s.collected_date DESC
    ''')
    cursor.execute("CREATE INDEX idx_tta_player ON temp_tanks_alltime (player_id);")
    cursor.execute("CREATE INDEX idx_tta_tank ON temp_tanks_alltime (tank_id);")
    cursor.execute("ANALYZE temp_tanks_alltime;")
    cursor.execute("SELECT COUNT(*) FROM temp_tanks_alltime;")
    count3 = cursor.fetchone()[0]
    print(f"   ✅ Подготовлено {count3:,} записей за {time.time() - step_start:.1f} сек")

    # ===== Шаг 4: Данные по танкам 7+ за 30 дней (дельты) =====
    print("\n⏳ Шаг 4/4: Подготовка данных по танкам (30 дней, дельты)...")
    step_start = time.time()

    cursor.execute("DROP TABLE IF EXISTS temp_tanks_in_period;")
    cursor.execute(f'''
        CREATE TEMPORARY TABLE temp_tanks_in_period AS
        SELECT DISTINCT ON (s.player_id, s.tank_id)
            s.player_id, s.tank_id,
            s.battles, s.wins, s.shots, s.hits,
            s.spotted, s.survived_battles, s.win_and_survived,
            s.damage_dealt, s.damage_received
        FROM player_tanks_stats s
        INNER JOIN tanks t ON s.tank_id = t.tank_id
        WHERE t.tier IN ({tiers_str})
          AND s.last_battle_time >= %s
        ORDER BY s.player_id, s.tank_id, s.collected_date DESC
    ''', (period_start_int,))

    cursor.execute("DROP TABLE IF EXISTS temp_tanks_before_period;")
    cursor.execute(f'''
        CREATE TEMPORARY TABLE temp_tanks_before_period AS
        SELECT DISTINCT ON (s.player_id, s.tank_id)
            s.player_id, s.tank_id,
            s.battles, s.wins, s.shots, s.hits,
            s.spotted, s.survived_battles, s.win_and_survived,
            s.damage_dealt, s.damage_received
        FROM player_tanks_stats s
        INNER JOIN tanks t ON s.tank_id = t.tank_id
        WHERE t.tier IN ({tiers_str})
          AND s.last_battle_time < %s
        ORDER BY s.player_id, s.tank_id, s.collected_date DESC
    ''', (period_start_int,))

    cursor.execute("DROP TABLE IF EXISTS temp_tanks_30d;")
    cursor.execute('''
        CREATE TEMPORARY TABLE temp_tanks_30d AS
        SELECT a.player_id, a.tank_id, t.tier,
               a.battles - COALESCE(b.battles, 0) AS battles,
               a.wins - COALESCE(b.wins, 0) AS wins,
               a.shots - COALESCE(b.shots, 0) AS shots,
               a.hits - COALESCE(b.hits, 0) AS hits,
               a.spotted - COALESCE(b.spotted, 0) AS spotted,
               a.survived_battles - COALESCE(b.survived_battles, 0) AS survived_battles,
               a.win_and_survived - COALESCE(b.win_and_survived, 0) AS win_and_survived,
               a.damage_dealt - COALESCE(b.damage_dealt, 0) AS damage_dealt,
               a.damage_received - COALESCE(b.damage_received, 0) AS damage_received
        FROM temp_tanks_in_period a
        INNER JOIN tanks t ON a.tank_id = t.tank_id
        LEFT JOIN temp_tanks_before_period b
            ON a.player_id = b.player_id AND a.tank_id = b.tank_id
        WHERE a.battles - COALESCE(b.battles, 0) > 0
    ''')
    cursor.execute("CREATE INDEX idx_tt30_player ON temp_tanks_30d (player_id);")
    cursor.execute("CREATE INDEX idx_tt30_tank ON temp_tanks_30d (tank_id);")
    cursor.execute("ANALYZE temp_tanks_30d;")
    cursor.execute("SELECT COUNT(*) FROM temp_tanks_30d;")
    count4 = cursor.fetchone()[0]

    cursor.execute("DROP TABLE IF EXISTS temp_tanks_in_period;")
    cursor.execute("DROP TABLE IF EXISTS temp_tanks_before_period;")
    print(f"   ✅ Подготовлено {count4:,} дельт за {time.time() - step_start:.1f} сек")

    return {
        'players_alltime': count1,
        'players_30d': count2,
        'tanks_alltime': count3,
        'tanks_30d': count4,
    }


# ============================================================
# ЛИСТЫ 1-2: Все танки (все игроки + игрок + дельта)
# ============================================================
def build_sheet_all_tanks(cursor, players_table, player_id):
    """Строит данные для листа 'Все танки'."""
    # Средние по всем игрокам
    cursor.execute(f'''
        SELECT AVG(wins) AS wins, AVG(battles) AS battles,
               AVG(shots) AS shots, AVG(hits) AS hits,
               AVG(spotted) AS spotted, AVG(survived_battles) AS survived_battles,
               AVG(win_and_survived) AS win_and_survived,
               AVG(damage_dealt) AS damage_dealt, AVG(damage_received) AS damage_received
        FROM {players_table}
        WHERE battles > 0
    ''')
    all_stats_raw = cursor.fetchone()

    # Данные целевого игрока
    cursor.execute(f'''
        SELECT wins, battles, shots, hits, spotted, survived_battles,
               win_and_survived, damage_dealt, damage_received
        FROM {players_table}
        WHERE player_id = %s
    ''', (player_id,))
    player_stats_raw = cursor.fetchone()

    rows = []

    # Строка "все игроки"
    if all_stats_raw and all_stats_raw['battles'] is not None:
        all_stats = {m: safe_num(all_stats_raw[m]) for m in ABS_METRICS}
        all_full = calculate_derived_metrics(all_stats)
        row = {'Players': 'all players'}
        for m in ALL_METRICS:
            row[m] = round(all_full[m], 2)
        rows.append(row)
    else:
        all_stats = None
        rows.append({'Players': 'all players', 'wins': 'нет данных'})

    # Строка "игрок"
    if player_stats_raw and player_stats_raw['battles'] is not None:
        player_stats = {m: safe_num(player_stats_raw[m]) for m in ABS_METRICS}
        player_full = calculate_derived_metrics(player_stats)
        row = {'Players': str(player_id)}
        for m in ALL_METRICS:
            row[m] = round(player_full[m], 2)
        rows.append(row)
    else:
        player_full = None
        rows.append({'Players': str(player_id), 'wins': 'нет данных'})

    # Строка "дельта"
    if all_stats and player_full:
        delta = calculate_delta(player_full, all_full)
        row = {'Players': 'delta'}
        for m in ALL_METRICS:
            row[m] = round(delta[m], 2)
        rows.append(row)
    else:
        rows.append({'Players': 'delta', 'wins': 'нет данных'})

    return rows


# ============================================================
# ЛИСТЫ 3-4: По уровням (все игроки + игрок + дельта)
# ============================================================
def build_sheet_by_tier(cursor, tanks_table, player_id):
    """Строит данные для листа 'По уровням'."""
    tiers_str = ','.join(map(str, TANK_TIERS))

    # Средние по всем игрокам для каждого уровня
    cursor.execute(f'''
        SELECT tier,
               AVG(per_player.wins) AS wins, AVG(per_player.battles) AS battles,
               AVG(per_player.shots) AS shots, AVG(per_player.hits) AS hits,
               AVG(per_player.spotted) AS spotted,
               AVG(per_player.survived_battles) AS survived_battles,
               AVG(per_player.win_and_survived) AS win_and_survived,
               AVG(per_player.damage_dealt) AS damage_dealt,
               AVG(per_player.damage_received) AS damage_received
        FROM (
            SELECT player_id, tier,
                   SUM(wins) AS wins, SUM(battles) AS battles,
                   SUM(shots) AS shots, SUM(hits) AS hits,
                   SUM(spotted) AS spotted, SUM(survived_battles) AS survived_battles,
                   SUM(win_and_survived) AS win_and_survived,
                   SUM(damage_dealt) AS damage_dealt, SUM(damage_received) AS damage_received
            FROM {tanks_table}
            WHERE tier IN ({tiers_str})
            GROUP BY player_id, tier
        ) per_player
        GROUP BY tier
        ORDER BY tier
    ''')
    all_by_tier_raw = {r['tier']: dict(r) for r in cursor.fetchall()}

    # Данные целевого игрока для каждого уровня
    cursor.execute(f'''
        SELECT tier,
               SUM(wins) AS wins, SUM(battles) AS battles,
               SUM(shots) AS shots, SUM(hits) AS hits,
               SUM(spotted) AS spotted, SUM(survived_battles) AS survived_battles,
               SUM(win_and_survived) AS win_and_survived,
               SUM(damage_dealt) AS damage_dealt, SUM(damage_received) AS damage_received
        FROM {tanks_table}
        WHERE player_id = %s AND tier IN ({tiers_str})
        GROUP BY tier
        ORDER BY tier
    ''', (player_id,))
    player_by_tier_raw = {r['tier']: dict(r) for r in cursor.fetchall()}

    rows = []
    for tier in TANK_TIERS:
        # Все игроки
        all_raw = all_by_tier_raw.get(tier)
        if all_raw and all_raw['battles'] is not None:
            all_stats = {m: safe_num(all_raw[m]) for m in ABS_METRICS}
            all_full = calculate_derived_metrics(all_stats)
            row = {'tier': tier, 'Players': 'all players'}
            for m in ALL_METRICS:
                row[m] = round(all_full[m], 2)
            rows.append(row)
        else:
            all_full = None
            rows.append({'tier': tier, 'Players': 'all players', 'wins': 'нет данных'})

        # Игрок
        player_raw = player_by_tier_raw.get(tier)
        if player_raw and player_raw['battles'] is not None:
            player_stats = {m: safe_num(player_raw[m]) for m in ABS_METRICS}
            player_full = calculate_derived_metrics(player_stats)
            row = {'tier': tier, 'Players': str(player_id)}
            for m in ALL_METRICS:
                row[m] = round(player_full[m], 2)
            rows.append(row)
        else:
            player_full = None
            rows.append({'tier': tier, 'Players': str(player_id), 'wins': 'нет данных'})

        # Дельта
        if all_full and player_full:
            delta = calculate_delta(player_full, all_full)
            row = {'tier': tier, 'Players': 'delta'}
            for m in ALL_METRICS:
                row[m] = round(delta[m], 2)
            rows.append(row)
        else:
            rows.append({'tier': tier, 'Players': 'delta', 'wins': 'нет данных'})

    return rows


# ============================================================
# ЛИСТЫ 5-6: По танкам (игрок + сумма всех игроков)
# ============================================================
def build_sheet_by_tank(cursor, tanks_table, player_id):
    """Строит данные для листа 'По танкам'."""
    tiers_str = ','.join(map(str, TANK_TIERS))

    # Получаем список танков целевого игрока
    cursor.execute(f'''
        SELECT DISTINCT tank_id FROM {tanks_table}
        WHERE player_id = %s AND tier IN ({tiers_str})
    ''', (player_id,))
    target_tanks = [r['tank_id'] for r in cursor.fetchall()]

    if not target_tanks:
        return [{'tank_name': 'нет данных', 'tier': '', 'Players': '', 'wins': 'нет данных'}]

    placeholders = ','.join(['%s'] * len(target_tanks))

    # Данные целевого игрока на каждом танке
    cursor.execute(f'''
        SELECT s.tank_id, t.tank_name, s.tier,
               s.wins, s.battles, s.shots, s.hits,
               s.spotted, s.survived_battles, s.win_and_survived,
               s.damage_dealt, s.damage_received
        FROM {tanks_table} s
        INNER JOIN tanks t ON s.tank_id = t.tank_id
        WHERE s.player_id = %s AND s.tank_id IN ({placeholders})
        ORDER BY s.tier DESC, s.battles DESC
    ''', [player_id] + target_tanks)
    player_tanks_raw = {r['tank_id']: dict(r) for r in cursor.fetchall()}

    # Суммарные показатели всех игроков на этих танках
    cursor.execute(f'''
        SELECT tank_id,
               SUM(wins) AS wins, SUM(battles) AS battles,
               SUM(shots) AS shots, SUM(hits) AS hits,
               SUM(spotted) AS spotted, SUM(survived_battles) AS survived_battles,
               SUM(win_and_survived) AS win_and_survived,
               SUM(damage_dealt) AS damage_dealt, SUM(damage_received) AS damage_received
        FROM {tanks_table}
        WHERE tank_id IN ({placeholders})
        GROUP BY tank_id
    ''', target_tanks)
    all_sum_raw = {r['tank_id']: dict(r) for r in cursor.fetchall()}

    rows = []
    # Сортируем танки по уровню и боям
    sorted_tanks = sorted(player_tanks_raw.values(),
                          key=lambda x: (-x['tier'], -(x['battles'] or 0)))

    for tank_data in sorted_tanks:
        tank_id = tank_data['tank_id']
        tank_name = tank_data['tank_name'] or f'tank_{tank_id}'
        tier = tank_data['tier']

        # Строка "игрок"
        player_stats = {m: safe_num(tank_data.get(m)) for m in ABS_METRICS}
        player_full = calculate_derived_metrics(player_stats)
        row = {'tank_name': tank_name, 'tier': tier, 'Players': 'player'}
        for m in ALL_METRICS:
            row[m] = round(player_full[m], 2)
        rows.append(row)

        # Строка "все игроки (сумма)"
        all_raw = all_sum_raw.get(tank_id)
        if all_raw:
            all_stats = {m: safe_num(all_raw.get(m)) for m in ABS_METRICS}
            all_full = calculate_derived_metrics(all_stats)
            row = {'tank_name': tank_name, 'tier': tier, 'Players': 'all players (sum)'}
            for m in ALL_METRICS:
                row[m] = round(all_full[m], 2)
            rows.append(row)
        else:
            rows.append({'tank_name': tank_name, 'tier': tier,
                         'Players': 'all players (sum)', 'wins': 'нет данных'})

    return rows


# ============================================================
# ОСНОВНАЯ ФУНКЦИЯ
# ============================================================
def main():
    start_time = time.time()

    print("\n" + "=" * 70)
    print(" ВЫГРУЗКА СРАВНИТЕЛЬНОЙ СТАТИСТИКИ ИГРОКА (ОБНОВЛЁННАЯ)")
    print("=" * 70)
    print(f"🎯 Целевой игрок: {TARGET_PLAYER_ID}")
    print(f"📅 Период: последние {DAYS_PERIOD} дней")
    print(f"🎖️ Уровни танков: {TANK_TIERS}")

    conn = get_db_connection()
    cursor = get_cursor(conn)
    try:
        cursor.execute("SET work_mem = '1GB';")

        period_start_int = int((datetime.now() - timedelta(days=DAYS_PERIOD)).replace(
            hour=0, minute=0, second=0, microsecond=0).timestamp())

        # Этап 1: Подготовка временных таблиц
        print("\n" + "=" * 70)
        print("📦 ЭТАП 1: ПОДГОТОВКА ВРЕМЕННЫХ ТАБЛИЦ")
        print("=" * 70)
        counts = prepare_temp_tables(cursor, period_start_int)

        # Этап 2: Сбор данных для листов
        print("\n" + "=" * 70)
        print("📊 ЭТАП 2: СБОР ДАННЫХ ДЛЯ ЛИСТОВ")
        print("=" * 70)

        sheets = {}

        print("\n⏳ Лист 1: Все танки (всё время)...")
        sheets['1.Все танки (время)'] = build_sheet_all_tanks(cursor, 'temp_players_alltime', TARGET_PLAYER_ID)

        print("⏳ Лист 2: Все танки (30 дней)...")
        sheets['2.Все танки (30д)'] = build_sheet_all_tanks(cursor, 'temp_players_30d', TARGET_PLAYER_ID)

        print("⏳ Лист 3: Уровни (всё время)...")
        sheets['3.Уровни (время)'] = build_sheet_by_tier(cursor, 'temp_tanks_alltime', TARGET_PLAYER_ID)

        print("⏳ Лист 4: Уровни (30 дней)...")
        sheets['4.Уровни (30д)'] = build_sheet_by_tier(cursor, 'temp_tanks_30d', TARGET_PLAYER_ID)

        print("⏳ Лист 5: Танки (всё время)...")
        sheets['5.Танки (время)'] = build_sheet_by_tank(cursor, 'temp_tanks_alltime', TARGET_PLAYER_ID)

        print("⏳ Лист 6: Танки (30 дней)...")
        sheets['6.Танки (30д)'] = build_sheet_by_tank(cursor, 'temp_tanks_30d', TARGET_PLAYER_ID)

        # Этап 3: Сохранение в xlsx
        print("\n" + "=" * 70)
        print("💾 ЭТАП 3: СОХРАНЕНИЕ В XLSX")
        print("=" * 70)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"player_comparison_{TARGET_PLAYER_ID}_{timestamp}.xlsx"

        # Определяем порядок столбцов для разных типов листов
        columns_all_tanks = ['Players'] + ALL_METRICS
        columns_by_tier = ['tier', 'Players'] + ALL_METRICS
        columns_by_tank = ['tank_name', 'tier', 'Players'] + ALL_METRICS

        with pd.ExcelWriter(filename, engine='openpyxl') as writer:
            for sheet_name, data in sheets.items():
                df = pd.DataFrame(data)
                # Определяем порядок столбцов
                if sheet_name.startswith('1.') or sheet_name.startswith('2.'):
                    cols = [c for c in columns_all_tanks if c in df.columns]
                elif sheet_name.startswith('3.') or sheet_name.startswith('4.'):
                    cols = [c for c in columns_by_tier if c in df.columns]
                else:
                    cols = [c for c in columns_by_tank if c in df.columns]
                df = df[cols]
                df.to_excel(writer, sheet_name=sheet_name, index=False)

        print(f"   ✅ Файл сохранён: {filename}")

        # Итог
        elapsed = time.time() - start_time
        print(f"\n{'=' * 70}")
        print(f"✅ Выгрузка завершена за {elapsed:.1f} сек ({elapsed / 60:.1f} мин)")
        print(f"   📊 Подготовлено записей:")
        print(f"      • players (всё время): {counts['players_alltime']:,}")
        print(f"      • players_stats_30d: {counts['players_30d']:,}")
        print(f"      • танки (всё время): {counts['tanks_alltime']:,}")
        print(f"      • танки (30 дней): {counts['tanks_30d']:,}")
        print(f"   💾 Файл: {filename}")

    except Exception as e:
        print(f"❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
        log_error("export_player_comparison", "DB_ERROR", "PG_ERROR", str(e), "main")
    finally:
        for table in ['temp_players_alltime', 'temp_players_30d',
                      'temp_tanks_alltime', 'temp_tanks_30d',
                      'temp_tanks_in_period', 'temp_tanks_before_period']:
            try:
                cursor.execute(f"DROP TABLE IF EXISTS {table};")
            except:
                pass
        release_db_connection(conn)

    print("\n" + "=" * 70)
    print("💾 СОХРАНЕНИЕ ЛОГА ОШИБОК")
    print("=" * 70)
    save_errors_to_db()
    export_errors_to_excel()


if __name__ == "__main__":
    main()