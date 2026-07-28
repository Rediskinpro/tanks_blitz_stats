import time
import threading
import requests
from common.config import API_KEY, BASE_URL, DELAY
from common.logger import log_error


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


rate_limiter = RateLimiter(15)


def get_api_data(ids, endpoint, param_name="account_id", depth=0, max_depth=4, extra=None, retry_count=0,
                 max_retries=3):
    """
    Универсальный запрос к API.

    Args:
        ids: список ID для запроса
        endpoint: путь API (например, "wotb/account/info/")
        param_name: имя параметра (по умолчанию "account_id")
        depth: глубина рекурсии при ошибке
        max_depth: максимальная глубина рекурсии
        extra: дополнительный параметр (например, "clan")

    Returns:
        dict с данными или пустой dict при ошибке
    """

    if not ids:
        return {}

    rate_limiter.wait()
    ids_str = ",".join(map(str, ids))
    params = {"application_id": API_KEY, param_name: ids_str}
    if extra:
        params["extra"] = extra

    try:
        response = requests.get(f"{BASE_URL}/{endpoint}", params=params, timeout=15)
        data = response.json()

        if data.get("status") == "ok":
            return data.get("data", {})
        else:
            error_info = data.get("error", {})
            error_code = error_info.get("code")

            if error_code == 504 and retry_count < max_retries:
                sleep_time = 2 ** retry_count
                time.sleep(sleep_time)
                return get_api_data(ids, endpoint, param_name, depth, max_depth, extra, retry_count + 1, max_retries)

            if depth == 0:
                log_error(
                    endpoint,
                    error_info.get("field", "unknown"),
                    error_info.get("code", "unknown"),
                    error_info.get("message", "unknown"),
                    ids_str
                )
            return {}
    except Exception as e:
        if depth == 0:
            log_error(endpoint, "EXCEPTION", "EXCEPTION", str(e), ids_str)
        return {}

    if depth < max_depth and len(ids) > 1:
        time.sleep(DELAY)
        mid = len(ids) // 2
        left = get_api_data(ids[:mid], endpoint, param_name, depth + 1, max_depth, extra, retry_count, max_retries)
        right = get_api_data(ids[mid:], endpoint, param_name, depth + 1, max_depth, extra, retry_count, max_retries)
        left.update(right)
        return left
    return {}