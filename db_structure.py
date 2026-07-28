import sqlite3
import pandas as pd
from datetime import datetime

DB_FILE = "test_tanks_blitz.db"
OUTPUT_FILE = f"db_structure_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"


def get_database_info():
    """Собирает информацию о таблицах и их колонках и сохраняет в Excel"""
    print("=" * 70)
    print(f" СТРУКТУРА БАЗЫ ДАННЫХ: {DB_FILE}")
    print("=" * 70)

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    # Получаем список всех таблиц
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
    tables = [row[0] for row in cursor.fetchall()]

    print(f"\n📋 Найдено таблиц: {len(tables)}")

    # Создаем Excel writer
    with pd.ExcelWriter(OUTPUT_FILE, engine='openpyxl') as writer:

        # Лист с общей информацией
        summary_data = []

        for table in tables:
            print(f"\n{'=' * 70}")
            print(f"📊 ТАБЛИЦА: {table}")
            print("=" * 70)

            # Получаем информацию о колонках
            cursor.execute(f"PRAGMA table_info({table})")
            columns = cursor.fetchall()

            # Получаем количество записей
            try:
                cursor.execute(f"SELECT COUNT(*) FROM {table}")
                row_count = cursor.fetchone()[0]
            except:
                row_count = 0

            print(f"   Количество записей: {row_count:,}")
            print(f"   Количество колонок: {len(columns)}")
            print()

            # Данные для Excel - структура таблицы
            columns_data = []
            for col in columns:
                col_id, col_name, col_type, not_null, default_val, is_pk = col
                pk_mark = "YES" if is_pk else "NO"
                nn_mark = "YES" if not_null else "NO"
                default_str = str(default_val) if default_val is not None else ""

                columns_data.append({
                    'column_name': col_name,
                    'column_type': col_type,
                    'is_primary_key': pk_mark,
                    'not_null': nn_mark,
                    'default_value': default_str
                })

                # Для вывода в консоль
                print(f"   {col_name:<25} {col_type:<10} PK:{pk_mark:<3} NOT NULL:{nn_mark:<3} {default_str}")

            # Получаем информацию об индексах
            cursor.execute(f"PRAGMA index_list({table})")
            indexes = cursor.fetchall()

            indexes_data = []
            if indexes:
                print(f"\n    Индексы ({len(indexes)}):")
                for idx in indexes:
                    idx_name = idx[1]
                    idx_unique = "UNIQUE" if idx[2] else ""
                    cursor.execute(f"PRAGMA index_info({idx_name})")
                    idx_cols = [row[2] for row in cursor.fetchall()]
                    print(f"      • {idx_name} {idx_unique}: ({', '.join(idx_cols)})")
                    indexes_data.append({
                        'index_name': idx_name,
                        'is_unique': 'YES' if idx[2] else 'NO',
                        'columns': ', '.join(idx_cols)
                    })

            # Сохраняем данные о таблице на отдельный лист
            if columns_data:
                df_columns = pd.DataFrame(columns_data)
                df_columns.to_excel(writer, sheet_name=f"{table[:31]}", index=False)  # Имя листа макс 31 символ

                # Добавляем информацию об индексах на тот же лист, если есть
                if indexes_data:
                    df_indexes = pd.DataFrame(indexes_data)
                    start_row = len(df_columns) + 2
                    df_indexes.to_excel(writer, sheet_name=f"{table[:31]}", startrow=start_row, index=False,
                                        header=False)

            # Добавляем в сводку
            summary_data.append({
                'table_name': table,
                'columns_count': len(columns),
                'rows_count': row_count
            })

        # Создаем лист с общей сводкой
        if summary_data:
            df_summary = pd.DataFrame(summary_data)
            df_summary.to_excel(writer, sheet_name='Сводка', index=False)

        # Добавляем метаданные
        metadata_sheet = pd.DataFrame({
            'database_file': [DB_FILE],
            'export_date': [datetime.now().strftime('%Y-%m-%d %H:%M:%S')],
            'total_tables': [len(tables)]
        })
        metadata_sheet.to_excel(writer, sheet_name='Метаданные', index=False)

    conn.close()

    print("\n" + "=" * 70)
    print(" ✅ СБОР ИНФОРМАЦИИ ЗАВЕРШЁН")
    print("=" * 70)
    print(f"\n💾 Результаты сохранены в файл: {OUTPUT_FILE}")
    print(f"📊 Всего таблиц: {len(tables)}")


if __name__ == "__main__":
    get_database_info()