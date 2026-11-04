# Phase 1 build guide

## Overview

Phase 1 builds the small package every driver inherits from: states, results, errors, a base class, locations, config and logging. It touches no hardware and imports nothing from ur\_rtde, Opentrons or IvoryOS, so it is fully testable on a laptop.

The central design choice is to **split each driver into logic and I/O**. The driver class owns state, preconditions, logging and timing. A small backend object owns the actual communication. A sim backend and a real backend then share every line of driver logic, so simulation tests cover the code that runs on hardware.

```text
drivers/
  __init__.py
  base.py            # DeviceState, ActionResult, exceptions, BaseDriver
  locations.py       # Location enum + YAML loader
  cell.py            # build_cell(): reads cell.yaml, builds drivers
  logging_setup.py   # one place that configures logging
  fake.py            # FakeDriver + FakeBackend for tests
config/
  cell.yaml
  locations.yaml
tests/
  test_base.py
  test_locations.py
```

Suggested order, about five sittings:

1. States, results and errors (Steps 1–2).
2. `BaseDriver` and its wrapper (Step 3).
3. Locations YAML, loader and enum (Step 4).
4. Cell config, factory and logging (Step 5).
5. `FakeDriver` and the test suite (Step 6). Writing tests alongside Steps 1–5 is even better.

## Step 1 — States

Every driver is always in exactly one of six states, and the base class, not each driver, enforces which calls are allowed in each.

```python
from enum import Enum

class DeviceState(str, Enum):
    DISCONNECTED = "disconnected"
    IDLE = "idle"
    BUSY = "busy"
    PAUSED = "paused"
    ERROR = "error"
    ESTOP = "estop"
```

The `str` mixin makes states print and serialize as plain text in logs, JSON and the IvoryOS UI.

| Method | Allowed from | Ends in |
| --- | --- | --- |
| `connect()` | DISCONNECTED | IDLE, or ERROR if the device reports a fault |
| `status()` | any | unchanged, unless the device reports E-stop or a fault |
| any action (`run`, `move_to`, `pick`…) | IDLE | BUSY while running, then IDLE on success or ERROR on failure |
| `pause()` | BUSY | PAUSED |
| `resume()` | PAUSED | BUSY |
| `abort()` | BUSY, PAUSED | ERROR, with reason "aborted" (no-op from IDLE) |
| `home()` | IDLE, ERROR | IDLE |
| `recover()` | ERROR, ESTOP | IDLE, or stays ERROR if the device isn't safe to resume |
| `disconnect()` | any except BUSY | DISCONNECTED |

Two rules make this work unattended:

- **Abort lands in ERROR, not IDLE.** The next action is refused until someone or something calls `recover()`. A workflow can't carry on with a tray in an unknown spot.
- **The device's own report wins.** `status()` asks the hardware every time. If the arm is protective-stopped or the Flex E-stop is pressed, the driver moves to ERROR or ESTOP even if its cached state said IDLE.

* [ ] Write `DeviceState` in `drivers/base.py`.
* [ ] Store the rules above as a dict in `base.py` (method name → allowed states), so the table and the code can't drift apart.

## Step 2 — Results and errors

Actions **return** an `ActionResult` when they succeed and **raise** when they fail. A raised error stops a Python workflow by default, which is the safe behaviour for an unattended cell. A returned `ok=False` is easy to ignore by accident.

```python
from dataclasses import dataclass, field

@dataclass
class ActionResult:
    device: str
    action: str
    data: dict = field(default_factory=dict)   # e.g. run_id, final pose, grip width
    duration_s: float = 0.0
    message: str = ""

class CellError(Exception):
    """Base for every error the cell raises."""
    def __init__(self, device: str, message: str, state=None):
        super().__init__(f"[{device}] {message}")
        self.device, self.state = device, state

class NotConnectedError(CellError): ...   # call made before connect()
class PreconditionError(CellError): ...   # refused before anything moved
class DeviceError(CellError): ...         # device reported a fault mid-action
class ActionTimeout(CellError): ...       # action took longer than its limit
```

The split matters for recovery. A `PreconditionError` means nothing moved, so the state stays as it was. A `DeviceError` or `ActionTimeout` means something may have moved, so the driver goes to ERROR.

- [ ] Add `ActionResult` and the exception classes to `base.py`.
- [ ] Make every error message name the device, the action and the state it was in.

