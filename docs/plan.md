# UR3e + Opentrons Flex IvoryOS Integration — Work Checklist

Oct 29, 2026 · @Nicholas

## How to use this checklist

Nine phases take you from an empty repo to the UR3e, Vention rail and Flex running one tray-transfer workflow from IvoryOS. Everything is built against simulators first (Phases 0–6). Hardware is touched only after the IvoryOS workflow passes in simulation (Phases 7–8).

- Each task is sized for one sitting, roughly 1–3 hours.
- Each phase ends with a **Done when** check. Don't start the next phase until it passes.
- Phases 2, 3 and 4 (Flex, UR3e and rail drivers) are independent and can be done in any order or interleaved.
- Repeat counts in the Done-when checks (20 runs, 20 handoffs) are suggested targets; tighten them if your process needs it.

&#91;embedded content: roadmap · 9 phases, 1 gate before hardware\]

The three drivers can be built side by side. Nothing runs on hardware until the IvoryOS workflow passes in simulation.

## Phase 0 — Setup and decisions

Get the repo, network and key layout decisions in place before writing driver code.

- [x] Create the git repo: `drivers/` (base, flex, ur3e, rail), `config/` (cell, locations, poses), `protocols/` (Flex Python protocols), `sim/`, `tests/`, and `cell.py` as the IvoryOS entry point.
- [x] Set up a Python environment and pin versions: `ur_rtde`, `requests` or `httpx`, `pyyaml`, `pydantic`, `ivoryos`, `opentrons` (for local protocol simulation), `pytest`.
- [ ] Put the UR3e, Flex, Vention controller and orchestration PC on one isolated subnet with static IPs, recorded in `config/cell.yaml`.
- [ ] Confirm each controller answers from the PC: Flex `GET /health` on port 31950, UR3e in remote-control mode on the pendant, Vention controller reachable from its Python API.
- [ ] Sketch the cell layout: rail travel, Flex position, tray storage, and every place a tray can sit. Give each one a name.
- [ ] Decide the Flex handoff slot and how the arm reaches it (arm opens the door, panel removed, or an actuator). Write the decision down.
- [ ] Confirm which gripper the UR3e uses and how it is controlled (URCap, tool I/O or Modbus).
- [ ] Safety basics: E-stops within reach, reduced-speed limits set in the UR safety settings for bring-up, and a short list of risks for unattended runs.

**Done when:** the repo exists, all three controllers answer from the PC, and the location list and handoff decision are written down.

## Phase 1 — Shared driver foundation

One base interface that every machine plugs into, so the orchestrator never needs to know which machine it's talking to.

- [ ] `DeviceState` enum: `IDLE`, `BUSY`, `PAUSED`, `ERROR`, `ESTOP`, `DISCONNECTED`.
- [ ] `ActionResult` dataclass: `ok`, `message`, `data`, `duration_s`.
- [ ] `BaseDriver` abstract class: `connect()`, `disconnect()`, `status()`, `abort()`, `pause()`, `resume()`, `home()`, `recover()`. The constructor takes `backend="sim"` or `"real"`.
- [ ] Custom exceptions: `DeviceError`, `PreconditionError`, `ActionTimeout`.
- [ ] `config/locations.yaml` and a pydantic loader. Each location stores its name and how each device sees it: Flex slot, UR3e pose name, rail station.
- [ ] A `Location` enum (or `Literal`) matching the YAML, so IvoryOS can offer a fixed list instead of free text. Check the IvoryOS input-types docs for which type hints it renders as choices.
- [ ] Logging: one named logger per driver, writing to file and console with timestamps.
- [ ] pytest skeleton, plus a `FakeDriver` subclass that exercises states and results.

**Done when:** `FakeDriver` passes tests for every state transition, abort and result object.

Step-by-step build guide with code sketches: Phase 1 build guide

## Phase 2 — Opentrons Flex driver

The Flex driver uploads protocols, starts runs with parameters and reports back. The Flex itself does the liquid handling and gripper moves.

**Protocols**

