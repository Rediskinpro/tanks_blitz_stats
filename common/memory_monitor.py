import psutil
import shutil
import threading
from common.logger import log_error


class MemoryMonitor:
    def __init__(self,
                 process_threshold_mb=3500,
                 system_percent_threshold=85,
                 disk_c_min_free_gb=20,
                 disk_e_min_free_gb=50,
                 check_interval=30):
        """
        Монитор использования памяти и дискового пространства

        Args:
            process_threshold_mb: порог памяти процесса Python в МБ
            system_percent_threshold: порог использования системной памяти в %
            disk_c_min_free_gb: минимум свободного места на диске C (где PostgreSQL)
            disk_e_min_free_gb: минимум свободного места на диске E (где бэкапы)
            check_interval: интервал проверки в секундах
        """
        self.process_threshold_mb = process_threshold_mb
        self.system_percent_threshold = system_percent_threshold
        self.disk_c_min_free_gb = disk_c_min_free_gb
        self.disk_e_min_free_gb = disk_e_min_free_gb
        self.check_interval = check_interval

        self._stop_event = threading.Event()
        self._thread = None
        self._warnings_count = 0

    def start(self):
        """Запускает мониторинг в фоновом потоке"""
        self._stop_event.clear()
        self._warnings_count = 0
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True, name="memory-monitor")
        self._thread.start()
        print(f"🔍 Мониторинг памяти запущен:")
        print(f"   Порог процесса: {self.process_threshold_mb} МБ")
        print(f"   Порог системной памяти: {self.system_percent_threshold}%")
        print(f"   Минимум на диске C: {self.disk_c_min_free_gb} ГБ")
        print(f"   Минимум на диске E: {self.disk_e_min_free_gb} ГБ")

    def stop(self):
        """Останавливает мониторинг"""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)
        print(f"🔍 Мониторинг памяти остановлен (предупреждений за сессию: {self._warnings_count})")

    def _monitor_loop(self):
        """Цикл проверки памяти и дисков"""
        while not self._stop_event.is_set():
            try:
                self._check_system_memory()
                self._check_process_memory()
                self._check_children_memory()
                self._check_disk_space("C:\\", self.disk_c_min_free_gb, "C (PostgreSQL)")
                self._check_disk_space("E:\\", self.disk_e_min_free_gb, "E (бэкапы)")
            except Exception as e:
                # Монитор не должен падать
                print(f"⚠️ Ошибка в мониторинге: {e}")

            self._stop_event.wait(self.check_interval)

    def _check_system_memory(self):
        """Проверяет использование системной памяти"""
        try:
            system_memory = psutil.virtual_memory()

            if system_memory.percent > self.system_percent_threshold:
                self._warnings_count += 1
                available_gb = system_memory.available / (1024 ** 3)
                print(f"⚠️  СИСТЕМНАЯ ПАМЯТЬ: {system_memory.percent:.1f}% "
                      f"(свободно {available_gb:.1f} ГБ)")

                # Критический уровень — логируем
                if system_memory.percent > 95:
                    log_error("memory_monitor", "SYSTEM_MEMORY", "CRITICAL",
                              f"{system_memory.percent:.1f}%", "system")
        except Exception as e:
            print(f"⚠️ Ошибка проверки системной памяти: {e}")

    def _check_process_memory(self):
        """Проверяет память main-процесса Python"""
        try:
            process = psutil.Process()
            process_mb = process.memory_info().rss / (1024 ** 2)

            if process_mb > self.process_threshold_mb:
                self._warnings_count += 1
                print(f"⚠️  MAIN-ПРОЦЕСС: {process_mb:.1f} МБ "
                      f"(порог: {self.process_threshold_mb} МБ)")
        except Exception as e:
            print(f"⚠️ Ошибка проверки процесса: {e}")

    def _check_children_memory(self):
        """Проверяет память дочерних процессов (запущенных скриптов)"""
        try:
            process = psutil.Process()
            children = process.children(recursive=True)

            if not children:
                return

            total_children_mb = 0
            for child in children:
                try:
                    total_children_mb += child.memory_info().rss / (1024 ** 2)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass

            # Если дочерние процессы потребляют много — предупреждаем
            if total_children_mb > self.process_threshold_mb:
                self._warnings_count += 1
                print(f"⚠️  ДОЧЕРНИЕ ПРОЦЕССЫ: {total_children_mb:.1f} МБ "
                      f"({len(children)} процессов)")
        except Exception as e:
            print(f"⚠️ Ошибка проверки дочерних процессов: {e}")

    def _check_disk_space(self, drive_path, min_free_gb, drive_label):
        """Проверяет свободное место на диске"""
        try:
            if not self._drive_exists(drive_path):
                return  # Диск не подключён — не предупреждаем

            disk_usage = shutil.disk_usage(drive_path)
            free_gb = disk_usage.free / (1024 ** 3)

            if free_gb < min_free_gb:
                self._warnings_count += 1
                print(f"⚠️  ДИСК {drive_label}: свободно {free_gb:.1f} ГБ "
                      f"(минимум: {min_free_gb} ГБ)")

                # Критический уровень — логируем
                if free_gb < min_free_gb / 2:
                    log_error("memory_monitor", "DISK_SPACE", "CRITICAL",
                              f"{drive_label}: {free_gb:.1f} ГБ", drive_path)
        except Exception as e:
            print(f"⚠️ Ошибка проверки диска {drive_path}: {e}")

    def _drive_exists(self, drive_path):
        """Проверяет, существует ли диск"""
        try:
            return shutil.disk_usage(drive_path) is not None
        except (FileNotFoundError, OSError):
            return False

    def get_memory_info(self):
        """Получает полную информацию о памяти и дисках"""
        process = psutil.Process()
        system_memory = psutil.virtual_memory()

        # Суммируем память main + дочерних процессов
        total_process_mb = process.memory_info().rss / (1024 ** 2)
        children_count = 0
        try:
            for child in process.children(recursive=True):
                try:
                    total_process_mb += child.memory_info().rss / (1024 ** 2)
                    children_count += 1
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

        # Диски
        disk_c_free = self._get_disk_free_gb("C:\\")
        disk_e_free = self._get_disk_free_gb("E:\\")

        return {
            'process_mb': process.memory_info().rss / (1024 ** 2),
            'total_python_mb': total_process_mb,
            'children_count': children_count,
            'system_total_mb': system_memory.total / (1024 ** 2),
            'system_available_mb': system_memory.available / (1024 ** 2),
            'system_percent': system_memory.percent,
            'disk_c_free_gb': disk_c_free,
            'disk_e_free_gb': disk_e_free,
        }

    def _get_disk_free_gb(self, drive_path):
        """Получает свободное место на диске в ГБ"""
        try:
            if not self._drive_exists(drive_path):
                return None
            return shutil.disk_usage(drive_path).free / (1024 ** 3)
        except Exception:
            return None

    def print_memory_status(self):
        """Выводит текущий статус памяти и дисков"""
        info = self.get_memory_info()

        print(f"\n{'=' * 70}")
        print(f"📊 СТАТУС ПАМЯТИ И ДИСКОВ")
        print(f"{'=' * 70}")

        print(f"  Python-процессы:")
        print(f"    Main-процесс (RSS): {info['process_mb']:.1f} МБ")
        print(f"    Всего с дочерними:  {info['total_python_mb']:.1f} МБ ({info['children_count']} процессов)")

        print(f"  Система:")
        print(f"    Всего: {info['system_total_mb'] / 1024:.1f} ГБ")
        print(f"    Доступно: {info['system_available_mb'] / 1024:.1f} ГБ")
        print(f"    Используется: {info['system_percent']:.1f}%")

        print(f"  Диски:")
        if info['disk_c_free_gb'] is not None:
            status_c = "✅" if info['disk_c_free_gb'] >= self.disk_c_min_free_gb else "⚠️"
            print(f"    {status_c} Диск C (PostgreSQL): {info['disk_c_free_gb']:.1f} ГБ свободно")
        else:
            print(f"    ❓ Диск C: не подключён")

        if info['disk_e_free_gb'] is not None:
            status_e = "✅" if info['disk_e_free_gb'] >= self.disk_e_min_free_gb else "⚠️"
            print(f"    {status_e} Диск E (бэкапы): {info['disk_e_free_gb']:.1f} ГБ свободно")
        else:
            print(f"    ❓ Диск E: не подключён")

        print(f"{'=' * 70}\n")



memory_monitor = MemoryMonitor(
    process_threshold_mb=3500,
    system_percent_threshold=85,
    disk_c_min_free_gb=20,
    disk_e_min_free_gb=50,
    check_interval=30
)