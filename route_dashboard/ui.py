"""Streamlit dashboard for scenario setup, optimization, and route inspection."""

from datetime import time
from math import isclose

import altair as alt
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

from quantum_route_optimisation import QAOAConfig

from .dynamic import TrafficReoptimization, calculate_route_impact, reoptimize_for_traffic
from .demo_mode import (
    DEMO_BREAKDOWN_VEHICLE,
    DEMO_QAOA_CONFIG,
    DemoStage,
    HackathonDemoResult,
    build_hackathon_demo_scenario,
    reset_demo_state,
    run_full_demo,
)
from .map_view import build_route_map
from .benchmark_suite import BenchmarkResult, generate_benchmark_scenarios, run_benchmark
from .currency import (
    AUTO_DETECT,
    BASE_CURRENCY,
    COUNTRY_CURRENCIES,
    COUNTRY_OPTIONS,
    DEFAULT_COUNTRY,
    MANUAL_SELECTION,
    CurrencyDisplay,
    ExchangeRates,
    build_currency_display,
    fetch_usd_exchange_rates,
    resolve_country,
)
from .fleet_disruption import (
    DEFAULT_PRIORITY,
    DEMO_DELIVERY_PRIORITIES,
    DeliveryPriority,
    FleetDisruptionResult,
    VehicleStatus,
    build_fleet_disruption_demo_scenario,
    reoptimize_fleet,
)
from ibm_quantum import (
    IBMBackendDiscovery,
    IBMBackendMetadata,
    connect_ibm_quantum,
    discover_ibm_backends,
    ibm_quantum_status_report,
)
from ibm_execution import (
    DEFAULT_SHOTS,
    MAX_SHOTS,
    HardwareDryRunResult,
    HardwareJobState,
    dry_run_optimized_qaoa_execution,
    refresh_hardware_job_status,
    submit_confirmed_hardware_job,
)
from ibm_qaoa_adapter import build_demo_route_qubo, route_qubo_fingerprint
from ibm_qaoa_parameters import (
    DEFAULT_DEMO_QAOA_CONFIG,
    optimize_demo_qaoa_parameters,
)
from .objectives import (
    OBJECTIVE_DESCRIPTIONS,
    ObjectiveConfig,
    ObjectiveName,
    optimize_with_objective,
)
from .optimization import OptimizationRun, RouteSummary, optimize_scenario
from .scenario import (
    FUEL_OPTIONS,
    DeliveryStop,
    ScenarioProblem,
    build_scenario,
    demo_stops,
)
from .theme import apply_theme


