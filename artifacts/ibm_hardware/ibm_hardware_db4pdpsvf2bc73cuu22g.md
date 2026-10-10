# Real IBM Quantum hardware execution evidence

This record describes a **REAL IBM Quantum hardware execution**. It is not a simulator result, mock, or synthetic fixture.

- Project: QuantumRoute
- Problem statement: VNQFF-08
- Backend: `ibm_marrakesh`
- Job ID: `db4pdpsvf2bc73cuu22g`
- Shots: 256
- Status: DONE
- Execution timestamp (UTC): 2026-10-10T01:37:41.861504+00:00
- IBM Runtime job creation timestamp (UTC): 2026-10-10T07:07:43.834808+05:30
- Completion timestamp (UTC): 2026-10-10T01:37:50.850422Z
- Application run ID: `aa673f1a-d9f1-4900-98d5-5d15c7688d5f`
- QUBO signature: `e480776b0746e90a21aca7c72b0255b89fa3530e68acea559f29f268bfec26d9`
- Most frequent measured bitstring: `1010`
- Pre-submission manifest SHA-256: `71578ee24b53a7c84c3b409907b31bc176c7e3f5d31d0d1775745b581278ba08`

## Measurement result

- Counts: `{"0000": 17, "0001": 17, "0010": 23, "0011": 28, "0100": 26, "0101": 11, "0110": 15, "0111": 2, "1000": 22, "1001": 12, "1010": 47, "1011": 10, "1100": 20, "1101": 1, "1110": 3, "1111": 2}`
- Selected bitstring: `1010`

## Decoded route and validation

- Feasibility validation: **PASSED**
- Validation detail: Lowest-QUBO-energy observed candidate decoded to a route that passed existing feasibility validation.
The selected candidate is the lowest-QUBO-energy observed assignment that passed feasibility validation; it is not asserted to be globally optimal.
- Selected feasible candidate QUBO objective: `0.05046670459332381` original QUBO objective units
- Route: `van-1` → Sunset Point
- Route: `van-2` → Civic Centre
- Total distance: 18.225 km
- Total travel time: 34.171 min
- Fuel estimate: 1.640 L
- Tailpipe CO₂ estimate: 4.396 kg

## Objective and cost

- Objective: 21.814028 model cost units
- Estimated operating cost: ₹1854.19

## Limitations

- Hardware measurements are stochastic; this small demonstration does not establish quantum advantage.
- Route feasibility and physical cost are calculated by the project model from the decoded measurement.
- Fuel and CO2 estimates, when shown by the application, use configured model assumptions.