- [ ] `protocols/move_to_handoff.py`: the Flex gripper moves the tray to the handoff slot.
- [ ] `protocols/take_from_handoff.py`: the reverse move.
- [ ] `protocols/cleanup.py`: puts the modules in a safe state (lids open, shaking and heating off).
- [ ] One real experiment protocol that uses runtime parameters (e.g. starting tip, volumes).
- [ ] Check every protocol passes `opentrons_simulate` locally.

**Sim backend**

- [ ] `FlexSimBackend`: a fake run lifecycle (queued → running → succeeded) with a set duration, plus switches to inject a failure, a timeout or an "awaiting recovery" pause.

**Real backend (HTTP API, port 31950)**

- [ ] Thin client covering health, protocol upload and list, run create (with runtime parameters), run actions (play, pause, stop), run status, run commands and errors, home, and door status.
- [ ] `ensure_protocol(file)`: hash the file, upload only if it changed, cache the protocol ID.
- [ ] `run(protocol, params) -> run_id` and `wait(run_id, timeout)`. Treat "awaiting recovery" as an error, never as still running.
- [ ] `status()` maps the robot and current run to `DeviceState`. `abort()` stops the current run. `home()` homes the gantry.
- [ ] Inventory the Flex can't track across runs (tips used, tray present) kept in a small JSON state file and passed in as runtime parameters.
- [ ] Preconditions: no active run before starting a new one, door state checked where it matters.

**Done when:** in sim, `run → wait → result` behaves correctly for success, failure, timeout and awaiting recovery, and all protocols pass simulation.

## Phase 3 — UR3e driver

The arm only moves along stored paths between named poses, and it is built and tested in URSim before it touches the real arm.

- [ ] Run URSim e-Series in Docker and connect the ur\_rtde receive and control interfaces.
- [ ] Read-only first: joint positions, TCP pose, robot mode, safety mode and protective-stop flags, all mapped into `status()`.
- [ ] `config/poses.yaml`: named poses with joint angles (primary), TCP pose (reference), rail station, speed and acceleration.
- [ ] Waypoint graph: list the allowed moves between poses. `move_to(pose)` finds a path from the current waypoint and runs it segment by segment. It refuses if the arm isn't within tolerance of a known waypoint.
- [ ] Gripper wrapper: open, close, width, object detected, plus a sim stub.
- [ ] `pick(location)` and `place(location)`: approach, slow final descent (`moveUntilContact` or reduced speed), grip, check the grip, retract.
- [ ] Pose-teach script: freedrive the arm, then save the current joints into `poses.yaml` under a name.
- [ ] `abort()` and `pause()` (stopJ or stopScript), protective-stop handling through the dashboard client (unlock, restart), and `recover()` back to the nearest waypoint.
- [ ] URSim tests: every edge in the graph runs, abort mid-move works, protective-stop recovery works.

**Done when:** in URSim the arm picks and places between every named location and recovers from an abort and a protective stop without hand edits.

## Phase 4 — Vention rail driver

The rail gets its own driver, plus one layer that coordinates rail and arm so neither moves at the wrong time.

- [ ] Wrap Vention's Python API in `RailDriver(BaseDriver)`: `home()`, `move_to(station)`, `position()`, `status()`, `abort()`.
- [ ] Sim backend: a virtual position that moves at a set speed.
- [ ] Add named rail stations to `locations.yaml`.
- [ ] Interlock: the rail moves only when the arm is in a travel pose; the arm moves only when the rail is idle at a known station.
- [ ] `Mover` layer with `go_to(location)`: moves the rail first when needed, then the arm. This is what pick and place call.

**Done when:** in sim, sending the arm to any location moves the rail first when needed, and unsafe orders are refused with a clear error.

## Phase 5 — Full-cell simulation

Run the whole transfer in plain Python against all the sim backends before adding IvoryOS, so orchestration bugs and driver bugs stay separate.

