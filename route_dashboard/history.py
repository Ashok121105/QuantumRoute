"""Local, versioned persistence for completed QuantumRoute operations."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import re
import sqlite3
from typing import Iterator, Mapping, Protocol
from uuid import uuid4

from .dynamic import calculate_route_impact
from .optimization import OptimizationRun, RouteSummary
from .scenario import FUEL_OPTIONS, ScenarioProblem


HISTORY_SCHEMA_VERSION = 2
_SENSITIVE_KEY = re.compile(
    r"password|passwd|secret|token|credential|api[_-]?key|access[_-]?key",
    re.IGNORECASE,
)


class HistoryStorageError(RuntimeError):
    """Raised when operation history cannot be durably saved or read."""


class MemoryProvider(Protocol):
    """Integration boundary for optional semantic-memory providers."""

    @property
    def enabled(self) -> bool: ...

    def publish(self, record_id: str, summary: Mapping[str, object]) -> None: ...

    def recall(self, query: str, limit: int = 5) -> tuple[Mapping[str, object], ...]: ...


class DisabledHindsightProvider:
    """No-network default until an explicitly configured Hindsight adapter exists."""

    @property
    def enabled(self) -> bool:
        return False

    def publish(self, record_id: str, summary: Mapping[str, object]) -> None:
        return None

    def recall(self, query: str, limit: int = 5) -> tuple[Mapping[str, object], ...]:
        return ()


def default_database_path() -> Path:
    """Keep private operational history outside the source checkout."""
    if os.name == "nt":
        base = Path(
            os.environ.get("LOCALAPPDATA")
            or Path.home() / "AppData" / "Local"
        )
    else:
        base = Path(
            os.environ.get("XDG_DATA_HOME")
            or Path.home() / ".local" / "share"
        )
    return base / "QuantumRoute" / "quantumroute_history.sqlite3"


def new_run_id() -> str:
    return str(uuid4())


def build_operation_record(
    scenario: ScenarioProblem,
    run: OptimizationRun,
    *,
    run_id: str | None = None,
    parent_run_id: str | None = None,
    operation_type: str = "OPTIMIZATION",
    feasibility_status: str = "VALIDATED",
    failure_details: str | None = None,
    reoptimization: Mapping[str, object] | None = None,
    extra_inputs: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Snapshot recorded scenario inputs and actual solver outputs without rerunning."""
    identifier = run_id or new_run_id()
    inputs: dict[str, object] = {
        "origin": {
            "name": scenario.location_names["depot"],
            "latitude": scenario.coordinates["depot"][0],
            "longitude": scenario.coordinates["depot"][1],
        },
        "destinations": [
            {
                "delivery_id": delivery.delivery_id,
                "name": scenario.location_names[delivery.delivery_id],
                "latitude": scenario.coordinates[delivery.location][0],
                "longitude": scenario.coordinates[delivery.location][1],
                "quantity": delivery.demand,
                "time_window_start_min": delivery.window_start_min,
                "time_window_end_min": delivery.window_end_min,
                "service_duration_min": delivery.service_duration_min,
            }
            for delivery in scenario.deliveries
        ],
        "vehicles": [
            {
                "vehicle_id": vehicle.vehicle_id,
                "capacity": vehicle.capacity,
                "start_location": vehicle.start_location,
                "end_location": vehicle.end_location,
                "shift_start_min": vehicle.shift_start_min,
                "shift_end_min": vehicle.shift_end_min,
            }
            for vehicle in scenario.vehicles
        ],
        "constraints": {
            "vehicle_capacity_per_vehicle": [
                {"vehicle_id": vehicle.vehicle_id, "capacity": vehicle.capacity}
                for vehicle in scenario.vehicles
            ],
            "delivery_time_windows": True,
            "vehicle_shift_windows": True,
            "each_selected_delivery_served_at_most_once": True,
        },
        "traffic": {
            "level": scenario.traffic_condition,
            "source": "SIMULATED",
            "travel_data_source": scenario.data_source,
        },
        "objective": run.objective_name,
        "fuel": {
            "type": scenario.fuel_type,
            "price_per_unit_cost_units": scenario.fuel_price_per_unit,
            "unit": scenario.fuel_unit,
            "consumption_per_km": FUEL_OPTIONS[scenario.fuel_type][
                "consumption_per_km"
            ],
            "tailpipe_co2_kg_per_unit": FUEL_OPTIONS[scenario.fuel_type][
                "tailpipe_co2_kg_per_unit"
            ],
            "driver_cost_per_hour_cost_units": (
                scenario.cost_weights.time_cost_per_min * 60
            ),
            "cost_weights": {
                "distance_cost_per_km": scenario.cost_weights.distance_cost_per_km,
                "time_cost_per_min": scenario.cost_weights.time_cost_per_min,
                "fixed_vehicle_cost": scenario.cost_weights.fixed_vehicle_cost,
            },
        },
    }
    if extra_inputs:
        inputs["operation_details"] = dict(extra_inputs)

    classical = _solver_snapshot(scenario, run.classical, "CLASSICAL")
    qaoa = (
        _solver_snapshot(scenario, run.quantum, "QAOA_AER")
        if run.quantum is not None
        else {
            "method": "QAOA_AER",
            "status": "NOT_AVAILABLE",
            "failure_details": run.quantum_error,
        }
    )
    classical["objective_value"] = run.classical_objective_value
    qaoa["objective_value"] = run.quantum_objective_value
    output: dict[str, object] = {
        "feasibility": {
            "status": feasibility_status,
            "failure_details": failure_details,
        },
        "solver_results": {"classical": classical, "qaoa_aer": qaoa},
        "default_display_solver": "classical",
        "recommendation_status": "NOT_RECORDED",
        "optimization_comparison": {
            "objective_delta": run.objective_delta,
            "relative_gap_percent": run.relative_gap_percent,
        },
        "ibm_hardware": {
            "status": "NOT_LINKED",
            "job_id": None,
            "backend": None,
            "shots": None,
            "verified_evidence_reference": None,
        },
    }
    timestamp = _now()
    return {
        "run_id": identifier,
        "parent_run_id": parent_run_id,
        "created_at": timestamp,
        "completed_at": timestamp,
        "status": feasibility_status,
        "operation_type": operation_type,
        "origin": scenario.location_names["depot"],
        "destination": ", ".join(
            scenario.location_names[delivery.delivery_id]
            for delivery in scenario.deliveries
        ),
        "traffic_level": scenario.traffic_condition,
        "traffic_source": "SIMULATED",
        "objective": run.objective_name,
        "inputs": inputs,
        "outputs": output,
        "reoptimization": dict(reoptimization) if reoptimization is not None else None,
        "source_metadata": _source_metadata(),
    }


