import unittest
from unittest.mock import patch

from qiskit.circuit import Parameter

from ibm_qaoa_adapter import (
    MAX_DEMO_QUBITS,
    QAOAAdapterValidationError,
    prepare_demo_qaoa_execution_package,
    qaoa_parameter_names,
)
from route_dashboard.demo_mode import build_hackathon_demo_scenario


class IBMQAOAAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bindings = {"beta_0": 0.31, "gamma_0": -0.27}

    def test_demo_package_contains_valid_bound_small_circuit(self) -> None:
        package = prepare_demo_qaoa_execution_package(self.bindings)

        self.assertEqual(package.problem.delivery_ids, ("delivery-1", "delivery-2"))
        self.assertEqual(package.problem.route_variable_count, 4)
        self.assertEqual(package.logical_qubits, 4)
        self.assertEqual(package.required_qubits, package.circuit.num_qubits)
        self.assertLessEqual(package.required_qubits, MAX_DEMO_QUBITS)
        self.assertEqual(package.qaoa_reps, 1)
        self.assertEqual(package.parameter_bindings, (("beta_0", 0.31), ("gamma_0", -0.27)))
        self.assertEqual(package.parameter_source, "caller_supplied")
        self.assertEqual(len(package.circuit.parameters), 0)
        self.assertTrue(package.validation.local_transpilation_passed)
        self.assertEqual(package.validation.unresolved_parameters, 0)
        self.assertIn("measure", package.circuit.count_ops())

    def test_parameter_names_and_dimensions_match_qaoa_depth(self) -> None:
        self.assertEqual(
            qaoa_parameter_names(2),
            ("beta_0", "beta_1", "gamma_0", "gamma_1"),
        )
        package = prepare_demo_qaoa_execution_package(
            {
                "beta_0": 0.1,
                "beta_1": 0.2,
                "gamma_0": 0.3,
                "gamma_1": 0.4,
            },
            reps=2,
        )
        self.assertEqual(package.qaoa_reps, 2)
        self.assertEqual(len(package.parameter_bindings), 4)
        self.assertEqual(len(package.circuit.parameters), 0)

    def test_route_variable_mapping_is_deterministic_and_complete(self) -> None:
        first = prepare_demo_qaoa_execution_package(self.bindings)
        second = prepare_demo_qaoa_execution_package(self.bindings)

        self.assertEqual(first.route_variable_mapping, second.route_variable_mapping)
        self.assertEqual(
            tuple(item.variable_name for item in first.route_variable_mapping),
            ("route_0000", "route_0001", "route_0002", "route_0003"),
        )
        self.assertEqual(
            tuple(item.logical_qubit_index for item in first.route_variable_mapping),
            (0, 1, 2, 3),
        )
        self.assertEqual(
            tuple(item.measured_bit_index for item in first.route_variable_mapping),
            (0, 1, 2, 3),
        )

    def test_route_mapping_retains_existing_vehicle_and_delivery_variables(self) -> None:
        package = prepare_demo_qaoa_execution_package(self.bindings)
        scenario = build_hackathon_demo_scenario()

        self.assertEqual(
            tuple(
                (item.vehicle_id, item.delivery_ids)
                for item in package.route_variable_mapping
            ),
            (
                ("van-1", ("delivery-1",)),
                ("van-1", ("delivery-2",)),
                ("van-1", ("delivery-2", "delivery-1")),
                ("van-2", ("delivery-1",)),
            ),
        )
        self.assertEqual(
            package.problem.delivery_ids,
            tuple(delivery.delivery_id for delivery in scenario.deliveries),
        )

    def test_unsupported_size_is_rejected_without_truncation(self) -> None:
        with patch("ibm_qaoa_adapter.MAX_DEMO_QUBITS", 3):
            with self.assertRaisesRegex(QAOAAdapterValidationError, "has 4.*No variables were truncated"):
                prepare_demo_qaoa_execution_package(self.bindings)

    def test_missing_extra_and_nonfinite_parameter_bindings_are_rejected(self) -> None:
        with self.assertRaisesRegex(QAOAAdapterValidationError, "mapping mismatch"):
            prepare_demo_qaoa_execution_package({"beta_0": 0.1})
        with self.assertRaisesRegex(QAOAAdapterValidationError, "mapping mismatch"):
            prepare_demo_qaoa_execution_package(
                {"beta_0": 0.1, "gamma_0": 0.2, "extra": 0.3}
            )
        with self.assertRaisesRegex(QAOAAdapterValidationError, "finite real number"):
            prepare_demo_qaoa_execution_package({"beta_0": float("nan"), "gamma_0": 0.2})

    def test_unresolved_parameters_are_rejected_before_transpilation(self) -> None:
        class UnboundAnsatz:
            num_qubits = 4
            parameters = (
                Parameter("\u03b2[0]"),
                Parameter("\u03b3[0]"),
            )

            def assign_parameters(self, *args, **kwargs):
                return self

        with (
            patch(
                "ibm_qaoa_adapter.QAOAAnsatz",
                return_value=UnboundAnsatz(),
            ),
            patch("ibm_qaoa_adapter.transpile") as transpile,
        ):
            with self.assertRaisesRegex(QAOAAdapterValidationError, "unresolved"):
                prepare_demo_qaoa_execution_package(self.bindings)

        transpile.assert_not_called()

    def test_adapter_stays_offline_and_has_no_job_submission_api(self) -> None:
        with (
            patch("ibm_quantum.connect_ibm_quantum") as connect,
            patch("ibm_quantum.discover_ibm_backends") as discover,
        ):
            package = prepare_demo_qaoa_execution_package(self.bindings)

        self.assertTrue(package.validation.local_transpilation_passed)
        connect.assert_not_called()
        discover.assert_not_called()
        self.assertFalse(hasattr(__import__("ibm_qaoa_adapter"), "submit_job"))
        self.assertFalse(hasattr(__import__("ibm_qaoa_adapter"), "execute_circuit"))


if __name__ == "__main__":
    unittest.main()