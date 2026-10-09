"""Prepare a local, validated QAOA circuit package for future Runtime use.

This adapter builds and transpiles circuits locally. It contains no IBM service,
sampler, job submission, or execution calls.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
import hashlib
from math import isfinite
from numbers import Real

from qiskit import QuantumCircuit, transpile
from qiskit.circuit.library import QAOAAnsatz

from quantum_route_optimisation.classical import generate_feasible_routes
from quantum_route_optimisation.qubo import RouteQubo, build_route_qubo
from route_dashboard.demo_mode import build_hackathon_demo_scenario
from route_dashboard.objectives import ObjectiveConfig, ObjectiveName, scenario_for_objective
from route_dashboard.scenario import ScenarioProblem


MAX_DEMO_QUBITS = 5
REPRESENTATIVE_BACKEND_NAME = "five_qubit_linear_topology"
REPRESENTATIVE_BACKEND_QUBITS = 5
REPRESENTATIVE_BASIS_GATES = ("rz", "sx", "x", "cx")
REPRESENTATIVE_COUPLING_MAP = tuple(
    edge
    for index in range(REPRESENTATIVE_BACKEND_QUBITS - 1)
    for edge in ((index, index + 1), (index + 1, index))
)
TRANSPILER_SEED = 42


class QAOAAdapterValidationError(ValueError):
    """Raised when a route QUBO cannot safely become a demo circuit package."""


@dataclass(frozen=True)
class RouteVariableBinding:
    variable_index: int
    variable_name: str
    logical_qubit_index: int
    measured_bit_index: int
    vehicle_id: str
    delivery_ids: tuple[str, ...]


@dataclass(frozen=True)
class QAOAProblemMetadata:
    delivery_ids: tuple[str, ...]
    route_variable_count: int
    penalty_weight: float
    ising_offset: float


@dataclass(frozen=True)
class CircuitValidation:
    local_transpilation_passed: bool
    unresolved_parameters: int
    representative_backend: str
    basis_gates: tuple[str, ...]
    coupling_map: tuple[tuple[int, int], ...]
    transpiled_depth: int


@dataclass(frozen=True)
class QAOAExecutionPackage:
    """Runtime-ready circuit data; angles are supplied by the caller, not optimized here."""

    circuit: QuantumCircuit = field(repr=False, compare=False)
    parameter_bindings: tuple[tuple[str, float], ...]
    parameter_source: str
    qaoa_reps: int
    logical_qubits: int
    required_qubits: int
    problem: QAOAProblemMetadata
    problem_signature: str
    route_variable_mapping: tuple[RouteVariableBinding, ...]
    validation: CircuitValidation
    formulation: RouteQubo | None = field(default=None, repr=False, compare=False)
    scenario: ScenarioProblem | None = field(default=None, repr=False, compare=False)
    objective_scenario: ScenarioProblem | None = field(default=None, repr=False, compare=False)
    logical_circuit: QuantumCircuit | None = field(default=None, repr=False, compare=False)
    measured_circuit: QuantumCircuit | None = field(default=None, repr=False, compare=False)
    optimizer_metadata: Mapping[str, object] | None = field(
        default=None,
        repr=False,
        compare=False,
    )


def qaoa_parameter_names(reps: int = 1) -> tuple[str, ...]:
    """Return the stable ASCII binding keys for QAOA layer angles."""
    if not isinstance(reps, int) or isinstance(reps, bool) or reps < 1:
        raise QAOAAdapterValidationError("QAOA reps must be a positive integer.")
    return tuple(f"beta_{index}" for index in range(reps)) + tuple(
        f"gamma_{index}" for index in range(reps)
    )


def qaoa_parameter_alias(parameter_name: str) -> str:
    """Map Qiskit's ordered beta/gamma parameters to stable ASCII binding keys."""
    for prefix, alias in (("\u03b2[", "beta"), ("\u03b3[", "gamma")):
        if parameter_name.startswith(prefix) and parameter_name.endswith("]"):
            try:
                index = int(parameter_name[len(prefix) : -1])
            except ValueError:
                break
            return f"{alias}_{index}"
    raise QAOAAdapterValidationError("Unexpected QAOA parameter name in the ansatz.")


