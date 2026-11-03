"""Comprehensive test suite for Smart Factory Maintenance Scheduler.

Verifies:
- Urgency status calculations and boundary conditions
- Strict ISO-8601 calendar date parsing
- Input validation and relational database constraints
- Atomic completion transactions and recurring cycle generation
- Rescheduling and demo data guards
- API endpoints and stats aggregation
"""

import sqlite3
import unittest
from datetime import date

from main import Store, parse_date, status_for


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.mid = self.store.add_machine("CNC", "Bay A", "High")

    def tearDown(self):
        self.store.close()

    def task(self, due="2026-10-01"):
        return self.store.add_task(self.mid, "Oil change", "Alex", due, 30, "High")

    def test_status_boundaries(self):
        today = date(2026, 10, 27)
        for due, expected in [
            ("2026-10-26", "Overdue"),
            ("2026-10-27", "Due today"),
            ("2026-10-28", "Due soon"),
            ("2026-11-03", "Due soon"),
            ("2026-11-04", "Scheduled"),
        ]:
            self.assertEqual(status_for(due, today), expected)

    def test_invalid_date(self):
        for value in ["2026-02-30", "27/10/2026", "20261027", "not-a-date"]:
            with self.assertRaises(ValueError):
                parse_date(value)

    def test_validation(self):
        with self.assertRaises(ValueError):
            self.store.add_machine("", "Bay", "High")
        for interval in [0, -1, "abc"]:
            with self.assertRaises(ValueError):
                self.store.add_task(self.mid, "Test", "Alex", "2026-10-27", interval, "Low")
        with self.assertRaises(ValueError):
            self.store.add_task(self.mid, "", "Alex", "2026-10-27", 30, "Low")
        with self.assertRaises(ValueError):
            self.store.add_task(self.mid, "Test", "", "2026-10-27", 30, "Low")

    def test_foreign_key(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.add_task(999, "Test", "Alex", "2026-10-27", 30, "Low")

    def test_complete_creates_next_cycle(self):
        tid = self.task()
        next_due = self.store.complete(tid, "Replaced oil", date(2026, 10, 27))
        self.assertEqual(next_due, "2026-11-26")
        self.assertEqual(len(self.store.tasks()), 1)
        self.assertEqual(len(self.store.tasks(True)), 1)
        self.assertEqual(self.store.tasks(True)[0]["notes"], "Replaced oil")
        self.assertEqual(self.store.tasks()[0]["due"], next_due)

    def test_double_completion_does_not_duplicate(self):
        tid = self.task()
        self.store.complete(tid)
        with self.assertRaises(ValueError):
            self.store.complete(tid)
        self.assertEqual(len(self.store.tasks()), 1)

    def test_reschedule(self):
        tid = self.task()
        self.store.reschedule(tid, "2026-12-01")
        self.assertEqual(self.store.tasks()[0]["due"], "2026-12-01")
        with self.assertRaises(ValueError):
            self.store.reschedule(999, "2026-12-01")

    def test_seed_guard(self):
        with self.assertRaises(ValueError):
            self.store.seed_demo()

    def test_demo(self):
        empty = Store(":memory:")
        try:
            empty.seed_demo()
            self.assertEqual(len(empty.machines()), 4)
            self.assertEqual(len(empty.tasks()), 4)
            self.assertEqual(
                {status_for(t["due"]) for t in empty.tasks()},
                {"Overdue", "Due today", "Due soon", "Scheduled"},
            )
        finally:
            empty.close()

    def test_same_due_date_priority_order(self):
        self.store.add_task(self.mid, "Low priority", "Alex", "2026-10-27", 30, "Low")
        self.store.add_task(self.mid, "High priority", "Alex", "2026-10-27", 30, "High")
        self.assertEqual(self.store.tasks()[0]["priority"], "High")

    def test_delete_machine_cascade(self):
        tid = self.task()
        self.assertTrue(self.store.delete_machine(self.mid))
        self.assertEqual(len(self.store.machines()), 0)
        self.assertEqual(len(self.store.tasks()), 0)

    def test_delete_task(self):
        tid = self.task()
        self.assertTrue(self.store.delete_task(tid))
        self.assertEqual(len(self.store.tasks()), 0)

    def test_stats_aggregation(self):
        self.task("2026-01-01")  # overdue
        stats = self.store.get_stats()
        self.assertEqual(stats["total_machines"], 1)
        self.assertEqual(stats["open_tasks"], 1)
        self.assertEqual(stats["high_criticality_machines"], 1)

    def test_export_csv(self):
        self.task()
        csv_tasks = self.store.export_csv("tasks")
        self.assertIn("CNC", csv_tasks)
        self.assertIn("Oil change", csv_tasks)
        csv_machines = self.store.export_csv("machines")
        self.assertIn("Bay A", csv_machines)


class APITests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from app.api import create_app

        self.app = create_app(":memory:")
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()
        self.app.state.store.close()

    def test_health_check(self):
        res = self.client.get("/api/health")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["status"], "healthy")

    def test_machine_and_task_lifecycle(self):
        # 1. Create machine
        m_res = self.client.post("/api/machines", json={"name": "Lathe", "location": "Bay C", "criticality": "Medium"})
        self.assertEqual(m_res.status_code, 201)
        mid = m_res.json()["id"]

        # 2. Create task
        t_res = self.client.post("/api/tasks", json={
            "machine_id": mid,
            "title": "Chuck alignment",
            "technician": "Chris",
            "due": "2026-11-15",
            "interval_days": 15,
            "priority": "High"
        })
        self.assertEqual(t_res.status_code, 201)
        tid = t_res.json()["id"]

        # 3. List tasks
        list_res = self.client.get("/api/tasks")
        self.assertEqual(len(list_res.json()), 1)

        # 4. Reschedule task
        reschedule_res = self.client.post(f"/api/tasks/{tid}/reschedule", json={"due": "2026-11-20"})
        self.assertEqual(reschedule_res.status_code, 200)

        # 5. Complete task
        comp_res = self.client.post(f"/api/tasks/{tid}/complete", json={"notes": "All calibrated"})
        self.assertEqual(comp_res.status_code, 200)

        # 6. Check history
        hist_res = self.client.get("/api/history")
        self.assertEqual(len(hist_res.json()), 1)
        self.assertEqual(hist_res.json()[0]["notes"], "All calibrated")


if __name__ == "__main__":
    unittest.main(verbosity=2)