## Step 3 — BaseDriver

Every public action is a one-line call into one private wrapper, `_run()`, which handles state checks, locking, logging, timing and error mapping. Drivers never repeat that logic, and IvoryOS only sees clean one-line methods with docstrings.

```python
import logging, threading, time
from typing import Any, Callable, Protocol

class Backend(Protocol):
    """What every sim or real backend must provide."""
    def connect(self) -> None: ...
    def disconnect(self) -> None: ...
    def read_status(self) -> dict: ...   # must include "estop": bool and "fault": str | None
    def abort(self) -> None: ...

class BaseDriver:
    def __init__(self, name: str, backend: Backend):
        self.name = name
        self._backend = backend
        self._state = DeviceState.DISCONNECTED
        self._error = ""
        self._lock = threading.Lock()
        self.log = logging.getLogger(f"cell.{name}")

    def connect(self) -> ActionResult:
        """Open the connection to the device."""
        return self._run("connect", self._backend.connect, allowed={DeviceState.DISCONNECTED})

    def status(self) -> dict:
        """Ask the device for its state; the device's report wins."""
        report = {} if self._state is DeviceState.DISCONNECTED else self._backend.read_status()
        if report.get("estop"):
            self._set_state(DeviceState.ESTOP, "E-stop reported by device")
        elif report.get("fault"):
            self._set_state(DeviceState.ERROR, report["fault"])
        return {"device": self.name, "state": self._state.value, "error": self._error, **report}

    def abort(self) -> ActionResult:
        """Stop the current action. Takes no lock: it must work mid-action."""
        if self._state in (DeviceState.BUSY, DeviceState.PAUSED):
            self._backend.abort()
            self._set_state(DeviceState.ERROR, "aborted")
        return ActionResult(self.name, "abort")

    def _run(self, action: str, fn: Callable[..., Any], *,
             allowed=frozenset({DeviceState.IDLE}), **kwargs) -> ActionResult:
        if self._state not in allowed:
            raise PreconditionError(self.name, f"{action} not allowed while {self._state.value}", self._state)
        if not self._lock.acquire(blocking=False):
            raise PreconditionError(self.name, f"{action} refused: another action is running", self._state)
        self._set_state(DeviceState.BUSY)
        t0 = time.monotonic()
        self.log.info("start %s %s", action, kwargs)
        try:
            data = fn(**kwargs) or {}
        except CellError as e:
            self._set_state(DeviceState.ERROR, str(e))
            raise
        except Exception as e:
            self._set_state(DeviceState.ERROR, repr(e))
            raise DeviceError(self.name, f"{action} failed: {e}") from e
        finally:
            self._lock.release()
        if self._state is not DeviceState.BUSY:   # aborted or E-stopped mid-action
            raise DeviceError(self.name, f"{action} interrupted: {self._error}", self._state)
        self._set_state(DeviceState.IDLE)
        dt = time.monotonic() - t0
        self.log.info("done %s in %.2fs", action, dt)
        return ActionResult(self.name, action, data=data, duration_s=dt)

    def _set_state(self, new: DeviceState, reason: str = "") -> None:
        if new is not self._state:
            self.log.info("state %s -> %s %s", self._state.value, new.value, reason)
        self._state, self._error = new, reason
```

A concrete driver then looks like this (Phase 2 fills in the backend):

```python
class FlexDriver(BaseDriver):
    def run_protocol(self, protocol: str, params: dict | None = None) -> ActionResult:
        """Run an uploaded protocol and wait for it to finish."""
        return self._run("run_protocol", self._backend.run_and_wait,
                         protocol=protocol, params=params or {})
```

Things to get right:

- **Timeouts live in the backend.** `_run()` can't interrupt a blocking call. Each backend's wait loop takes a deadline and raises `ActionTimeout` itself.
- **Mid-action abort.** `abort()` changes the state while `_run()` is still inside `fn`. The check after `fn` returns turns that into an error instead of silently going back to IDLE.
- **`home()`, `recover()`, `pause()`, `resume()`** follow the same pattern with the allowed states from Step 1. `recover()` from ESTOP calls `status()` first and refuses if the E-stop is still pressed. A backend that can't pause raises `PreconditionError("pause not supported")`.
- **Keep helpers private.** Anything that isn't a real action gets a leading underscore so it stays out of the IvoryOS UI.