def _format_time(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


DEFAULT_ROWS = [
    {
        "destination": stop.destination,
        "latitude": stop.latitude,
        "longitude": stop.longitude,
        "demand": stop.demand,
        "window_start": _format_time(stop.window_start_min),
        "window_end": _format_time(stop.window_end_min),
        "service_minutes": stop.service_duration_min,
    }
    for stop in demo_stops()
]


@st.cache_data(ttl=86400, show_spinner=False)
def _cached_usd_exchange_rates() -> ExchangeRates:
    return fetch_usd_exchange_rates()


def main() -> None:
    st.set_page_config(
        page_title="QuantumRoute | Smarter Routes",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    apply_theme()
    qaoa_config, use_osrm, page = _render_sidebar()
    current = st.session_state.get("current_run")
    disruption = st.session_state.get("fleet_disruption_result")
    if current is None and disruption is not None and disruption.after_run is not None:
        current = {"scenario": disruption.optimization_scenario, "run": disruption.after_run}
    _render_page_header(page, current)
    _render_currency_status()

    if page == "Dashboard":
        _render_dashboard(current)
    elif page == "Route Optimization":
        submitted, form_values = _render_scenario_form()
        if submitted:
            _run_from_form(form_values, qaoa_config, use_osrm)
        run_error = st.session_state.get("run_error")
        if run_error:
            st.error(run_error)
        current = st.session_state.get("current_run")
        if current is not None:
            dynamic_comparison = st.session_state.get("dynamic_comparison")
            if dynamic_comparison is not None:
                _render_dynamic_comparison(dynamic_comparison)
            _render_results(current["scenario"], current["run"])
            if dynamic_comparison is None:
                _render_traffic_comparison(st.session_state.get("traffic_comparison"))
        else:
            st.info("Configure a scenario above to calculate a validated fleet plan.")
    elif page == "Dynamic Traffic":
        _render_dynamic_routing(qaoa_config)
        current = st.session_state.get("current_run")
        if current is not None:
            comparison = st.session_state.get("dynamic_comparison")
            if comparison is not None:
                _render_dynamic_comparison(comparison)
            _render_fleet_view(current["scenario"], current["run"])
            _render_route_map(current["scenario"], current["run"], comparison)
    elif page == "Fleet Disruption":
        _render_fleet_disruption(qaoa_config)
    elif page == "Demo Mode":
        _render_demo_mode()
    elif page == "IBM Quantum Hardware":
        _render_ibm_quantum_hardware()
    else:
        _render_benchmark_mode(qaoa_config)


def _render_sidebar() -> tuple[QAOAConfig, bool, str]:
    with st.sidebar:
        st.markdown(
            '<div class="qr-brand"><span class="qr-mark">QR</span>QuantumRoute</div>'
            '<div class="qr-subtitle">Smarter Routes. Greener Tomorrow.</div>',
            unsafe_allow_html=True,
        )
        st.markdown("#### Workspace")
        nav_icons = {
            "Dashboard": "dashboard",
            "Route Optimization": "route",
            "Dynamic Traffic": "traffic",
            "Fleet Disruption": "local_shipping",
            "Demo Mode": "slideshow",
            "Benchmark Mode": "query_stats",
            "IBM Quantum Hardware": "memory",
        }
        page = st.radio(
            "Workspace navigation",
            (
                "Dashboard",
                "Route Optimization",
                "Dynamic Traffic",
                "Fleet Disruption",
                "IBM Quantum Hardware",
                "Demo Mode",
                "Benchmark Mode",
            ),
            format_func=lambda value: f":material/{nav_icons[value]}: {value}",
            key="active_page",
            label_visibility="collapsed",
        )
        st.divider()
        with st.expander("Location & currency", expanded=False):
            location_mode = st.selectbox(
                "Location mode",
                (AUTO_DETECT, MANUAL_SELECTION),
                key="currency_location_mode",
            )
            accept_language = st.context.headers.get("Accept-Language", "")
            manual_country = None
            if location_mode == MANUAL_SELECTION:
                manual_country = st.selectbox(
                    "Country",
                    COUNTRY_OPTIONS,
                    index=COUNTRY_OPTIONS.index(DEFAULT_COUNTRY),
                    key="manual_currency_country",
                )

            resolution = resolve_country(location_mode, accept_language, manual_country)
            target_currency = COUNTRY_CURRENCIES[resolution.country]
            exchange_rates = None
            rate_error = None
            if target_currency != BASE_CURRENCY:
                try:
                    exchange_rates = _cached_usd_exchange_rates()
                except Exception as error:
                    rate_error = f"Exchange-rate lookup failed: {type(error).__name__}."
            currency_display = build_currency_display(
                resolution.country,
                exchange_rates,
                fallback_message=rate_error,
            )
            st.session_state["currency_display"] = currency_display
            st.caption(f"Country: {resolution.country} ({resolution.method})")
            if currency_display.fallback_message:
                st.caption(currency_display.fallback_message)
            elif currency_display.converted:
                st.caption(
                    f"USD to {currency_display.display_currency_code} rate updated "
                    f"{currency_display.rate_updated_at_utc}. Display conversion only."
                )
            else:
                st.caption("USD base currency; no conversion applied.")
            st.markdown(
                '[Rates by ExchangeRate-API](https://www.exchangerate-api.com)',
                unsafe_allow_html=False,
            )

        st.divider()
        with st.expander("Solver & routing settings", expanded=False):
            st.caption("QAOA executes locally on Qiskit Aer.")
            reps = st.number_input("QAOA depth (reps)", min_value=1, max_value=3, value=1)
            maxiter = st.number_input("Optimizer iterations", min_value=5, max_value=100, value=25, step=5)
            shots = st.number_input("Sampler shots", min_value=128, max_value=4096, value=1024, step=128)
            seed = st.number_input("Random seed", min_value=0, max_value=2_147_483_647, value=7, step=1)
            use_osrm = st.checkbox("Request OSRM travel matrix", value=False)
            st.caption(
                "Optional public OSRM table lookup. If unavailable, local coordinate estimates are used."
            )
            st.caption("Map basemap tiles are provided by OpenStreetMap.")
    return QAOAConfig(reps=int(reps), maxiter=int(maxiter), shots=int(shots), seed=int(seed)), use_osrm, page


def _currency_display() -> CurrencyDisplay:
    return st.session_state.get(
        "currency_display",
        build_currency_display(DEFAULT_COUNTRY, None),
    )


def _format_money(amount_in_usd: float) -> str:
    return _currency_display().format_money(amount_in_usd)


def _format_money_delta(amount_in_usd: float) -> str:
    formatted = _currency_display().format_money(abs(amount_in_usd))
    return f"-{formatted}" if amount_in_usd < 0 else f"+{formatted}"


def _render_currency_status() -> None:
    display = _currency_display()
    if display.fallback_message:
        st.warning(display.fallback_message)
    elif display.converted:
        st.caption(
            f"Displaying USD-based amounts in {display.country} ({display.display_currency_code}) "
            f"using daily {display.rate_source} rates updated {display.rate_updated_at_utc}. "
            "Optimization inputs and calculations remain in USD base units."
        )
    else:
        st.caption("Displaying USD base amounts. Optimization calculations are unchanged.")


def _render_page_header(page: str, current: dict[str, object] | None) -> None:
    titles = {
        "Dashboard": "Operations overview",
        "Route Optimization": "Route optimization",
        "Dynamic Traffic": "Dynamic traffic",
        "Fleet Disruption": "Fleet disruption",
        "Demo Mode": "Demo Mode",
        "Benchmark Mode": "Benchmark laboratory",
        "IBM Quantum Hardware": "IBM Quantum Hardware",
    }
    header, action = st.columns([8, 2], vertical_alignment="center")
    with header:
        st.markdown('<div class="qr-kicker">QUANTUMROUTE / LOGISTICS CONTROL</div>', unsafe_allow_html=True)
        st.title(titles[page])
        st.markdown('<div class="qr-subtitle">Smarter Routes. Greener Tomorrow.</div>', unsafe_allow_html=True)
    with action:
        status = (
            "Fleet status ready"
            if page == "Fleet Disruption"
            else "Read-only backend discovery"
            if page == "IBM Quantum Hardware"
            else "Routes validated"
            if current
            else "No active plan"
        )
        st.markdown(
            f'<div class="qr-status"><span class="qr-status-dot"></span>{status}</div>',
            unsafe_allow_html=True,
        )
        if page not in ("Route Optimization", "Demo Mode", "IBM Quantum Hardware"):
            st.button(
                "New Optimization",
                key="new_optimization",
                use_container_width=True,
                on_click=lambda: st.session_state.update({"active_page": "Route Optimization"}),
            )


def _render_dashboard(current: dict[str, object] | None) -> None:
    st.markdown("### Network snapshot")
    disruption: FleetDisruptionResult | None = st.session_state.get("fleet_disruption_result")
    if disruption is not None and disruption.after_run is not None and disruption.after_metrics is not None:
        metrics = (
            f"{disruption.after_metrics.total_distance_km:.1f} km",
            f"{_format_duration(disruption.after_metrics.total_travel_time_min)}",
            _format_money(disruption.after_metrics.total_cost),
            f"{disruption.after_metrics.tailpipe_co2_kg:.2f} kg",
        )
    elif current is None:
        metrics = ("--", "--", "--", "--")
        st.info("No active route plan. Start a new optimization to populate live scenario metrics.")
    else:
        scenario: ScenarioProblem = current["scenario"]
        run: OptimizationRun = current["run"]
        impact = calculate_route_impact(scenario, run.classical)
        metrics = (
            f"{impact.distance_km:.1f} km",
            f"{_format_duration(impact.travel_time_min)}",
            _format_money(impact.cost),
            f"{impact.tailpipe_co2_kg:.2f} kg",
        )
    metric_columns = st.columns(4)
    for column, label, value in zip(
        metric_columns,
        ("Total distance", "Total travel time", "Total cost", "Estimated CO2"),
        metrics,
    ):
        column.metric(label, value)

    if disruption is not None and disruption.after_run is not None:
        st.markdown("### Latest fleet re-optimization")
        _render_fleet_before_after(disruption)
        _render_waitlist(disruption)
        st.markdown("### Updated fleet map")
        _render_route_map(disruption.after_scenario, disruption.after_run, None)
        st.markdown("### Updated optimization methods")
        _render_comparison(disruption.optimization_scenario, disruption.after_run)
        _render_fleet_view(disruption.optimization_scenario, disruption.after_run)
    elif current is not None:
        scenario = current["scenario"]
        run = current["run"]
        st.markdown("### Route map")
        _render_route_map(scenario, run, st.session_state.get("dynamic_comparison"))
        st.markdown("### Optimization methods")
        _render_comparison(scenario, run)
        _render_fleet_view(scenario, run)


def _navigate_to_optimization() -> None:
    st.session_state["active_page"] = "Route Optimization"


def _render_scenario_form() -> tuple[bool, dict[str, object]]:
    with st.form("route_scenario"):
        st.markdown("## 01 / Scenario setup")
        depot_col, latitude_col, longitude_col = st.columns([1.3, 1, 1])
        depot_name = depot_col.text_input("Depot name", value="Mission Depot")
        depot_latitude = latitude_col.number_input(
            "Depot latitude", min_value=-90.0, max_value=90.0, value=37.7749, format="%.5f"
        )
        depot_longitude = longitude_col.number_input(
            "Depot longitude", min_value=-180.0, max_value=180.0, value=-122.4194, format="%.5f"
        )

        st.markdown("## 02 / Delivery & vehicle constraints")
        vehicle_col, capacity_col, start_col, end_col = st.columns(4)
        vehicle_count = vehicle_col.number_input("Vehicles", min_value=1, max_value=8, value=2)
        capacity = capacity_col.number_input("Capacity per vehicle", min_value=0.1, max_value=1000.0, value=2.0, step=0.5)
        shift_start = start_col.time_input("Shift starts", value=time(8, 0))
        shift_end = end_col.time_input("Shift ends", value=time(17, 0))

        traffic_col, fuel_col, price_col, labor_col = st.columns(4)
        traffic = traffic_col.selectbox(
            "Traffic condition",
            ("Calm", "Normal", "Moderate", "Heavy", "Storm"),
            index=2,
        )
        fuel_type = fuel_col.selectbox("Fuel / energy", tuple(FUEL_OPTIONS), index=0)
        fuel = FUEL_OPTIONS[fuel_type]
        fuel_price = price_col.number_input(
            f"Fuel price (USD / {fuel['unit']})",
            min_value=0.0,
            max_value=1000.0,
            value=float(fuel["default_price"]),
            step=0.05,
        )
        price_col.caption(
            f"Display equivalent: {_format_money(float(fuel_price))} per {fuel['unit']} (reference only)"
        )
        driver_cost = labor_col.number_input(
            "Driver cost per hour (USD)", min_value=0.0, max_value=1000.0, value=25.0, step=1.0
        )
        objective_name = st.selectbox(
            "Optimization Objective",
            tuple(objective.value for objective in ObjectiveName),
            index=0,
            format_func=lambda value: f"{value}: {OBJECTIVE_DESCRIPTIONS[ObjectiveName(value)]}",
            key="route_optimization_objective",
        )
        st.caption(
            "Objective = estimated fuel/energy cost by distance + driver cost by elapsed time. "
            "Inputs and physical costs use USD base units; objective metrics are normalized. "
            "Currency conversion affects display only."
        )

        rows = st.data_editor(
            pd.DataFrame(DEFAULT_ROWS),
            num_rows="dynamic",
            hide_index=True,
            width="stretch",
            column_config={
                "destination": st.column_config.TextColumn("Destination", required=True),
                "latitude": st.column_config.NumberColumn("Latitude", min_value=-90.0, max_value=90.0, format="%.5f"),
                "longitude": st.column_config.NumberColumn("Longitude", min_value=-180.0, max_value=180.0, format="%.5f"),
                "demand": st.column_config.NumberColumn("Demand", min_value=0.0, format="%.2f"),
                "window_start": st.column_config.TextColumn("Window start (HH:MM)"),
                "window_end": st.column_config.TextColumn("Window end (HH:MM)"),
                "service_minutes": st.column_config.NumberColumn("Service (min)", min_value=0, step=1),
            },
            key="delivery_editor",
        )
        st.markdown("## 03 / Optimization")
        submitted = st.form_submit_button("Run optimization", type="primary", width="stretch")

    return submitted, {
        "depot_name": depot_name,
        "depot_latitude": float(depot_latitude),
        "depot_longitude": float(depot_longitude),
        "vehicle_count": int(vehicle_count),
        "capacity": float(capacity),
        "shift_start": shift_start,
        "shift_end": shift_end,
        "traffic": traffic,
        "fuel_type": fuel_type,
        "fuel_price": float(fuel_price),
        "driver_cost": float(driver_cost),
        "objective": objective_name,
        "delivery_rows": rows.to_dict(orient="records"),
    }


def _run_from_form(
    values: dict[str, object], qaoa_config: QAOAConfig, use_osrm: bool
) -> None:
    st.session_state["run_error"] = None
    try:
        stops = _delivery_stops(values["delivery_rows"])
        scenario = build_scenario(
            stops,
            vehicle_count=values["vehicle_count"],
            vehicle_capacity=values["capacity"],
            depot_name=values["depot_name"],
            depot_latitude=values["depot_latitude"],
            depot_longitude=values["depot_longitude"],
            shift_start_min=_time_to_minutes(values["shift_start"]),
            shift_end_min=_time_to_minutes(values["shift_end"]),
            traffic_condition=values["traffic"],
            fuel_type=values["fuel_type"],
            fuel_price_per_unit=values["fuel_price"],
            driver_cost_per_hour=values["driver_cost"],
            use_osrm=use_osrm,
        )
        previous = st.session_state.get("current_run")
        with st.spinner("Generating feasible routes and sampling QAOA on local Aer..."):
            objective_config = ObjectiveConfig.for_name(values["objective"])
            run = optimize_with_objective(scenario, objective_config, qaoa_config)
        st.session_state["traffic_comparison"] = (
            {"before": previous, "after": {"scenario": scenario, "run": run}}
            if previous and previous["scenario"].traffic_condition != scenario.traffic_condition
            else None
        )
        st.session_state["current_run"] = {"scenario": scenario, "run": run}
        st.session_state["selected_objective"] = run.objective_name
        st.session_state["dynamic_optimization_objective"] = run.objective_name
        st.session_state["fleet_optimization_objective"] = run.objective_name
        st.session_state["dynamic_comparison"] = None
    except (ValueError, TypeError, KeyError) as error:
        st.session_state["run_error"] = f"Scenario could not be optimized: {error}"
    except Exception as error:
        st.session_state["run_error"] = f"Optimization failed: {type(error).__name__}: {error}"


def _delivery_stops(rows: list[dict[str, object]]) -> tuple[DeliveryStop, ...]:
    stops = []
    for row in rows:
        destination = row.get("destination")
        if destination is None or pd.isna(destination) or not str(destination).strip():
            continue
        stops.append(
            DeliveryStop(
                destination=str(destination).strip(),
                latitude=float(row["latitude"]),
                longitude=float(row["longitude"]),
                demand=float(row["demand"]),
                window_start_min=_time_to_minutes(row["window_start"]),
                window_end_min=_time_to_minutes(row["window_end"]),
                service_duration_min=int(row["service_minutes"]),
            )
        )
    return tuple(stops)


def _time_to_minutes(value: object) -> int:
    if isinstance(value, time):
        return value.hour * 60 + value.minute
    if not isinstance(value, str):
        raise ValueError("times must use 24-hour HH:MM format")
    try:
        parsed = time.fromisoformat(value.strip())
    except ValueError as error:
        raise ValueError(f"invalid time {value!r}; expected HH:MM") from error
    return parsed.hour * 60 + parsed.minute


def _render_results(scenario: ScenarioProblem, run: OptimizationRun) -> None:
    st.markdown("### Optimization results")
    if run.quantum_error:
        st.warning(f"QAOA status: {run.quantum_error}")
    else:
        st.info(
            f"QAOA ran locally on Qiskit Aer. Travel data: {scenario.data_source}. "
            f"Traffic: {scenario.traffic_condition}."
        )
    if scenario.data_source.startswith("Local estimate (OSRM unavailable"):
        st.warning(scenario.data_source)
    _render_comparison(scenario, run)
    _render_fleet_view(scenario, run)


def _render_route_map(
    scenario: ScenarioProblem,
    run: OptimizationRun,
    comparison: TrafficReoptimization | None,
    key_prefix: str = "route_map",
) -> None:
    st.markdown("### Route map")
    map_col, map_info = st.columns([2.2, 1])
    with map_info:
        st.markdown("#### Route layers")
        show_classical = st.checkbox(
            "Classical routes",
            value=True,
            key=f"{key_prefix}_classical_routes",
        )
        show_quantum = st.checkbox(
            "QAOA / Aer routes",
            value=run.quantum is not None,
            disabled=run.quantum is None,
            key=f"{key_prefix}_quantum_routes",
        )
        st.caption("Before-incident routes are dashed; current routes are solid.")
    with map_col:
        route_map = build_route_map(
            scenario,
            run,
            show_classical,
            show_quantum,
            comparison,
            currency_display=_currency_display(),
        )
        st_folium(route_map.map, height=520, use_container_width=True, returned_objects=[])
        st.caption(route_map.geometry_status)


def _render_comparison(scenario: ScenarioProblem, run: OptimizationRun) -> None:
    st.markdown("#### Solver comparison")
    st.caption(
        f"Selected objective: {run.objective_name}. "
        f"{OBJECTIVE_DESCRIPTIONS[ObjectiveName(run.objective_name)]} "
        "Physical costs and impacts below remain separately measured."
    )
    classical_tab, quantum_tab = st.tabs(("Classical Optimization", "QAOA (Quantum)"))
    with classical_tab:
        _render_route_summary(
            "Classical exact",
            run.classical,
            scenario,
            _run_objective_value(run, quantum=False),
        )
    with quantum_tab:
        if run.quantum is None:
            st.markdown("### QAOA / Quantum")
            qaoa_message = run.quantum_error or "No valid QAOA route result was returned."
            if "not run" in qaoa_message.lower():
                st.info(
                    f"QAOA skipped: {qaoa_message.rstrip('.')} "
                    "(no substitute result was used)."
                )
            else:
                st.error(f"No valid QAOA route result: {qaoa_message}. No substitute result was used.")
        else:
            _render_route_summary(
                "QAOA / Aer",
                run.quantum,
                scenario,
                _run_objective_value(run, quantum=True),
            )
            quantum_result = run.quantum_result
            if quantum_result is not None:
                st.caption(
                    f"QUBO energy {quantum_result.qubo_energy:.3f} | "
                    f"sample probability {quantum_result.sample_probability:.3e} | "
                    f"{quantum_result.observed_unique_samples} unique samples"
                )

    if run.quantum is not None:
        delta_col, gap_col, match_col = st.columns(3)
        delta_col.metric("QAOA minus classical objective", f"{run.objective_delta:+.6f}")
        gap = "N/A" if run.relative_gap_percent is None else f"{run.relative_gap_percent:+.2f}%"
        gap_col.metric("Relative objective gap", gap)
        match_col.metric("Same objective", "Yes" if run.objective_delta == 0 else "No")


    st.markdown("#### Optimization Trade-offs")
    tradeoff_rows = []
    tradeoff_solvers = [
        ("Classical exact", run.classical, _run_objective_value(run, quantum=False))
    ]
    if run.quantum is not None:
        tradeoff_solvers.append(
            ("QAOA / Aer", run.quantum, _run_objective_value(run, quantum=True))
        )
    for solver, summary, objective_value in tradeoff_solvers:
        impact = calculate_route_impact(scenario, summary)
        tradeoff_rows.append(
            {
                "Solver": solver,
                "Selected objective value": f"{objective_value:.6f}",
                "Operating cost": _format_money(impact.cost),
                "Travel time (min)": f"{impact.travel_time_min:.1f}",
                "Distance (km)": f"{impact.distance_km:.2f}",
                f"Fuel ({impact.fuel_unit})": f"{impact.fuel_used:.3f}",
                "Estimated CO2 (kg)": f"{impact.tailpipe_co2_kg:.3f}",
                "Vehicles used": len(summary.routes),
                "Deliveries served": sum(len(route.plan.delivery_ids) for route in summary.routes),
            }
        )
    st.dataframe(pd.DataFrame(tradeoff_rows), hide_index=True, width="stretch")


def _run_objective_value(run: OptimizationRun, *, quantum: bool) -> float:
    value = run.quantum_objective_value if quantum else run.classical_objective_value
    summary = run.quantum if quantum else run.classical
    return value if value is not None else summary.total_cost


def _render_route_summary(
    label: str,
    summary: RouteSummary,
    scenario: ScenarioProblem,
    objective_value: float,
) -> None:
    st.markdown(f"### {label}")
    impact = calculate_route_impact(scenario, summary)
    metric_cols = st.columns(6)
    metric_cols[0].metric("Total cost", _format_money(impact.cost))
    metric_cols[1].metric("Distance", f"{impact.distance_km:.1f} km")
    metric_cols[2].metric("Travel time", _format_duration(impact.travel_time_min))
    metric_cols[3].metric("Fuel / energy", f"{impact.fuel_used:.2f} {impact.fuel_unit}")
    metric_cols[4].metric("Estimated CO2", f"{impact.tailpipe_co2_kg:.2f} kg")
    metric_cols[5].metric("Optimization objective", f"{objective_value:.6f}")
    st.success("Validated route set")
    for route in summary.routes:
        route_summary = RouteSummary(
            routes=(route,),
            total_cost=route.total_cost,
            total_distance_km=route.distance_km,
            total_travel_time_min=route.travel_time_min,
        )
        route_impact = calculate_route_impact(scenario, route_summary)
        destination_names = [
            scenario.location_names[delivery_id]
            for delivery_id in route.plan.delivery_ids
        ]
        route_text = " -> ".join([scenario.location_names["depot"], *destination_names, scenario.location_names["depot"]])
        with st.container(border=True):
            st.markdown(f"**{route.plan.vehicle_id}**  ·  Route order")
            st.write(route_text)
            st.caption(
                f"{route.distance_km:.1f} km | {route.travel_time_min:.0f} min travel | "
                f"{route.elapsed_time_min:.0f} min elapsed | {_format_money(route.total_cost)} | "
                f"{route_impact.fuel_used:.2f} {route_impact.fuel_unit} | "
                f"{route_impact.tailpipe_co2_kg:.2f} kg CO2"
            )


def _render_traffic_comparison(comparison: dict[str, object] | None) -> None:
    if comparison is None:
        return
    before = comparison["before"]
    after = comparison["after"]
    previous_scenario: ScenarioProblem = before["scenario"]
    next_scenario: ScenarioProblem = after["scenario"]
    previous_run: OptimizationRun = before["run"]
    next_run: OptimizationRun = after["run"]
    st.markdown("## Traffic Change Summary")
    st.caption(
        f"Traffic changed from {previous_scenario.traffic_condition} to "
        f"{next_scenario.traffic_condition}. Values compare the prior submitted run with this run."
    )
    before_col, after_col = st.columns(2, gap="large")
    _render_before_after_column(before_col, "Before", previous_scenario, previous_run)
    _render_before_after_column(after_col, "After", next_scenario, next_run)


def _render_before_after_column(
    column: st.delta_generator.DeltaGenerator,
    label: str,
    scenario: ScenarioProblem,
    run: OptimizationRun,
) -> None:
    with column:
        st.markdown(f"### {label} | {scenario.traffic_condition}")
        st.metric("Classical cost", _format_money(run.classical.total_cost))
        if run.quantum is None:
            st.caption("QAOA did not return a valid sample for this run.")
        else:
            st.metric("QAOA cost", _format_money(run.quantum.total_cost))
        st.caption(
            f"Classical: {run.classical.total_distance_km:.1f} km, "
            f"{run.classical.total_travel_time_min:.0f} min driving"
        )


def _render_dynamic_routing(qaoa_config: QAOAConfig) -> None:
    st.markdown("### Traffic incident simulator")
    st.caption(
        "Traffic incidents here are simulated scenario changes, not live traffic or GPS feeds. "
        "OSRM travel matrices are cached; local scenarios make no external routing calls."
    )
    current = st.session_state.get("current_run")
    if current is None:
        st.info("Run an initial scenario before applying a simulated traffic incident.")
        return

    scenario: ScenarioProblem = current["scenario"]
    objective_names = tuple(objective.value for objective in ObjectiveName)
    selected_objective = st.selectbox(
        "Optimization Objective",
        objective_names,
        index=objective_names.index(current["run"].objective_name),
        key="dynamic_optimization_objective",
    )
    if selected_objective != current["run"].objective_name:
        objective_config = ObjectiveConfig.for_name(selected_objective)
        with st.spinner(f"Re-optimizing the current scenario for {selected_objective}..."):
            current["run"] = optimize_with_objective(
                scenario,
                objective_config,
                qaoa_config,
            )
        st.session_state["current_run"] = current
        st.session_state["selected_objective"] = selected_objective
        st.session_state["dynamic_comparison"] = None
        st.session_state["traffic_comparison"] = None
        st.rerun()

    traffic_class, traffic_label = {
        "Calm": ("calm", "Calm traffic - low disruption"),
        "Normal": ("normal", "Normal traffic - baseline conditions"),
        "Moderate": ("moderate", "Moderate traffic - elevated delay"),
        "Heavy": ("heavy", "Heavy traffic - major delay"),
        "Storm": ("storm", "Storm traffic - severe disruption"),
    }[scenario.traffic_condition]
    st.markdown(
        f'<div class="qr-traffic qr-traffic-{traffic_class}"><span aria-hidden="true">●</span>'
        f'<span>{traffic_label}</span></div>',
        unsafe_allow_html=True,
    )
    condition_col, fleet_col, fuel_col, source_col = st.columns(4)
    condition_col.metric("Current traffic", scenario.traffic_condition)
    vehicle = scenario.vehicles[0]
    fleet_col.metric(
        "Fleet",
        f"{len(scenario.vehicles)} vehicles / {vehicle.capacity:g} capacity each",
    )
    fuel_col.metric(
        "Fuel / energy",
        f"{scenario.fuel_type} @ {scenario.fuel_price_per_unit:g}/{scenario.fuel_unit}",
    )
    source_col.metric(
        "Travel data",
        "OSRM road matrix"
        if scenario.osrm_active
        else "Local fallback"
        if scenario.osrm_requested
        else "Local estimate",
    )
    incident_names = {
        "Calm": "Traffic clears (Calm)",
        "Normal": "Normal traffic",
        "Moderate": "Moderate congestion",
        "Heavy": "Heavy congestion",
        "Storm": "Storm disruption",
    }
    traffic_levels = tuple(incident_names)
    current_index = (
        traffic_levels.index(scenario.traffic_condition)
        if scenario.traffic_condition in traffic_levels
        else traffic_levels.index("Moderate")
    )
    with st.form("simulated_traffic_incident"):
        selected_traffic = st.selectbox(
            "Simulated traffic incident",
            traffic_levels,
            index=current_index,
            format_func=incident_names.__getitem__,
        )
        submitted = st.form_submit_button(
            "Apply incident and re-optimize",
            type="primary",
            width="stretch",
        )

    if submitted:
        if selected_traffic == scenario.traffic_condition:
            st.info("Choose a different traffic condition to run a new optimization.")
        else:
            with st.spinner(
                f"Rebuilding travel times for {selected_traffic} traffic and rerunning both optimizers..."
            ):
                comparison = reoptimize_for_traffic(
                    scenario,
                    current["run"],
                    selected_traffic,
                    qaoa_config,
                    ObjectiveConfig.for_name(current["run"].objective_name),
                )
            st.session_state["dynamic_comparison"] = comparison
            st.session_state["traffic_comparison"] = None
            if comparison.after_run is not None:
                st.session_state["current_run"] = {
                    "scenario": comparison.after_scenario,
                    "run": comparison.after_run,
                }
                st.rerun()
            else:
                st.error(comparison.error or "Re-optimization failed; prior routes remain active.")


def _render_fleet_disruption(qaoa_config: QAOAConfig) -> None:
    st.markdown("### Vehicle availability and delivery priority")
    st.caption(
        "Breakdowns and traffic changes are scenario inputs. Feasible deliveries are selected "
        "Critical, High, Normal, then Low before the existing optimizers score the selected routes."
    )
    state = _fleet_disruption_state(qaoa_config)
    scenario: ScenarioProblem = state["scenario"]
    objective_names = tuple(objective.value for objective in ObjectiveName)
    selected_objective = st.selectbox(
        "Optimization Objective",
        objective_names,
        index=objective_names.index(state["objective_name"]),
        key="fleet_optimization_objective",
    )
    if selected_objective != state["objective_name"]:
        state["objective_name"] = selected_objective
        state["result"] = None
        st.session_state.pop("fleet_disruption_result", None)

    traffic_options = ("Calm", "Normal", "Moderate", "Heavy", "Storm")
    traffic_condition = st.selectbox(
        "Traffic for this re-optimization",
        traffic_options,
        index=traffic_options.index(state["traffic_condition"]),
        key="fleet_traffic_condition",
    )
    if traffic_condition != state["traffic_condition"]:
        state["traffic_condition"] = traffic_condition
        state["result"] = None
        st.session_state.pop("fleet_disruption_result", None)

    vehicle_rows = [
        {
            "Vehicle": vehicle.vehicle_id,
            "Capacity": vehicle.capacity,
            "Shift": f"{_format_time(vehicle.shift_start_min)}-{_format_time(vehicle.shift_end_min)}",
            "Status": state["availability"][vehicle.vehicle_id].value,
        }
        for vehicle in scenario.vehicles
    ]
    st.dataframe(pd.DataFrame(vehicle_rows), hide_index=True, width="stretch")
    selected_vehicle = st.selectbox(
        "Vehicle for status change",
        tuple(vehicle.vehicle_id for vehicle in scenario.vehicles),
        key="fleet_status_vehicle",
    )
    breakdown_col, restore_col = st.columns(2)
    if breakdown_col.button("Simulate Vehicle Breakdown", type="secondary", use_container_width=True):
        state["availability"][selected_vehicle] = VehicleStatus.UNAVAILABLE
        state["result"] = None
        st.session_state.pop("fleet_disruption_result", None)
        st.rerun()
    if restore_col.button("Restore Selected Vehicle", use_container_width=True):
        state["availability"][selected_vehicle] = VehicleStatus.AVAILABLE
        state["result"] = None
        st.session_state.pop("fleet_disruption_result", None)
        st.rerun()

    st.markdown("#### Delivery priorities")
    priority_values = tuple(priority.value for priority in DeliveryPriority)
    priority_columns = st.columns(2)
    priorities_changed = False
    for index, delivery in enumerate(scenario.deliveries):
        delivery_id = delivery.delivery_id
        value = priority_columns[index % 2].selectbox(
            f"{scenario.location_names[delivery_id]} priority",
            priority_values,
            index=priority_values.index(state["priorities"][delivery_id].value),
            key=f"fleet_priority_{delivery_id}",
        )
        priority = DeliveryPriority(value)
        if priority is not state["priorities"][delivery_id]:
            state["priorities"][delivery_id] = priority
            priorities_changed = True
    if priorities_changed:
        state["result"] = None
        st.session_state.pop("fleet_disruption_result", None)

    if st.button("Re-Optimize Fleet", type="primary", use_container_width=True):
        try:
            with st.spinner("Checking priority-feasible assignments, then rerunning classical and QAOA where supported..."):
                result = reoptimize_fleet(
                    scenario,
                    state["baseline_run"],
                    state["availability"],
                    state["priorities"],
                    state["traffic_condition"],
                    qaoa_config,
                    ObjectiveConfig.for_name(state["objective_name"]),
                )
            state["result"] = result
            st.session_state["fleet_disruption_result"] = result
        except Exception as error:
            state["result"] = None
            st.session_state.pop("fleet_disruption_result", None)
            st.error(f"Fleet re-optimization failed; no replacement routes were displayed: {error}")

    result: FleetDisruptionResult | None = state.get("result")
    if result is not None:
        _render_fleet_disruption_result(result)


def _fleet_disruption_state(qaoa_config: QAOAConfig) -> dict[str, object]:
    current = st.session_state.get("current_run")
    state = st.session_state.get("fleet_disruption_state")
    if state is not None:
        if current is not None and (
            current["scenario"] != state["scenario"]
            or current["run"].objective_name != state["baseline_run"].objective_name
        ):
            previous_statuses = state["availability"]
            previous_priorities = state["priorities"]
            scenario = current["scenario"]
            state["scenario"] = scenario
            state["baseline_run"] = current["run"]
            state["availability"] = {
                vehicle.vehicle_id: previous_statuses.get(
                    vehicle.vehicle_id, VehicleStatus.AVAILABLE
                )
                for vehicle in scenario.vehicles
            }
            state["priorities"] = {
                delivery.delivery_id: previous_priorities.get(
                    delivery.delivery_id, DEFAULT_PRIORITY
                )
                for delivery in scenario.deliveries
            }
            state["traffic_condition"] = scenario.traffic_condition
            state["objective_name"] = current["run"].objective_name
            state["result"] = None
            st.session_state["fleet_traffic_condition"] = scenario.traffic_condition
            st.session_state["fleet_optimization_objective"] = state["objective_name"]
            st.session_state.pop("fleet_disruption_result", None)
        return state

    if current is None:
        scenario = build_fleet_disruption_demo_scenario()
        with st.spinner("Preparing the 4-vehicle, 8-delivery disruption baseline..."):
            baseline_run = optimize_scenario(scenario, qaoa_config)
    else:
        scenario = current["scenario"]
        baseline_run = current["run"]
    default_priorities = DEMO_DELIVERY_PRIORITIES if current is None else {}
    state = {
        "scenario": scenario,
        "baseline_run": baseline_run,
        "availability": {
            vehicle.vehicle_id: VehicleStatus.AVAILABLE for vehicle in scenario.vehicles
        },
        "priorities": {
            delivery.delivery_id: default_priorities.get(
                delivery.delivery_id, DEFAULT_PRIORITY
            )
            for delivery in scenario.deliveries
        },
        "traffic_condition": scenario.traffic_condition,
        "objective_name": baseline_run.objective_name,
        "result": None,
    }
    st.session_state["fleet_disruption_state"] = state
    return state


def _render_fleet_disruption_result(result: FleetDisruptionResult) -> None:
    st.markdown("## Before vs After fleet plan")
    st.caption(
        f"Traffic: {result.before_scenario.traffic_condition} -> "
        f"{result.after_scenario.traffic_condition}. Costs are display-converted; "
        "route optimization values remain in USD base units."
    )
    if result.error:
        st.error(result.error)
        return
    if result.after_run is None or result.after_metrics is None:
        st.error("No after-route result is available; the prior plan remains the only valid plan.")
        return

    before_col, after_col = st.columns(2)
    with before_col:
        _render_fleet_metric_group("Before disruption", result.before_metrics)
    with after_col:
        _render_fleet_metric_group("After disruption", result.after_metrics)

    if result.reassignments:
        st.markdown("#### Deliveries moved from unavailable vehicles")
        names = result.before_scenario.location_names
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Delivery": names[item.delivery_id],
                        "From vehicle": item.from_vehicle,
                        "To vehicle": item.to_vehicle,
                        "Priority": result.delivery_priorities[item.delivery_id].value,
                    }
                    for item in result.reassignments
                ]
            ),
            hide_index=True,
            width="stretch",
        )
    else:
        st.info("No deliveries moved from an unavailable vehicle in this result.")

    _render_waitlist(result)
    if result.feasibility_status != "Complete":
        st.warning(f"Feasibility status: {result.feasibility_status}")
    if result.after_run.quantum is not None:
        st.success("QAOA returned a route set that passed the existing feasibility validation.")

    st.markdown("#### Updated routes and solver comparison")
    _render_comparison(result.optimization_scenario, result.after_run)
    _render_fleet_view(result.optimization_scenario, result.after_run)
    _render_route_map(result.after_scenario, result.after_run, None)


