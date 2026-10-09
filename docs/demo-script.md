# QuantumRoute — 2–3 Minute Demo Script

## Before the presentation

From the project root, start the local app:

```powershell
python -m streamlit run app.py
```

Wait for the browser page to load. In the left sidebar, choose **Demo Mode**.
This walkthrough uses the deterministic local scenario and local Qiskit Aer;
do not open the IBM hardware submission flow.

## Presenter script and exact actions

### 0:00–0:20 — Set up the problem

**Action:** On **Demo Mode**, click **▶ Run Full Demo**. Wait for the
demonstration to complete.

**Say:** “Last-mile planning has to assign deliveries while respecting vehicle
capacity, delivery time windows, and shift limits. Traffic or a vehicle
breakdown can change which plan is feasible.”

### 0:20–0:55 — Baseline and quantum workflow

**Action:** Point to **Classical and local Aer results**, then the
**Quantum Optimization** stages.

**Say:** “We first build feasible vehicle routes using the classical
constraint checks. Each feasible route becomes a binary variable in a QUBO.
QAOA in Qiskit samples that small problem locally with Aer. We decode the
measured bits and validate the route again. The classical optimizer is our
baseline; this demo does not claim quantum advantage.”

### 0:55–1:25 — Traffic change

**Action:** Scroll to **03 / DYNAMIC TRAFFIC · SIMULATED DEMO**. Point to the
**BEFORE · NORMAL TRAFFIC** and **AFTER · HEAVY TRAFFIC** panels and their
displayed route, distance, travel time, and cost.

**Say:** “Here the scenario changes from Normal to Heavy traffic, and the
system re-optimizes. This is a deterministic simulated incident, not live
traffic or GPS data. The values shown are from this run.”

### 1:25–1:55 — Fleet disruption and objectives

**Action:** Scroll to **04 / FLEET DISRUPTION · DEMO**, point to the van-2
unavailable label, reassigned deliveries, feasibility, and waitlist if shown.
Then point to **05 / SUSTAINABILITY + OBJECTIVE TRADE-OFFS · DEMO**.

**Say:** “Next, van-2 is unavailable in the scenario. The app reassigns work
where feasible and keeps unassigned deliveries visible. The objective table
compares Cost, Time, Green, and Balanced priorities using the metrics generated
by this run.”

### 1:55–2:20 — Map and history

**Action:** Expand **Baseline route map** or **Traffic re-optimization map**.
If time permits, choose **History** in the sidebar and point to the locally
saved run.

**Say:** “The map visualizes the selected optimizer routes. Optional OSRM road
alternatives are displayed separately and do not choose the optimizer's
route. This completed demo is saved to local SQLite history.”

### 2:20–2:40 — IBM status and close

**Action:** Stay on Demo Mode; do not open a submission control.

**Say:** “We also have an optional, guarded IBM Quantum pathway. Authentication,
backend discovery, and a no-submission dry run were verified. No hardware
result is claimed here; a real run would require explicit approval.”

## Likely judge questions

### 1. Why quantum computing?

Route selection can be represented as a combinatorial optimization problem.
Quantum optimization is an experimental pathway we can compare with a
classical baseline; this project does not claim quantum advantage.

### 2. Why QAOA?

QAOA is a variational algorithm that can work with a QUBO/Ising objective and
sample candidate assignments. We use a small instance suitable for a
demonstration.

### 3. Why QUBO?

A QUBO expresses binary route choices with an objective and penalties. In this
project, route costs are combined with penalties for exact delivery coverage
and assigning more than one route to a vehicle.

### 4. Why Qiskit?

The implementation uses Qiskit and Qiskit Optimization to build and map the
QUBO/QAOA workflow, and Qiskit Aer for local circuit simulation.

### 5. Why IBM Quantum?

IBM Quantum Runtime is an optional pathway for a controlled hardware
experiment. Backend discovery is read-only, the dry run does not submit, and
submission requires an explicit confirmation flow.

### 6. What is classical and what is quantum?

Classical code generates feasible route candidates, builds the QUBO, runs the
classical optimizer, decodes QAOA samples, and checks route feasibility.
QAOA circuit sampling runs locally on Aer in this demo. IBM hardware execution
has not been performed for this presentation.

### 7. What happens when traffic changes?

The app applies a selected simulated traffic profile, rebuilds the scenario's
travel-time/cost inputs, re-optimizes, validates the resulting routes, and
shows before/after values. It does not consume a live traffic feed.

### 8. What happens when a vehicle becomes unavailable?

Fleet Disruption marks that vehicle unavailable, selects a feasible delivery
mix by priority, reruns the existing optimizers where supported, and shows
reassignments and any waitlisted deliveries.

### 9. Are you using live traffic?

No. Traffic changes are simulated. Optional OSRM requests provide road
distances, base durations, or route geometry; they are not a live traffic
feed.

### 10. Did you run on real quantum hardware?

No real hardware job has been run for this demo. Authentication, read-only
backend discovery, and a safe dry run were verified, but no job or circuit was
submitted to IBM hardware.

### 11. Where is the database?

Operation history uses a local SQLite database in the user's application-data
directory. On Windows the default is
`%LOCALAPPDATA%\QuantumRoute\quantumroute_history.sqlite3`.

### 12. What is the role of Streamlit?

Streamlit provides the application interface, workspace navigation, inputs,
tables, metrics, controls, and interactive map presentation.

### 13. What is the role of Python?

Python implements scenario handling, feasibility constraints, route
optimization, QUBO/QAOA integration, re-optimization, validation, and history
storage.

### 14. What is the limitation of the current solution?

The scenarios are small and deterministic, and traffic/fleet events are
simulated. QAOA samples are stochastic, local simulation is not hardware
execution, and neither quantum advantage nor general performance speedup has
been established.

### 15. How is this different from a normal route optimizer?

The application retains classical feasible-route optimization and adds an
inspectable QUBO/QAOA experimental path, then revalidates sampled assignments.
It also demonstrates simulated traffic and fleet-disruption re-optimization
with objective and sustainability comparisons. It does not claim that the
quantum path is better than a normal optimizer.
