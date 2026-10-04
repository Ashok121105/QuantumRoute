"""Core models and classical optimization for the route optimizer."""

from .classical import (
    NoFeasibleSolutionError,
    OptimizationResult,
    generate_feasible_routes,
    optimize_classically,
)
from .costs import calculate_route_cost
from .feasibility import RouteInfeasibleError, evaluate_route
from .models import (
    Delivery,
    FeasibleRoute,
    RouteCostWeights,
    RoutePlan,
    TravelData,
    Vehicle,
)
from .qubo import RouteQubo, build_route_qubo
from .qaoa import (
    InvalidQuantumSampleError,
    NoFeasibleQuantumSampleError,
    QAOAConfig,
    QuantumOptimizationResult,
    decode_route_selection,
    solve_qaoa,
)
from .benchmark import OptimizationComparison, compare_classical_and_qaoa

__all__ = [
    "Delivery",
    "FeasibleRoute",
    "NoFeasibleSolutionError",
    "NoFeasibleQuantumSampleError",
    "InvalidQuantumSampleError",
    "OptimizationComparison",
    "OptimizationResult",
    "QAOAConfig",
    "QuantumOptimizationResult",
    "RouteCostWeights",
    "RouteInfeasibleError",
    "RoutePlan",
    "RouteQubo",
    "TravelData",
    "Vehicle",
    "calculate_route_cost",
    "build_route_qubo",
    "compare_classical_and_qaoa",
    "decode_route_selection",
    "evaluate_route",
    "generate_feasible_routes",
    "optimize_classically",
    "solve_qaoa",
]