def _render_fleet_metric_group(label: str, metrics) -> None:
    st.markdown(f"#### {label}")
    columns = st.columns(2)
    columns[0].metric("Vehicles available", metrics.vehicles_available)
    columns[1].metric(
        "Deliveries served / unassigned",
        f"{metrics.deliveries_served} / {metrics.deliveries_unassigned}",
    )
    columns[0].metric("Total distance", f"{metrics.total_distance_km:.1f} km")
    columns[1].metric("Travel time", _format_duration(metrics.total_travel_time_min))
    columns[0].metric("Total cost", _format_money(metrics.total_cost))
    columns[1].metric("Fuel / energy", f"{metrics.fuel_used:.2f} {metrics.fuel_unit}")
    columns[0].metric("Estimated CO2", f"{metrics.tailpipe_co2_kg:.2f} kg")


def _render_waitlist(result: FleetDisruptionResult) -> None:
    st.markdown("#### Unassigned / waitlist")
    if not result.unassigned_deliveries:
        st.success("All deliveries were assigned to validated routes.")
        return
    names = result.after_scenario.location_names
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Delivery": names[item.delivery_id],
                    "Priority": item.priority.value,
                    "Reason": item.reason,
                }
                for item in result.unassigned_deliveries
            ]
        ),
        hide_index=True,
        width="stretch",
    )


