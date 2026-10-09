import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from route_dashboard.history import (
    DisabledHindsightProvider,
    HistoryStorageError,
    SQLiteOperationHistory,
    build_operation_record,
)
from route_dashboard.optimization import (
    OptimizationRun,
    RouteSummary,
    validate_route_set,
)
from route_dashboard.scenario import build_demo_scenario
from quantum_route_optimisation import optimize_classically


def _record(
    run_id: str,
    *,
    created_at: str = "2026-10-09T10:00:00+00:00",
    origin: str = "Depot A",
    destination: str = "North Market",
    status: str = "VALIDATED",
) -> dict[str, object]:
    return {
        "run_id": run_id,
        "parent_run_id": None,
        "created_at": created_at,
        "completed_at": created_at,
        "status": status,
        "operation_type": "TEST_FIXTURE",
        "origin": origin,
        "destination": destination,
        "traffic_level": "Moderate",
        "traffic_source": "SIMULATED",
        "objective": "Cost Priority",
        "inputs": {
            "origin": {"name": origin, "latitude": 37.0, "longitude": -122.0},
            "access_key": "fixture-key-must-not-be-stored",
            "nested": {"password": "fixture-password-must-not-be-stored"},
        },
        "outputs": {
            "feasibility": {"status": status},
            "solver_results": {
                "classical": {
                    "status": "VALIDATED",
                    "metrics": {"distance_km": 12.5},
                    "routes": [],
                },
                "qaoa_aer": {"status": "NOT_AVAILABLE"},
            },
            "ibm_hardware": {
                "status": "NOT_LINKED",
                "job_id": None,
                "backend": None,
                "shots": None,
                "verified_evidence_reference": None,
            },
        },
        "reoptimization": None,
        "source_metadata": {"project": "QuantumRoute", "source_revision": None},
    }


class OperationHistoryStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "history.sqlite3"
        self.history = SQLiteOperationHistory(self.database_path)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_schema_creation_and_upgrade_from_version_one(self) -> None:
        with closing(sqlite3.connect(self.database_path)) as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual(version, 2)
        self.assertEqual(self.history.list_operations(), ())

        older_path = Path(self.temporary_directory.name) / "v1.sqlite3"
        with closing(sqlite3.connect(older_path)) as connection:
            connection.execute(
                """
                CREATE TABLE operations (
                    run_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    origin TEXT NOT NULL,
                    destination TEXT NOT NULL,
                    traffic_level TEXT NOT NULL,
                    traffic_source TEXT NOT NULL,
                    objective TEXT NOT NULL,
                    inputs_json TEXT NOT NULL,
                    outputs_json TEXT NOT NULL
                )
                """
            )
            connection.execute("PRAGMA user_version = 1")
            connection.commit()
        upgraded = SQLiteOperationHistory(older_path)
        with closing(sqlite3.connect(older_path)) as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(operations)")
            }
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        self.assertEqual(version, 2)
        self.assertIn("operation_type", columns)
        self.assertIn("memory_outbox", tables)
        self.assertEqual(upgraded.list_operations(), ())

    def test_completed_run_round_trips_and_is_idempotent(self) -> None:
        record = _record("run-1")
        self.assertTrue(self.history.save_completed_operation(record))
        self.assertFalse(self.history.save_completed_operation(record))
        loaded = self.history.get_operation("run-1")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["inputs"]["origin"], record["inputs"]["origin"])
        self.assertEqual(loaded["outputs"], record["outputs"])
        self.assertEqual(self.history.count_operations(), 1)

    def test_existing_run_id_cannot_overwrite_different_data(self) -> None:
        self.history.save_completed_operation(_record("immutable-run"))
        conflicting = _record("immutable-run", origin="Different Depot")
        with self.assertRaisesRegex(HistoryStorageError, "different operation data"):
            self.history.save_completed_operation(conflicting)
        self.assertEqual(
            self.history.get_operation("immutable-run")["origin"],
            "Depot A",
        )

    def test_records_survive_connection_close_and_reopen(self) -> None:
        self.history.save_completed_operation(_record("persistent-run"))
        reopened = SQLiteOperationHistory(self.database_path)
        self.assertEqual(
            reopened.get_operation("persistent-run")["run_id"],
            "persistent-run",
        )

    def test_filters_and_pagination_are_bounded(self) -> None:
        self.history.save_completed_operation(
            _record("run-a", created_at="2026-10-08T10:00:00+00:00")
        )
        self.history.save_completed_operation(
            _record(
                "run-b",
                created_at="2026-10-09T10:00:00+00:00",
                origin="Depot B",
                destination="Harbor Point",
                status="PARTIAL",
            )
        )
        self.history.save_completed_operation(
            _record(
                "run-c",
                created_at="2026-10-09T12:00:00+00:00",
                origin="Depot C",
            )
        )
        page = self.history.list_operations(limit=1, offset=1)
        self.assertEqual(len(page), 1)
        self.assertEqual(page[0]["run_id"], "run-b")
        filtered = self.history.list_operations(
            search="harbor",
            status="PARTIAL",
            date_from="2026-10-09",
            date_to="2026-10-09",
        )
        self.assertEqual([item["run_id"] for item in filtered], ["run-b"])
        self.assertEqual(
            self.history.count_operations(
                search="harbor", status="PARTIAL", date_from="2026-10-09"
            ),
            1,
        )
        with self.assertRaises(ValueError):
            self.history.list_operations(limit=101)

    def test_sensitive_fields_are_removed_and_outbox_is_disabled_by_default(self) -> None:
        self.history.save_completed_operation(_record("private-run"))
        stored = json.dumps(self.history.get_operation("private-run"))
        self.assertNotIn("fixture-key", stored)
        self.assertNotIn("fixture-password", stored)
        self.assertEqual(self.history.recall_summaries("similar route"), ())
        self.assertEqual(self.history.pending_memory_summaries(), ())

        provider = DisabledHindsightProvider()
        self.assertFalse(provider.enabled)
        provider.publish("private-run", {"summary": "offline"})
        self.assertEqual(provider.recall("anything"), ())

    def test_outbox_supports_explicit_retry_without_external_calls(self) -> None:
        self.history.save_completed_operation(_record("outbox-run"))
        self.history.queue_memory_summary("outbox-run")
        pending = self.history.pending_memory_summaries()
        self.assertEqual(pending[0]["run_id"], "outbox-run")
        self.history.mark_memory_delivery(
            "outbox-run", delivered=False, error="provider unavailable"
        )
        retry = self.history.pending_memory_summaries()
        self.assertEqual(retry[0]["attempts"], 1)

    def test_missing_required_provenance_fails_explicitly(self) -> None:
        incomplete = _record("incomplete")
        del incomplete["inputs"]
        with self.assertRaisesRegex(HistoryStorageError, "missing required fields"):
            self.history.save_completed_operation(incomplete)

    def test_storage_failure_is_reported(self) -> None:
        with patch.object(
            self.history,
            "_connect",
            side_effect=sqlite3.OperationalError("database unavailable"),
        ):
            with self.assertRaisesRegex(HistoryStorageError, "Could not save"):
                self.history.save_completed_operation(_record("failed-save"))

    def test_recalled_summary_must_resolve_to_sqlite_for_exact_details(self) -> None:
        self.history.save_completed_operation(_record("authoritative-run"))
        recalled = {"record_id": "authoritative-run", "metrics": {"distance_km": 999}}
        authoritative = self.history.resolve_memory_reference(recalled)
        self.assertEqual(
            authoritative["outputs"]["solver_results"]["classical"]["metrics"],
            {"distance_km": 12.5},
        )
        self.assertIsNone(
            self.history.resolve_memory_reference({"record_id": "unknown-run"})
        )

    def test_snapshot_uses_the_completed_scenario_and_solver_outputs(self) -> None:
        scenario = build_demo_scenario()
        result = optimize_classically(
            scenario.vehicles,
            scenario.deliveries,
            scenario.travel,
            scenario.cost_weights,
        )
        routes = validate_route_set(result.routes, scenario)
        summary = RouteSummary(
            routes=routes,
            total_cost=sum(route.total_cost for route in routes),
            total_distance_km=sum(route.distance_km for route in routes),
            total_travel_time_min=sum(route.travel_time_min for route in routes),
        )
        run = OptimizationRun(
            classical=summary,
            quantum=None,
            quantum_result=None,
            quantum_error="QAOA not run in this storage fixture.",
            objective_delta=None,
            relative_gap_percent=None,
        )
        record = build_operation_record(
            scenario,
            run,
            run_id="snapshot-run",
            operation_type="TEST_FIXTURE",
        )
        self.assertEqual(record["inputs"]["origin"]["name"], "Mission Depot")
        self.assertEqual(
            record["inputs"]["destinations"][0]["quantity"],
            scenario.deliveries[0].demand,
        )
        self.assertEqual(
            record["outputs"]["solver_results"]["classical"]["metrics"]["distance_km"],
            summary.total_distance_km,
        )
        self.assertEqual(
            record["outputs"]["solver_results"]["qaoa_aer"]["status"],
            "NOT_AVAILABLE",
        )


if __name__ == "__main__":
    unittest.main()