def _solver_snapshot(
    scenario: ScenarioProblem,
    summary: RouteSummary,
    method: str,
) -> dict[str, object]:
    impact = calculate_route_impact(scenario, summary)
    routes = [
        {
            "vehicle_id": route.plan.vehicle_id,
            "delivery_ids": list(route.plan.delivery_ids),
            "delivery_names": [
                scenario.location_names[delivery_id]
                for delivery_id in route.plan.delivery_ids
            ],
            "distance_km": route.distance_km,
            "travel_time_min": route.travel_time_min,
            "cost": route.total_cost,
        }
        for route in summary.routes
    ]
    return {
        "method": method,
        "status": "VALIDATED",
        "routes": routes,
        "metrics": {
            "distance_km": impact.distance_km,
            "estimated_travel_time_min": impact.travel_time_min,
            "estimated_fuel": impact.fuel_used,
            "fuel_unit": impact.fuel_unit,
            "estimated_cost": impact.cost,
            "estimated_tailpipe_co2_kg": impact.tailpipe_co2_kg,
        },
    }


def _source_metadata() -> dict[str, object]:
    versions: dict[str, str] = {}
    for package in ("streamlit", "qiskit", "qiskit-aer", "qiskit-optimization"):
        try:
            versions[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            continue
    return {
        "project": "QuantumRoute",
        "project_version": "working-tree",
        "source_revision": None,
        "python_version": platform.python_version(),
        "package_versions": versions,
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json_safe(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _json_safe(item)
            for key, item in value.items()
            if not _SENSITIVE_KEY.search(str(key))
        }
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"History values must be JSON-compatible, got {type(value).__name__}.")


class SQLiteOperationHistory:
    """Short-lived SQLite connections with idempotent writes and bounded reads."""

    def __init__(self, database_path: str | Path | None = None) -> None:
        self.database_path = Path(database_path) if database_path else default_database_path()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._migrate()
        except sqlite3.Error as error:
            raise HistoryStorageError(f"Could not initialize operation history: {error}") from error

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=5.0)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.execute("PRAGMA journal_mode = WAL")
        except Exception:
            connection.close()
            raise
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _migrate(self) -> None:
        with self._connection() as connection:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version > HISTORY_SCHEMA_VERSION:
                raise HistoryStorageError(
                    f"History database schema {version} is newer than supported "
                    f"schema {HISTORY_SCHEMA_VERSION}."
                )
            if version < 1:
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
                connection.execute("CREATE INDEX idx_operations_created ON operations(created_at DESC)")
                connection.execute("CREATE INDEX idx_operations_origin ON operations(origin)")
                connection.execute("CREATE INDEX idx_operations_destination ON operations(destination)")
                connection.execute("CREATE INDEX idx_operations_status ON operations(status)")
                connection.execute("PRAGMA user_version = 1")
                version = 1
            if version < 2:
                connection.execute(
                    "ALTER TABLE operations ADD COLUMN parent_run_id TEXT"
                )
                connection.execute(
                    "ALTER TABLE operations ADD COLUMN operation_type TEXT NOT NULL DEFAULT 'OPTIMIZATION'"
                )
                connection.execute(
                    "ALTER TABLE operations ADD COLUMN reoptimization_json TEXT"
                )
                connection.execute(
                    "ALTER TABLE operations ADD COLUMN source_metadata_json TEXT NOT NULL DEFAULT '{}'"
                )
                connection.execute(
                    "CREATE INDEX idx_operations_parent ON operations(parent_run_id)"
                )
                connection.execute(
                    """
                    CREATE TABLE memory_outbox (
                        run_id TEXT PRIMARY KEY REFERENCES operations(run_id) ON DELETE CASCADE,
                        summary_json TEXT NOT NULL,
                        status TEXT NOT NULL,
                        attempts INTEGER NOT NULL DEFAULT 0,
                        last_error TEXT,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                connection.execute("PRAGMA user_version = 2")

    def save_completed_operation(self, record: Mapping[str, object]) -> bool:
        """Atomically save once per run ID and prepare a no-network memory outbox item."""
        required = {
            "run_id",
            "created_at",
            "completed_at",
            "status",
            "origin",
            "destination",
            "traffic_level",
            "traffic_source",
            "objective",
            "inputs",
            "outputs",
        }
        missing = required.difference(record)
        if missing:
            raise HistoryStorageError(
                "Operation history is missing required fields: "
                + ", ".join(sorted(missing))
            )
        run_id = str(record["run_id"])
        if not run_id:
            raise HistoryStorageError("Operation history run_id must not be empty.")
        safe_record = _json_safe(record)
        inputs_json = _canonical_json(safe_record["inputs"])
        outputs_json = _canonical_json(safe_record["outputs"])
        reoptimization = safe_record.get("reoptimization")
        summary = _compact_summary(safe_record)
        try:
            with self._connection() as connection:
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO operations (
                        run_id, created_at, completed_at, status, origin, destination,
                        traffic_level, traffic_source, objective, inputs_json, outputs_json,
                        parent_run_id, operation_type, reoptimization_json,
                        source_metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        safe_record["created_at"],
                        safe_record["completed_at"],
                        safe_record["status"],
                        safe_record["origin"],
                        safe_record["destination"],
                        safe_record["traffic_level"],
                        safe_record["traffic_source"],
                        safe_record["objective"],
                        inputs_json,
                        outputs_json,
                        safe_record.get("parent_run_id"),
                        safe_record.get("operation_type", "OPTIMIZATION"),
                        _canonical_json(reoptimization) if reoptimization is not None else None,
                        _canonical_json(safe_record.get("source_metadata", {})),
                    ),
                )
                if cursor.rowcount == 0:
                    existing = connection.execute(
                        """
                        SELECT created_at, completed_at, status, origin, destination,
                               traffic_level, traffic_source, objective, inputs_json,
                               outputs_json, parent_run_id, operation_type,
                               reoptimization_json, source_metadata_json
                        FROM operations WHERE run_id = ?
                        """,
                        (run_id,),
                    ).fetchone()
                    expected = (
                        safe_record["created_at"],
                        safe_record["completed_at"],
                        safe_record["status"],
                        safe_record["origin"],
                        safe_record["destination"],
                        safe_record["traffic_level"],
                        safe_record["traffic_source"],
                        safe_record["objective"],
                        inputs_json,
                        outputs_json,
                        safe_record.get("parent_run_id"),
                        safe_record.get("operation_type", "OPTIMIZATION"),
                        _canonical_json(reoptimization) if reoptimization is not None else None,
                        _canonical_json(safe_record.get("source_metadata", {})),
                    )
                    if existing is None or tuple(existing) != expected:
                        raise HistoryStorageError(
                            f"Run ID {run_id} already exists with different operation data."
                        )
                    return False
                connection.execute(
                    """
                    INSERT INTO memory_outbox (
                        run_id, summary_json, status, updated_at
                    ) VALUES (?, ?, 'DISABLED', ?)
                    """,
                    (run_id, _canonical_json(summary), _now()),
                )
            return True
        except (sqlite3.Error, OSError, TypeError) as error:
            raise HistoryStorageError(
                f"Could not save completed operation {run_id}: {error}"
            ) from error

    def list_operations(
        self,
        *,
        search: str = "",
        status: str = "",
        date_from: str = "",
        date_to: str = "",
        limit: int = 25,
        offset: int = 0,
    ) -> tuple[dict[str, object], ...]:
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("History page size must be 1-100 and offset non-negative.")
        clauses: list[str] = []
        params: list[object] = []
        if search.strip():
            clauses.append("(origin LIKE ? OR destination LIKE ?)")
            term = f"%{search.strip()}%"
            params.extend((term, term))
        if status:
            clauses.append("status = ?")
            params.append(status)
        if date_from:
            clauses.append("created_at >= ?")
            params.append(f"{date_from}T00:00:00")
        if date_to:
            clauses.append("created_at < ?")
            params.append(f"{date_to}T23:59:59.999999")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    f"""
                    SELECT * FROM operations {where}
                    ORDER BY created_at DESC, run_id
                    LIMIT ? OFFSET ?
                    """,
                    (*params, limit, offset),
                ).fetchall()
            return tuple(_decode_row(row) for row in rows)
        except sqlite3.Error as error:
            raise HistoryStorageError(f"Could not read operation history: {error}") from error

    def get_operation(self, run_id: str) -> dict[str, object] | None:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "SELECT * FROM operations WHERE run_id = ?", (run_id,)
                ).fetchone()
            return _decode_row(row) if row is not None else None
        except sqlite3.Error as error:
            raise HistoryStorageError(f"Could not read operation {run_id}: {error}") from error

    def count_operations(
        self,
        *,
        search: str = "",
        status: str = "",
        date_from: str = "",
        date_to: str = "",
    ) -> int:
        clauses: list[str] = []
        params: list[object] = []
        if search.strip():
            clauses.append("(origin LIKE ? OR destination LIKE ?)")
            term = f"%{search.strip()}%"
            params.extend((term, term))
        if status:
            clauses.append("status = ?")
            params.append(status)
        if date_from:
            clauses.append("created_at >= ?")
            params.append(f"{date_from}T00:00:00")
        if date_to:
            clauses.append("created_at < ?")
            params.append(f"{date_to}T23:59:59.999999")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        try:
            with self._connection() as connection:
                return int(
                    connection.execute(
                        f"SELECT COUNT(*) FROM operations {where}", params
                    ).fetchone()[0]
                )
        except sqlite3.Error as error:
            raise HistoryStorageError(f"Could not count operation history: {error}") from error

    def recall_summaries(self, query: str, limit: int = 5) -> tuple[dict[str, object], ...]:
        """Return disabled-provider empty results; recalled text never supplies exact data."""
        provider = DisabledHindsightProvider()
        return tuple(dict(summary) for summary in provider.recall(query, limit))

    def resolve_memory_reference(
        self, summary: Mapping[str, object]
    ) -> dict[str, object] | None:
        """Resolve recalled summaries to authoritative SQLite records by stable ID."""
        record_id = summary.get("sqlite_record_id", summary.get("record_id"))
        return self.get_operation(record_id) if isinstance(record_id, str) else None

    def queue_memory_summary(self, run_id: str) -> None:
        try:
            with self._connection() as connection:
                cursor = connection.execute(
                    """
                    UPDATE memory_outbox SET status = 'PENDING', updated_at = ?
                    WHERE run_id = ? AND status IN ('DISABLED', 'RETRY')
                    """,
                    (_now(), run_id),
                )
                if cursor.rowcount == 0:
                    raise HistoryStorageError(
                        f"No disabled or retryable memory summary exists for run {run_id}."
                    )
        except sqlite3.Error as error:
            raise HistoryStorageError(
                f"Could not queue memory summary for {run_id}: {error}"
            ) from error

    def pending_memory_summaries(
        self, limit: int = 20
    ) -> tuple[dict[str, object], ...]:
        if not 1 <= limit <= 100:
            raise ValueError("Outbox page size must be 1-100.")
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    """
                    SELECT run_id, summary_json, attempts
                    FROM memory_outbox
                    WHERE status IN ('PENDING', 'RETRY')
                    ORDER BY updated_at, run_id LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            return tuple(
                {
                    "run_id": row["run_id"],
                    "summary": json.loads(row["summary_json"]),
                    "attempts": row["attempts"],
                }
                for row in rows
            )
        except sqlite3.Error as error:
            raise HistoryStorageError(f"Could not read memory outbox: {error}") from error

    def mark_memory_delivery(
        self, run_id: str, *, delivered: bool, error: str | None = None
    ) -> None:
        status = "DELIVERED" if delivered else "RETRY"
        try:
            with self._connection() as connection:
                cursor = connection.execute(
                    """
                    UPDATE memory_outbox
                    SET status = ?, attempts = attempts + 1, last_error = ?, updated_at = ?
                    WHERE run_id = ?
                    """,
                    (status, error, _now(), run_id),
                )
                if cursor.rowcount == 0:
                    raise HistoryStorageError(f"No memory outbox item exists for run {run_id}.")
        except sqlite3.Error as database_error:
            raise HistoryStorageError(
                f"Could not update memory outbox item {run_id}: {database_error}"
            ) from database_error


def _decode_row(row: sqlite3.Row) -> dict[str, object]:
    return {
        "run_id": row["run_id"],
        "parent_run_id": row["parent_run_id"],
        "created_at": row["created_at"],
        "completed_at": row["completed_at"],
        "status": row["status"],
        "origin": row["origin"],
        "destination": row["destination"],
        "traffic_level": row["traffic_level"],
        "traffic_source": row["traffic_source"],
        "objective": row["objective"],
        "operation_type": row["operation_type"],
        "inputs": json.loads(row["inputs_json"]),
        "outputs": json.loads(row["outputs_json"]),
        "reoptimization": (
            json.loads(row["reoptimization_json"])
            if row["reoptimization_json"]
            else None
        ),
        "source_metadata": json.loads(row["source_metadata_json"]),
    }


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _compact_summary(record: Mapping[str, object]) -> dict[str, object]:
    outputs = record["outputs"]
    if not isinstance(outputs, Mapping):
        raise HistoryStorageError("Operation outputs must be a structured mapping.")
    solvers = outputs.get("solver_results", {})
    if not isinstance(solvers, Mapping):
        raise HistoryStorageError("Operation solver results must be a structured mapping.")
    classical = solvers.get("classical", {})
    if not isinstance(classical, Mapping):
        raise HistoryStorageError("The classical result must be a structured mapping.")
    metrics = classical.get("metrics", {})
    if not isinstance(metrics, Mapping):
        raise HistoryStorageError("The classical metrics must be a structured mapping.")
    return {
        "record_id": record["run_id"],
        "status": record["status"],
        "operation_type": record.get("operation_type", "OPTIMIZATION"),
        "objective": record["objective"],
        "traffic_source": record["traffic_source"],
        "traffic_level": record["traffic_level"],
        "classical_feasibility": classical.get("status"),
        "metrics": dict(metrics),
        "sqlite_record_id": record["run_id"],
    }