def _render_demo_mode() -> None:
    st.markdown("## End-to-end Quantum Logistics Demonstration")
    st.caption(
        "A fixed local scenario runs through classical/QAOA planning, objective trade-offs, "
        "simulated traffic, and fleet disruption. No live traffic or routing APIs are used."
    )
    run_col, reset_col = st.columns([3, 1])
    run_demo = run_col.button(
        "▶ Run Full Demo",
        type="primary",
        use_container_width=True,
        key="run_full_hackathon_demo",
    )
    reset_demo = reset_col.button(
        "↻ Reset Demo",
        use_container_width=True,
        key="reset_hackathon_demo",
    )
    if reset_demo:
        reset_demo_state(st.session_state)
        st.rerun()

    if run_demo:
        stage_order = tuple(DemoStage)
        stage_index = {stage: index + 1 for index, stage in enumerate(stage_order)}
        try:
            with st.status("Running deterministic demonstration...", expanded=True) as status:
                progress = st.progress(0.0)

                def report_stage(stage: DemoStage) -> None:
                    st.session_state["hackathon_demo_stage"] = stage
                    progress.progress(stage_index[stage] / len(stage_order))
                    status.write(f"Completed: {stage.value}")

                st.session_state.pop("hackathon_demo_error", None)
                st.session_state["hackathon_demo_result"] = run_full_demo(
                    DEMO_QAOA_CONFIG,
                    progress_callback=report_stage,
                )
                status.update(
                    label="QuantumRoute Demonstration Complete",
                    state="complete",
                    expanded=False,
                )
        except Exception as error:
            st.session_state["hackathon_demo_result"] = None
            st.session_state["hackathon_demo_error"] = (
                f"Demo stopped without substituting results: {type(error).__name__}: {error}"
            )

    demo_error = st.session_state.get("hackathon_demo_error")
    if demo_error:
        st.error(demo_error)
    demo: HackathonDemoResult | None = st.session_state.get("hackathon_demo_result")
    if demo is None:
        st.info("Run Full Demo to calculate the baseline and each re-optimization stage.")
        _render_quantum_execution_comparison(None)
        return

    _render_demo_baseline(demo)
    _render_demo_quantum_optimization(demo)
    _render_demo_traffic(demo)
    _render_demo_fleet(demo)
    _render_demo_objectives(demo)
    _render_demo_sustainability(demo)
    _render_quantum_execution_comparison(demo)
    _render_demo_summary(demo)


