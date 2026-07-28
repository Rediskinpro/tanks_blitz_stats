import subprocess
import sys
import time
from datetime import datetime

SCRIPTS = [
    "1. update clans.py",
    "2. update players.py",
    "3. update players_tanks_stats.py",
]

def main():
    start_time = time.time()
    print("\n" + "=" * 70)
    print(" ПОЛНЫЙ ЦИКЛ ОБНОВЛЕНИЯ ДАННЫХ")
    print(f" Запуск: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    for i, script in enumerate(SCRIPTS, 1):
        print(f"\n{'=' * 70}")
        print(f" ШАГ {i}/{len(SCRIPTS)}: {script}")
        print(f"{'=' * 70}")

        result = subprocess.run([sys.executable, script])

        if result.returncode != 0:
            print(f"\n❌ Ошибка в скрипте {script} (код: {result.returncode})")
            print("⛔ Прерываем выполнение.")
            break

    elapsed = time.time() - start_time
    print(f"\n{'=' * 70}")
    print(" ✅ ПОЛНЫЙ ЦИКЛ ЗАВЕРШЁН!")
    print(f"{'=' * 70}")
    print(f"⏱️  Общее время: {elapsed:.0f} сек ({elapsed / 60:.1f} мин)")

if __name__ == "__main__":
    main()