"""Графический интерфейс напоминалки."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import tkinter as tk
from tkinter import messagebox, ttk

from database import (
    REPEAT_LABELS,
    REPEAT_NONE,
    REPEAT_RULES,
    STATUS_CANCELLED,
    STATUS_DONE,
    STATUS_OVERDUE,
    STATUS_WAITING,
    ReminderDatabase,
    parse_datetime,
)
from notifications import APP_NAME, NotificationManager

FILTER_ALL = "Все"
FILTER_NEAREST = "Ближайшие"
FILTERS = (
    FILTER_ALL,
    STATUS_WAITING,
    FILTER_NEAREST,
    STATUS_OVERDUE,
    STATUS_DONE,
    STATUS_CANCELLED,
)
QUICK_TIMES = (("5 мин", 5), ("15 мин", 15), ("30 мин", 30), ("1 час", 60))


class ReminderApp:
    def __init__(
        self,
        db_path: str | Path | None = None,
        *,
        database: ReminderDatabase | None = None,
        notifications: NotificationManager | None = None,
        start_notifications: bool = True,
    ) -> None:
        self.database = database if database is not None else ReminderDatabase(db_path)
        self.root = tk.Tk()
        if notifications is None:
            self.notifications = NotificationManager(self.database, self.root)
        else:
            self.notifications = notifications
            self.notifications.database = self.database
            self.notifications.set_root(self.root)
        self.filter_var = tk.StringVar(value=FILTER_ALL)
        self.title_var = tk.StringVar()
        self.date_var = tk.StringVar()
        self.time_var = tk.StringVar()
        self.repeat_var = tk.StringVar(value=REPEAT_LABELS[REPEAT_NONE])
        self._status_var = tk.StringVar()
        self._signature: tuple | None = None
        self._repeat_by_label = {label: code for code, label in REPEAT_LABELS.items()}
        self.setup_ui()
        self.set_quick_time(15)
        self.refresh_reminders()
        if start_notifications:
            self.notifications.start()
        self._schedule_refresh()

    def setup_ui(self) -> None:
        self.root.title(APP_NAME)
        self.root.geometry("920x640")
        self.root.minsize(780, 520)
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("Treeview", rowheight=26)
        style.configure("Treeview.Heading", font=("", 10, "bold"))
        style.configure("Status.TLabel", padding=(4, 6))

        form = ttk.LabelFrame(self.root, text="Новое напоминание", padding=12)
        form.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 6))
        form.columnconfigure(1, weight=1)

        ttk.Label(form, text="Название").grid(row=0, column=0, sticky="w")
        self.title_entry = ttk.Entry(form, textvariable=self.title_var)
        self.title_entry.grid(row=0, column=1, sticky="ew", padx=(8, 8))
        self.title_entry.bind("<Return>", lambda _event: self.add_reminder())
        ttk.Button(form, text="Добавить", command=self.add_reminder).grid(row=0, column=2)
        ttk.Button(form, text="Тест уведомления", command=self.test_notification).grid(
            row=0, column=3, padx=(8, 0)
        )

        ttk.Label(form, text="Описание").grid(row=1, column=0, sticky="nw", pady=(8, 0))
        self.description = tk.Text(form, height=2, wrap="word", font=("", 10))
        self.description.grid(row=1, column=1, columnspan=3, sticky="ew", padx=(8, 0), pady=(8, 0))

        ttk.Label(form, text="Дата").grid(row=2, column=0, sticky="w", pady=(8, 0))
        when = ttk.Frame(form)
        when.grid(row=2, column=1, columnspan=3, sticky="ew", padx=(8, 0), pady=(8, 0))
        ttk.Entry(when, textvariable=self.date_var, width=12).pack(side="left")
        ttk.Label(when, text="Время").pack(side="left", padx=(12, 8))
        ttk.Entry(when, textvariable=self.time_var, width=8).pack(side="left")
        for label, minutes in QUICK_TIMES:
            ttk.Button(
                when,
                text=label,
                command=lambda amount=minutes: self.set_quick_time(amount),
            ).pack(side="left", padx=(8, 0))

        ttk.Label(form, text="Повтор").grid(row=3, column=0, sticky="w", pady=(8, 0))
        ttk.Combobox(
            form,
            textvariable=self.repeat_var,
            values=[REPEAT_LABELS[rule] for rule in REPEAT_RULES],
            state="readonly",
            width=22,
        ).grid(row=3, column=1, sticky="w", padx=(8, 0), pady=(8, 0))

        listing = ttk.LabelFrame(self.root, text="Напоминания", padding=12)
        listing.grid(row=1, column=0, sticky="nsew", padx=12, pady=6)
        listing.columnconfigure(0, weight=1)
        listing.rowconfigure(1, weight=1)

        filters = ttk.Frame(listing)
        filters.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        for name in FILTERS:
            ttk.Radiobutton(
                filters,
                text=name,
                value=name,
                variable=self.filter_var,
                command=self._on_filter,
            ).pack(side="left", padx=(0, 10))

        columns = ("title", "description", "due_at", "status", "repeat")
        self.tree = ttk.Treeview(listing, columns=columns, show="headings", selectmode="browse")
        self.tree.heading("title", text="Название")
        self.tree.heading("description", text="Описание")
        self.tree.heading("due_at", text="Когда")
        self.tree.heading("status", text="Статус")
        self.tree.heading("repeat", text="Повтор")
        self.tree.column("title", width=180, anchor="w")
        self.tree.column("description", width=240, anchor="w")
        self.tree.column("due_at", width=150, anchor="w")
        self.tree.column("status", width=110, anchor="w")
        self.tree.column("repeat", width=140, anchor="w")
        self.tree.tag_configure("waiting", foreground="#1d3557")
        self.tree.tag_configure("overdue", foreground="#9b2335")
        self.tree.tag_configure("done", foreground="#5c6570")
        self.tree.tag_configure("cancelled", foreground="#8a8175")
        self.tree.bind("<Double-1>", self.on_double_click)
        self.tree.bind("<Return>", self.on_double_click)
        self.tree.grid(row=1, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(listing, orient="vertical", command=self.tree.yview)
        scroll.grid(row=1, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)

        actions = ttk.Frame(self.root, padding=(12, 0))
        actions.grid(row=2, column=0, sticky="ew")
        ttk.Button(actions, text="Готово", command=self.mark_as_done).pack(side="left")
        ttk.Button(actions, text="Отменить", command=self.mark_as_cancelled).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Удалить", command=self.delete_reminder).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Обновить", command=self.refresh_reminders).pack(side="left", padx=(8, 0))

        ttk.Label(
            self.root,
            textvariable=self._status_var,
            style="Status.TLabel",
            anchor="w",
        ).grid(row=3, column=0, sticky="ew", padx=12, pady=(6, 10))
        self.title_entry.focus_set()

    def update_status_bar(self) -> None:
        total = self.database.get_reminders_count()
        visible = len(self.tree.get_children())
        self._status_var.set(f"Всего напоминаний: {total} · в списке: {visible}")

    def test_notification(self) -> None:
        if self.notifications.test_notification():
            return
        messagebox.showwarning(
            "Уведомление",
            "Не удалось показать уведомление.",
            parent=self.root,
        )

    def set_quick_time(self, minutes: int) -> None:
        moment = datetime.now() + timedelta(minutes=minutes)
        moment = moment.replace(second=0, microsecond=0)
        self.date_var.set(moment.strftime("%Y-%m-%d"))
        self.time_var.set(moment.strftime("%H:%M"))

    def add_reminder(self) -> None:
        title = self.title_var.get().strip()
        description = self.description.get("1.0", "end").strip()
        repeat_rule = self._repeat_by_label.get(self.repeat_var.get(), "")
        try:
            if repeat_rule not in REPEAT_RULES:
                raise ValueError("Неизвестное правило повторения")
            due_at = parse_datetime(f"{self.date_var.get().strip()} {self.time_var.get().strip()}")
            self.database.add_reminder(title, description, due_at, repeat_rule)
        except ValueError as exc:
            messagebox.showerror("Напоминание", str(exc), parent=self.root)
            return
        self.title_var.set("")
        self.description.delete("1.0", "end")
        self.title_entry.focus_set()
        self._signature = None
        self.refresh_reminders()

    def refresh_reminders(self) -> None:
        reminders = self._visible_reminders()
        signature = tuple(
            (item["id"], item["title"], item["description"], item["due_at"], item["status"], item["repeat_rule"])
            for item in reminders
        )
        if signature == self._signature:
            self.update_status_bar()
            return
        self._signature = signature
        selected = self.tree.selection()
        selected_id = selected[0] if selected else ""
        children = self.tree.get_children()
        if children:
            self.tree.delete(*children)
        for item in reminders:
            self.tree.insert(
                "",
                "end",
                iid=str(item["id"]),
                values=(
                    item["title"],
                    _short(item["description"]),
                    item["due_at"],
                    item["status"],
                    REPEAT_LABELS.get(item["repeat_rule"], item["repeat_rule"]),
                ),
                tags=(_status_tag(item["status"]),),
            )
        if selected_id and self.tree.exists(selected_id):
            self.tree.selection_set(selected_id)
            self.tree.focus(selected_id)
        self.update_status_bar()

    def mark_as_done(self) -> None:
        reminder = self._require_selection()
        if reminder is None or reminder["status"] == STATUS_DONE:
            return
        previous = reminder["status"]
        self.database.update_status(reminder["id"], STATUS_DONE)
        if previous in (STATUS_WAITING, STATUS_OVERDUE):
            self.database.schedule_next(reminder["id"])
        self._signature = None
        self.refresh_reminders()

    def mark_as_cancelled(self) -> None:
        reminder = self._require_selection()
        if reminder is None or reminder["status"] == STATUS_CANCELLED:
            return
        self.database.update_status(reminder["id"], STATUS_CANCELLED)
        self._signature = None
        self.refresh_reminders()

    def delete_reminder(self) -> None:
        reminder = self._require_selection()
        if reminder is None:
            return
        self.database.delete_reminder(reminder["id"])
        self._signature = None
        self.refresh_reminders()

    def on_double_click(self, _event=None) -> None:
        reminder = self._selected_reminder()
        if reminder is None:
            return
        window = tk.Toplevel(self.root)
        window.title("Напоминание")
        window.transient(self.root)
        window.resizable(False, False)
        frame = ttk.Frame(window, padding=16)
        frame.pack(fill="both", expand=True)
        rows = (
            ("Название", reminder["title"]),
            ("Описание", reminder["description"] or "—"),
            ("Когда", reminder["due_at"]),
            ("Статус", reminder["status"]),
            ("Повтор", REPEAT_LABELS.get(reminder["repeat_rule"], reminder["repeat_rule"])),
            ("Создано", reminder["created_at"]),
        )
        for row, (label, value) in enumerate(rows):
            ttk.Label(frame, text=label, font=("", 10, "bold")).grid(row=row, column=0, sticky="nw", pady=2)
            ttk.Label(frame, text=value, wraplength=360, justify="left").grid(
                row=row, column=1, sticky="w", padx=(12, 0), pady=2
            )
        ttk.Button(frame, text="Закрыть", command=window.destroy).grid(
            row=len(rows), column=1, sticky="e", pady=(12, 0)
        )
        window.bind("<Escape>", lambda _event: window.destroy())
        window.update_idletasks()
        window.geometry(f"+{self.root.winfo_rootx() + 80}+{self.root.winfo_rooty() + 80}")
        window.focus_force()

    def on_closing(self) -> None:
        if not messagebox.askyesno("Выход", "Вы уверены, что хотите выйти?", parent=self.root):
            return
        self.notifications.stop()
        self.database.close()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()

    def _on_filter(self) -> None:
        self._signature = None
        self.refresh_reminders()

    def _schedule_refresh(self) -> None:
        try:
            if not self.root.winfo_exists():
                return
            self.refresh_reminders()
            self.root.after(1000, self._schedule_refresh)
        except tk.TclError:
            return

    def _visible_reminders(self) -> list[dict]:
        choice = self.filter_var.get()
        if choice == FILTER_NEAREST:
            return self.database.get_nearest_reminders()
        reminders = self.database.sort_by_due_time()
        if choice == FILTER_ALL:
            return reminders
        return [item for item in reminders if item["status"] == choice]

    def _selected_reminder(self) -> dict | None:
        selection = self.tree.selection()
        if not selection:
            return None
        return self.database.get_reminder_by_id(int(selection[0]))

    def _require_selection(self) -> dict | None:
        reminder = self._selected_reminder()
        if reminder is None:
            messagebox.showinfo("Напоминание", "Сначала выберите напоминание в списке.", parent=self.root)
        return reminder


def _short(text: str, limit: int = 48) -> str:
    compact = " ".join((text or "").split())
    if not compact:
        return "—"
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"


def _status_tag(status: str) -> str:
    return {
        STATUS_WAITING: "waiting",
        STATUS_OVERDUE: "overdue",
        STATUS_DONE: "done",
        STATUS_CANCELLED: "cancelled",
    }.get(status, "waiting")


if __name__ == "__main__":
    ReminderApp().run()
