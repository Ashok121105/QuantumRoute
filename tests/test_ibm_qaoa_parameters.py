from math import isfinite
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from qiskit.circuit.library import QAOAAnsatz
from qiskit_optimization import QuadraticProgram

from ibm_qaoa_adapter import (
    build_demo_route_qubo,
    prepare_demo_qaoa_execution_package,
    route_qubo_fingerprint,
)
from ibm_qaoa_parameters import (
    QAOAParameterProvenance,
    extract_local_qaoa_parameters,
    optimize_demo_qaoa_parameters,
)
from quantum_route_optimisation.qaoa import QAOAConfig


class IBMQAOAParameterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.result = optimize_demo_qaoa_parameters(
            QAOAConfig(reps=1, maxiter=8, shots=256, seed=42)
        )

    def test_extracts_genuine_local_aer_parameters_for_same_demo_qubo(self) -> None:
        result = self.result

        self.assertTrue(result.available)
        self.assertEqual(result.provenance, QAOAParameterProvenance.LOCAL_AER_OPTIMIZED)
        self.assertEqual(result.parameter_names, ("beta_0", "gamma_0"))
        self.assertEqual(len(result.parameter_values), 2)
        self.assertTrue(all(isfinite(value) for value in result.parameter_values))
        self.assertTrue(result.validation_passed, result.validation_message)
        self.assertIsNotNone(result.optimizer_energy)
        package = result.execution_package
        self.assertIsNotNone(package)
        self.assertEqual(
            package.parameter_source,
            QAOAParameterProvenance.LOCAL_AER_OPTIMIZED.value,
        )
        self.assertEqual(result.problem.problem_signature, package.problem_signature)
        self.assertEqual(
            result.problem.variable_names,
            tuple(item.variable_name for item in package.route_variable_mapping),
        )
        self.assertEqual(package.problem.route_variable_count, 4)
        self.assertLessEqual(package.required_qubits, 5)
        self.assertEqual(len(package.circuit.parameters), 0)
        self.assertTrue(package.validation.local_transpilation_passed)

    def test_unavailable_optimizer_parameters_are_explicit(self) -> None:
        formulation = build_demo_route_qubo()
        run = SimpleNamespace(
            optimizer_result=SimpleNamespace(min_eigen_solver_result=None)
        )

        result = extract_local_qaoa_parameters(run, formulation, reps=1)

        self.assertFalse(result.available)
        self.assertEqual(result.provenance, QAOAParameterProvenance.UNAVAILABLE)
        self.assertEqual(result.parameter_values, ())
        self.assertFalse(result.validation_passed)
        self.assertIsNone(result.execution_package)

    def test_problem_fingerprint_detects_changed_qubo_with_same_route_metadata(self) -> None:
        formulation = build_demo_route_qubo()
        changed_problem = QuadraticProgram("different_route_objective")
        for variable_name in formulation.variable_names:
            changed_problem.binary_var(variable_name)
        changed_problem.minimize(
            constant=0.0,
            linear={index: float(index + 1) for index in range(len(formulation.variable_names))},
        )

        changed_formulation = replace(formulation, problem=changed_problem)

        self.assertEqual(formulation.variable_names, changed_formulation.variable_names)
        self.assertEqual(formulation.routes, changed_formulation.routes)
        self.assertNotEqual(
            route_qubo_fingerprint(formulation),
            route_qubo_fingerprint(changed_formulation),
        )

    def test_incompatible_parameter_count_is_reported_as_unavailable(self) -> None:
        formulation = build_demo_route_qubo()
        operator, _ = formulation.problem.to_ising()
        ansatz = QAOAAnsatz(operator, reps=1)
        first_parameter = tuple(ansatz.parameters)[0]
        solver_result = SimpleNamespace(
            optimal_parameters={first_parameter: 0.2},
            optimal_point=(0.2,),
            optimal_value=-1.0,
        )
        run = SimpleNamespace(
            qaoa=SimpleNamespace(ansatz=ansatz),
            optimizer_result=SimpleNamespace(min_eigen_solver_result=solver_result),
        )

        result = extract_local_qaoa_parameters(run, formulation, reps=1)

        self.assertFalse(result.available)
        self.assertIn("count", result.validation_message)
        self.assertEqual(result.provenance, QAOAParameterProvenance.UNAVAILABLE)

    def test_order_comes_from_ansatz_parameters_not_mapping_insertion(self) -> None:
        formulation = build_demo_route_qubo()
        operator, _ = formulation.problem.to_ising()
        ansatz = QAOAAnsatz(operator, reps=1)
        beta, gamma = tuple(ansatz.parameters)
        solver_result = SimpleNamespace(
            optimal_parameters={gamma: -0.3, beta: 0.4},
            optimal_point=(0.4, -0.3),
            optimal_value=-1.25,
        )
        run = SimpleNamespace(
            qaoa=SimpleNamespace(ansatz=ansatz),
            optimizer_result=SimpleNamespace(min_eigen_solver_result=solver_result),
        )

        result = extract_local_qaoa_parameters(run, formulation, reps=1)

        self.assertEqual(result.parameter_names, ("beta_0", "gamma_0"))
        self.assertEqual(result.parameter_values, (0.4, -0.3))
        self.assertTrue(result.validation_passed)

    def test_nonfinite_optimizer_values_are_not_reported_as_optimized(self) -> None:
        formulation = build_demo_route_qubo()
        operator, _ = formulation.problem.to_ising()
        ansatz = QAOAAnsatz(operator, reps=1)
        beta, gamma = tuple(ansatz.parameters)
        solver_result = SimpleNamespace(
            optimal_parameters={beta: float("nan"), gamma: 0.2},
            optimal_point=(float("nan"), 0.2),
            optimal_value=-1.0,
        )
        run = SimpleNamespace(
            qaoa=SimpleNamespace(ansatz=ansatz),
            optimizer_result=SimpleNamespace(min_eigen_solver_result=solver_result),
        )

        result = extract_local_qaoa_parameters(run, formulation, reps=1)

        self.assertEqual(result.provenance, QAOAParameterProvenance.UNAVAILABLE)
        self.assertFalse(result.available)
        self.assertIn("finite", result.validation_message)

    def test_caller_supplied_angles_remain_distinct_from_optimized_values(self) -> None:
        package = prepare_demo_qaoa_execution_package(
            {"beta_0": 0.1, "gamma_0": 0.2}
        )

        self.assertEqual(
            package.parameter_source,
            QAOAParameterProvenance.CALLER_SUPPLIED.value,
        )
        self.assertNotEqual(
            package.parameter_source,
            QAOAParameterProvenance.LOCAL_AER_OPTIMIZED.value,
        )

    def test_optimizer_bridge_never_calls_ibm_services_or_job_apis(self) -> None:
        with (
            patch("ibm_quantum.connect_ibm_quantum") as connect,
            patch("ibm_quantum.discover_ibm_backends") as discover,
            patch("qiskit_ibm_runtime.QiskitRuntimeService") as runtime_service,
        ):
            result = optimize_demo_qaoa_parameters(
                QAOAConfig(reps=1, maxiter=8, shots=256, seed=42)
            )

        self.assertEqual(result.provenance, QAOAParameterProvenance.LOCAL_AER_OPTIMIZED)
        connect.assert_not_called()
        discover.assert_not_called()
        runtime_service.assert_not_called()


if __name__ == "__main__":
    unittest.main()
