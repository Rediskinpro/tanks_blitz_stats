import pandas as pd
import threading
from datetime import datetime
from common.config import ERRORS_FILE
from common.db import get_db_connection, db_lock

error_log = []
error_log_lock = threading.Lock()


def log_error(endpoint, error_field, error_code, error_message, ids):
    """
    Добавляет ошибку в глобальный список.

    Args:
        endpoint: путь API
        error_field: поле, в котором возникла ошибка
        error_code: код ошибки
        error_message: текст ошибки
        ids: ID, при запросе которых возникла ошибка
    """
    with error_log_lock:
        error_log.append({
            'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'endpoint': endpoint,
            'error_field': str(error_field),
            'error_code': str(error_code),
            'error_message': str(error_message)[:200],
            'ids': str(ids)[:100]
        })


def export_errors_to_excel(filename=ERRORS_FILE):
    with error_log_lock:
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
    with error_log_lock:
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