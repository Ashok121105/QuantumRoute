"""Read-only operation history UI with no routing or solver imports."""

from __future__ import annotations

from datetime import date
import json
from typing import MutableMapping
from uuid import uuid4

import pandas as pd
import streamlit as st

from .history import HistoryStorageError, SQLiteOperationHistory


def render_history_page(
    state: MutableMapping[str, object] | None = None,
    history: SQLiteOperationHistory | None = None,
) -> None:
    state = state if state is not None else st.session_state
    if history is None:
        try:
            history = SQLiteOperationHistory()
        except (HistoryStorageError, OSError) as error:
            st.error(f"Operation history could not be opened: {error}")
            return
    st.markdown("## Operation history")
    st.caption(
        "Saved local optimization records only. Opening history does not call routing APIs "
        "or rerun classical/QAOA optimization."
    )

    filter_columns = st.columns([2, 1, 1, 1])
    search = filter_columns[0].text_input(
        "Search origin or destination", key="history_search"
    )
    status = filter_columns[1].selectbox(
        "Validation status",
        ("All", "VALIDATED", "PARTIAL", "FAILED"),
        key="history_status",
    )
    start_date = filter_columns[2].date_input(
        "From", value=None, key="history_start_date"
    )
    end_date = filter_columns[3].date_input(
        "To", value=None, key="history_end_date"
    )
    page_size = st.selectbox("Runs per page", (10, 25, 50), index=0, key="history_page_size")
    total = history.count_operations(
        search=search,
        status="" if status == "All" else status,
        date_from=_date_value(start_date),
        date_to=_date_value(end_date),
    )
    page_count = max(1, (total + page_size - 1) // page_size)
    page = st.number_input(
        "Page",
        min_value=1,
        max_value=page_count,
        value=min(int(state.get("history_page", 1)), page_count),
        step=1,
        key="history_page",
    )
    try:
        records = history.list_operations(
            search=search,
            status="" if status == "All" else status,
            date_from=_date_value(start_date),
            date_to=_date_value(end_date),
            limit=int(page_size),
            offset=(int(page) - 1) * int(page_size),
        )
    except HistoryStorageError as error:
        st.error(f"Operation history could not be loaded: {error}")
        return
    if not records:
        st.info("No saved operations match these filters.")
        return

    rows = [
        {
            "Completed": item["completed_at"],
            "Origin": item["origin"],
            "Destinations": item["destination"],
            "Status": item["status"],
            "Objective": item["objective"],
            "Traffic": f'{item["traffic_level"]} ({item["traffic_source"]})',
            "Type": item["operation_type"],
            "Run ID": item["run_id"],
        }
        for item in records
    ]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    selected_id = st.selectbox(
        "Open a saved operation",
        tuple(str(item["run_id"]) for item in records),
        format_func=lambda identifier: (
            f'{next(item["completed_at"] for item in records if item["run_id"] == identifier)}'
            f' · {next(item["origin"] for item in records if item["run_id"] == identifier)}'
            f' → {next(item["destination"] for item in records if item["run_id"] == identifier)}'
        ),
        key="history_selected_run",
    )
    selected = history.get_operation(selected_id)
    if selected is None:
        st.warning("The selected history record is no longer available.")
        return
    _render_operation(selected, state)


def _date_value(value: object) -> str:
    return value.isoformat() if isinstance(value, date) else ""


def _render_operation(
    record: dict[str, object], state: MutableMapping[str, object]
) -> None:
    st.markdown(f"### {record['origin']} → {record['destination']}")
    st.caption(
        f"Run `{record['run_id']}` · {record['operation_type']} · "
        f"completed {record['completed_at']} · traffic {record['traffic_source']}"
    )
    outputs = record["outputs"]
    inputs = record["inputs"]
    if not isinstance(outputs, dict) or not isinstance(inputs, dict):
        st.error("This stored record has an unsupported data shape.")
        return

    feasibility = outputs.get("feasibility", {})
    if isinstance(feasibility, dict):
        st.write("Feasibility:", feasibility.get("status", "UNKNOWN"))
        if feasibility.get("failure_details"):
            st.warning(str(feasibility["failure_details"]))

    solver_results = outputs.get("solver_results", {})
    if isinstance(solver_results, dict):
        classical, qaoa = st.tabs(("Classical", "QAOA / Aer simulation"))
        with classical:
            st.json(solver_results.get("classical", {}))
        with qaoa:
            st.json(solver_results.get("qaoa_aer", {}))

    st.markdown("#### Recorded inputs")
    st.json(inputs)
    st.markdown("#### Hardware evidence")
    hardware = outputs.get("ibm_hardware", {})
    if isinstance(hardware, dict):
        st.write(
            f"Status: {hardware.get('status', 'NOT_LINKED')} · "
            f"Job ID: {hardware.get('job_id') or 'not linked'} · "
            f"Evidence: {hardware.get('verified_evidence_reference') or 'not linked'}"
        )
    event = record.get("reoptimization")
    if isinstance(event, dict):
        st.markdown("#### Re-optimization comparison")
        st.json(event)

    reusable = _has_editor_compatible_inputs(inputs)
    if not reusable:
        st.info(
            "A new plan can only be copied when this record contains complete inputs "
            "supported by the current shared-capacity scenario editor."
        )
    if st.button(
        "Create a new plan from this history",
        key=f"history_new_plan_{record['run_id']}",
        type="primary",
        disabled=not reusable,
    ):
        state["history_plan_draft"] = json.loads(json.dumps(inputs))
        state["history_plan_editor_key"] = f"delivery_editor_history_{uuid4().hex}"
        state["history_navigation_requested"] = True
        state["history_plan_notice"] = (
            f"Copied inputs from historical run {record['run_id']}. "
            "This creates a new operation; the saved result remains unchanged."
        )
        st.rerun()


def _has_editor_compatible_inputs(inputs: dict[str, object]) -> bool:
    origin = inputs.get("origin")
    destinations = inputs.get("destinations")
    vehicles = inputs.get("vehicles")
    fuel = inputs.get("fuel")
    traffic = inputs.get("traffic")
    if not (
        isinstance(origin, dict)
        and isinstance(destinations, list)
        and destinations
        and all(isinstance(item, dict) for item in destinations)
        and isinstance(vehicles, list)
        and vehicles
        and all(isinstance(item, dict) for item in vehicles)
        and isinstance(fuel, dict)
        and isinstance(fuel.get("cost_weights"), dict)
        and isinstance(traffic, dict)
    ):
        return False
    first_vehicle = vehicles[0]
    required_vehicle_fields = ("capacity", "shift_start_min", "shift_end_min")
    required_delivery_fields = (
        "name",
        "latitude",
        "longitude",
        "quantity",
        "time_window_start_min",
        "time_window_end_min",
        "service_duration_min",
    )
    return (
        all(field in first_vehicle for field in required_vehicle_fields)
        and all(
            all(field in vehicle for field in required_vehicle_fields)
            and all(vehicle[field] == first_vehicle[field] for field in required_vehicle_fields)
            for vehicle in vehicles
        )
        and all(all(field in item for field in required_delivery_fields) for item in destinations)
        and all(field in origin for field in ("name", "latitude", "longitude"))
        and "price_per_unit_cost_units" in fuel
        and "time_cost_per_min" in fuel["cost_weights"]
        and "level" in traffic
    )