def build_demo_route_qubo() -> RouteQubo:
    """Build the exact Cost Priority route QUBO used by Demo Mode."""
    return build_demo_route_problem()[2]


def build_demo_route_problem() -> tuple[ScenarioProblem, ScenarioProblem, RouteQubo]:
    """Return the exact scenario pair and QUBO used by the hardware demo."""
    scenario = build_hackathon_demo_scenario()
    objective_scenario = scenario_for_objective(
        scenario,
        ObjectiveConfig.for_name(ObjectiveName.COST),
    )
    feasible_routes = generate_feasible_routes(
        objective_scenario.vehicles,
        objective_scenario.deliveries,
        objective_scenario.travel,
        objective_scenario.cost_weights,
    )
    formulation = build_route_qubo(feasible_routes, objective_scenario.deliveries)
    return scenario, objective_scenario, formulation


def route_qubo_fingerprint(formulation: RouteQubo) -> str:
    """Fingerprint the ordered route variables and costs that define this QUBO."""
    operator, ising_offset = formulation.problem.to_ising()
    ising_terms = tuple(
        (
            label,
            float(complex(coefficient).real).hex(),
            float(complex(coefficient).imag).hex(),
        )
        for label, coefficient in operator.to_list()
    )
    identity = (
        "quantumroute-route-qubo-v1",
        formulation.variable_names,
        formulation.delivery_ids,
        float(formulation.penalty_weight).hex(),
        float(ising_offset).hex(),
        ising_terms,
        tuple(
            (
                route.plan.vehicle_id,
                route.plan.delivery_ids,
                float(route.total_cost).hex(),
            )
            for route in formulation.routes
        ),
    )
    return hashlib.sha256(repr(identity).encode("utf-8")).hexdigest()


def prepare_demo_qaoa_execution_package(
    parameter_bindings: Mapping[str, Real],
    reps: int = 1,
) -> QAOAExecutionPackage:
    """Build the existing deterministic Demo Mode QUBO and prepare it locally."""
    scenario, objective_scenario, formulation = build_demo_route_problem()
    return prepare_route_qaoa_execution_package(
        formulation,
        parameter_bindings,
        reps,
        scenario=scenario,
        objective_scenario=objective_scenario,
    )


