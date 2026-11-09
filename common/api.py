import time
import threading
import requests
from common.config import API_KEYS, BASE_URL
from common.logger import log_error


class AdaptiveRateLimiter:
    def __init__(
            self,
            base_delay: float = 0.2,
            min_delay: float = 0.1,
            max_delay: float = 5.0,
            cooldown_base: float = 5.0,
            cooldown_max: float = 60.0,
            slowdown_factor: float = 5,
            recovery_factor: float = 0.95,
    ):
        self.base_delay = base_delay
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.cooldown_base = cooldown_base
        self.cooldown_max = cooldown_max
        self.slowdown_factor = slowdown_factor
        self.recovery_factor = recovery_factor

        self._current_delay = base_delay
        self._delay_lock = threading.Lock()

        self._cooldown_until = 0.0
        self._cooldown_lock = threading.Lock()

        self._slowdown_count = 0
        self._recovery_count = 0

    def wait(self):
        with self._cooldown_lock:
            cooldown_remaining = self._cooldown_until - time.time()

        if cooldown_remaining > 0:
            actual_delay = max(self._current_delay, self.base_delay * self.slowdown_factor)
            time.sleep(min(cooldown_remaining, actual_delay))
            return

        with self._delay_lock:
            actual_delay = self._current_delay

        time.sleep(actual_delay)

    def report_success(self):
        with self._delay_lock:
            new_delay = self._current_delay * self.recovery_factor
            self._current_delay = max(self.min_delay, min(new_delay, self.base_delay))
            self._recovery_count += 1

    def report_error(self, error_type: str = "unknown"):
        now = time.time()

        with self._delay_lock:
            new_delay = self._current_delay * self.slowdown_factor
            self._current_delay = min(new_delay, self.max_delay)

        with self._cooldown_lock:
            remaining = self._cooldown_until - now
            if remaining > 0:
                new_cooldown = min(remaining * self.slowdown_factor, self.cooldown_max)
            else:
                new_cooldown = self.cooldown_base

            self._cooldown_until = now + new_cooldown
            self._slowdown_count += 1

    def get_stats(self) -> dict:
        with self._delay_lock:
            current_delay = self._current_delay
            recovery_count = self._recovery_count
            slowdown_count = self._slowdown_count
        with self._cooldown_lock:
            cooldown_remaining = max(0, self._cooldown_until - time.time())

        return {
            'current_delay': round(current_delay, 3),
            'cooldown_remaining': round(cooldown_remaining, 1),
            'slowdown_count': slowdown_count,
            'recovery_count': recovery_count,
        }


rate_limiter = AdaptiveRateLimiter(
    base_delay=0.5,
    min_delay=0.3,
    max_delay=5.0,
    cooldown_base=10.0,
    cooldown_max=60.0,
    slowdown_factor=1.5,
    recovery_factor=0.95,
)

def get_api_data(ids, endpoint, param_name="account_id", extra=None, depth=0, max_depth=6, retry_count=0, max_retries=3):

    if not ids:
        return {}, []

    limiter = rate_limiter
    api_key = API_KEYS[0]

    limiter.wait()
    ids_str = ",".join(map(str, ids))
    params = {"application_id": api_key, param_name: ids_str}
    if extra:
        params["extra"] = extra

    try:
        response = requests.get(f"{BASE_URL}/{endpoint}", params=params, timeout=15)
        data = response.json()

        if data.get("status") == "ok":
            limiter.report_success()
            return data.get("data", {}), []

        error_info = data.get("error", {})
        error_code = error_info.get("code")
        error_msg = error_info.get("message", "")

        retryable_errors = ["REQUEST_LIMIT_EXCEEDED", "SOURCE_NOT_AVAILABLE"]
        is_retryable = (error_msg in retryable_errors) or (str(error_code) in ["429", "502", "503", "504"])

        if is_retryable:
            limiter.report_error(error_msg)

            if retry_count < max_retries:
                time.sleep(1)
                return get_api_data(ids, endpoint, param_name, extra, depth=depth, max_depth=max_depth, retry_count=retry_count + 1, max_retries=max_retries)

            log_error(endpoint, error_info.get("field", "unknown"), str(error_code), error_msg, ids_str)
            return {}, list(ids)

        if depth < max_depth and len(ids) > 1:
            time.sleep(0.1)
            mid = len(ids) // 2
            left_data, left_failed = get_api_data(ids[:mid], endpoint, param_name, extra, depth + 1, max_depth, 0, max_retries)
            right_data, right_failed = get_api_data(ids[mid:], endpoint, param_name, extra, depth + 1, max_depth, 0, max_retries)
            left_data.update(right_data)
            return left_data, left_failed + right_failed

        log_error(endpoint, error_info.get("field", "unknown"),
                  str(error_code), error_info.get("message", "unknown"), ids_str)
        return {}, list(ids)

    except requests.exceptions.Timeout as e:
        limiter.report_error("TIMEOUT")

        if retry_count < max_retries:
            time.sleep(1)
            return get_api_data(ids, endpoint, param_name, extra, depth=depth, max_depth=max_depth, retry_count=retry_count + 1, max_retries=max_retries)

        if depth < max_depth and len(ids) > 1:
            time.sleep(0.1)
            mid = len(ids) // 2
            left_data, left_failed = get_api_data(ids[:mid], endpoint, param_name, extra, depth + 1, max_depth, 0, max_retries)
            right_data, right_failed = get_api_data(ids[mid:], endpoint, param_name, extra, depth + 1, max_depth, 0, max_retries)
            left_data.update(right_data)
            return left_data, left_failed + right_failed

        log_error(endpoint, "EXCEPTION", "NETWORK_ERROR", str(e), ids_str)
        return {}, list(ids)

    except Exception as e:
        if depth < max_depth and len(ids) > 1:
            time.sleep(0.1)
            mid = len(ids) // 2
            left_data, left_failed = get_api_data(ids[:mid], endpoint, param_name, extra, depth + 1, max_depth, 0, max_retries)
            right_data, right_failed = get_api_data(ids[mid:], endpoint, param_name, extra, depth + 1, max_depth, 0, max_retries)
            left_data.update(right_data)
            return left_data, left_failed + right_failed

        log_error(endpoint, "EXCEPTION", "NETWORK_ERROR", str(e), ids_str)
        return {}, list(ids)