* [ ] Write `Backend` and `BaseDriver` with `connect`, `disconnect`, `status`, `abort` and `_run`.
* [ ] Add `home`, `recover`, `pause`, `resume` using the Step 1 table.
* [ ] Write one throwaway subclass with a `wait(seconds)` action to try the wrapper by hand.

## Step 4 — Locations

Each physical place a tray can sit has one name in `locations.yaml`, along with what that place means to each device. That one name is how the Flex, arm and rail drivers agree on where the handoff is.

```yaml
# config/locations.yaml  (slot, pose and station names are placeholders)
locations:
  flex_handoff:
    description: Flex deck slot the arm picks from and places to
    flex:  { slot: D4 }
    ur3e:  { approach_pose: flex_handoff_above, grip_pose: flex_handoff_grip }
    rail:  { station: flex }
  storage_1:
    description: First tray nest on the storage rack
    ur3e:  { approach_pose: storage_1_above, grip_pose: storage_1_grip }
    rail:  { station: storage }
```

```python
from enum import Enum
from pathlib import Path
import yaml
from pydantic import BaseModel

class FlexRef(BaseModel):
    slot: str

class URRef(BaseModel):
    approach_pose: str
    grip_pose: str

class RailRef(BaseModel):
    station: str

class LocationSpec(BaseModel):
    description: str = ""
    flex: FlexRef | None = None
    ur3e: URRef | None = None
    rail: RailRef | None = None

class LocationsFile(BaseModel):
    locations: dict[str, LocationSpec]

class Location(str, Enum):
    """Hand-written to match locations.yaml; a test keeps them in sync."""
    FLEX_HANDOFF = "flex_handoff"
    STORAGE_1 = "storage_1"

def load_locations(path: Path) -> dict[str, LocationSpec]:
    return LocationsFile.model_validate(yaml.safe_load(path.read_text())).locations
```

Why a hand-written enum rather than one generated from the YAML: IvoryOS and your editor both see a fixed list of choices, and a typo becomes an immediate test failure instead of a runtime surprise. Check the IvoryOS input-types docs to confirm it renders `Enum` hints as a dropdown.

- [ ] Write the pydantic models and `load_locations()` in `drivers/locations.py`.
- [ ] Fill `locations.yaml` with every place from your Phase 0 layout sketch.
- [ ] Write the `Location` enum to match.
- [ ] Add a `validate_refs(poses, stations)` hook, empty for now. Phases 3–4 use it to check every pose and station named here actually exists.

## Step 5 — Cell config, factory and logging

One YAML file says which drivers exist and whether each runs against sim or real hardware. Moving a machine to hardware is then a one-word edit, and you can mix (real Flex, simulated arm), which helps a lot in Phase 7.

```yaml
# config/cell.yaml  (hosts are placeholders)
log_dir: logs
devices:
  flex:  { driver: flex, backend: sim, host: 192.168.10.20 }
  ur3e:  { driver: ur3e, backend: sim, host: 192.168.10.30 }
  rail:  { driver: rail, backend: sim, host: 192.168.10.40 }
```

```python
# drivers/cell.py
from pathlib import Path
import yaml

# (driver, backend) -> (driver class, backend class). Phases 2-4 add entries.
REGISTRY = {
    ("fake", "sim"): (FakeDriver, FakeBackend),
}

def build_cell(path: Path) -> dict[str, BaseDriver]:
    cfg = yaml.safe_load(path.read_text())
    configure_logging(Path(cfg.get("log_dir", "logs")))
    drivers = {}
    for name, d in cfg["devices"].items():
        driver_cls, backend_cls = REGISTRY[(d["driver"], d["backend"])]
        opts = {k: v for k, v in d.items() if k not in ("driver", "backend")}
        drivers[name] = driver_cls(name, backend_cls(**opts))
    return drivers
```

```python
# drivers/logging_setup.py
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

def configure_logging(log_dir: Path, level: int = logging.INFO) -> None:
    root = logging.getLogger("cell")
    if root.handlers:            # already configured; don't add duplicate handlers
        return
    log_dir.mkdir(parents=True, exist_ok=True)
    root.setLevel(level)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    for h in (RotatingFileHandler(log_dir / "cell.log", maxBytes=5_000_000, backupCount=10),
              logging.StreamHandler()):
        h.setFormatter(fmt)
        root.addHandler(h)
```

