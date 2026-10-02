"""Хранение напоминаний в SQLite."""

from __future__ import annotations

import calendar
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path

STATUS_WAITING = "Ожидает"
STATUS_DONE = "Готово"
STATUS_OVERDUE = "Просрочено"
STATUS_CANCELLED = "Отменено"
STATUSES = (STATUS_WAITING, STATUS_DONE, STATUS_OVERDUE, STATUS_CANCELLED)

REPEAT_NONE = "none"
REPEAT_DAILY = "daily"
REPEAT_WEEKLY = "weekly"
REPEAT_MONTHLY = "monthly"
REPEAT_RULES = (REPEAT_NONE, REPEAT_DAILY, REPEAT_WEEKLY, REPEAT_MONTHLY)
REPEAT_LABELS = {
    REPEAT_NONE: "Без повтора",
    REPEAT_DAILY: "Каждый день",
    REPEAT_WEEKLY: "Каждую неделю",
    REPEAT_MONTHLY: "Каждый месяц",
}

DUE_GRACE = timedelta(minutes=1)
NEAREST_WINDOW = timedelta(hours=1)
DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"
DEFAULT_DB_PATH = Path(__file__).resolve().parent / "reminders.db"


def format_datetime(value: datetime) -> str:
    return value.strftime(DATETIME_FORMAT)


def parse_datetime(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        return value.replace(microsecond=0)
    text = str(value).strip()
    for fmt in (DATETIME_FORMAT, "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError("Некорректные дата и время")


def advance_due(due_at: datetime, repeat_rule: str) -> datetime:
    """Следующий срок после due_at по правилу повторения."""
    if repeat_rule == REPEAT_DAILY:
        return due_at + timedelta(days=1)
    if repeat_rule == REPEAT_WEEKLY:
        return due_at + timedelta(days=7)
    if repeat_rule == REPEAT_MONTHLY:
        return _add_months(due_at, 1)
    raise ValueError("Неизвестное правило повторения")


def _add_months(moment: datetime, months: int) -> datetime:
    month_index = moment.month - 1 + months
    year = moment.year + month_index // 12
    month = month_index % 12 + 1
    day = min(moment.day, calendar.monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day)


class ReminderDatabase:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
        self._lock = threading.Lock()
        self._connection = sqlite3.connect(self.db_path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self.init_database()

    def init_database(self) -> None:
        with self._lock:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    due_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    repeat_rule TEXT NOT NULL DEFAULT 'none'
                )
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_reminders_status_due
                ON reminders (status, due_at)
                """
            )
            self._connection.commit()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def add_reminder(
        self,
        title: str,
        description: str,
        due_at: datetime | str,
        repeat_rule: str = REPEAT_NONE,
        status: str = STATUS_WAITING,
        created_at: datetime | str | None = None,
    ) -> dict:
        clean_title = title.strip()
        if not clean_title:
            raise ValueError("Укажите название напоминания")
        if status not in STATUSES:
            raise ValueError("Неизвестный статус")
        if repeat_rule not in REPEAT_RULES:
            raise ValueError("Неизвестное правило повторения")
        due = parse_datetime(due_at)
        created = parse_datetime(created_at) if created_at is not None else _now()
        with self._lock:
            cursor = self._connection.execute(
                """
                INSERT INTO reminders (
                    title, description, due_at, status, created_at, repeat_rule
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    clean_title,
                    description.strip(),
                    format_datetime(due),
                    status,
                    format_datetime(created),
                    repeat_rule,
                ),
            )
            self._connection.commit()
            reminder_id = cursor.lastrowid
        reminder = self.get_reminder_by_id(reminder_id)
        if reminder is None:
            raise RuntimeError("Не удалось сохранить напоминание")
        return reminder

    def get_all_reminders(self) -> list[dict]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM reminders ORDER BY due_at, id"
            ).fetchall()
        return [_as_dict(row) for row in rows]

    def get_due_reminders(self, now: datetime | None = None) -> list[dict]:
        """Ожидающие, чьё время наступило и ещё не вышло больше чем на минуту."""
        moment = _now(now)
        earliest = format_datetime(moment - DUE_GRACE)
        latest = format_datetime(moment)
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM reminders
                WHERE status = ? AND due_at >= ? AND due_at <= ?
                ORDER BY due_at, id
                """,
                (STATUS_WAITING, earliest, latest),
            ).fetchall()
        return [_as_dict(row) for row in rows]

    def get_nearest_reminders(
        self,
        now: datetime | None = None,
        within: timedelta = NEAREST_WINDOW,
    ) -> list[dict]:
        """Ожидающие, которые сработают в ближайший час."""
        moment = _now(now)
        start = format_datetime(moment)
        end = format_datetime(moment + within)
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM reminders
                WHERE status = ? AND due_at >= ? AND due_at <= ?
                ORDER BY due_at, id
                """,
                (STATUS_WAITING, start, end),
            ).fetchall()
        return [_as_dict(row) for row in rows]

    def sort_by_due_time(self, reminders: list[dict] | None = None) -> list[dict]:
        items = self.get_all_reminders() if reminders is None else list(reminders)
        return sorted(items, key=lambda item: (item["due_at"], item["id"]))

    def update_status(self, reminder_id: int, status: str) -> bool:
        if status not in STATUSES:
            raise ValueError("Неизвестный статус")
        with self._lock:
            cursor = self._connection.execute(
                "UPDATE reminders SET status = ? WHERE id = ?",
                (status, reminder_id),
            )
            self._connection.commit()
            return cursor.rowcount > 0

    def delete_reminder(self, reminder_id: int) -> bool:
        with self._lock:
            cursor = self._connection.execute(
                "DELETE FROM reminders WHERE id = ?",
                (reminder_id,),
            )
            self._connection.commit()
            return cursor.rowcount > 0

    def mark_overdue(self, now: datetime | None = None) -> int:
        """Переводит в «Просрочено» ожидающие, у которых время вышло больше чем на минуту."""
        moment = _now(now)
        cutoff = format_datetime(moment - DUE_GRACE)
        with self._lock:
            cursor = self._connection.execute(
                """
                UPDATE reminders
                SET status = ?
                WHERE status = ? AND due_at < ?
                """,
                (STATUS_OVERDUE, STATUS_WAITING, cutoff),
            )
            self._connection.commit()
            return cursor.rowcount

    def get_reminder_by_id(self, reminder_id: int) -> dict | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM reminders WHERE id = ?",
                (reminder_id,),
            ).fetchone()
        return _as_dict(row) if row is not None else None

    def get_reminders_count(self) -> int:
        with self._lock:
            row = self._connection.execute(
                "SELECT COUNT(*) AS total FROM reminders"
            ).fetchone()
        return int(row["total"])

    def schedule_next(self, reminder_id: int, now: datetime | None = None) -> dict | None:
        """Создаёт следующее повторение. Для правила «без повтора» ничего не делает."""
        source = self.get_reminder_by_id(reminder_id)
        if source is None or source["repeat_rule"] == REPEAT_NONE:
            return None
        moment = _now(now)
        due = advance_due(parse_datetime(source["due_at"]), source["repeat_rule"])
        while due <= moment:
            due = advance_due(due, source["repeat_rule"])
        return self.add_reminder(
            title=source["title"],
            description=source["description"],
            due_at=due,
            repeat_rule=source["repeat_rule"],
            status=STATUS_WAITING,
        )


def _now(now: datetime | None = None) -> datetime:
    return (now or datetime.now()).replace(microsecond=0)


def _as_dict(row: sqlite3.Row) -> dict:
    return {key: row[key] for key in row.keys()}
