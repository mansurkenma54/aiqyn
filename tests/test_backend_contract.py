"""Backend contract regression tests (stdlib-only, no external test runner needed)."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from portal import app as api
from portal import db
from vision import categories
from vision.zones import Zone, _distance_to_polyline_m


class ZoneGeometryTests(unittest.TestCase):
    def test_model_confidence_does_not_change_preliminary_risk(self):
        expected = categories.get("pothole").base_severity
        self.assertEqual(categories.severity_for("pothole", 0.20, 0.001), expected)
        self.assertEqual(categories.severity_for("pothole", 0.99, 0.200), expected)

    def test_polyline_corridor_contains_nearby_point(self):
        points = [[42.3000, 69.5000], [42.3100, 69.5000]]
        self.assertLess(_distance_to_polyline_m(42.3050, 69.5001, points), 20)

        zone = Zone(
            id=1,
            name="Жол жұмысы",
            kind="repair",
            shape="polyline",
            polygon=points,
            corridor_m=20,
        )
        self.assertTrue(zone.contains(42.3050, 69.5001))
        self.assertFalse(zone.contains(42.3050, 69.5010))

    def test_date_deadline_is_inclusive(self):
        zone = Zone(
            id=1,
            name="Бүгінге дейін",
            kind="repair",
            shape="circle",
            lat=42.3,
            lon=69.5,
            valid_until=date.today().isoformat(),
        )
        self.assertFalse(zone.is_expired)


class DatabaseContractTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "aiqyn-test.db"
        self.db_patch = patch.object(db, "DB_PATH", self.db_path)
        self.db_patch.start()
        # init_db() бос дерекқорға демо snapshot-ты автоматты жүктейді
        # (portal/db.py::_load_demo_if_empty) — бұл Vercel-дегі суық старт
        # үшін керек. Тестке ол керек емес: бұл жерде дерекқор шынымен
        # бос болуы тиіс, әйтпесе аймақ тізімі демо деректерімен басталады.
        self.demo_patch = patch.object(
            db, "DEMO_SNAPSHOT", Path(self.tempdir.name) / "no-demo.json"
        )
        self.demo_patch.start()
        db.init_db()

    def tearDown(self):
        self.demo_patch.stop()
        self.db_patch.stop()
        self.tempdir.cleanup()

    def _insert_document(self, event_id: str = "AIQYN-TEST"):
        db.insert_document(
            {
                "event_id": event_id,
                "defect_type_official": "Шұңқыр",
                "address_text": "Бастапқы мекенжай",
                "lat": 42.3000,
                "lon": 69.5000,
                "severity": "medium",
                "confidence": 0.8,
            },
            None,
            None,
        )

    def test_polyline_zone_roundtrip_and_soft_delete(self):
        zone_id = db.insert_zone(
            {
                "name": "Сынақ учаскесі",
                "kind": "closed",
                "shape": "polyline",
                "points": [[42.30, 69.50], [42.31, 69.51]],
                "corridor_m": 18,
                "valid_until": "2099-12-31",
                "boundary_verified": True,
            },
            actor="tester",
        )
        zone = db.get_zone(zone_id)
        self.assertEqual(zone["shape"], "polyline")
        self.assertEqual(zone["points"], [[42.30, 69.50], [42.31, 69.51]])
        self.assertEqual(zone["corridor_m"], 18)
        self.assertTrue(zone["boundary_verified"])
        self.assertTrue(zone["effective_active"])

        replacement = [[42.30, 69.50], [42.32, 69.52], [42.33, 69.53]]
        self.assertTrue(db.update_zone(zone_id, {"points": replacement}, actor="tester"))
        self.assertEqual(db.get_zone(zone_id)["points"], replacement)

        self.assertTrue(db.delete_zone(zone_id, actor="tester"))
        archived = db.get_zone(zone_id)
        self.assertEqual(archived["active"], 0)
        self.assertIsNotNone(archived["deactivated_at"])
        self.assertEqual(len(db.list_zones()), 0)

    def test_zone_api_accepts_verified_official_fields(self):
        payload = api.ZonePayload.model_validate(
            {
                "name": "Расталған сынақ учаскесі",
                "kind": "repair",
                "shape": "polyline",
                "points": [[42.30, 69.50], [42.31, 69.51]],
                "corridor_m": 14,
                "external_ref": "TEST-2026/1",
                "source_url": "https://example.gov.kz/work/1",
                "responsible_org": "Сынақ басқармасы",
                "boundary_verified": True,
            }
        )
        result = api.create_zone(payload, operator="tester")
        self.assertTrue(result["ok"])
        self.assertTrue(result["zone"]["boundary_verified"])
        self.assertEqual(result["zone"]["external_ref"], "TEST-2026/1")

    def test_expired_zone_is_not_listed_or_counted(self):
        db.insert_zone(
            {
                "name": "Ескі жұмыс",
                "kind": "repair",
                "shape": "circle",
                "lat": 42.3,
                "lon": 69.5,
                "radius_m": 100,
                "valid_until": "2000-01-01",
            },
            actor="tester",
        )
        self.assertEqual(db.list_zones(), [])
        self.assertEqual(db.stats()["active_zones"], 0)

    def test_location_correction_preserves_original_and_audits(self):
        self._insert_document()
        self.assertTrue(
            db.correct_document_location(
                "AIQYN-TEST",
                42.3010,
                69.5020,
                "Түзетілген мекенжай",
                "GPS нүктесі жол шетіне түсті",
                "tester",
            )
        )
        document = db.get_document("AIQYN-TEST")
        self.assertEqual(document["original_lat"], 42.3000)
        self.assertEqual(document["display_lat"], 42.3010)
        self.assertEqual(document["lat"], 42.3010)
        self.assertTrue(document["location_corrected"])
        self.assertEqual(len(document["location_history"]), 1)
        self.assertTrue(document["is_draft"])

    def test_guarded_status_and_delivery_ticket(self):
        self._insert_document()
        with self.assertRaises(ValueError):
            db.set_status("AIQYN-TEST", "sent", actor="tester")

        self.assertTrue(db.set_status("AIQYN-TEST", "confirmed", "Тексерілді", "tester"))
        approved = db.get_document("AIQYN-TEST")
        self.assertTrue(approved["is_approved"])
        self.assertEqual(approved["approved_by"], "tester")

        delivery = {
            "ok": True,
            "ticket_source": "mock-109",
            "ticket_id": "MOCK-109-ABC123",
            "status": "accepted",
            "mock_109": True,
            "email": False,
        }
        self.assertTrue(db.record_delivery("AIQYN-TEST", delivery, "tester"))
        self.assertTrue(db.set_status("AIQYN-TEST", "sent", "Жіберілді", "tester"))
        sent = db.get_document("AIQYN-TEST")
        self.assertEqual(sent["external_ticket_id"], "MOCK-109-ABC123")
        self.assertEqual(len(sent["delivery_attempts"]), 1)

        self.assertTrue(db.set_status("AIQYN-TEST", "in_progress", "Жұмыс басталды", "tester"))
        self.assertTrue(db.set_status("AIQYN-TEST", "repaired", "After фото қабылданды", "tester"))
        self.assertEqual(db.allowed_transitions("repaired"), {"closed", "reopened"})
        self.assertTrue(db.set_status("AIQYN-TEST", "closed", "Қайта тексерілді", "tester"))
        self.assertEqual(db.get_document("AIQYN-TEST")["status"], "closed")

    def test_confirm_approves_sends_and_persists_ticket(self):
        self._insert_document()
        ticket_log = Path(self.tempdir.name) / "tickets.jsonl"
        with (
            patch.object(api, "TICKETS_LOG", ticket_log),
            patch.object(api, "SMTP_HOST", ""),
            patch.object(api, "SMTP_USER", ""),
        ):
            result = api.confirm_document("AIQYN-TEST", operator="tester")

        self.assertEqual(result["status"], "sent")
        self.assertTrue(result["delivery"]["ticket_id"].startswith("MOCK-109-"))
        document = db.get_document("AIQYN-TEST")
        self.assertEqual(document["status"], "sent")
        self.assertEqual(document["external_ticket_id"], result["delivery"]["ticket_id"])
        self.assertEqual(document["approved_by"], "tester")


if __name__ == "__main__":
    unittest.main()