- [ ] `cell.py` builds every driver from config with `backend="sim"`.
- [ ] Write one workflow as a plain Python function: the Flex runs the experiment, moves the tray to the handoff slot, the arm picks it, the rail moves, and the arm places it at its destination (and the reverse).
- [ ] Add precondition checks between steps: Flex idle, handoff slot empty or full as expected, access open.
- [ ] Failure-injection tests: the Flex fails mid-run, the arm protective-stops while holding the tray, the rail times out. Each must leave the cell in a known, logged state.
- [ ] Optional: watch the arm in the URSim GUI during runs. A 3D cell model for collision previews can come later.

**Done when:** the workflow runs 20 times in a row in sim, and every injected failure ends in a logged state you can recover from.

## Phase 6 — IvoryOS integration

IvoryOS builds its UI by introspecting the Python objects in `cell.py`, so this phase is mostly about tidying what those objects expose ([IvoryOS quick start](https://ivoryos.readthedocs.io/en/latest/integrators/quick-start.html)).

- [ ] `pip install ivoryos`. In `cell.py`, create the driver instances at module level, then call `ivoryos.run(__name__, logger=[...])` with your driver logger names.
- [ ] Open `http://localhost:8000` and change the default `admin` / `admin` login.
- [ ] Devices tab: confirm each driver's actions appear. Keep helpers private with a leading underscore so only real actions show.
- [ ] Tidy signatures: type hints, defaults, one-line docstrings, `Location` choices instead of strings.
- [ ] Add high-level functions at module level, such as `transfer_tray(src, dst)` and `run_experiment(protocol, params)`, so a non-programmer drags one block rather than ten.
- [ ] Design tab: rebuild the Phase 5 workflow from blocks, using `#parameter_name` for values set at run time.
- [ ] Add a pause step with a notification handler (Slack or email) for failures during unattended runs.
- [ ] Check what IvoryOS does when a step raises an exception, and make sure every driver leaves a safe state when that happens.
- [ ] Run the workflow from IvoryOS against sim and check the records in the Data tab.

**Done when:** the full transfer workflow runs from the IvoryOS UI against sim, with run-time parameters set in the UI and results recorded in the Data tab.

## Phase 7 — Hardware bring-up

Switch one machine at a time to `backend="real"` and prove it alone before connecting anything to anything else.

- [ ] Flex alone: upload the protocols through the driver and run each one, first with empty labware, then with real labware.
- [ ] Calibrate the Flex gripper and run labware position checks for the handoff slot.
- [ ] UR3e alone at reduced speed: connect, read status, teach every pose with freedrive, then run each edge of the waypoint graph at 10–25% speed with a hand on the E-stop.
- [ ] Rail alone: home it, visit each station, check it returns to the same position.
- [ ] Handoff mechanics: with a tray in the handoff slot, have the arm pick and place it 20 times. Log every miss, and add chamfers or compliance to the fingers or nest if needed.
- [ ] Access check: confirm the arm can reach the handoff slot through the chosen door or panel, and that the door sensor reads correctly.

**Done when:** each machine runs all its actions on hardware from its driver, and 20 handoffs in a row succeed.

## Phase 8 — Integrated hardware runs

Run the same IvoryOS workflow on hardware, attended at first and then unattended overnight.

- [ ] Run the IvoryOS workflow on hardware, attended, with the arm at reduced speed. Then raise to full speed.
- [ ] Failure drills on hardware: abort from IvoryOS, press each E-stop, trigger a Flex error-recovery pause, power-cycle one controller mid-run.
- [ ] Attended multi-cycle run of 10 cycles.
- [ ] Unattended overnight run with notifications on. Review the logs and Data tab the next morning.
- [ ] Write a runbook for startup, shutdown and recovery, and save it in this project.

**Done when:** an overnight unattended run either completes or stops safely and sends a notification, and someone else can start the cell using only the runbook.

## Later

These are worth doing once the core cell is running, but none of them block the IvoryOS integration.

- [ ] Camera driver for tray presence and seating checks, called from `pick` and `place`.
- [ ] Add a third machine: subclass `BaseDriver`, add a sim backend, add its locations, then expose it in `cell.py`.
- [ ] 3D cell model for collision previews before teaching new paths.
- [ ] Review MADSci's node model if the line grows past one PC.
