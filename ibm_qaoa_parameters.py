"""Extract genuine local Aer QAOA parameters for a matching route QUBO.

No IBM service or execution API is imported or called from this module.
"""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import Enum
from math import isclose, isfinite
from numbers import Real
from typing import Any

from ibm_qaoa_adapter import (
    QAOAExecutionPackage,
    RouteVariableBinding,
    build_demo_route_qubo,
    prepare_route_qaoa_execution_package,
    qaoa_parameter_alias,
    qaoa_parameter_names,
    route_qubo_fingerprint,
)
from quantum_route_optimisation.qaoa import (
    LocalQAOAOptimizerRun,
    QAOAConfig,
    run_qaoa_optimizer,
)
from quantum_route_optimisation.qubo import RouteQubo


class QAOAParameterProvenance(str, Enum):
    LOCAL_AER_OPTIMIZED = "local_aer_optimized"
    CALLER_SUPPLIED = "caller_supplied"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class QAOAParameterProblemMetadata:
    delivery_ids: tuple[str, ...]
    variable_names: tuple[str, ...]
    route_variable_count: int
    penalty_weight: float
    problem_signature: str
    route_variable_mapping: tuple[RouteVariableBinding, ...]


@dataclass(frozen=True)
class QAOAParameterResult:
    provenance: QAOAParameterProvenance
    parameter_names: tuple[str, ...]
    parameter_values: tuple[float, ...]
    qaoa_reps: int
    optimizer_energy: float | None
    problem: QAOAParameterProblemMetadata
    validation_passed: bool
    validation_message: str
    execution_package: QAOAExecutionPackage | None = None

    @property
    def available(self) -> bool:
        return self.provenance is not QAOAParameterProvenance.UNAVAILABLE


DEFAULT_DEMO_QAOA_CONFIG = QAOAConfig(reps=1, maxiter=20, shots=2048, seed=42)


def optimize_demo_qaoa_parameters(
    config: QAOAConfig = DEFAULT_DEMO_QAOA_CONFIG,
) -> QAOAParameterResult:
    """Run the existing seeded local Aer QAOA on the Demo Mode's exact route QUBO."""
    formulation = build_demo_route_qubo()
    try:
        optimizer_run = run_qaoa_optimizer(formulation, config)
    except Exception:
        return _unavailable_result(
            formulation,
            config.reps,
            "The local Aer optimizer did not return optimized QAOA parameters.",
        )
    return extract_local_qaoa_parameters(optimizer_run, formulation, config.reps)


