API_KEY = "59fbd1d08ae11577dc9355ad1ab44166"
BASE_URL = "https://papi.tanksblitz.ru"
BATCH_SIZE = 100
RATE_LIMIT = 15
MAX_ROUNDS = 3
MAX_WORKERS = 15
BATCH_WORKERS = 15
DELAY = 0.1
ERRORS_FILE = "errors_log.xlsx"
TANKS_SAVE_INTERVAL = 100

DB_CONFIG = {
    'host': 'localhost',
    'port': 5432,
    'database': 'tanks_blitz',
    'user': 'tanks_user',
    'password': 'DD75832b!7391',
}

DB_POOL_MIN = 5
DB_POOL_MAX = 50