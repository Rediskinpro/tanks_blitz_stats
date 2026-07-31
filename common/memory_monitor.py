import psutil
import threading
import time
from datetime import datetime


class MemoryMonitor:
    def __init__(self, threshold_mb=3500, check_interval=30):
        """
        Монитор использования памяти

        Args:
            threshold_mb: порог памяти в МБ, при превышении выводится предупреждение
            check_interval: интервал проверки в секундах
        """
        self.threshold_mb = threshold_mb
        self.check_interval = check_interval
        self._stop_event = threading.Event()
        self._thread = None

    def start(self):
        """Запускает мониторинг в фоновом потоке"""
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()
        print(f"🔍 Мониторинг памяти запущен (порог: {self.threshold_mb} МБ)")

    def stop(self):
        """Останавливает мониторинг"""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)
        print("🔍 Мониторинг памяти остановлен")

    def _monitor_loop(self):
        """Цикл проверки памяти"""
        while not self._stop_event.is_set():
            memory_info = self.get_memory_info()

            if memory_info['system_percent'] > 90:
                print(f"⚠️  ВНИМАНИЕ: Высокое использование памяти!")

            if memory_info['process_mb'] > self.threshold_mb:
                print(f"⚠️  ВНИМАНИЕ: Процесс использует {memory_info['process_mb']:.1f} МБ "
                      f"(порог: {self.threshold_mb} МБ)")

            self._stop_event.wait(self.check_interval)

    def get_memory_info(self):
        """Получает информацию о памяти"""
        process = psutil.Process()
        memory_info = process.memory_info()
        system_memory = psutil.virtual_memory()

        return {
            'process_mb': memory_info.rss / 1024 / 1024,  # RSS - реальное использование
            'process_vms_mb': memory_info.vms / 1024 / 1024,  # VMS - виртуальная память
            'system_total_mb': system_memory.total / 1024 / 1024,
            'system_available_mb': system_memory.available / 1024 / 1024,
            'system_percent': system_memory.percent
        }

    def print_memory_status(self):
        """Выводит текущий статус памяти"""
        info = self.get_memory_info()
        print(f"\n{'=' * 70}")
        print(f"📊 СТАТУС ПАМЯТИ")
        print(f"{'=' * 70}")
        print(f"  Процесс:")
        print(f"    RSS (физическая): {info['process_mb']:.1f} МБ")
        print(f"    VMS (виртуальная): {info['process_vms_mb']:.1f} МБ")
        print(f"  Система:")
        print(f"    Всего: {info['system_total_mb']:.1f} МБ")
        print(f"    Доступно: {info['system_available_mb']:.1f} МБ")
        print(f"    Используется: {info['system_percent']:.1f}%")
        print(f"{'=' * 70}\n")


# Глобальный монитор (можно использовать в любом скрипте)
memory_monitor = MemoryMonitor(threshold_mb=3500, check_interval=30)