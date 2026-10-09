"""Validated local evidence records for completed IBM Quantum hardware jobs."""

from __future__ import annotations

from datetime import datetime
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import re
import sys
from typing import Mapping

from ibm_execution import HardwareJobState
from route_dashboard.currency import cost_units_to_inr
from route_dashboard.dynamic import calculate_route_impact
from route_dashboard.optimization import RouteSummary
from route_dashboard.scenario import ScenarioProblem


PROJECT_NAME = "QuantumRoute"
PROBLEM_STATEMENT = "VNQFF-08"
REAL_EXECUTION_TYPE = "REAL_IBM_QUANTUM_HARDWARE"
REAL_DATA_ORIGIN = "ibm_quantum_runtime"
EVIDENCE_DIRECTORY = Path(__file__).resolve().parent / "artifacts" / "ibm_hardware"
_JOB_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")
_MOCK_MARKER_PATTERN = re.compile(r"mock|synthetic|fixture|test", re.IGNORECASE)


class HardwareEvidenceError(ValueError):
    """Raised when a hardware record cannot be safely validated or persisted."""


def build_hardware_evidence_record(
    job_state: HardwareJobState,
    scenario: ScenarioProblem,
) -> dict[str, object]:
    """Build a record only from a confirmed real submission and retrieved result."""
    if not job_state.is_real_hardware_execution:
        raise HardwareEvidenceError("Only confirmed real IBM hardware jobs may be recorded.")
    if (
        not job_state.submission_attempted
        or not job_state.measurement_result_received
        or job_state.status.upper() != "DONE"
    ):
        raise HardwareEvidenceError(
            "A completed IBM job with retrieved measurements is required for evidence."
        )
    if job_state.job_id is None or job_state.backend_name is None or job_state.shots is None:
        raise HardwareEvidenceError("The completed job is missing required execution metadata.")
    if not job_state.raw_counts:
        raise HardwareEvidenceError("The completed job has no measurement counts.")
    if job_state.route_valid is None:
        raise HardwareEvidenceError("The completed job has no route validation result.")
    if not job_state.submitted_at:
        raise HardwareEvidenceError("The completed job has no execution timestamp.")
    if not job_state.completed_at:
        raise HardwareEvidenceError("The completed job has no Runtime completion timestamp.")
    manifest = job_state.execution_manifest
    if (
        not job_state.run_id
        or not job_state.manifest_path
        or not job_state.manifest_sha256
        or not isinstance(manifest, Mapping)
        or not job_state.confirmation_timestamp
    ):
        raise HardwareEvidenceError(
            "A durable confirmed pre-submission provenance manifest is required."
        )
    manifest_path = Path(job_state.manifest_path)
    try:
        manifest_content = manifest_path.read_bytes()
    except OSError as error:
        raise HardwareEvidenceError(
            "The durable pre-submission manifest is unavailable."
        ) from error
    if hashlib.sha256(manifest_content).hexdigest() != job_state.manifest_sha256:
        raise HardwareEvidenceError("The pre-submission manifest hash does not match.")
    try:
        persisted_manifest = json.loads(manifest_content)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise HardwareEvidenceError("The pre-submission manifest is not valid JSON.") from error
    if persisted_manifest != manifest:
        raise HardwareEvidenceError("The in-memory and durable manifests do not match.")
    if (
        manifest.get("manifest_status") != "PRE_SUBMISSION_CONFIRMED"
        or manifest.get("run_id") != job_state.run_id
        or not isinstance(manifest.get("user_confirmation"), Mapping)
        or not isinstance(manifest.get("qubo"), Mapping)
        or not isinstance(manifest.get("requested_execution"), Mapping)
        or not isinstance(manifest.get("qaoa"), Mapping)
        or not isinstance(manifest.get("circuits"), Mapping)
        or manifest.get("user_confirmation", {}).get("confirmed") is not True
        or manifest.get("user_confirmation", {}).get("confirmed_at_utc")
        != job_state.confirmation_timestamp
        or manifest.get("qubo", {}).get("signature")
        != _execution_package_signature(job_state)
        or manifest.get("requested_execution", {}).get("backend")
        != job_state.backend_name
        or manifest.get("requested_execution", {}).get("shots") != job_state.shots
        or manifest.get("qaoa", {}).get("parameter_source") != "local_aer_optimized"
    ):
        raise HardwareEvidenceError(
            "The confirmed manifest does not match this job's execution provenance."
        )
    from ibm_hardware_provenance import scenario_record

    if manifest.get("scenario") != scenario_record(scenario):
        raise HardwareEvidenceError(
            "Evidence scenario does not match the pre-submission manifest."
        )
    if (
        not job_state.actual_backend_name
        or job_state.actual_backend_is_simulator is not False
        or job_state.shots_returned != job_state.shots
        or not job_state.result_register_counts
    ):
        raise HardwareEvidenceError(
            "Actual backend type or returned measurement metadata is incomplete."
        )
    if not manifest.get("circuits", {}).get("selected_backend_transpiled_submitted"):
        raise HardwareEvidenceError("The manifest is missing the submitted circuit.")

    routes = job_state.decoded_routes if job_state.route_valid else ()
    summary = RouteSummary(
        routes=tuple(routes),
        total_cost=sum(route.total_cost for route in routes),
        total_distance_km=sum(route.distance_km for route in routes),
        total_travel_time_min=sum(route.travel_time_min for route in routes),
    )
    impact = calculate_route_impact(scenario, summary)
    route_entries = [
        {
            "vehicle_id": route.plan.vehicle_id,
            "delivery_ids": list(route.plan.delivery_ids),
            "delivery_names": [
                scenario.location_names[delivery_id]
                for delivery_id in route.plan.delivery_ids
            ],
            "distance_km": route.distance_km,
            "travel_time_min": route.travel_time_min,
            "objective_cost_model_units": route.total_cost,
        }
        for route in routes
    ]
    route_impact = (
        {
            "distance_km": impact.distance_km,
            "travel_time_min": impact.travel_time_min,
            "objective_cost_model_units": impact.cost,
            "fuel_used": impact.fuel_used,
            "fuel_unit": impact.fuel_unit,
            "tailpipe_co2_kg": impact.tailpipe_co2_kg,
        }
        if job_state.route_valid
        else None
    )
    result: dict[str, object] = {
        "project_name": PROJECT_NAME,
        "problem_statement": PROBLEM_STATEMENT,
        "execution_type": REAL_EXECUTION_TYPE,
        "run_id": job_state.run_id,
        "pre_submission_manifest": dict(manifest),
        "pre_submission_manifest_sha256": job_state.manifest_sha256,
        "pre_submission_manifest_path": str(manifest_path),
        "timestamp_utc": job_state.submitted_at,
        "backend": job_state.actual_backend_name,
        "job_id": job_state.job_id,
        "shots": job_state.shots,
        "shots_returned": job_state.shots_returned,
        "status": job_state.status,
        "measurement_counts": dict(job_state.raw_counts),
        "measurement_registers": dict(job_state.result_register_counts),
        "most_frequent_bitstring": job_state.most_frequent_bitstring,
        "decoded_solution": (
            {
                "selected_bitstring": job_state.selected_solution_bitstring,
                "routes": route_entries,
                "total_distance_km": impact.distance_km,
                "total_travel_time_min": impact.travel_time_min,
            }
            if job_state.route_valid
            else None
        ),
        "route_validation": {
            "passed": job_state.route_valid,
            "message": job_state.route_message or job_state.message,
        },
        "route_impact": route_impact,
        "qubo_objective_value": job_state.qubo_objective_value,
        "qubo_objective_unit": "original QUBO objective units",
        "objective_value": impact.cost if job_state.route_valid else None,
        "objective_unit": "model cost units",
        "operating_cost_inr": (
            cost_units_to_inr(impact.cost) if job_state.route_valid else None
        ),
        "execution_metadata": {
            "data_origin": REAL_DATA_ORIGIN,
            "backend_is_simulator": job_state.actual_backend_is_simulator,
            "submitted_at_utc": job_state.submitted_at,
            "runtime_job_creation_timestamp_utc": job_state.runtime_submitted_at,
            "completed_at_utc": job_state.completed_at,
            "requested_backend": job_state.backend_name,
            "actual_backend": job_state.actual_backend_name,
            "backend_is_simulator": job_state.actual_backend_is_simulator,
            "confirmation_timestamp_utc": job_state.confirmation_timestamp,
            "manifest_sha256": job_state.manifest_sha256,
            "manifest_path": str(manifest_path),
            "provenance_complete": True,
            "shots_returned": job_state.shots_returned,
            "measurement_count_total": sum(
                count for _, count in job_state.raw_counts
            ),
            "parameter_source": getattr(
                job_state.execution_package, "parameter_source", None
            ),
            "problem_signature": _execution_package_signature(job_state),
            "traffic_condition": scenario.traffic_condition,
            "fuel_type": scenario.fuel_type,
            "travel_data_source": scenario.data_source,
        },
        "software_versions": _software_versions(),
        "error_info": job_state.error_info,
    }
    validate_hardware_evidence_record(result)
    return result