def _render_quantum_execution_comparison(
    demo: HackathonDemoResult | None,
) -> None:
    st.markdown("## 06 / Quantum Execution Comparison · DEMO")
    job_state: HardwareJobState | None = st.session_state.get("ibm_hardware_job_state")
    dry_run: HardwareDryRunResult | None = st.session_state.get("ibm_hardware_dry_run")
    if (
        job_state is None
        or not job_state.job_id
        or job_state.status.upper() != "DONE"
        or not job_state.measurement_result_received
        or dry_run is None
        or dry_run.execution_package is None
    ):
        st.info("No completed IBM Quantum hardware result available for this session.")
        return

    if demo is None:
        st.info("Run Full Demo to compare the completed hardware result with local and classical results.")
        return

    if not _completed_hardware_result_compatible(job_state, dry_run, demo):
        st.warning(
            "The completed IBM hardware result belongs to a different scenario; "
            "its metrics are not combined with the current Demo Mode results."
        )
        st.caption("The captured hardware measurement distribution remains available in the IBM Quantum Hardware workspace.")
        return

    st.caption(
        "Classical Optimization → Local Aer QAOA → Real IBM Quantum Hardware. "
        "This is an actual Qiskit workflow executed on IBM Quantum hardware. "
        "This small demo is stochastic; matching results do not demonstrate quantum advantage."
    )
    st.markdown("#### REAL IBM QUANTUM HARDWARE")
    st.write(
        f"Backend: {job_state.backend_name} | Shots: {job_state.shots} | "
        f"Status: {job_state.status} | Job ID: {job_state.job_id}"
    )
    counts = pd.DataFrame(
        [
            {"Measured bitstring": bitstring, "Count": count}
            for bitstring, count in job_state.raw_counts
        ]
    )
    if not counts.empty:
        with st.expander("Hardware measurement distribution", expanded=False):
            st.dataframe(counts, hide_index=True, width="stretch")
            st.caption(f"Most frequent bitstring: {job_state.most_frequent_bitstring}")

    if job_state.route_valid is not True:
        st.error("Hardware result did not produce a valid route.")
        st.caption(job_state.route_message or "No hardware route metrics are mixed into this comparison.")
        return

    st.success("Real IBM hardware result passed feasibility validation.")
    scenario = demo.scenario
    classical = demo.baseline_run.classical
    local_qaoa = demo.baseline_run.quantum
    hardware = _route_summary_from_routes(job_state.decoded_routes)
    columns = st.columns(3)
    _render_execution_comparison_column(columns[0], "CLASSICAL OPTIMIZATION", scenario, classical)
    if local_qaoa is not None:
        _render_execution_comparison_column(columns[1], "LOCAL AER QAOA", scenario, local_qaoa)
    else:
        columns[1].markdown("#### LOCAL AER QAOA")
        columns[1].info("No valid local Aer QAOA result is available for comparison.")
    _render_execution_comparison_column(
        columns[2],
        "REAL IBM QUANTUM HARDWARE",
        scenario,
        hardware,
        backend=job_state.backend_name,
        shots=job_state.shots,
        status=job_state.status,
        job_id=job_state.job_id,
    )

    if local_qaoa is not None and _execution_metrics_match(
        scenario,
        (classical, local_qaoa, hardware),
    ):
        st.info("The three sets of route metrics matched for this demo case.")
    st.caption("Hardware measurements are stochastic; no approach is ranked.")


def _render_execution_comparison_column(
    column,
    heading: str,
    scenario: ScenarioProblem,
    summary: RouteSummary,
    *,
    backend: str | None = None,
    shots: int | None = None,
    status: str | None = None,
    job_id: str | None = None,
) -> None:
    column.markdown(f"#### {heading}")
    impact = calculate_route_impact(scenario, summary)
    column.caption(impact.route_description or "No route")
    column.metric("Distance", f"{impact.distance_km:.2f} km")
    column.metric("Travel time", _format_duration(impact.travel_time_min))
    column.metric("Cost", _format_money(impact.cost))
    column.metric("Fuel", f"{impact.fuel_used:.3f} {impact.fuel_unit}")
    column.metric("CO2", f"{impact.tailpipe_co2_kg:.3f} kg")
    if backend is not None:
        column.caption(f"Backend: {backend} | Shots: {shots} | Status: {status} | Job ID: {job_id}")


def _execution_metrics_match(
    scenario: ScenarioProblem,
    summaries: tuple[RouteSummary, ...],
) -> bool:
    impacts = tuple(calculate_route_impact(scenario, summary) for summary in summaries)
    reference = impacts[0]
    for impact in impacts[1:]:
        if (
            not isclose(reference.distance_km, impact.distance_km, abs_tol=0.01)
            or not isclose(reference.travel_time_min, impact.travel_time_min, abs_tol=0.5)
            or not isclose(reference.cost, impact.cost, abs_tol=0.01)
            or not isclose(reference.fuel_used, impact.fuel_used, abs_tol=0.001)
            or not isclose(reference.tailpipe_co2_kg, impact.tailpipe_co2_kg, abs_tol=0.001)
        ):
            return False
    return True


def _completed_hardware_result_compatible(
    job_state: HardwareJobState,
    dry_run: HardwareDryRunResult | None,
    demo: HackathonDemoResult | None,
) -> bool:
    if (
        demo is None
        or job_state.status.upper() != "DONE"
        or not job_state.job_id
        or not job_state.measurement_result_received
        or dry_run is None
        or dry_run.execution_package is None
    ):
        return False
    package = dry_run.execution_package
    return (
        dry_run.ready
        and dry_run.backend is not None
        and dry_run.backend.name == job_state.backend_name
        and dry_run.shots == job_state.shots
        and package.parameter_source == "local_aer_optimized"
        and demo.scenario == build_hackathon_demo_scenario()
        and package.problem_signature == route_qubo_fingerprint(build_demo_route_qubo())
        and package.problem.route_variable_count == 4
    )


def _render_demo_baseline(demo: HackathonDemoResult) -> None:
    scenario = demo.scenario
    run = demo.baseline_run
    st.markdown("## 01 / BASELINE · DEMO")
    st.caption(
        f"Scenario: {scenario.location_names['depot']} → "
        f"{' · '.join(scenario.location_names[item.delivery_id] for item in scenario.deliveries)}"
    )
    detail_columns = st.columns(4)
    detail_columns[0].metric("Fleet size", len(scenario.vehicles))
    detail_columns[1].metric("Deliveries", len(scenario.deliveries))
    detail_columns[2].metric("Traffic condition", scenario.traffic_condition)
    detail_columns[3].metric("Selected objective", run.objective_name)

    st.caption(
        f"Depot: {scenario.location_names['depot']} | Fuel: {scenario.fuel_type} | "
        f"Fuel price input: {_format_money(scenario.fuel_price_per_unit)} / {scenario.fuel_unit} | "
        f"Travel data: {scenario.data_source}"
    )
    fleet_rows = [
        {
            "Vehicle": vehicle.vehicle_id,
            "Capacity": vehicle.capacity,
            "Shift": f"{_format_time(vehicle.shift_start_min)}-{_format_time(vehicle.shift_end_min)}",
        }
        for vehicle in scenario.vehicles
    ]
    vehicle_column, delivery_column = st.columns(2)
    with vehicle_column:
        st.markdown("#### Vehicles")
        st.dataframe(pd.DataFrame(fleet_rows), hide_index=True, width="stretch")
    stop_rows = [
        {
            "Destination": scenario.location_names[delivery.delivery_id],
            "Demand": delivery.demand,
            "Priority": DEMO_DELIVERY_PRIORITIES[delivery.delivery_id].value,
            "Time window": (
                f"{_format_time(delivery.window_start_min)}-"
                f"{_format_time(delivery.window_end_min)}"
            ),
        }
        for delivery in scenario.deliveries
    ]
    with delivery_column:
        st.markdown("#### Deliveries")
        st.dataframe(pd.DataFrame(stop_rows), hide_index=True, width="stretch")

    st.markdown("### Classical and local Aer results")
    result_columns = st.columns(2, gap="large")
    with result_columns[0].container(border=True):
        _render_execution_comparison_column(
            result_columns[0], "CLASSICAL OPTIMIZATION", scenario, run.classical
        )
        result_columns[0].success("Route passed feasibility validation.")
    with result_columns[1].container(border=True):
        if run.quantum is None:
            result_columns[1].markdown("#### LOCAL AER QAOA")
            result_columns[1].info(
                f"No valid local Aer route is available: "
                f"{run.quantum_error or 'no QAOA result was returned.'}"
            )
        else:
            _render_execution_comparison_column(
                result_columns[1], "LOCAL AER QAOA", scenario, run.quantum
            )
            if demo.baseline_qaoa.valid:
                result_columns[1].success("Route passed feasibility validation.")
            else:
                result_columns[1].warning(demo.baseline_qaoa.message)

    st.caption(
        "Matching classical and QAOA results on this small demo do not demonstrate quantum advantage."
    )
    with st.expander("Baseline route map", expanded=False):
        _render_route_map(scenario, run, None, key_prefix="demo_baseline")


def _render_demo_quantum_optimization(demo: HackathonDemoResult) -> None:
    st.markdown("## 02 / QUANTUM OPTIMIZATION · DEMO")
    st.caption(
        "The same validated route problem is followed through its existing QUBO and local QAOA workflow."
    )
    stages = st.columns(5, gap="small")
    explanations = (
        ("Classical optimizer", "Builds and validates feasible vehicle routes."),
        (
            "QUBO formulation",
            "Binary route variables; penalties enforce exact delivery coverage and one route per vehicle.",
        ),
        ("QAOA", f"Variational circuit at depth {DEMO_QAOA_CONFIG.reps} samples route selections."),
        ("Local simulation", "Qiskit Aer samples the circuit locally; measurements are stochastic."),
        ("Decode + validate", "Measured bits map to routes, which pass the existing feasibility checks."),
    )
    for column, (heading, explanation) in zip(stages, explanations):
        with column.container(border=True):
            column.markdown(f"#### {heading}")
            column.caption(explanation)

    status = demo.baseline_qaoa
    if status.valid:
        st.success(
            f"Local Aer returned a feasible route from {demo.baseline_candidate_count} route variables."
        )
        if status.sample_probability is not None and status.unique_samples is not None:
            st.caption(
                f"Observed sample probability: {status.sample_probability:.6f} | "
                f"Unique samples: {status.unique_samples}"
            )
    else:
        st.info(status.message)
    st.caption("This is a workflow demonstration, not evidence of quantum advantage.")


