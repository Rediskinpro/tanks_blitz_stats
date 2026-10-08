import random
from collections import defaultdict
COND_NO_1_2 = 0
COND_10_SIDED = 0
COND_REROLL = 0
NUM_GAMES = 10000
NUM_CELLS = 12
MAX_CROSSES = 40
CELL_NAMES = {
    1: "Обрядовый контейнер",
    2: "Ресурсный контейнер 1",
    3: "Диковинный контейнер",
    4: "Ресурсный контейнер 1",
    5: "Резной контейнер",
    6: "Многоликий контейнер",
    7: "Зачарованный контейнер",
    8: "Ресурсный контейнер 1",
    9: "Мини-мистический контейнер",
    10: "Ресурсный контейнер 1",
    11: "Резной контейнер",
    12: "Маскировочный контейнер"
}

def run_simulation(dice_min: int, dice_max: int, reroll_chance: float) -> tuple[list[tuple[str, float]], int]:
    stats = [0] * (NUM_CELLS + 1)
    total_rolls = 0

    randint = random.randint
    random_random = random.random

    for _ in range(NUM_GAMES):
        pos = 1
        crosses = 0

        while crosses < MAX_CROSSES:
            total_rolls += 1
            pos += randint(dice_min, dice_max)

            if pos > NUM_CELLS:
                crosses += 1
                pos -= NUM_CELLS

            stats[pos] += 1
            if reroll_chance > 0 and crosses < MAX_CROSSES and random_random() < reroll_chance:
                total_rolls += 1
                pos += randint(dice_min, dice_max)

                if pos > NUM_CELLS:
                    crosses += 1
                    pos -= NUM_CELLS

                stats[pos] += 1

    aggregated = defaultdict(int)
    for i in range(1, NUM_CELLS + 1):
        aggregated[CELL_NAMES[i]] += stats[i]

    results_list = [
        (name, count / NUM_GAMES)
        for name, count in aggregated.items()
    ]

    results_list.sort(key=lambda x: x[1], reverse=True)

    return results_list, total_rolls

def print_results(results_list: list[tuple[str, float]], total_rolls: int) -> None:
    print(f"Среднее количество выпадений контейнеров за 1 игру (симуляция {NUM_GAMES} игр):")

    for name, avg_count in results_list:
        print(f"{name:<26} | {avg_count:>6.2f} раз(а)")

    avg_rolls = total_rolls / NUM_GAMES
    print(f"Среднее количество бросков кубика на игру: {avg_rolls:.2f}\n")

def main():
    dice_min = 3 if COND_NO_1_2 else 1
    dice_max = 10 if COND_10_SIDED else 6
    reroll_chance = 0.20 if COND_REROLL else 0.0

    results, total_rolls = run_simulation(dice_min, dice_max, reroll_chance)

    print_results(results, total_rolls)

if __name__ == "__main__":
    main()