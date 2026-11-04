# UR3e + Opentrons Flex cell, orchestrated by IvoryOS

Research automation cell: a UR3e arm on a Vention rail moves trays to and from an
Opentrons Flex. The end goal is the whole cell running unattended from IvoryOS.
It is for research use, so it doesn't need production-grade quality, but it must
fail safe because it runs unattended.

## Hardware and libraries

| Device | Talks via | Notes |
| --- | --- | --- |
| Opentrons Flex | HTTP API, port 31950 | Protocols are written in the Opentrons Python Protocol API and uploaded by the driver |
| UR3e arm | `ur_rtde` (receive, control, dashboard) | Moves follow stored waypoint paths; no active collision detection |
| Vention rail | Vention Python API | The rail has its own controller; the arm is mounted on it |
| Orchestrator | IvoryOS | Builds its UI by introspecting the objects in `cell.py`. Pin the version. |

The arm and the Flex take turns: they never need to work at the same time.

## Plan

- `docs/plan.md`: the nine-phase checklist. Phases 0–6 run entirely in simulation;
  hardware starts only after the IvoryOS workflow passes in sim.
- `docs/phase1-build-guide.md`: detailed design and code sketches for the shared
  driver foundation.

Before starting work, check `docs/plan.md` for the current phase and its "Done when" check.

## Architecture rules

- **Driver/backend split.** Each driver class (`BaseDriver` subclass) owns state,
  preconditions, logging and timing. A backend object owns the I/O. Every driver
  has a `sim` and a `real` backend that share all driver logic.
- **One wrapper.** Every public action is a one-line call into `BaseDriver._run()`.
  Don't duplicate state checks, locking or logging in subclasses.
- **States:** `DISCONNECTED`, `IDLE`, `BUSY`, `PAUSED`, `ERROR`, `ESTOP`.
  Abort lands in `ERROR`, never `IDLE`; only `recover()` leaves `ERROR` or `ESTOP`.
  `status()` asks the device every time, and the device's report overrides cached state.
- **Return on success, raise on failure.** Actions return an `ActionResult`.
  Failures raise `CellError` subclasses: `PreconditionError` (nothing moved, state
  unchanged), `DeviceError` / `ActionTimeout` (something may have moved, state goes to ERROR).
- **Timeouts live in backends.** Backend wait loops take a deadline and raise `ActionTimeout`.
- **One action at a time per driver.** A second action is refused, not queued.
  `abort()` and `status()` take no lock.
- **Locations** are named once in `config/locations.yaml`, with what each place means
  to the Flex (slot), the arm (approach and grip poses) and the rail (station).
  `drivers/locations.py` holds a hand-written `Location` enum that a test keeps in
  sync with the YAML.
- **Sim or real is set in `config/cell.yaml`,** per device. Never hard-code it.
- **Arm poses** are stored in `config/poses.yaml` (joint angles primary), never on the
  teach pendant. The rail moves only when the arm is in a travel pose.
- **No IvoryOS imports in `drivers/`.** IvoryOS touches only `cell.py`. Keep driver
  methods IvoryOS-friendly: type hints, defaults, one-line docstrings, `Location`
  instead of strings, and a leading underscore on anything that isn't a real action.
- **Logging:** child loggers of `cell` (`cell.flex`, `cell.ur3e`, `cell.rail`),
  configured once in `drivers/logging_setup.py`.

## Layout

```text
drivers/     base.py, locations.py, cell.py, logging_setup.py, fake.py, flex/, ur3e/, rail/
config/      cell.yaml, locations.yaml, poses.yaml
protocols/   Flex Python protocols (each must pass opentrons_simulate)
sim/         simulator helpers (URSim setup, etc.)
tests/       pytest
cell.py      IvoryOS entry point
docs/        plan.md, phase1-build-guide.md
```

## Commands

- Run tests: `pytest`
- Simulate a Flex protocol: `opentrons_simulate protocols/<file>.py`

## Working rules for Claude

- Don't write code that moves real hardware unless I ask for it in that session.
  Default every new device in `cell.yaml` to `backend: sim`.
- Every new driver action needs a test against its sim backend.
- When a phase's "Done when" check passes, tick its boxes in `docs/plan.md`.