def _render_demo_objectives(demo: HackathonDemoResult) -> None:
    st.markdown("## 05 / SUSTAINABILITY + OBJECTIVE TRADE-OFFS · DEMO")
    st.caption(
        "Compare Cost, Time, Green, and Balanced priorities using the same scenario. "
        "Different objectives trade distance, travel time, operating cost, fuel, and CO2; there is no universal best route."
    )
    rows = [
        {
            "Objective": result.objective.value,
            "Normalized objective": f"{result.objective_value:.6f}",
            "Distance (km)": f"{result.impact.distance_km:.2f}",
            "Time (min)": f"{result.impact.travel_time_min:.1f}",
            "Cost": _format_money(result.impact.cost),
            f"Fuel ({result.impact.fuel_unit})": f"{result.impact.fuel_used:.3f}",
            "CO2 (kg)": f"{result.impact.tailpipe_co2_kg:.3f}",
        }
        for result in demo.objective_results
    ]
    with st.container(border=True):
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        st.caption(
            "Objective comparisons use the existing classical optimizer. Physical values remain "
            "separate from each dimensionless normalized objective score."
        )


def _render_demo_traffic(demo: HackathonDemoResult) -> None:
    st.markdown("## 03 / DYNAMIC TRAFFIC · SIMULATED DEMO")
    st.caption("Normal → Heavy traffic. Scenario simulation only; this is not live traffic or GPS data.")
    comparison = demo.traffic_result
    if comparison.classical is None or comparison.after_run is None:
        st.error(comparison.error or "Traffic re-optimization did not return a validated result.")
        return

    before_column, after_column = st.columns(2, gap="large")
    for column, label, condition, impact in (
        (
            before_column,
            "BEFORE",
            comparison.before_scenario.traffic_condition,
            comparison.classical.before,
        ),
        (
            after_column,
            "AFTER",
            comparison.after_scenario.traffic_condition,
            comparison.classical.after,
        ),
    ):
        with column.container(border=True):
            column.markdown(f"#### {label} · {condition.upper()} TRAFFIC")
            column.caption(impact.route_description or "No route")
            column.metric("Distance", f"{impact.distance_km:.2f} km")
            column.metric("Travel time", _format_duration(impact.travel_time_min))
            column.metric("Cost", _format_money(impact.cost))

    with st.expander("Traffic re-optimization map", expanded=False):
        _render_route_map(
            comparison.after_scenario,
            comparison.after_run,
            comparison,
            key_prefix="demo_traffic",
        )


def _render_demo_fleet(demo: HackathonDemoResult) -> None:
    st.markdown("## 04 / FLEET DISRUPTION · DEMO")
    fleet = demo.fleet_result
    st.caption(
        f"Simulated breakdown: {DEMO_BREAKDOWN_VEHICLE} unavailable. "
        f"Traffic remains {fleet.after_scenario.traffic_condition}."
    )
    if fleet.after_run is None or fleet.after_metrics is None:
        st.error(fleet.error or "Fleet re-optimization did not return a validated result.")
        return

    names = fleet.optimization_scenario.location_names
    reassigned = [
        f"{names[item.delivery_id]}: {item.from_vehicle} → {item.to_vehicle}"
        for item in fleet.reassignments
    ]
    st.markdown("#### Reassigned deliveries")
    st.write("\n".join(f"- {item}" for item in reassigned) if reassigned else "No deliveries required reassignment.")

    metrics = fleet.after_metrics
    metric_columns = st.columns(4)
    metric_columns[0].metric("Deliveries served", metrics.deliveries_served)
    metric_columns[1].metric("Waitlisted", metrics.deliveries_unassigned)
    metric_columns[2].metric("Vehicles available", metrics.vehicles_available)
    metric_columns[3].metric("Feasibility", fleet.feasibility_status)
    next_row = st.columns(4)
    next_row[0].metric("Distance", f"{metrics.total_distance_km:.2f} km")
    next_row[1].metric("Travel time", _format_duration(metrics.total_travel_time_min))
    next_row[2].metric("Cost", _format_money(metrics.total_cost))
    next_row[3].metric(
        "Fuel / CO2",
        f"{metrics.fuel_used:.3f} {metrics.fuel_unit} / {metrics.tailpipe_co2_kg:.3f} kg",
    )
    route_impact = calculate_route_impact(
        fleet.optimization_scenario,
        fleet.after_run.classical,
    )
    st.caption(f"Updated route: {route_impact.route_description or 'No route'}")
    if fleet.unassigned_deliveries:
        _render_waitlist(fleet)
    else:
        st.success("All deliveries were served; none were waitlisted.")
    with st.expander("Updated fleet route map", expanded=False):
        _render_route_map(
            fleet.optimization_scenario,
            fleet.after_run,
            None,
            key_prefix="demo_fleet",
        )


def _render_demo_sustainability(demo: HackathonDemoResult) -> None:
    st.markdown("#### Fleet sustainability snapshot")
    summary = demo.sustainability
    metrics = summary.metrics
    st.caption(f"Optimization Objective Used: {summary.objective_name}")
    metric_columns = st.columns(4)
    metric_columns[0].metric("Total distance", f"{metrics.total_distance_km:.2f} km")
    metric_columns[1].metric(
        "Travel time",
        _format_duration(metrics.total_travel_time_min),
    )
    metric_columns[2].metric("Operating cost", _format_money(metrics.total_cost))
    metric_columns[3].metric(
        "Fuel consumption",
        f"{metrics.fuel_used:.3f} {metrics.fuel_unit}",
    )
    final_columns = st.columns(3)
    final_columns[0].metric("CO2 emissions", f"{metrics.tailpipe_co2_kg:.3f} kg")
    final_columns[1].metric("Deliveries served", metrics.deliveries_served)
    final_columns[2].metric("Deliveries unassigned", metrics.deliveries_unassigned)
    if metrics.deliveries_served != summary.baseline_deliveries_served:
        st.warning(summary.cost_comparability_note)
    else:
        st.caption(summary.cost_comparability_note)


def _render_demo_summary(demo: HackathonDemoResult) -> None:
    st.markdown("## 07 / FINAL SUMMARY · DEMO")
    with st.container(border=True):
        st.markdown("### QuantumRoute — From Classical Routing to Real Quantum Hardware")
        st.caption("A small demonstration of constrained routing, hybrid optimization, and operational analysis.")
        pipeline = (
            "Real-world constraints",
            "Classical feasibility",
            "QUBO formulation",
            "QAOA",
            "Local quantum simulation",
            "IBM Quantum hardware",
            "Validated route",
            "Traffic / fleet re-optimization",
            "Sustainability analysis",
        )
        pipeline_markup = "".join(
            f'<div class="qr-demo-flow-step">{step}</div>'
            + ('<div class="qr-demo-flow-arrow">&darr;</div>' if index < len(pipeline) - 1 else "")
            for index, step in enumerate(pipeline)
        )
        st.markdown(
            f'<div class="qr-demo-pipeline">{pipeline_markup}</div>',
            unsafe_allow_html=True,
        )

        job_state: HardwareJobState | None = st.session_state.get("ibm_hardware_job_state")
        if job_state is not None and job_state.job_id and job_state.status.upper() == "DONE":
            st.caption(
                f"Hardware evidence: {job_state.backend_name} · {job_state.shots} shots · "
                f"{job_state.status}. Measurements are stochastic; this is not a claim of quantum advantage."
            )
        else:
            st.caption("Hardware evidence: no completed IBM Quantum result is available for this session.")

        summary = demo.sustainability.metrics
        metric_columns = st.columns(5)
        metric_columns[0].metric("Fleet distance", f"{summary.total_distance_km:.2f} km")
        metric_columns[1].metric("Travel time", _format_duration(summary.total_travel_time_min))
        metric_columns[2].metric("Operating cost", _format_money(summary.total_cost))
        metric_columns[3].metric("Fuel", f"{summary.fuel_used:.3f} {summary.fuel_unit}")
        metric_columns[4].metric("Tailpipe CO2", f"{summary.tailpipe_co2_kg:.3f} kg")
        st.caption(
            f"Fleet outcome: {summary.deliveries_served} deliveries served, "
            f"{summary.deliveries_unassigned} waitlisted. {demo.sustainability.cost_comparability_note}"
        )
    st.success("QuantumRoute Demonstration Complete")


