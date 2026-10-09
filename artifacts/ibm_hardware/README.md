# IBM Quantum hardware evidence

This directory contains durable evidence for completed, real IBM Quantum
hardware executions of QuantumRoute's VNQFF-08 demonstration.

## Valid evidence

A valid execution has been explicitly confirmed and submitted to an IBM
hardware backend (not a simulator), reached `DONE`, and returned measurement
counts that were decoded and checked by the application's route-feasibility
validator. Immediately before the submit call, the app writes an immutable
pre-submission manifest to `manifests/`. It contains the run ID, original
scenario and QUBO coefficients/signature, QAOA parameter provenance and
available optimizer history, logical and submitted circuits (QPY), measurement
wiring, transpiler layouts when available, source hashes, package versions,
backend, shots, and confirmation timestamp. If this manifest cannot be
persisted or does not match the completed job, verified evidence is refused.
The submission attempt, job ID, later status, and fetched result are separately
updated under `in_progress/` so they survive a Streamlit session restart. These
progress JSON records are explicitly not verified evidence.

The evidence includes the exact manifest and its SHA-256, Runtime job ID,
actual backend/status, requested and returned shots, submission/completion
timestamps, raw register-specific counts and layout, decoded candidate,
original-QUBO objective evaluation, feasibility outcome, and scenario-derived
route metrics where valid.

The UI retains the full measured distribution and selects the lowest-original-
QUBO-energy candidate among observed bitstrings that pass the existing route
feasibility validator. The most frequent bitstring remains separately visible;
neither frequency nor this small run establishes global optimality or quantum
advantage.

- Machine-readable JSON: `ibm_hardware_<job_id>.json`
- Human-readable report: `ibm_hardware_<job_id>.md`

The app creates both files only after it retrieves a completed result from the
saved real IBM job. The manifest is written before submission, but it is not
itself evidence that a job was submitted. Evidence generation requires the
manifest, actual job ID/backend/result, shot consistency, and route validation
outcome. It does not retry job submission to create evidence.

Mock, test, simulator, and synthetic data MUST NOT be presented as real
hardware evidence or placed here. Test data belongs under test-only fixtures
or temporary directories. The system must never fabricate a job ID,
measurement counts, backend result, or completion status. An absent optional
completion timestamp is reported as unavailable rather than invented.

`unverified_recovery/` is reserved for historical recovery records and is never
read or promoted by the verified evidence writer. If no matching JSON and
Markdown evidence pair exists, a claimed hardware execution is unverified.
Evidence is immutable: an existing job ID's evidence is never silently
overwritten.
