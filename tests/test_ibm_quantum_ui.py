import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from ibm_quantum import IBMBackendDiscovery, IBMBackendMetadata, IBMQuantumStatus
from route_dashboard import ui


class IBMQuantumHardwareUITests(unittest.TestCase):
    def test_discovery_runs_only_on_click_and_displays_safe_backend_metadata(self) -> None:
        result = IBMBackendDiscovery(
            connected=True,
            status=IBMQuantumStatus.BACKEND_DISCOVERY_SUCCESSFUL,
            message="IBM Quantum backend discovery successful: 2 backend(s) found.",
            backends=(
                IBMBackendMetadata(
                    name="ibm_example",
                    num_qubits=127,
                    operational=True,
                    status_message="active",
                    pending_jobs=2,
                    simulator=False,
                    supported_operations=("cx", "measure", "rz", "sx"),
                ),
                IBMBackendMetadata(
                    name="simulator_example",
                    num_qubits=32,
                    operational=True,
                    status_message="active",
                    pending_jobs=0,
                    simulator=True,
                ),
                IBMBackendMetadata(
                    name="offline_example",
                    num_qubits=5,
                    operational=False,
                    status_message="maintenance",
                    pending_jobs=None,
                    simulator=False,
                ),
            ),
        )
        with patch.object(
            ui,
            "discover_ibm_backends",
            return_value=result,
        ) as discover:
            app = AppTest.from_file("app.py", default_timeout=300).run()
            self.assertFalse(app.exception, [str(item.value) for item in app.exception])

            app.radio[0].set_value("IBM Quantum Hardware").run()
            self.assertFalse(app.exception, [str(item.value) for item in app.exception])
            discover.assert_not_called()
            self.assertTrue(
                any(button.label == "Discover IBM Backends" for button in app.button)
            )

            next(
                button
                for button in app.button
                if button.label == "Discover IBM Backends"
            ).click().run()
            self.assertFalse(app.exception, [str(item.value) for item in app.exception])
            discover.assert_called_once_with()
            self.assertIn("No quantum circuits or jobs are submitted", " ".join(
                item.value for item in app.caption
            ))
            dataframe = app.dataframe[0].value
            self.assertEqual(
                dataframe["Type"].tolist(),
                ["Real hardware", "Simulator", "Real hardware"],
            )
            self.assertEqual(
                dataframe["Backend"].tolist(),
                ["ibm_example", "simulator_example", "offline_example"],
            )
            self.assertEqual(dataframe["Qubits"].tolist(), ["127", "32", "5"])
            self.assertEqual(
                dataframe["Availability"].tolist(),
                ["Operational", "Operational", "Not operational"],
            )

            selector = next(
                widget for widget in app.selectbox
                if widget.label == "Select discovered backend"
            )
            selector.set_value("simulator_example").run()
            self.assertFalse(app.exception, [str(item.value) for item in app.exception])
            self.assertEqual(
                next(metric.value for metric in app.metric if metric.label == "Backend type"),
                "Simulator",
            )
            self.assertTrue(any("IBM Quantum simulator" in item.value for item in app.info))
            discover.assert_called_once_with()

            selector = next(
                widget for widget in app.selectbox
                if widget.label == "Select discovered backend"
            )
            selector.set_value("offline_example").run()
            self.assertFalse(app.exception, [str(item.value) for item in app.exception])
            metrics = {metric.label: metric.value for metric in app.metric}
            self.assertEqual(metrics["Availability"], "Not operational")
            self.assertEqual(metrics["Backend type"], "Real hardware")
            self.assertTrue(any("maintenance" in item.value for item in app.caption))
            discover.assert_called_once_with()

        self.assertIsNone(ui._find_ibm_backend("removed_backend", result.backends))


if __name__ == "__main__":
    unittest.main()