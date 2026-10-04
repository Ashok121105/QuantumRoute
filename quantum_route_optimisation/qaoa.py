"""Aer-backed QAOA execution and strict decoding into validated vehicle routes."""

from dataclasses import dataclass, field
from math import pi
from typing import Any, Sequence

import numpy as np
from qiskit import generate_preset_pass_manager
from qiskit_aer import AerSimulator
from qiskit_aer.primitives import SamplerV2
from qiskit_algorithms import QAOA
from qiskit_algorithms.optimizers import COBYLA
from qiskit_optimization.algorithms import MinimumEigenOptimizer

from .classical import generate_feasible_routes
from .feasibility import RouteInfeasibleError, evaluate_route
from .models import (
    Delivery,
    FeasibleRoute,
    RouteCostWeights,
    TravelData,
    Vehicle,
)
from .qubo import RouteQubo, build_route_qubo


class InvalidQuantumSampleError(ValueError):
    """Raised when a sampled bit vector does not encode a feasible route set."""


class NoFeasibleQuantumSampleError(RuntimeError):
    """Raised when QAOA returns no sampled route set that passes validation."""


@dataclass(frozen=True)
class QAOAConfig:
    reps: int = 1
    maxiter: int = 30
    shots: int = 1024
    seed: int = 7

    def __post_init__(self) -> None:
        if self.reps < 1:
            raise ValueError("reps must be at least 1")
        if self.maxiter < 1:
            raise ValueError("maxiter must be at least 1")
        if self.shots < 1:
            raise ValueError("shots must be at least 1")


@dataclass(frozen=True)
class DecodedRouteSelection:
    routes: tuple[FeasibleRoute, ...]
    total_cost: float
    total_distance_km: float


@dataclass(frozen=True)
class QuantumOptimizationResult:
    routes: tuple[FeasibleRoute, ...]
    objective_value: float
    total_distance_km: float
    qubo_energy: float
    sample_probability: float
    observed_unique_samples: int
    is_valid: bool


@dataclass(frozen=True)
class LocalQAOAOptimizerRun:
    """The QAOA instance and public MinimumEigenOptimizer result from one Aer run."""

    qaoa: QAOA = field(repr=False, compare=False)
    optimizer_result: Any = field(repr=False, compare=False)


def decode_route_selection(
    bit_vector: Sequence[float],
    formulation: RouteQubo,
    vehicles: Sequence[Vehicle],
    deliveries: Sequence[Delivery],
    travel: TravelData,
    cost_weights: RouteCostWeights,
) -> DecodedRouteSelection:
    """Decode one route-variable assignment and recheck it with Day 1 logic."""
    if len(bit_vector) != len(formulation.routes):
        raise InvalidQuantumSampleError("sample length does not match the QUBO variables")

    selected_indexes: list[int] = []
    for index, value in enumerate(bit_vector):
        rounded = round(float(value))
        if not np.isclose(value, rounded, atol=1e-6) or rounded not in (0, 1):
            raise InvalidQuantumSampleError("sample values must be binary")
        if rounded:
            selected_indexes.append(index)
    if not selected_indexes:
        raise InvalidQuantumSampleError("sample selects no routes")

    selected_candidates = [formulation.routes[index] for index in selected_indexes]
    covered = [
        delivery_id
        for route in selected_candidates
        for delivery_id in route.plan.delivery_ids
    ]
    if len(covered) != len(set(covered)):
        raise InvalidQuantumSampleError("sample serves a delivery more than once")
    if set(covered) != set(formulation.delivery_ids):
        raise InvalidQuantumSampleError("sample does not cover every delivery exactly once")
    vehicle_ids = [route.plan.vehicle_id for route in selected_candidates]
    if len(vehicle_ids) != len(set(vehicle_ids)):
        raise InvalidQuantumSampleError("sample assigns multiple routes to one vehicle")

    vehicle_map = {vehicle.vehicle_id: vehicle for vehicle in vehicles}
    delivery_map = {delivery.delivery_id: delivery for delivery in deliveries}
    validated_routes: list[FeasibleRoute] = []
    try:
        for candidate in selected_candidates:
            vehicle = vehicle_map[candidate.plan.vehicle_id]
            validated_routes.append(
                evaluate_route(
                    vehicle,
                    candidate.plan,
                    delivery_map,
                    travel,
                    cost_weights,
                )
            )
    except (KeyError, RouteInfeasibleError) as error:
        raise InvalidQuantumSampleError(f"decoded route failed feasibility checks: {error}") from error

    ordered_routes = tuple(sorted(validated_routes, key=lambda route: route.plan.vehicle_id))
    return DecodedRouteSelection(
        routes=ordered_routes,
        total_cost=sum(route.total_cost for route in ordered_routes),
        total_distance_km=sum(route.distance_km for route in ordered_routes),
    )


def run_qaoa_optimizer(
    formulation: RouteQubo,
    config: QAOAConfig = QAOAConfig(),
) -> LocalQAOAOptimizerRun:
    """Run the existing seeded QAOA/Aer stack and retain its public raw result."""
    simulator = AerSimulator()
    pass_manager = generate_preset_pass_manager(
        backend=simulator,
        optimization_level=1,
    )
    sampler = SamplerV2(default_shots=config.shots, seed=config.seed)
    initial_point = np.random.default_rng(config.seed).uniform(
        -pi,
        pi,
        2 * config.reps,
    )
    qaoa = QAOA(
        sampler=sampler,
        optimizer=COBYLA(maxiter=config.maxiter),
        reps=config.reps,
        initial_point=initial_point,
        transpiler=pass_manager,
    )
    result = MinimumEigenOptimizer(qaoa).solve(formulation.problem)
    return LocalQAOAOptimizerRun(qaoa=qaoa, optimizer_result=result)


def solve_qaoa(
    vehicles: Sequence[Vehicle],
    deliveries: Sequence[Delivery],
    travel: TravelData,
    cost_weights: RouteCostWeights,
    config: QAOAConfig = QAOAConfig(),
) -> QuantumOptimizationResult:
    """Generate Qiskit QAOA samples on Aer, then return the best valid route set."""
    feasible_routes = generate_feasible_routes(vehicles, deliveries, travel, cost_weights)
    formulation = build_route_qubo(feasible_routes, deliveries)
    result = run_qaoa_optimizer(formulation, config).optimizer_result

    valid_samples: list[tuple[DecodedRouteSelection, float, float]] = []
    for sample in result.samples:
        try:
            decoded = decode_route_selection(
                sample.x,
                formulation,
                vehicles,
                deliveries,
                travel,
                cost_weights,
            )
        except InvalidQuantumSampleError:
            continue
        valid_samples.append(
            (decoded, float(sample.fval), float(sample.probability))
        )

    if not valid_samples:
        raise NoFeasibleQuantumSampleError(
            f"QAOA produced no valid route sample among {len(result.samples)} unique samples"
        )

    valid_samples.sort(
        key=lambda item: (
            item[0].total_cost,
            tuple((route.plan.vehicle_id, route.plan.delivery_ids) for route in item[0].routes),
        )
    )
    best, qubo_energy, probability = valid_samples[0]
    return QuantumOptimizationResult(
        routes=best.routes,
        objective_value=best.total_cost,
        total_distance_km=best.total_distance_km,
        qubo_energy=qubo_energy,
        sample_probability=probability,
        observed_unique_samples=len(result.samples),
        is_valid=True,
    )