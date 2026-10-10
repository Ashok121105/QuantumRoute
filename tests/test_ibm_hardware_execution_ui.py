import json
import tempfile
import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest

from ibm_hardware_evidence import HardwareEvidenceError
from route_dashboard import ui


class IBMHardwareReadOnlyUITests(unittest.TestCase):
    def test_saved_historical_record_and_manifest_validate(self) -> None:
        record = ui._load_historical_ibm_evidence()
        self.assertEqual(record["job_id"], "db4pdpsvf2bc73cuu22g")
        self.assertEqual(record["backend"], "ibm_marrakesh")
        self.assertEqual(record["status"], "DONE")
        self.assertEqual(record["shots"], 256)
        self.assertEqual(record["shots_returned"], 256)
        self.assertEqual(sum(record["measurement_counts"].values()), 256)
        self.assertEqual(record["decoded_solution"]["selected_bitstring"], "1010")
        self.assertTrue(record["route_validation"]["passed"])

    def test_evidence_with_wrong_job_metadata_is_rejected(self) -> None:
        original = ui._HISTORICAL_IBM_EVIDENCE_PATH
        with tempfile.TemporaryDirectory() as directory:
            altered = json.loads(original.read_text(encoding="utf-8"))
            altered["backend"] = "ibm_other_backend"
            path = Path(directory) / original.name
            path.write_text(json.dumps(altered), encoding="utf-8")
            old_path = ui._HISTORICAL_IBM_EVIDENCE_PATH
            ui._HISTORICAL_IBM_EVIDENCE_PATH = path
            try:
                with self.assertRaises(HardwareEvidenceError):
                    ui._load_historical_ibm_evidence()
            finally:
                ui._HISTORICAL_IBM_EVIDENCE_PATH = old_path

    def test_hardware_workspace_is_read_only_and_displays_saved_evidence(self) -> None:
        app = AppTest.from_file("app.py", default_timeout=300).run()
        self.assertFalse(app.exception, [str(item.value) for item in app.exception])
        app.radio[0].set_value("IBM Quantum Hardware").run()
        self.assertFalse(app.exception, [str(item.value) for item in app.exception])
        text = " ".join(
            item.value
            for collection in (app.title, app.header, app.subheader, app.caption, app.info, app.success)
            for item in collection
        )
        self.assertIn("db4pdpsvf2bc73cuu22g", text)
        self.assertIn("ibm_marrakesh", text)
        self.assertIn("Previous hardware run", text)
        labels = {button.label for button in app.button}
        self.assertFalse(any("Submit" in label or "Refresh Job" in label for label in labels))
        self.assertFalse(hasattr(ui, "submit_confirmed_hardware_job"))
        self.assertFalse(hasattr(ui, "refresh_hardware_job_status"))


if __name__ == "__main__":
    unittest.main()