def validate_hardware_evidence_record(record: Mapping[str, object]) -> None:
    """Reject incomplete, malformed, simulated, mock, or synthetic evidence."""
    if not isinstance(record, Mapping):
        raise HardwareEvidenceError("Evidence must be a JSON object.")
    if record.get("project_name") != PROJECT_NAME:
        raise HardwareEvidenceError("Evidence project name is invalid.")
    if record.get("problem_statement") != PROBLEM_STATEMENT:
        raise HardwareEvidenceError("Evidence problem statement is invalid.")
    if record.get("execution_type") != REAL_EXECUTION_TYPE:
        raise HardwareEvidenceError("Only real IBM Quantum hardware records are accepted.")

    job_id = _required_text(record, "job_id")
    backend = _required_text(record, "backend")
    if not _JOB_ID_PATTERN.fullmatch(job_id):
        raise HardwareEvidenceError("Evidence job ID contains invalid characters.")
    if _MOCK_MARKER_PATTERN.search(job_id) or _MOCK_MARKER_PATTERN.search(backend):
        raise HardwareEvidenceError("Mock or synthetic execution metadata is not real evidence.")
    shots = record.get("shots")
    if not isinstance(shots, int) or isinstance(shots, bool) or shots < 1:
        raise HardwareEvidenceError("Evidence shots must be a positive integer.")
    if record.get("status") != "DONE":
        raise HardwareEvidenceError("Only completed hardware jobs may be written as evidence.")
    _validate_timestamp(record.get("timestamp_utc"), "timestamp_utc")
    counts = record.get("measurement_counts")
    if not isinstance(counts, Mapping) or not counts:
        raise HardwareEvidenceError("Evidence requires non-empty measurement counts.")
    if any(
        not isinstance(bitstring, str)
        or not bitstring
        or not bitstring.replace(" ", "")
        or any(bit not in "01" for bit in bitstring.replace(" ", ""))
        or not isinstance(count, int)
        or isinstance(count, bool)
        or count < 0
        for bitstring, count in counts.items()
    ):
        raise HardwareEvidenceError("Measurement counts must map bitstrings to non-negative integers.")
    if sum(counts.values()) != shots:
        raise HardwareEvidenceError("Measurement counts must sum to the recorded shot count.")

    validation = record.get("route_validation")
    if not isinstance(validation, Mapping) or not isinstance(validation.get("passed"), bool):
        raise HardwareEvidenceError("Evidence requires a route validation outcome.")
    if not _is_single_line_text(validation.get("message")):
        raise HardwareEvidenceError("Evidence route validation requires a message.")
    solution = record.get("decoded_solution")
    qubo_objective = record.get("qubo_objective_value")
    if qubo_objective is not None and (
        isinstance(qubo_objective, bool)
        or not isinstance(qubo_objective, (int, float))
        or not math.isfinite(qubo_objective)
    ):
        raise HardwareEvidenceError("QUBO objective value must be finite or null.")
    if validation["passed"]:
        if (
            not isinstance(solution, Mapping)
            or not solution.get("routes")
            or not _is_single_line_text(solution.get("selected_bitstring"))
        ):
            raise HardwareEvidenceError("A validated route result requires a decoded solution.")
        selected = solution["selected_bitstring"].replace(" ", "")
        if selected not in {
            str(bitstring).replace(" ", "") for bitstring in counts
        }:
            raise HardwareEvidenceError(
                "The selected decoded candidate is absent from the measurement counts."
            )
        if qubo_objective is None:
            raise HardwareEvidenceError(
                "A validated route requires the original QUBO objective evaluation."
            )
        route_impact = record.get("route_impact")
        if not isinstance(route_impact, Mapping) or any(
            isinstance(route_impact.get(field), bool)
            or not isinstance(route_impact.get(field), (int, float))
            or not math.isfinite(route_impact[field])
            for field in (
                "distance_km",
                "travel_time_min",
                "objective_cost_model_units",
                "fuel_used",
                "tailpipe_co2_kg",
            )
        ):
            raise HardwareEvidenceError(
                "A validated route requires scenario-derived impact metrics."
            )
    elif solution is not None:
        raise HardwareEvidenceError("An invalid route result must not include a selected solution.")
    elif record.get("route_impact") is not None:
        raise HardwareEvidenceError("An invalid route must not include route impact metrics.")

    objective = record.get("objective_value")
    if objective is not None and (
        isinstance(objective, bool)
        or not isinstance(objective, (int, float))
        or not math.isfinite(objective)
    ):
        raise HardwareEvidenceError("Evidence objective value must be finite or null.")
    metadata = record.get("execution_metadata")
    if not isinstance(metadata, Mapping):
        raise HardwareEvidenceError("Evidence execution metadata is missing.")
    _validate_timestamp(metadata.get("completed_at_utc"), "completed_at_utc")
    if metadata.get("data_origin") != REAL_DATA_ORIGIN:
        raise HardwareEvidenceError("Mock or synthetic results cannot be written as hardware evidence.")
    if metadata.get("backend_is_simulator") is not False:
        raise HardwareEvidenceError("Evidence backend must be confirmed as real hardware.")
    if metadata.get("actual_backend") != record.get("backend"):
        raise HardwareEvidenceError("Evidence backend does not match the retrieved backend.")
    if metadata.get("requested_backend") != metadata.get("actual_backend"):
        raise HardwareEvidenceError("Retrieved backend does not match the requested backend.")
    if metadata.get("submitted_at_utc") != record.get("timestamp_utc"):
        raise HardwareEvidenceError("Evidence submission timestamp does not match execution metadata.")
    runtime_created_at = metadata.get("runtime_job_creation_timestamp_utc")
    if runtime_created_at is not None:
        _validate_timestamp(runtime_created_at, "runtime_job_creation_timestamp_utc")
    if metadata.get("measurement_count_total") != shots:
        raise HardwareEvidenceError("Execution metadata shot count does not match the measurements.")
    if record.get("shots_returned") != shots or metadata.get("shots_returned") != shots:
        raise HardwareEvidenceError("Returned shots do not match the requested shots.")
    if metadata.get("provenance_complete") is not True:
        raise HardwareEvidenceError("Execution provenance is incomplete.")
    manifest = record.get("pre_submission_manifest")
    if (
        not isinstance(manifest, Mapping)
        or not isinstance(manifest.get("user_confirmation"), Mapping)
        or not isinstance(manifest.get("qubo"), Mapping)
        or not isinstance(manifest.get("requested_execution"), Mapping)
        or not isinstance(manifest.get("qaoa"), Mapping)
        or not isinstance(manifest.get("circuits"), Mapping)
        or manifest.get("manifest_status") != "PRE_SUBMISSION_CONFIRMED"
        or manifest.get("run_id") != record.get("run_id")
        or manifest.get("user_confirmation", {}).get("confirmed") is not True
        or manifest.get("user_confirmation", {}).get("confirmed_at_utc")
        != metadata.get("confirmation_timestamp_utc")
        or manifest.get("qubo", {}).get("signature")
        != metadata.get("problem_signature")
        or metadata.get("manifest_sha256")
        != record.get("pre_submission_manifest_sha256")
        or manifest.get("requested_execution", {}).get("backend")
        != metadata.get("requested_backend")
        or manifest.get("requested_execution", {}).get("shots") != shots
        or manifest.get("qaoa", {}).get("parameter_source") != "local_aer_optimized"
        or not manifest.get("circuits", {}).get("selected_backend_transpiled_submitted")
    ):
        raise HardwareEvidenceError("Evidence does not link to complete confirmed run provenance.")
    serialized_manifest = (
        json.dumps(manifest, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    ).encode("utf-8")
    if (
        hashlib.sha256(serialized_manifest).hexdigest()
        != record.get("pre_submission_manifest_sha256")
    ):
        raise HardwareEvidenceError("Embedded pre-submission manifest hash is invalid.")
    registers = record.get("measurement_registers")
    if not isinstance(registers, Mapping) or len(registers) != 1:
        raise HardwareEvidenceError("Evidence requires the actual single-register result layout.")
    for register_name, register in registers.items():
        submitted_registers = manifest["circuits"][
            "selected_backend_transpiled_submitted"
        ].get("classical_registers", [])
        submitted_register = next(
            (
                item
                for item in submitted_registers
                if item.get("name") == register_name
            ),
            None,
        ) if isinstance(submitted_registers, list) else None
        if (
            not isinstance(register_name, str)
            or not isinstance(register, Mapping)
            or register.get("counts") != counts
            or register.get("num_shots") != shots
            or not isinstance(submitted_register, Mapping)
            or register.get("num_bits") != submitted_register.get("size")
        ):
            raise HardwareEvidenceError("Result register metadata does not match saved counts.")
    completed_at = metadata.get("completed_at_utc")
    _validate_timestamp(completed_at, "completed_at_utc")
    error_info = record.get("error_info")
    if error_info is not None and not _is_single_line_text(error_info):
        raise HardwareEvidenceError("Evidence error information must be a single-line string or null.")
    versions = record.get("software_versions")
    if not isinstance(versions, Mapping) or not versions:
        raise HardwareEvidenceError("Evidence software versions are missing.")
    if any(
        not isinstance(name, str)
        or not name
        or (version is not None and not isinstance(version, str))
        for name, version in versions.items()
    ):
        raise HardwareEvidenceError("Evidence software versions are malformed.")


def write_hardware_evidence(
    job_state: HardwareJobState,
    scenario: ScenarioProblem,
    evidence_directory: Path | str | None = None,
) -> tuple[Path, Path]:
    """Persist one validated completed real result without overwriting evidence."""
    record = build_hardware_evidence_record(job_state, scenario)
    directory = Path(evidence_directory) if evidence_directory is not None else EVIDENCE_DIRECTORY
    stem = f"ibm_hardware_{record['job_id']}"
    json_path = directory / f"{stem}.json"
    report_path = directory / f"{stem}.md"
    json_content = json.dumps(record, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    report_content = render_hardware_evidence_report(record)
    directory.mkdir(parents=True, exist_ok=True)

    created_paths: list[Path] = []
    try:
        for path, content in ((json_path, json_content), (report_path, report_content)):
            if path.exists():
                if path.read_text(encoding="utf-8") != content:
                    raise HardwareEvidenceError(
                        f"Refusing to overwrite different evidence at {path.name}."
                    )
                continue
            with path.open("x", encoding="utf-8", newline="\n") as evidence_file:
                evidence_file.write(content)
            created_paths.append(path)
    except Exception:
        for path in created_paths:
            path.unlink(missing_ok=True)
        raise
    return json_path, report_path


def render_hardware_evidence_report(record: Mapping[str, object]) -> str:
    """Render a human-readable summary after validating the machine record."""
    validate_hardware_evidence_record(record)
    validation = record["route_validation"]
    solution = record["decoded_solution"]
    lines = [
        "# Real IBM Quantum hardware execution evidence",
        "",
        "This record describes a **REAL IBM Quantum hardware execution**. "
        "It is not a simulator result, mock, or synthetic fixture.",
        "",
        f"- Project: {record['project_name']}",
        f"- Problem statement: {record['problem_statement']}",
        f"- Backend: `{record['backend']}`",
        f"- Job ID: `{record['job_id']}`",
        f"- Shots: {record['shots']}",
        f"- Status: {record['status']}",
        f"- Execution timestamp (UTC): {record['timestamp_utc']}",
        f"- IBM Runtime job creation timestamp (UTC): "
        f"{record['execution_metadata'].get('runtime_job_creation_timestamp_utc') or 'Not provided by Runtime'}",
        f"- Completion timestamp (UTC): "
        f"{record['execution_metadata'].get('completed_at_utc') or 'Not provided by Runtime'}",
        f"- Application run ID: `{record['run_id']}`",
        f"- QUBO signature: "
        f"`{record['execution_metadata'].get('problem_signature')}`",
        f"- Most frequent measured bitstring: "
        f"`{record.get('most_frequent_bitstring')}`",
        f"- Pre-submission manifest SHA-256: "
        f"`{record['pre_submission_manifest_sha256']}`",
        "",
        "## Measurement result",
        "",
        f"- Counts: `{json.dumps(record['measurement_counts'], sort_keys=True)}`",
        f"- Selected bitstring: "
        f"`{solution.get('selected_bitstring') if isinstance(solution, Mapping) else 'None'}`",
        "",
        "## Decoded route and validation",
        "",
        f"- Feasibility validation: **{'PASSED' if validation['passed'] else 'FAILED'}**",
        f"- Validation detail: {validation['message']}",
    ]
    if validation["passed"]:
        lines.append(
            "The selected candidate is the lowest-QUBO-energy observed assignment "
            "that passed feasibility validation; it is not asserted to be globally optimal."
        )
        lines.append(
            f"- Selected feasible candidate QUBO objective: "
            f"`{record['qubo_objective_value']}` {record['qubo_objective_unit']}"
        )
    else:
        lines.append(
            "No observed assignment passed feasibility validation; the most frequent "
            "bitstring is not reported as a route."
        )
        lines.append(
            "- Candidate QUBO objective not reported because no observed route "
            "passed feasibility validation."
        )
    if isinstance(solution, Mapping):
        for route in solution["routes"]:
            lines.append(
                f"- Route: `{route['vehicle_id']}` → "
                + " → ".join(route["delivery_names"])
            )
        lines.extend(
            (
                f"- Total distance: {solution['total_distance_km']:.3f} km",
                f"- Total travel time: {solution['total_travel_time_min']:.3f} min",
                f"- Fuel estimate: {record['route_impact']['fuel_used']:.3f} "
                f"{record['route_impact']['fuel_unit']}",
                f"- Tailpipe CO₂ estimate: "
                f"{record['route_impact']['tailpipe_co2_kg']:.3f} kg",
            )
        )
    if record["objective_value"] is not None:
        lines.extend(
            (
                "",
                "## Objective and cost",
                "",
                f"- Objective: {record['objective_value']:.6f} "
                f"{record['objective_unit']}",
                f"- Estimated operating cost: ₹{record['operating_cost_inr']:.2f}",
            )
        )
    error_info = record.get("error_info")
    if error_info:
        lines.extend(("", "## Execution or validation notes", "", str(error_info)))
    lines.extend(
        (
            "",
            "## Limitations",
            "",
            "- Hardware measurements are stochastic; this small demonstration does not establish quantum advantage.",
            "- Route feasibility and physical cost are calculated by the project model from the decoded measurement.",
            "- Fuel and CO2 estimates, when shown by the application, use configured model assumptions.",
            "",
        )
    )
    return "\n".join(lines)


def _required_text(record: Mapping[str, object], field: str) -> str:
    value = record.get(field)
    if not _is_single_line_text(value):
        raise HardwareEvidenceError(f"Evidence {field} is missing or malformed.")
    return value


def _is_single_line_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and not any(
        character in value for character in "\r\n\x00"
    )


def _validate_timestamp(value: object, field: str) -> None:
    if not _is_single_line_text(value):
        raise HardwareEvidenceError(f"Evidence {field} is missing or malformed.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise HardwareEvidenceError(f"Evidence {field} must be an ISO-8601 timestamp.") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise HardwareEvidenceError(f"Evidence {field} must include a timezone.")


def _execution_package_signature(job_state: HardwareJobState) -> str | None:
    signature = getattr(job_state.execution_package, "problem_signature", None)
    return signature if isinstance(signature, str) else None


def _software_versions() -> dict[str, str | None]:
    versions: dict[str, str | None] = {"python": sys.version.split()[0]}
    for distribution in ("qiskit", "qiskit-aer", "qiskit-ibm-runtime"):
        try:
            versions[distribution] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            versions[distribution] = None
    return versions
