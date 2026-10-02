"""Точка входа в напоминалку."""

from database import ReminderDatabase
from gui import ReminderApp
from notifications import NotificationManager


def main() -> None:
    database = ReminderDatabase()
    notifications = NotificationManager(database)
    app = ReminderApp(database=database, notifications=notifications)
    app.run()


if __name__ == "__main__":
    main()
