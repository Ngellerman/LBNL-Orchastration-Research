"""Smoke tests: pinned dependencies import and Opentrons simulation works."""
import io
import textwrap

import pytest

OT2_PROTOCOL = """
requirements = {"robotType": "OT-2", "apiLevel": "2.20"}
def run(ctx):
    tips = ctx.load_labware("opentrons_96_tiprack_300ul", 1)
    plate = ctx.load_labware("corning_96_wellplate_360ul_flat", 2)
    p = ctx.load_instrument("p300_single_gen2", "right", tip_racks=[tips])
    p.transfer(50, plate["A1"], plate["B1"])
"""

FLEX_PROTOCOL = """
requirements = {"robotType": "Flex", "apiLevel": "2.20"}
def run(ctx):
    tips = ctx.load_labware("opentrons_flex_96_tiprack_200ul", "C1")
    plate = ctx.load_labware("corning_96_wellplate_360ul_flat", "C2")
    ctx.load_trash_bin("A3")
    p = ctx.load_instrument("flex_1channel_1000", "left", tip_racks=[tips])
    p.transfer(50, plate["A1"], plate["B1"])
"""


def test_imports():
    import httpx, ivoryos, pydantic, rtde_control, rtde_receive, yaml  # noqa: F401


@pytest.mark.parametrize("source", [OT2_PROTOCOL, FLEX_PROTOCOL], ids=["ot2", "flex"])
def test_opentrons_simulate(source):
    from opentrons.simulate import simulate

    runlog, _ = simulate(io.StringIO(textwrap.dedent(source)), file_name="smoke.py")
    actions = " ".join(entry["payload"]["text"] for entry in runlog)
    assert "Aspirating 50.0 uL" in actions
    assert "Dispensing 50.0 uL" in actions
