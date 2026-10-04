"""Reproducible small-instance comparison of the existing classical and QAOA solvers."""

from dataclasses import dataclass, replace
from math import isclose
from random import Random
from time import perf_counter
from typing import Sequence

from quantum_route_optimisation import (
    QAOAConfig,
    generate_feasible_routes,
    optimize_classically,
    solve_qaoa,
)

from .optimization import MAX_LOCAL_QAOA_VARIABLES, validate_route_set
from .scenario import DeliveryStop, ScenarioProblem, build_scenario


@dataclass(frozen=True)
class BenchmarkCase:
    name: str
    difficulty: str
    seed: int
    problem: ScenarioProblem


@dataclass(frozen=True)
class BenchmarkResult:
    scenario_name: str
    difficulty: str
    scenario_seed: int
    qaoa_seed: int | None
    vehicle_count: int
    delivery_count: int
    route_variable_count: int
    classical_objective: float
    qaoa_objective: float | None
    objective_gap: float | None
    relative_gap_percent: float | None
    classical_runtime_seconds: float
    qaoa_runtime_seconds: float | None
    qaoa_sample_probability: float | None
    qaoa_unique_samples: int | None
    classical_routes_valid: bool
    qaoa_routes_valid: bool | None
    qaoa_executed: bool
    qaoa_status: str
    qaoa_message: str | None = None


BENCHMARK_CASES = (
    ("Small / 2 deliveries", "Small", 2, 1, 2.0, "Light", (1.0, 1.0), 101, 150),
    ("Medium / 3 deliveries", "Medium", 3, 2, 2.2, "Moderate", (1.0, 1.1, 0.9), 202, 135),
    ("Large / 4 deliveries", "Large", 4, 2, 2.0, "Heavy", (1.0, 1.0, 1.0, 1.0), 303, 105),
    ("Larger / 5 deliveries", "Larger", 5, 2, 3.0, "Severe", (1.0, 1.0, 1.0, 1.0, 1.0), 404, 90),
)


def generate_benchmark_scenarios() -> tuple[BenchmarkCase, ...]:
    """Build the fixed benchmark suite using per-case deterministic coordinate seeds."""
    cases: list[BenchmarkCase] = []
    depot_latitude = 37.7749
    depot_longitude = -122.4194

    for name, difficulty, delivery_count, vehicle_count, capacity, traffic, demands, seed, window_span in BENCHMARK_CASES:
        rng = Random(seed)
        stops = tuple(
            DeliveryStop(
                destination=f"{difficulty} stop {index + 1}",
                latitude=depot_latitude + rng.uniform(-0.018, 0.018),
                longitude=depot_longitude + rng.uniform(-0.018, 0.018),
                demand=demands[index],
                window_start_min=480 + index * 12 + rng.randint(0, 12),
                window_end_min=480 + index * 12 + rng.randint(0, 12) + window_span,
                service_duration_min=5 + index % 3 * 3,
            )
            for index in range(delivery_count)
        )
        problem = build_scenario(
            stops,
            vehicle_count=vehicle_count,
            vehicle_capacity=capacity,
            depot_name="Benchmark depot",
            depot_latitude=depot_latitude,
            depot_longitude=depot_longitude,
            shift_start_min=480,
            shift_end_min=1020,
            traffic_condition=traffic,
            fuel_type="Diesel",
            fuel_price_per_unit=1.10,
        )
        cases.append(BenchmarkCase(name, difficulty, seed, problem))

    return tuple(cases)


def calculate_objective_gaps(
    classical_objective: float, qaoa_objective: float | None
) -> tuple[float | None, float | None]:
    """Return absolute and relative gaps, omitting relative gaps at a zero baseline."""
    if qaoa_objective is None:
        return None, None
    gap = qaoa_objective - classical_objective
    relative_gap = (
        None
        if isclose(classical_objective, 0.0, abs_tol=1e-12)
        else gap / classical_objective * 100.0
    )
    return gap, relative_gap


def run_benchmark(
    selected_scenarios: Sequence[str] | None = None,
    qaoa_config: QAOAConfig | None = None,
) -> tuple[BenchmarkResult, ...]:
    """Run the exact baseline and QAOA/Aer for selected built-in cases."""
    config = qaoa_config or QAOAConfig()
    cases = generate_benchmark_scenarios()
    selected = set(selected_scenarios) if selected_scenarios is not None else None
    unknown = selected.difference(case.name for case in cases) if selected is not None else set()
    if unknown:
        raise ValueError(f"unknown benchmark scenario(s): {sorted(unknown)}")
    chosen_cases = tuple(
        case for case in cases if selected is None or case.name in selected
    )
    if not chosen_cases:
        raise ValueError("select at least one benchmark scenario")

    return tuple(_run_case(case, config) for case in chosen_cases)