Every driver logs to a child of `cell` (`cell.flex`, `cell.ur3e`, `cell.rail`), so one file holds the whole cell's history in time order. In Phase 6, pass `"cell"` to `ivoryos.run(__name__, logger=...)` and the same log shows up in IvoryOS.

- [ ] Write `cell.yaml` with all three devices on `backend: sim` and driver: fake for now. Switch each to its real driver name as Phases 2–4 register it.
- [ ] Write `configure_logging()` and `build_cell()`.
- [ ] Make an unknown `(driver, backend)` pair raise a clear error that lists the registered pairs.

## Step 6 — FakeDriver and tests

A fake backend you can push into every bad situation is what proves the base class works. It also becomes the template for the real sim backends in Phases 2–4.

```python
# drivers/fake.py
import threading, time

class FakeBackend:
    def __init__(self, action_s: float = 0.05, **_):   # **_ swallows host etc. from cell.yaml
        self.action_s = action_s
        self.estop = False
        self.fault: str | None = None
        self.fail_next: Exception | None = None
        self._abort = threading.Event()

    def connect(self): pass
    def disconnect(self): pass
    def home(self): pass
    def recover(self): self.fault = None
    def read_status(self): return {"estop": self.estop, "fault": self.fault}
    def abort(self): self._abort.set()

    def work(self, seconds: float, timeout_s: float = 5.0) -> dict:
        if self.fail_next:
            err, self.fail_next = self.fail_next, None
            raise err
        start = time.monotonic()
        while time.monotonic() - start < seconds:
            if self._abort.is_set():
                self._abort.clear()
                return {}
            if time.monotonic() - start > timeout_s:
                raise ActionTimeout("fake", "work timed out")
            time.sleep(0.005)
        return {"worked_s": seconds}

class FakeDriver(BaseDriver):
    def work(self, seconds: float = 0.05, timeout_s: float = 5.0) -> ActionResult:
        """Pretend to do something for `seconds`."""
        return self._run("work", self._backend.work, seconds=seconds, timeout_s=timeout_s)
```

The `Backend` protocol from Step 3 also needs `home()` and `recover()`, since the base class calls them.

| Test | What it proves |
| --- | --- |
| `test_starts_disconnected` | New driver is DISCONNECTED; any action raises `PreconditionError` |
| `test_connect_then_idle` | `connect()` lands in IDLE and returns an `ActionResult` |
| `test_action_success` | `work()` returns data and a duration above zero, ends in IDLE |
| `test_precondition_keeps_state` | A refused call leaves the state exactly as it was |
| `test_failure_goes_to_error` | `fail_next = RuntimeError()` raises `DeviceError` and lands in ERROR |
| `test_error_blocks_until_recover` | Actions are refused in ERROR; `recover()` returns to IDLE |
| `test_abort_mid_action` | `abort()` from another thread raises `DeviceError` in the caller and lands in ERROR |
| `test_timeout` | `work(seconds=1, timeout_s=0.05)` raises `ActionTimeout` and lands in ERROR |
| `test_device_estop_wins` | `estop = True`, then `status()` reports ESTOP even though the cache said IDLE |
| `test_one_action_at_a_time` | A second action while one runs is refused, not queued |
| `test_location_enum_matches_yaml` | `Location` members equal the keys in `locations.yaml` |
| `test_bad_locations_yaml` | A missing field or wrong type raises a pydantic validation error |
| `test_build_cell` | `build_cell()` returns three drivers that all connect |

The abort test is the trickiest to write, so here it is:

```python
import threading, pytest

@pytest.fixture
def drv():
    d = FakeDriver("fake", FakeBackend())
    d.connect()
    return d

def test_abort_mid_action(drv):
    threading.Timer(0.02, drv.abort).start()
    with pytest.raises(DeviceError):
        drv.work(seconds=1.0)
    assert drv.status()["state"] == "error"
    with pytest.raises(PreconditionError):
        drv.work()
    drv.recover()
    assert drv.status()["state"] == "idle"
```

- [ ] Write `FakeBackend` and `FakeDriver` in `drivers/fake.py`.
- [ ] Write every test in the table.
- [ ] Run `pytest` and fix the base class until everything passes.

**Done when:** `pytest` passes every test above, and `build_cell(Path("config/cell.yaml"))` returns three fake drivers that connect, run `work()`, abort and recover.