def _render_ibm_quantum_hardware() -> None:
    st.markdown("## IBM Quantum Hardware")
    st.caption(
        "Discovery remains read-only. No quantum circuits or jobs are submitted from this workspace until you explicitly confirm a REAL IBM hardware job."
    )
    job_state: HardwareJobState = st.session_state.get(
        "ibm_hardware_job_state",
        HardwareJobState(),
    )
    st.session_state["ibm_hardware_job_state"] = job_state
    discover_clicked = st.button(
        "Discover IBM Backends",
        type="secondary",
        key="discover_ibm_backends",
    )
    if discover_clicked:
        st.session_state.pop("selected_ibm_backend", None)
        st.session_state.pop("ibm_hardware_dry_run", None)
        st.session_state.pop("ibm_hardware_local_comparison", None)
        st.session_state.pop("ibm_hardware_dry_run_backend", None)
        st.session_state.pop("ibm_hardware_dry_run_shots", None)
        st.session_state.pop("ibm_hardware_submission_confirmed", None)
        with st.spinner("Reading backend metadata from the connected IBM Quantum account..."):
            try:
                st.session_state["ibm_backend_discovery"] = discover_ibm_backends()
            except Exception:
                st.session_state["ibm_backend_discovery"] = None
                st.error(
                    "IBM backend discovery could not be completed. Check credentials and service availability."
                )

    discovery: IBMBackendDiscovery | None = st.session_state.get(
        "ibm_backend_discovery"
    )
    if discovery is None:
        st.info("Select Discover IBM Backends to request read-only backend metadata.")
        if job_state.job_id is None:
            st.info("No job submitted yet.")
        return

    status_report = ibm_quantum_status_report(discovery)
    if discovery.status.value == "backend_discovery_successful":
        st.success(status_report)
    elif discovery.status.value == "no_accessible_backends":
        st.warning(status_report)
    else:
        st.error(status_report)

    if not discovery.backends:
        return

    rows = [_ibm_backend_display_row(backend) for backend in discovery.backends]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    st.caption("Backend discovery is read-only; backend execution is not available here.")

    backend_names = tuple(backend.name for backend in discovery.backends)
    previous_selection = st.session_state.get("selected_ibm_backend")
    if previous_selection is not None and previous_selection not in backend_names:
        st.warning("The previous backend is no longer available. Select one from the latest discovery results.")
        st.session_state.pop("selected_ibm_backend", None)

    selected_name = st.selectbox(
        "Select discovered backend",
        backend_names,
        key="selected_ibm_backend",
    )
    selected_backend = _find_ibm_backend(selected_name, discovery.backends)
    if selected_backend is None:
        st.warning("The selected backend is not in the latest discovery results. Run discovery again.")
        return
    _render_selected_ibm_backend(selected_backend)

    shots = st.number_input(
        "Hardware shots",
        min_value=1,
        max_value=MAX_SHOTS,
        value=DEFAULT_SHOTS,
        step=1,
        key="ibm_hardware_shots",
    )
    job_exists = job_state.submission_attempted or job_state.job_id is not None
    can_preflight = (
        selected_backend.operational is True
        and selected_backend.simulator is False
        and not job_exists
    )
    run_dry = st.button(
        "Run Hardware Dry-Run",
        disabled=not can_preflight,
        key="run_ibm_hardware_dry_run",
    )
    if run_dry:
        st.session_state.pop("ibm_hardware_submission_confirmed", None)
        st.session_state.pop("ibm_hardware_dry_run", None)
        st.session_state.pop("ibm_hardware_local_comparison", None)
        try:
            with st.spinner("Checking IBM access and preparing the four-variable Demo Mode circuit locally..."):
                connection = connect_ibm_quantum()
                if not connection.connected:
                    st.session_state["ibm_hardware_dry_run_error"] = ibm_quantum_status_report(connection)
                else:
                    parameters = optimize_demo_qaoa_parameters(DEFAULT_DEMO_QAOA_CONFIG)
                    dry_run = dry_run_optimized_qaoa_execution(
                        connection,
                        selected_backend.name,
                        parameters,
                        int(shots),
                    )
                    st.session_state["ibm_hardware_dry_run"] = dry_run
                    st.session_state["ibm_hardware_dry_run_backend"] = selected_backend.name
                    st.session_state["ibm_hardware_dry_run_shots"] = int(shots)
                    st.session_state["ibm_hardware_dry_run_error"] = None
                    if dry_run.ready:
                        try:
                            scenario = build_hackathon_demo_scenario()
                            local_comparison = optimize_with_objective(
                                scenario,
                                ObjectiveConfig.for_name(ObjectiveName.COST),
                                DEFAULT_DEMO_QAOA_CONFIG,
                            )
                            st.session_state["ibm_hardware_local_comparison"] = local_comparison
                        except Exception:
                            st.session_state["ibm_hardware_local_comparison"] = None
        except Exception:
            st.session_state["ibm_hardware_dry_run"] = None
            st.session_state["ibm_hardware_dry_run_error"] = (
                "Hardware dry-run failed safely. No hardware job was submitted. "
                "Check IBM credentials, backend availability, and local circuit compatibility."
            )

    dry_run_error = st.session_state.get("ibm_hardware_dry_run_error")
    if dry_run_error:
        st.error(dry_run_error)
    dry_run: HardwareDryRunResult | None = st.session_state.get("ibm_hardware_dry_run")
    dry_run_matches = (
        dry_run is not None
        and st.session_state.get("ibm_hardware_dry_run_backend") == selected_backend.name
        and st.session_state.get("ibm_hardware_dry_run_shots") == int(shots)
    )
    if dry_run_matches and dry_run is not None:
        if dry_run.ready:
            _render_ibm_pre_submission_confirmation(dry_run)
            confirmed = st.checkbox(
                "I understand this submits one REAL IBM Quantum hardware job.",
                key="ibm_hardware_submission_confirmed",
                disabled=job_exists,
            )
            submit_clicked = st.button(
                "Submit ONE REAL IBM Quantum Job",
                type="primary",
                disabled=not confirmed or job_exists,
                key="submit_one_ibm_hardware_job",
            )
            if submit_clicked and confirmed and not job_exists:
                try:
                    connection = connect_ibm_quantum()
                    job_state = submit_confirmed_hardware_job(
                        connection,
                        dry_run,
                        confirmed=True,
                        state=job_state,
                    )
                except Exception:
                    job_state.message = (
                        "Submission could not be verified. No retry was attempted; "
                        "check IBM Quantum job history before taking further action."
                    )
                    job_state.submission_attempted = True
                    job_state.status = "SUBMISSION_UNKNOWN"
                st.session_state["ibm_hardware_job_state"] = job_state
        else:
            st.error(dry_run.message)

    if job_state.job_id:
        _render_ibm_job_state(job_state)
        if st.button("Refresh Job Status", key="refresh_ibm_hardware_job"):
            try:
                connection = connect_ibm_quantum()
                execution_package = st.session_state.get("ibm_hardware_dry_run").execution_package
                job_state = refresh_hardware_job_status(
                    connection,
                    job_state,
                    execution_package,
                )
            except Exception:
                job_state.message = "Could not refresh the existing IBM job. No new job was submitted."
            st.session_state["ibm_hardware_job_state"] = job_state
            st.rerun()
        if job_state.measurement_result_received:
            _render_ibm_hardware_result(job_state)
            if _completed_hardware_result_compatible(
                job_state,
                st.session_state.get("ibm_hardware_dry_run"),
                st.session_state.get("hackathon_demo_result"),
            ):
                st.info("Comparison available in Demo Mode.")
    elif not job_state.submission_attempted:
        st.info("No job submitted yet.")
    else:
        st.warning(job_state.message)


def _render_ibm_pre_submission_confirmation(dry_run: HardwareDryRunResult) -> None:
    package = dry_run.execution_package
    if package is None or dry_run.backend is None:
        st.error("Dry-run package is incomplete; hardware submission is unavailable.")
        return
    st.success("Dry-run passed. No remote job has been submitted.")
    st.markdown("#### Pre-submission confirmation")
    st.write("Problem: QuantumRoute Demo Mode")
    st.write(f"Route variables: {package.problem.route_variable_count}")
    st.write(f"Backend: {dry_run.backend.name} (REAL IBM QUANTUM HARDWARE)")
    st.write(f"Required qubits: {dry_run.circuit_qubits}")
    st.write(f"Shots: {dry_run.shots}")
    st.write(f"QAOA depth: {package.qaoa_reps}")
    st.warning(
        "Confirming will submit a REAL IBM Quantum job. IBM queue and execution time may vary. "
        "The hardware measurement is not a guaranteed optimal solution."
    )


def _render_ibm_job_state(job_state: HardwareJobState) -> None:
    st.markdown("### REAL IBM QUANTUM HARDWARE JOB")
    st.write(f"Job ID: {job_state.job_id}")
    st.write(f"Backend: {job_state.backend_name}")
    st.write(f"Status: {job_state.status}")
    st.write(f"Shots: {job_state.shots}")
    if job_state.submitted_at:
        st.caption(f"Submitted: {job_state.submitted_at}")
    st.caption(job_state.message)


def _render_ibm_hardware_result(job_state: HardwareJobState) -> None:
    st.markdown("### REAL IBM QUANTUM HARDWARE RESULT")
    counts = pd.DataFrame(
        [
            {"Measured bitstring": bitstring, "Count": count}
            for bitstring, count in job_state.raw_counts
        ]
    )
    if not counts.empty:
        st.dataframe(counts, hide_index=True, width="stretch")
    st.write(f"Most frequent measured bitstring: {job_state.most_frequent_bitstring}")
    if job_state.route_valid is False:
        st.error("Hardware result did not produce a valid route.")
        st.caption(job_state.route_message or "Raw measurements are retained above; no replacement route was used.")
    elif job_state.route_valid is True:
        st.success(job_state.route_message or "Hardware route passed existing feasibility validation.")
        scenario = build_hackathon_demo_scenario()
        hardware_summary = _route_summary_from_routes(job_state.decoded_routes)
        _render_hardware_route_metrics(
            "REAL IBM QUANTUM HARDWARE RESULT",
            scenario,
            hardware_summary,
        )

    local_comparison = st.session_state.get("ibm_hardware_local_comparison")
    scenario = build_hackathon_demo_scenario()
    if local_comparison is not None:
        if local_comparison.quantum is not None:
            _render_hardware_route_metrics(
                "LOCAL AER QAOA RESULT",
                scenario,
                local_comparison.quantum,
            )
        else:
            st.info("LOCAL AER QAOA RESULT unavailable; no valid local sampled route was returned.")
        _render_hardware_route_metrics(
            "CLASSICAL RESULT",
            scenario,
            local_comparison.classical,
        )
    else:
        st.info("Local QAOA/classical comparison was unavailable; no substitute was used.")
    st.caption(
        "A result from this small demonstration does not establish quantum advantage. "
        "Hardware measurements are stochastic and may differ from local Aer and classical results."
    )


def _route_summary_from_routes(routes) -> RouteSummary:
    return RouteSummary(
        routes=tuple(routes),
        total_cost=sum(route.total_cost for route in routes),
        total_distance_km=sum(route.distance_km for route in routes),
        total_travel_time_min=sum(route.travel_time_min for route in routes),
    )


def _render_hardware_route_metrics(
    heading: str,
    scenario: ScenarioProblem,
    summary: RouteSummary,
) -> None:
    st.markdown(f"#### {heading}")
    impact = calculate_route_impact(scenario, summary)
    metric_columns = st.columns(5)
    metric_columns[0].metric("Distance", f"{impact.distance_km:.2f} km")
    metric_columns[1].metric("Travel time", _format_duration(impact.travel_time_min))
    metric_columns[2].metric("Cost", _format_money(impact.cost))
    metric_columns[3].metric("Fuel", f"{impact.fuel_used:.3f} {impact.fuel_unit}")
    metric_columns[4].metric("CO2", f"{impact.tailpipe_co2_kg:.3f} kg")
    st.caption(impact.route_description)


def _find_ibm_backend(
    backend_name: str | None,
    backends: tuple[IBMBackendMetadata, ...],
) -> IBMBackendMetadata | None:
    if backend_name is None:
        return None
    return next((backend for backend in backends if backend.name == backend_name), None)


def _render_selected_ibm_backend(backend: IBMBackendMetadata) -> None:
    st.markdown("#### Selected backend capabilities")
    if backend.simulator is True:
        st.info(f"{backend.name} is an IBM Quantum simulator.")
        backend_type = "Simulator"
    elif backend.simulator is False:
        st.success(f"{backend.name} is real IBM quantum hardware.")
        backend_type = "Real hardware"
    else:
        st.warning(f"Hardware classification is unavailable for {backend.name}.")
        backend_type = "Unknown"

    operational = (
        "Operational"
        if backend.operational is True
        else "Not operational"
        if backend.operational is False
        else "Unknown"
    )
    columns = st.columns(4)
    columns[0].metric("Backend type", backend_type)
    columns[1].metric(
        "Qubits",
        backend.num_qubits if backend.num_qubits is not None else "Unknown",
    )
    columns[2].metric("Availability", operational)
    columns[3].metric(
        "Pending jobs",
        backend.pending_jobs if backend.pending_jobs is not None else "Not reported",
    )
    if backend.status_message:
        st.caption(f"Status: {backend.status_message}")
    if backend.supported_operations:
        st.caption("Supported operations: " + ", ".join(backend.supported_operations))


def _ibm_backend_display_row(backend: IBMBackendMetadata) -> dict[str, object]:
    if backend.simulator is True:
        backend_type = "Simulator"
    elif backend.simulator is False:
        backend_type = "Real hardware"
    else:
        backend_type = "Unknown"

    if backend.operational is True:
        operational = "Operational"
    elif backend.operational is False:
        operational = "Not operational"
    else:
        operational = "Unknown"

    return {
        "Backend": backend.name,
        "Type": backend_type,
        "Qubits": str(backend.num_qubits) if backend.num_qubits is not None else "Unknown",
        "Availability": operational,
        "Status": backend.status_message or "Not reported",
        "Pending jobs": (
            str(backend.pending_jobs)
            if backend.pending_jobs is not None
            else "Not reported"
        ),
        "Supported operations": (
            ", ".join(backend.supported_operations)
            if backend.supported_operations
            else "Not reported"
        ),
    }


