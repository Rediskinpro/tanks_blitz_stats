import threading
import psycopg2
import psycopg2.pool
import psycopg2.extras
from common.config import DB_CONFIG, DB_POOL_MIN, DB_POOL_MAX

db_lock = threading.Lock()

_connection_pool = None
_pool_lock = threading.Lock()


def _get_pool():
    """Ленивая инициализация пула соединений"""
    global _connection_pool
    if _connection_pool is None:
        with _pool_lock:
            if _connection_pool is None:
                _connection_pool = psycopg2.pool.ThreadedConnectionPool(
                    minconn=DB_POOL_MIN,
                    maxconn=DB_POOL_MAX,
                    **DB_CONFIG
                )
    return _connection_pool


def get_db_connection():
    """Получает соединение из пула"""
    pool = _get_pool()
    conn = pool.getconn()
    return conn


def get_cursor(conn):
    """Создаёт курсор с поддержкой доступа по имени колонки"""
    return conn.cursor(cursor_factory=psycopg2.extras.DictCursor)


def release_db_connection(conn):
    """Возвращает соединение в пул"""
    pool = _get_pool()
    pool.putconn(conn)


def close_all_connections():
    """Закрывает все соединения в пуле"""
    global _connection_pool
    if _connection_pool:
        _connection_pool.closeall()
        _connection_pool = None