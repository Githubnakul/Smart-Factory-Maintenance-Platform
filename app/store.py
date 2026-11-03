"""SQLite storage engine and preventive maintenance business logic."""

from __future__ import annotations

import csv
import io
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def parse_date(value: str | date) -> date:
    """Accept an ISO calendar date (YYYY-MM-DD), rejecting invalid or noncanonical strings."""
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise ValueError("Use YYYY-MM-DD for dates.")
    stripped = value.strip()
    try:
        result = date.fromisoformat(stripped)
    except Exception as exc:
        raise ValueError("Use YYYY-MM-DD for dates.") from exc
    if result.isoformat() != stripped:
        raise ValueError("Use YYYY-MM-DD for dates.")
    return result


def status_for(due: str | date, today: Optional[date] = None) -> str:
    """Calculate maintenance urgency based on the difference between due date and reference date."""
    target_date = parse_date(due)
    ref_date = today or date.today()
    days = (target_date - ref_date).days
    if days < 0:
        return "Overdue"
    if days == 0:
        return "Due today"
    if days <= 7:
        return "Due soon"
    return "Scheduled"


class Store:
    """Database interface with transactional integrity for equipment and maintenance cycles."""

    def __init__(self, path: str | Path = ":memory:"):
        self.path = str(path)
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            self.db.execute("PRAGMA journal_mode = WAL")
        self._init_schema()

    def _init_schema(self) -> None:
        with self.db:
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS machines (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    location TEXT NOT NULL,
                    criticality TEXT NOT NULL CHECK(criticality IN ('Low', 'Medium', 'High'))
                );

                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    machine_id INTEGER NOT NULL REFERENCES machines(id) ON DELETE CASCADE,
                    title TEXT NOT NULL,
                    technician TEXT NOT NULL,
                    due TEXT NOT NULL,
                    interval_days INTEGER NOT NULL CHECK(interval_days > 0),
                    priority TEXT NOT NULL CHECK(priority IN ('Low', 'Medium', 'High')),
                    completed_on TEXT,
                    notes TEXT NOT NULL DEFAULT ''
                );

                CREATE INDEX IF NOT EXISTS idx_tasks_machine_id ON tasks(machine_id);
                CREATE INDEX IF NOT EXISTS idx_tasks_due ON tasks(due);
                CREATE INDEX IF NOT EXISTS idx_tasks_completed ON tasks(completed_on);
                """
            )

    def machines(self) -> List[sqlite3.Row]:
        """Fetch all registered machines sorted by ID."""
        return self.db.execute("SELECT * FROM machines ORDER BY id").fetchall()

    def get_machine(self, machine_id: int) -> Optional[sqlite3.Row]:
        """Fetch single machine by ID."""
        return self.db.execute("SELECT * FROM machines WHERE id = ?", (machine_id,)).fetchone()

    def add_machine(self, name: str, location: str, criticality: str) -> int:
        """Register a new machine after validating inputs."""
        clean_name = (name or "").strip()
        clean_loc = (location or "").strip()
        clean_crit = (criticality or "").strip()

        if not clean_name or not clean_loc:
            raise ValueError("Machine name and location are required.")
        if clean_crit not in ("Low", "Medium", "High"):
            raise ValueError("Choose a valid criticality.")

        with self.db:
            cursor = self.db.execute(
                "INSERT INTO machines(name, location, criticality) VALUES(?, ?, ?)",
                (clean_name, clean_loc, clean_crit),
            )
            return cursor.lastrowid

    def delete_machine(self, machine_id: int) -> bool:
        """Delete a machine and cascade its tasks."""
        with self.db:
            cursor = self.db.execute("DELETE FROM machines WHERE id = ?", (machine_id,))
            return cursor.rowcount > 0

    def add_task(
        self,
        machine_id: int,
        title: str,
        technician: str,
        due: str | date,
        interval: int | str,
        priority: str,
        notes: str = "",
    ) -> int:
        """Schedule a preventive maintenance task."""
        # Validate date
        due_date = parse_date(due).isoformat()

        # Validate interval
        try:
            interval_int = int(interval)
        except (ValueError, TypeError) as exc:
            raise ValueError("Service interval must be a positive number of days.") from exc

        if interval_int <= 0:
            raise ValueError("Service interval must be a positive number of days.")

        clean_title = (title or "").strip()
        clean_tech = (technician or "").strip()
        clean_priority = (priority or "").strip()
        clean_notes = (notes or "").strip()

        if not clean_title or not clean_tech:
            raise ValueError("Task description and technician are required.")
        if clean_priority not in ("Low", "Medium", "High"):
            raise ValueError("Choose a valid priority.")

        with self.db:
            cursor = self.db.execute(
                """
                INSERT INTO tasks (machine_id, title, technician, due, interval_days, priority, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    machine_id,
                    clean_title,
                    clean_tech,
                    due_date,
                    interval_int,
                    clean_priority,
                    clean_notes,
                ),
            )
            return cursor.lastrowid

    def tasks(self, completed: bool = False) -> List[sqlite3.Row]:
        """Fetch open or completed tasks ordered by due date, then priority, then ID."""
        condition = "IS NOT NULL" if completed else "IS NULL"
        return self.db.execute(
            f"""
            SELECT t.*, m.name AS machine, m.location, m.criticality AS machine_criticality
            FROM tasks t
            JOIN machines m ON m.id = t.machine_id
            WHERE t.completed_on {condition}
            ORDER BY t.due,
                     CASE t.priority WHEN 'High' THEN 0 WHEN 'Medium' THEN 1 ELSE 2 END,
                     t.id
            """
        ).fetchall()

    def get_task(self, task_id: int) -> Optional[sqlite3.Row]:
        """Fetch a specific task with machine details."""
        return self.db.execute(
            """
            SELECT t.*, m.name AS machine, m.location, m.criticality AS machine_criticality
            FROM tasks t
            JOIN machines m ON m.id = t.machine_id
            WHERE t.id = ?
            """,
            (task_id,),
        ).fetchone()

    def complete(self, task_id: int, notes: str = "", today: Optional[date] = None) -> str:
        """Mark an open task complete and atomically generate the next scheduled cycle."""
        completion_date = today or date.today()
        clean_notes = (notes or "").strip()

        with self.db:
            task = self.db.execute(
                "SELECT * FROM tasks WHERE id = ? AND completed_on IS NULL",
                (task_id,),
            ).fetchone()

            if task is None:
                raise ValueError("This task is missing or already completed.")

            # Record completion on current task
            self.db.execute(
                "UPDATE tasks SET completed_on = ?, notes = ? WHERE id = ?",
                (completion_date.isoformat(), clean_notes, task_id),
            )

            # Calculate and insert next recurring task
            next_due = (completion_date + timedelta(days=task["interval_days"])).isoformat()
            self.db.execute(
                """
                INSERT INTO tasks (machine_id, title, technician, due, interval_days, priority)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    task["machine_id"],
                    task["title"],
                    task["technician"],
                    next_due,
                    task["interval_days"],
                    task["priority"],
                ),
            )

        return next_due

    def reschedule(self, task_id: int, due: str | date) -> str:
        """Update due date for an open task."""
        valid_due = parse_date(due).isoformat()
        with self.db:
            cursor = self.db.execute(
                "UPDATE tasks SET due = ? WHERE id = ? AND completed_on IS NULL",
                (valid_due, task_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("Select an open maintenance task.")
        return valid_due

    def delete_task(self, task_id: int) -> bool:
        """Delete an open or completed task."""
        with self.db:
            cursor = self.db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            return cursor.rowcount > 0

    def seed_demo(self) -> None:
        """Populate realistic starter factory machines and tasks."""
        if self.machines():
            raise ValueError("Demo data can only be added when there are no machines.")

        examples = [
            ("CNC Milling Machine", "Production A", "High", "Lubricate spindle", -3, 30),
            ("Conveyor Belt", "Assembly", "Medium", "Inspect belt tension", 0, 14),
            ("Air Compressor", "Utilities", "High", "Inspect filter and oil", 4, 60),
            ("Packaging Unit", "Packaging", "Low", "Inspect safety guards", 18, 30),
        ]

        today = date.today()
        with self.db:
            for name, location, crit, title, offset, interval in examples:
                mid = self.db.execute(
                    "INSERT INTO machines(name, location, criticality) VALUES(?, ?, ?)",
                    (name, location, crit),
                ).lastrowid
                self.db.execute(
                    """
                    INSERT INTO tasks(machine_id, title, technician, due, interval_days, priority)
                    VALUES(?, ?, ?, ?, ?, ?)
                    """,
                    (
                        mid,
                        title,
                        "Demo technician",
                        (today + timedelta(days=offset)).isoformat(),
                        interval,
                        crit,
                    ),
                )

    def reset_database(self) -> None:
        """Clear all machines and tasks."""
        with self.db:
            self.db.execute("DELETE FROM tasks")
            self.db.execute("DELETE FROM machines")

    def get_stats(self) -> Dict[str, Any]:
        """Aggregate real-time metrics for dashboard cards."""
        machines = self.machines()
        open_tasks = self.tasks(completed=False)
        completed_tasks = self.tasks(completed=True)

        counts = {
            "total_machines": len(machines),
            "high_criticality_machines": sum(1 for m in machines if m["criticality"] == "High"),
            "open_tasks": len(open_tasks),
            "completed_tasks": len(completed_tasks),
            "overdue": 0,
            "due_today": 0,
            "due_soon": 0,
            "scheduled": 0,
            "high_priority_open": 0,
        }

        for t in open_tasks:
            state = status_for(t["due"])
            if state == "Overdue":
                counts["overdue"] += 1
            elif state == "Due today":
                counts["due_today"] += 1
            elif state == "Due soon":
                counts["due_soon"] += 1
            elif state == "Scheduled":
                counts["scheduled"] += 1

            if t["priority"] == "High":
                counts["high_priority_open"] += 1

        total_work = counts["open_tasks"] + counts["completed_tasks"]
        counts["completion_rate"] = (
            round((counts["completed_tasks"] / total_work) * 100, 1) if total_work > 0 else 0.0
        )

        return counts

    def export_csv(self, dataset: str = "tasks") -> str:
        """Export tasks, history, or machines as CSV string with spreadsheet formula escaping."""
        output = io.StringIO()
        writer = csv.writer(output)

        def sanitize(val: Any) -> str:
            text = str(val) if val is not None else ""
            if text.lstrip().startswith(("=", "+", "-", "@")):
                return "'" + text
            return text

        if dataset == "machines":
            writer.writerow(["ID", "Machine Name", "Location", "Criticality"])
            for m in self.machines():
                writer.writerow([sanitize(m["id"]), sanitize(m["name"]), sanitize(m["location"]), sanitize(m["criticality"])])
        elif dataset == "history":
            writer.writerow(["ID", "Machine", "Location", "Task", "Technician", "Due Date", "Completed On", "Notes"])
            for t in self.tasks(completed=True):
                writer.writerow([
                    sanitize(t["id"]),
                    sanitize(t["machine"]),
                    sanitize(t["location"]),
                    sanitize(t["title"]),
                    sanitize(t["technician"]),
                    sanitize(t["due"]),
                    sanitize(t["completed_on"]),
                    sanitize(t["notes"]),
                ])
        else:  # open tasks default
            writer.writerow(["ID", "Machine", "Location", "Task", "Technician", "Due Date", "Interval (Days)", "Priority", "Status"])
            for t in self.tasks(completed=False):
                writer.writerow([
                    sanitize(t["id"]),
                    sanitize(t["machine"]),
                    sanitize(t["location"]),
                    sanitize(t["title"]),
                    sanitize(t["technician"]),
                    sanitize(t["due"]),
                    sanitize(t["interval_days"]),
                    sanitize(t["priority"]),
                    sanitize(status_for(t["due"])),
                ])

        return output.getvalue()

    def close(self) -> None:
        """Close SQLite connection."""
        self.db.close()