def extract_local_qaoa_parameters(
    optimizer_run: LocalQAOAOptimizerRun,
    formulation: RouteQubo,
    reps: int,
) -> QAOAParameterResult:
    """Extract public optimizer output and bind it only to the same RouteQubo."""
    problem = _problem_metadata(formulation)
    try:
        solver_result = optimizer_run.optimizer_result.min_eigen_solver_result
    except (AttributeError, TypeError):
        return _unavailable_result(
            formulation,
            reps,
            "MinimumEigenOptimizer did not expose a QAOA solver result.",
        )
    if solver_result is None:
        return _unavailable_result(
            formulation,
            reps,
            "MinimumEigenOptimizer did not expose optimized QAOA parameters.",
        )

    try:
        ansatz_parameters = tuple(optimizer_run.qaoa.ansatz.parameters)
        parameter_names = tuple(
            qaoa_parameter_alias(parameter.name) for parameter in ansatz_parameters
        )
        expected_names = qaoa_parameter_names(reps)
    except (AttributeError, TypeError, ValueError):
        return _unavailable_result(
            formulation,
            reps,
            "The optimized QAOA ansatz parameter order is unavailable.",
        )

    if parameter_names != expected_names:
        return _unavailable_result(
            formulation,
            reps,
            "The optimizer parameter order does not match the adapter parameter mapping.",
            parameter_names,
        )

    optimized_mapping = getattr(solver_result, "optimal_parameters", None)
    optimal_point = getattr(solver_result, "optimal_point", None)
    if not isinstance(optimized_mapping, Mapping):
        return _unavailable_result(
            formulation,
            reps,
            "The QAOA solver result does not provide an optimized parameter mapping.",
            parameter_names,
        )
    if len(optimized_mapping) != len(ansatz_parameters) or set(
        optimized_mapping
    ) != set(ansatz_parameters):
        return _unavailable_result(
            formulation,
            reps,
            "The optimizer parameter count or parameter identities do not match the QAOA ansatz.",
            parameter_names,
        )
    if optimal_point is None or len(optimal_point) != len(ansatz_parameters):
        return _unavailable_result(
            formulation,
            reps,
            "The optimizer point dimension does not match the QAOA ansatz.",
            parameter_names,
        )

    parameter_values: list[float] = []
    for index, parameter in enumerate(ansatz_parameters):
        mapped_value = optimized_mapping[parameter]
        point_value = optimal_point[index]
        if (
            isinstance(mapped_value, bool)
            or not isinstance(mapped_value, Real)
            or isinstance(point_value, bool)
            or not isinstance(point_value, Real)
        ):
            return _unavailable_result(
                formulation,
                reps,
                "The optimizer returned a non-numeric QAOA parameter.",
                parameter_names,
            )
        mapped_float = float(mapped_value)
        point_float = float(point_value)
        if not isfinite(mapped_float) or not isfinite(point_float):
            return _unavailable_result(
                formulation,
                reps,
                "The optimizer returned a non-finite QAOA parameter.",
                parameter_names,
            )
        if not isclose(mapped_float, point_float, rel_tol=1e-10, abs_tol=1e-10):
            return _unavailable_result(
                formulation,
                reps,
                "The named optimizer parameters do not match its ordered optimal point.",
                parameter_names,
            )
        parameter_values.append(mapped_float)

    optimizer_energy = _finite_optional_float(
        getattr(solver_result, "optimal_value", None)
    )
    bindings = dict(zip(parameter_names, parameter_values))
    try:
        execution_package = prepare_route_qaoa_execution_package(
            formulation,
            bindings,
            reps,
        )
    except Exception:
        return QAOAParameterResult(
            provenance=QAOAParameterProvenance.LOCAL_AER_OPTIMIZED,
            parameter_names=parameter_names,
            parameter_values=tuple(parameter_values),
            qaoa_reps=reps,
            optimizer_energy=optimizer_energy,
            problem=problem,
            validation_passed=False,
            validation_message=(
                "Genuine local Aer parameters were extracted, but their circuit package "
                "failed adapter validation."
            ),
        )

    if (
        execution_package.problem_signature != problem.problem_signature
        or execution_package.parameter_bindings
        != tuple(zip(parameter_names, parameter_values))
        or execution_package.problem.route_variable_count != problem.route_variable_count
        or len(execution_package.circuit.parameters) != 0
    ):
        return QAOAParameterResult(
            provenance=QAOAParameterProvenance.LOCAL_AER_OPTIMIZED,
            parameter_names=parameter_names,
            parameter_values=tuple(parameter_values),
            qaoa_reps=reps,
            optimizer_energy=optimizer_energy,
            problem=problem,
            validation_passed=False,
            validation_message=(
                "The prepared circuit did not preserve the optimized QAOA problem identity."
            ),
        )

    execution_package = replace(
        execution_package,
        parameter_source=QAOAParameterProvenance.LOCAL_AER_OPTIMIZED.value,
    )
    return QAOAParameterResult(
        provenance=QAOAParameterProvenance.LOCAL_AER_OPTIMIZED,
        parameter_names=parameter_names,
        parameter_values=tuple(parameter_values),
        qaoa_reps=reps,
        optimizer_energy=optimizer_energy,
        problem=problem,
        validation_passed=execution_package.validation.local_transpilation_passed,
        validation_message=(
            "Local Aer optimizer parameters match the same route QUBO and were "
            "successfully bound and locally transpiled."
        ),
        execution_package=execution_package,
    )


def _problem_metadata(formulation: RouteQubo) -> QAOAParameterProblemMetadata:
    return QAOAParameterProblemMetadata(
        delivery_ids=formulation.delivery_ids,
        variable_names=formulation.variable_names,
        route_variable_count=len(formulation.variable_names),
        penalty_weight=float(formulation.penalty_weight),
        problem_signature=route_qubo_fingerprint(formulation),
        route_variable_mapping=tuple(
            RouteVariableBinding(
                variable_index=index,
                variable_name=name,
                logical_qubit_index=index,
                measured_bit_index=index,
                vehicle_id=route.plan.vehicle_id,
                delivery_ids=route.plan.delivery_ids,
            )
            for index, (name, route) in enumerate(
                zip(formulation.variable_names, formulation.routes)
            )
        ),
    )


def _unavailable_result(
    formulation: RouteQubo,
    reps: int,
    message: str,
    parameter_names: tuple[str, ...] | None = None,
) -> QAOAParameterResult:
    return QAOAParameterResult(
        provenance=QAOAParameterProvenance.UNAVAILABLE,
        parameter_names=parameter_names or qaoa_parameter_names(reps),
        parameter_values=(),
        qaoa_reps=reps,
        optimizer_energy=None,
        problem=_problem_metadata(formulation),
        validation_passed=False,
        validation_message=message,
        execution_package=None,
    )


def _finite_optional_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, Real):
        return None
    normalized = float(value)
    return normalized if isfinite(normalized) else None
