import time
import threading
import requests
from common.config import API_KEY, BASE_URL
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


def get_api_data(ids, endpoint, param_name="account_id", extra=None, depth=0, max_depth=6, is_retry=False):
    if not ids:
        return {}, []

    rate_limiter.wait()
    ids_str = ",".join(map(str, ids))
    params = {"application_id": API_KEY, param_name: ids_str}
    if extra:
        params["extra"] = extra

    try:
        response = requests.get(f"{BASE_URL}/{endpoint}", params=params, timeout=15)
        data = response.json()

        if data.get("status") == "ok":
            return data.get("data", {}), []

        error_info = data.get("error", {})
        error_code = error_info.get("code")

        if error_code == 504 and not is_retry and (depth >= max_depth or len(ids) == 1):
            time.sleep(2)
            return get_api_data(ids, endpoint, param_name, extra, depth, max_depth, is_retry=True)

        if depth < max_depth and len(ids) > 1:
            time.sleep(0.1)
            mid = len(ids) // 2
            left_data, left_failed = get_api_data(ids[:mid], endpoint, param_name, extra, depth + 1, max_depth)
            right_data, right_failed = get_api_data(ids[mid:], endpoint, param_name, extra, depth + 1, max_depth)
            left_data.update(right_data)
            return left_data, left_failed + right_failed

        log_error(endpoint, error_info.get("field", "unknown"), str(error_code),
                  error_info.get("message", "unknown"), ids_str)
        return {}, list(ids)

    except Exception as e:
        if not is_retry and (depth >= max_depth or len(ids) == 1):
            time.sleep(2)
            return get_api_data(ids, endpoint, param_name, extra, depth, max_depth, is_retry=True)

        if depth < max_depth and len(ids) > 1:
            time.sleep(0.1)
            mid = len(ids) // 2
            left_data, left_failed = get_api_data(ids[:mid], endpoint, param_name, extra, depth + 1, max_depth)
            right_data, right_failed = get_api_data(ids[mid:], endpoint, param_name, extra, depth + 1, max_depth)
            left_data.update(right_data)
            return left_data, left_failed + right_failed

        log_error(endpoint, "EXCEPTION", "NETWORK_ERROR", str(e), ids_str)
        return {}, list(ids)