def _render_dynamic_comparison(comparison: TrafficReoptimization) -> None:
    st.markdown("## 07 / Before vs After")
    st.caption(
        f"Simulated traffic changed from {comparison.before_scenario.traffic_condition} "
        f"to {comparison.after_scenario.traffic_condition}. Changes are After - Before."
    )
    if comparison.after_run is None:
        st.error(comparison.error or "Re-optimization failed; no after result is available.")
        return

    before_objective = _run_objective_value(comparison.before_run, quantum=False)
    after_objective = _run_objective_value(comparison.after_run, quantum=False)
    st.caption(
        f"Selected objective: {comparison.after_run.objective_name}. "
        f"Classical normalized objective: {before_objective:.6f} -> {after_objective:.6f}."
    )

    _render_impact_change("Classical exact", comparison.classical)
    if comparison.quantum is not None:
        _render_impact_change("QAOA / Aer", comparison.quantum)
    else:
        st.warning(
            "QAOA before/after metrics are unavailable because at least one run "
            "did not return a valid sampled route set. No substitute route was used."
        )
    st.caption(
        "Fuel and CO2 are estimates from configured per-kilometer consumption; CO2 is tailpipe-only "
        "(electric vehicles report zero tailpipe emissions, not zero lifecycle emissions)."
    )


def _render_impact_change(label: str, change) -> None:
    if change is None:
        return
    before = change.before
    after = change.after
    rows = [
        {
            "Metric": "Route order",
            "Before": before.route_description,
            "After": after.route_description,
            "Change": "Changed" if before.route_description != after.route_description else "Unchanged",
        },
        {
            "Metric": "Distance",
            "Before": f"{before.distance_km:.2f} km",
            "After": f"{after.distance_km:.2f} km",
            "Change": f"{change.distance_change_km:+.2f} km",
        },
        {
            "Metric": "Travel time",
            "Before": f"{before.travel_time_min:.1f} min",
            "After": f"{after.travel_time_min:.1f} min",
            "Change": f"{change.travel_time_change_min:+.1f} min",
        },
        {
            "Metric": "Cost",
            "Before": _format_money(before.cost),
            "After": _format_money(after.cost),
            "Change": _format_money_delta(change.cost_change),
        },
        {
            "Metric": f"Fuel / energy ({before.fuel_unit})",
            "Before": f"{before.fuel_used:.3f}",
            "After": f"{after.fuel_used:.3f}",
            "Change": f"{change.fuel_change:+.3f}",
        },
        {
            "Metric": "Estimated CO2 (tailpipe only)",
            "Before": f"{before.tailpipe_co2_kg:.3f} kg",
            "After": f"{after.tailpipe_co2_kg:.3f} kg",
            "Change": f"{change.tailpipe_co2_change_kg:+.3f} kg",
        },
    ]
    st.markdown(f"#### {label}")
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def _render_fleet_view(scenario: ScenarioProblem, run: OptimizationRun) -> None:
    st.markdown("## 08 / Fleet Routes")
    solver_options = ["Classical exact"]
    if run.quantum is not None:
        solver_options.append("QAOA / Aer")
    selected_solver = st.selectbox("Fleet result", solver_options, key="fleet_solver_view")
    summary = run.classical if selected_solver == "Classical exact" else run.quantum
    delivery_map = {delivery.delivery_id: delivery for delivery in scenario.deliveries}
    vehicle_map = {vehicle.vehicle_id: vehicle for vehicle in scenario.vehicles}
    assigned = [
        delivery_id
        for route in summary.routes
        for delivery_id in route.plan.delivery_ids
    ]
    if len(assigned) != len(set(assigned)) or set(assigned) != set(delivery_map):
        st.error("Fleet result failed the unique-delivery assignment check and is not displayed.")
        return

    rows = []
    for route in summary.routes:
        vehicle = vehicle_map[route.plan.vehicle_id]
        demand_used = sum(delivery_map[item].demand for item in route.plan.delivery_ids)
        rows.append(
            {
                "Vehicle": route.plan.vehicle_id,
                "Assigned deliveries": " -> ".join(
                    scenario.location_names[item] for item in route.plan.delivery_ids
                ),
                "Capacity used": f"{demand_used:g} / {vehicle.capacity:g}",
                "Remaining capacity": f"{vehicle.capacity - demand_used:g}",
                "Route distance (km)": route.distance_km,
                "Travel time (min)": route.travel_time_min,
                "Cost": _format_money(route.total_cost),
                "Valid": "Yes",
            }
        )
    st.success("Every displayed vehicle route was revalidated; each delivery is assigned exactly once.")
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def _render_benchmark_mode(qaoa_config: QAOAConfig) -> None:
    st.markdown("## 10 / Benchmark Mode")
    st.caption(
        "Run deterministic small routing cases and record measured solver-plus-validation "
        "wall times. Each scenario uses its fixed input seed and a reproducible QAOA seed. "
        "Benchmark objective is fixed to Cost Priority for comparability."
    )
    case_names = [case.name for case in generate_benchmark_scenarios()]
    selected = st.selectbox(
        "Benchmark scenario",
        ("All scenarios", *case_names),
        key="benchmark_scenario_selection",
    )
    if st.button("Run Benchmark", type="primary", key="run_benchmark"):
        try:
            with st.spinner("Running the selected exact and QAOA/Aer experiments..."):
                results = run_benchmark(
                    None if selected == "All scenarios" else (selected,),
                    qaoa_config,
                )
            st.session_state["benchmark_results"] = results
            st.session_state["benchmark_error"] = None
        except Exception as error:
            st.session_state["benchmark_error"] = (
                f"Benchmark run failed: {type(error).__name__}: {error}"
            )

    benchmark_error = st.session_state.get("benchmark_error")
    if benchmark_error:
        st.error(benchmark_error)
    results = st.session_state.get("benchmark_results")
    if not results:
        st.info("Choose a scenario or All scenarios, then run the benchmark.")
        _render_benchmark_interpretation()
        return

    st.dataframe(_benchmark_table(results), hide_index=True, width="stretch")
    objective_col, runtime_col = st.columns(2, gap="large")
    with objective_col:
        st.markdown("### Objective comparison")
        st.altair_chart(
            _benchmark_chart(results, "objective", "Objective / cost"),
            width="stretch",
        )
    with runtime_col:
        st.markdown("### Runtime comparison")
        st.altair_chart(
            _benchmark_chart(results, "runtime", "Seconds (solver + validation)"),
            width="stretch",
        )
    skipped = [result for result in results if not result.qaoa_executed]
    if skipped:
        st.warning(
            "QAOA was skipped for: "
            + "; ".join(
                f"{result.scenario_name} ({result.qaoa_message})" for result in skipped
            )
        )
    _render_benchmark_interpretation()


def _benchmark_table(results: tuple[BenchmarkResult, ...]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Scenario": result.scenario_name,
                "Difficulty": result.difficulty,
                "Vehicles": result.vehicle_count,
                "Deliveries": result.delivery_count,
                "Route variables": result.route_variable_count,
                "Classical objective": _format_money(result.classical_objective),
                "QAOA objective": (
                    None if result.qaoa_objective is None else _format_money(result.qaoa_objective)
                ),
                "Objective gap": (
                    None if result.objective_gap is None else _format_money_delta(result.objective_gap)
                ),
                "Relative gap (%)": result.relative_gap_percent,
                "Classical runtime (s)": result.classical_runtime_seconds,
                "QAOA runtime (s)": result.qaoa_runtime_seconds,
                "QAOA sample probability": result.qaoa_sample_probability,
                "Unique QAOA samples": result.qaoa_unique_samples,
                "Classical routes valid": result.classical_routes_valid,
                "QAOA routes valid": result.qaoa_routes_valid,
                "QAOA executed": result.qaoa_executed,
                "QAOA status": result.qaoa_status,
                "QAOA detail": result.qaoa_message,
                "Scenario seed": result.scenario_seed,
                "QAOA seed": result.qaoa_seed,
            }
            for result in results
        ]
    )


def _benchmark_chart(
    results: tuple[BenchmarkResult, ...], metric: str, axis_title: str
) -> alt.Chart:
    currency = _currency_display()
    if metric == "objective":
        axis_title = f"Objective ({currency.display_currency_code})"
    chart_rows = []
    for result in results:
        classical_value = (
            currency.convert_amount(result.classical_objective)
            if metric == "objective"
            else result.classical_runtime_seconds
        )
        chart_rows.append(
            {
                "Scenario": result.scenario_name,
                "Solver": "Classical exact",
                "Series": f"{result.scenario_name} | Classical exact",
                "Value": classical_value,
            }
        )
        quantum_value = result.qaoa_runtime_seconds
        if metric == "objective" and result.qaoa_objective is not None:
            quantum_value = currency.convert_amount(result.qaoa_objective)
        if quantum_value is not None:
            chart_rows.append(
                {
                    "Scenario": result.scenario_name,
                    "Solver": "QAOA / Aer",
                    "Series": f"{result.scenario_name} | QAOA / Aer",
                    "Value": quantum_value,
                }
            )

    chart_data = pd.DataFrame(chart_rows)
    return (
        alt.Chart(chart_data)
        .mark_point(filled=True, size=90)
        .encode(
            x=alt.X("Value:Q", title=axis_title),
            y=alt.Y("Series:N", title=None, axis=alt.Axis(labelLimit=300)),
            color=alt.Color(
                "Solver:N",
                scale=alt.Scale(
                    domain=["Classical exact", "QAOA / Aer"],
                    range=["#087f5b", "#e76f51"],
                ),
                legend=alt.Legend(orient="top"),
            ),
            tooltip=[
                alt.Tooltip("Scenario:N", title="Scenario"),
                alt.Tooltip("Solver:N", title="Solver"),
                alt.Tooltip("Value:Q", title=axis_title, format=".4f"),
            ],
        )
        .properties(height=max(260, len(chart_rows) * 30))
        .configure(
            background="transparent",
            view={"stroke": "transparent"},
            axis={"labelColor": "#c5d1e2", "titleColor": "#c5d1e2", "gridColor": "#29364d"},
            legend={"labelColor": "#c5d1e2", "titleColor": "#c5d1e2"},
        )
    )


def _render_benchmark_interpretation() -> None:
    st.markdown("### What this experiment means")
    st.markdown(
        "- The classical optimizer provides the exact baseline for these small instances.\n"
        "- QAOA is evaluated with Qiskit Aer on this machine.\n"
        "- Matching objectives mean QAOA found an equally good objective for that scenario.\n"
        "- A non-zero objective gap means QAOA found a different objective.\n"
        "- Runtime measurements describe this local setup and should not be generalized."
    )


def _format_duration(minutes: float) -> str:
    hours = int(minutes // 60)
    remainder = int(round(minutes % 60))
    if remainder == 60:
        hours += 1
        remainder = 0
    return f"{hours} h {remainder:02d} min" if hours else f"{remainder} min"