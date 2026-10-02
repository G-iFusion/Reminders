"""Уведомления о напоминаниях: системные и запасное окно."""

from __future__ import annotations

import queue
import shutil
import subprocess
import threading
import warnings

import tkinter as tk
from plyer import notification
from tkinter import ttk

from database import STATUS_DONE, ReminderDatabase

APP_NAME = "Напоминалка"
CHECK_INTERVAL_SECONDS = 1.0
POPUP_TIMEOUT_MS = 30_000
SYSTEM_TIMEOUT_MS = "10000"
EMPTY_MESSAGE = "Пора выполнить напоминание"


class NotificationManager:
    def __init__(self, database: ReminderDatabase, root: tk.Misc | None = None) -> None:
        self.database = database
        self.root = root
        self.last_error: BaseException | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._queue: queue.Queue[tuple[str, str, int | None]] = queue.Queue()
        self._guard = threading.Lock()
        self._notify_lock = threading.Lock()
        self._inflight: set[int] = set()
        self._finished: set[int] = set()
        self._polling = False
        self._popup_shift = 0

    def set_root(self, root: tk.Misc) -> None:
        self.root = root
        self._ensure_poll()

    def system_notifications_available(self) -> bool:
        """На Kali plyer показывает уведомление через notify-send."""
        return shutil.which("notify-send") is not None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            self._ensure_poll()
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._monitor,
            name="reminder-notifications",
            daemon=True,
        )
        self._thread.start()
        self._ensure_poll()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)
        self._thread = None

    def show_manual_notification(self, title: str, message: str) -> bool:
        clean_title = title.strip() or APP_NAME
        clean_message = message.strip() or clean_title
        if self._try_system(clean_title, clean_message):
            return True
        if self.root is None:
            return False
        self._show_popup(clean_title, clean_message)
        return True

    def test_notification(self) -> bool:
        return self.show_manual_notification(
            "Тестовое уведомление",
            "Проверка уведомлений напоминалки",
        )

    def _monitor(self) -> None:
        while not self._stop.is_set():
            try:
                self._scan()
            except Exception as exc:
                self.last_error = exc
            if self._stop.wait(CHECK_INTERVAL_SECONDS):
                return

    def _scan(self) -> None:
        self.database.mark_overdue()
        for reminder in self.database.get_due_reminders():
            self._show_notification(reminder)

    def _show_notification(self, reminder: dict) -> None:
        reminder_id = int(reminder["id"])
        with self._guard:
            if reminder_id in self._inflight or reminder_id in self._finished:
                return
            self._inflight.add(reminder_id)
        title = str(reminder["title"]).strip() or APP_NAME
        message = str(reminder["description"] or "").strip() or EMPTY_MESSAGE
        if self._try_system(title, message):
            self._finish(reminder_id)
            return
        if self._enqueue_popup(title, message, reminder_id):
            return
        with self._guard:
            self._inflight.discard(reminder_id)

    def _try_system(self, title: str, message: str) -> bool:
        """True, если рабочий стол принял уведомление plyer."""
        if not self.system_notifications_available():
            return False
        outcome = {"ok": True}
        original_call = subprocess.call

        def call(args, *extra, **kwargs):
            command = list(args)
            if command and command[0].endswith("notify-send") and "-t" in command:
                timeout_at = command.index("-t") + 1
                if timeout_at < len(command):
                    command[timeout_at] = SYSTEM_TIMEOUT_MS
            code = original_call(command, *extra, **kwargs)
            if command and command[0].endswith("notify-send") and code != 0:
                outcome["ok"] = False
            return code

        with self._notify_lock:
            subprocess.call = call
            try:
                with warnings.catch_warnings():
                    warnings.filterwarnings(
                        "ignore",
                        message="The Python dbus package is not installed.*",
                    )
                    warnings.filterwarnings("ignore", message="notify-send not found.*")
                    notification.notify(
                        title=title,
                        message=message,
                        app_name=APP_NAME,
                        timeout=10,
                    )
            except Exception:
                return False
            finally:
                subprocess.call = original_call
        return outcome["ok"]

    def _enqueue_popup(self, title: str, message: str, reminder_id: int | None) -> bool:
        if self.root is None:
            return False
        self._queue.put((title, message, reminder_id))
        return True

    def _ensure_poll(self) -> None:
        root = self.root
        if root is None or self._polling or self._stop.is_set():
            return
        try:
            if not root.winfo_exists():
                return
        except tk.TclError:
            return
        self._polling = True
        root.after(100, self._drain)

    def _drain(self) -> None:
        self._polling = False
        while True:
            try:
                title, message, reminder_id = self._queue.get_nowait()
            except queue.Empty:
                break
            try:
                self._show_popup(title, message, reminder_id)
            except Exception as exc:
                self.last_error = exc
                if reminder_id is not None:
                    with self._guard:
                        self._inflight.discard(reminder_id)
        if self.root is not None and not self._stop.is_set():
            self._ensure_poll()

    def _show_popup(self, title: str, message: str, reminder_id: int | None = None) -> None:
        root = self.root
        if root is None:
            raise RuntimeError("Нет окна для запасного уведомления")
        popup = tk.Toplevel(root)
        popup.title(APP_NAME)
        popup.resizable(False, False)
        frame = ttk.Frame(popup, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(
            frame,
            text=title,
            font=("", 13, "bold"),
            wraplength=360,
            justify="left",
        ).pack(anchor="w")
        ttk.Label(
            frame,
            text=message,
            wraplength=360,
            justify="left",
        ).pack(anchor="w", pady=(8, 16))

        closed = False

        def close() -> None:
            nonlocal closed
            if closed:
                return
            closed = True
            try:
                popup.destroy()
            except tk.TclError:
                pass

        ttk.Button(frame, text="Закрыть", command=close).pack(anchor="e")
        popup.protocol("WM_DELETE_WINDOW", close)
        popup.update_idletasks()
        width = max(popup.winfo_reqwidth(), 320)
        height = max(popup.winfo_reqheight(), 140)
        shift = self._popup_shift * 28
        self._popup_shift += 1
        x = max((popup.winfo_screenwidth() - width) // 2 + shift, 0)
        y = max((popup.winfo_screenheight() - height) // 2 + shift, 0)
        popup.geometry(f"{width}x{height}+{x}+{y}")
        popup.deiconify()
        popup.lift()
        popup.attributes("-topmost", True)
        popup.focus_force()
        popup.after(POPUP_TIMEOUT_MS, close)
        if reminder_id is not None:
            self._finish(reminder_id)

    def _finish(self, reminder_id: int) -> None:
        with self._guard:
            if reminder_id in self._finished:
                return
            self._finished.add(reminder_id)
            self._inflight.discard(reminder_id)
        self.database.update_status(reminder_id, STATUS_DONE)
        self.database.schedule_next(reminder_id)