def prepare_route_qaoa_execution_package(
    formulation: RouteQubo,
    parameter_bindings: Mapping[str, Real],
    reps: int = 1,
    *,
    scenario: ScenarioProblem | None = None,
    objective_scenario: ScenarioProblem | None = None,
    optimizer_metadata: Mapping[str, object] | None = None,
) -> QAOAExecutionPackage:
    """Bind a small route-QUBO QAOA ansatz and validate it by local transpilation."""
    expected_parameter_names = qaoa_parameter_names(reps)
    variable_count = len(formulation.variable_names)
    if not 1 <= variable_count <= MAX_DEMO_QUBITS:
        raise QAOAAdapterValidationError(
            f"The first IBM demo supports 1-{MAX_DEMO_QUBITS} route variables; "
            f"this QUBO has {variable_count}. No variables were truncated."
        )
    if (
        formulation.problem.get_num_vars() != variable_count
        or len(formulation.routes) != variable_count
        or len(set(formulation.variable_names)) != variable_count
    ):
        raise QAOAAdapterValidationError(
            "QUBO variable names, problem variables, and route options must align exactly."
        )
    if not isinstance(parameter_bindings, Mapping):
        raise QAOAAdapterValidationError("QAOA parameter bindings must be a name-to-value mapping.")

    supplied_names = set(parameter_bindings)
    expected_names = set(expected_parameter_names)
    if supplied_names != expected_names:
        missing = sorted(expected_names - supplied_names)
        unexpected = sorted(supplied_names - expected_names)
        raise QAOAAdapterValidationError(
            f"QAOA parameter mapping mismatch; missing={missing}, unexpected={unexpected}."
        )

    normalized_bindings: dict[str, float] = {}
    for name in expected_parameter_names:
        value = parameter_bindings[name]
        if isinstance(value, bool) or not isinstance(value, Real) or not isfinite(float(value)):
            raise QAOAAdapterValidationError(
                f"QAOA parameter {name} must be a finite real number."
            )
        normalized_bindings[name] = float(value)

    try:
        cost_operator, ising_offset = formulation.problem.to_ising()
        ansatz = QAOAAnsatz(cost_operator=cost_operator, reps=reps)
    except Exception as error:
        raise QAOAAdapterValidationError(
            "The route QUBO could not be converted into a QAOA ansatz."
        ) from error

    if ansatz.num_qubits != variable_count:
        raise QAOAAdapterValidationError(
            "The QAOA ansatz width does not match the route-variable count."
        )

    qiskit_parameters = tuple(ansatz.parameters)
    aliases = tuple(qaoa_parameter_alias(parameter.name) for parameter in qiskit_parameters)
    if aliases != expected_parameter_names:
        raise QAOAAdapterValidationError(
            "The installed QAOA parameter order does not match the adapter mapping."
        )
    bound_circuit = ansatz.assign_parameters(
        {
            parameter: normalized_bindings[alias]
            for parameter, alias in zip(qiskit_parameters, aliases)
        },
        inplace=False,
    )
    if bound_circuit is None or bound_circuit.parameters:
        raise QAOAAdapterValidationError(
            "Parameter binding left unresolved QAOA parameters in the circuit."
        )

    measured_circuit = bound_circuit.copy()
    measured_circuit.measure_all(inplace=True)
    try:
        compiled_circuit = transpile(
            measured_circuit,
            basis_gates=list(REPRESENTATIVE_BASIS_GATES),
            coupling_map=[list(edge) for edge in REPRESENTATIVE_COUPLING_MAP],
            optimization_level=1,
            seed_transpiler=TRANSPILER_SEED,
        )
    except Exception as error:
        raise QAOAAdapterValidationError(
            "The bound QAOA circuit failed local representative-backend transpilation."
        ) from error

    unresolved_parameters = len(compiled_circuit.parameters)
    if unresolved_parameters:
        raise QAOAAdapterValidationError(
            "Local transpilation returned a circuit with unresolved parameters."
        )
    if not 1 <= compiled_circuit.num_qubits <= MAX_DEMO_QUBITS:
        raise QAOAAdapterValidationError(
            f"The transpiled circuit requires {compiled_circuit.num_qubits} qubits; "
            f"the first IBM demo limit is {MAX_DEMO_QUBITS}. No circuit was truncated."
        )

    route_mapping = tuple(
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
    )
    if tuple(binding.variable_index for binding in route_mapping) != tuple(
        range(variable_count)
    ):
        raise QAOAAdapterValidationError("Route-variable mapping is not deterministic.")

    return QAOAExecutionPackage(
        circuit=compiled_circuit,
        parameter_bindings=tuple(
            (name, normalized_bindings[name]) for name in expected_parameter_names
        ),
        parameter_source="caller_supplied",
        qaoa_reps=reps,
        logical_qubits=ansatz.num_qubits,
        required_qubits=compiled_circuit.num_qubits,
        problem=QAOAProblemMetadata(
            delivery_ids=formulation.delivery_ids,
            route_variable_count=variable_count,
            penalty_weight=float(formulation.penalty_weight),
            ising_offset=float(ising_offset),
        ),
        problem_signature=route_qubo_fingerprint(formulation),
        route_variable_mapping=route_mapping,
        validation=CircuitValidation(
            local_transpilation_passed=True,
            unresolved_parameters=unresolved_parameters,
            representative_backend=REPRESENTATIVE_BACKEND_NAME,
            basis_gates=REPRESENTATIVE_BASIS_GATES,
            coupling_map=REPRESENTATIVE_COUPLING_MAP,
            transpiled_depth=compiled_circuit.depth(),
        ),
        formulation=formulation,
        scenario=scenario,
        objective_scenario=objective_scenario,
        logical_circuit=bound_circuit,
        measured_circuit=measured_circuit,
        optimizer_metadata=optimizer_metadata,
    )