def _run_case(case: BenchmarkCase, config: QAOAConfig) -> BenchmarkResult:
    problem = case.problem
    route_options = generate_feasible_routes(
        problem.vehicles,
        problem.deliveries,
        problem.travel,
        problem.cost_weights,
    )
    variable_count = len(route_options)

    classical_start = perf_counter()
    classical = optimize_classically(
        problem.vehicles,
        problem.deliveries,
        problem.travel,
        problem.cost_weights,
    )
    classical_routes = validate_route_set(classical.routes, problem)
    classical_runtime = perf_counter() - classical_start
    classical_objective = sum(route.total_cost for route in classical_routes)

    if variable_count > MAX_LOCAL_QAOA_VARIABLES:
        return BenchmarkResult(
            scenario_name=case.name,
            difficulty=case.difficulty,
            scenario_seed=case.seed,
            qaoa_seed=None,
            vehicle_count=len(problem.vehicles),
            delivery_count=len(problem.deliveries),
            route_variable_count=variable_count,
            classical_objective=classical_objective,
            qaoa_objective=None,
            objective_gap=None,
            relative_gap_percent=None,
            classical_runtime_seconds=classical_runtime,
            qaoa_runtime_seconds=None,
            qaoa_sample_probability=None,
            qaoa_unique_samples=None,
            classical_routes_valid=True,
            qaoa_routes_valid=None,
            qaoa_executed=False,
            qaoa_status="Skipped (variable limit)",
            qaoa_message=(
                f"{variable_count} route variables exceeds the local Aer limit "
                f"of {MAX_LOCAL_QAOA_VARIABLES}"
            ),
        )

    quantum_config = replace(config, seed=(config.seed + case.seed) % 2_147_483_648)
    qaoa_start = perf_counter()
    try:
        quantum = solve_qaoa(
            problem.vehicles,
            problem.deliveries,
            problem.travel,
            problem.cost_weights,
            quantum_config,
        )
        qaoa_routes = validate_route_set(quantum.routes, problem)
        qaoa_runtime = perf_counter() - qaoa_start
        qaoa_objective = sum(route.total_cost for route in qaoa_routes)
        gap, relative_gap = calculate_objective_gaps(classical_objective, qaoa_objective)
        return BenchmarkResult(
            scenario_name=case.name,
            difficulty=case.difficulty,
            scenario_seed=case.seed,
            qaoa_seed=quantum_config.seed,
            vehicle_count=len(problem.vehicles),
            delivery_count=len(problem.deliveries),
            route_variable_count=variable_count,
            classical_objective=classical_objective,
            qaoa_objective=qaoa_objective,
            objective_gap=gap,
            relative_gap_percent=relative_gap,
            classical_runtime_seconds=classical_runtime,
            qaoa_runtime_seconds=qaoa_runtime,
            qaoa_sample_probability=quantum.sample_probability,
            qaoa_unique_samples=quantum.observed_unique_samples,
            classical_routes_valid=True,
            qaoa_routes_valid=True,
            qaoa_executed=True,
            qaoa_status="Executed",
        )
    except Exception as error:
        qaoa_runtime = perf_counter() - qaoa_start
        return BenchmarkResult(
            scenario_name=case.name,
            difficulty=case.difficulty,
            scenario_seed=case.seed,
            qaoa_seed=quantum_config.seed,
            vehicle_count=len(problem.vehicles),
            delivery_count=len(problem.deliveries),
            route_variable_count=variable_count,
            classical_objective=classical_objective,
            qaoa_objective=None,
            objective_gap=None,
            relative_gap_percent=None,
            classical_runtime_seconds=classical_runtime,
            qaoa_runtime_seconds=qaoa_runtime,
            qaoa_sample_probability=None,
            qaoa_unique_samples=None,
            classical_routes_valid=True,
            qaoa_routes_valid=False,
            qaoa_executed=True,
            qaoa_status=f"Failed ({type(error).__name__})",
            qaoa_message=str(error),
        )