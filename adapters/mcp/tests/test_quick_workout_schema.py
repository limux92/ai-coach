import math
import pytest
from pydantic import ValidationError
from ai_coach_mcp.quick_workout_schema import QuickWorkout, WorkoutStep, to_zwo
import xml.etree.ElementTree as ET

def plan_dict():
    return {
        "schema_version": 1,
        "day": "2026-09-25",
        "decision": "workout",
        "title": "Ride & <recover>",
        "rationale": "Easy endurance",
        "caveats": ["Uses current Zwift FTP"],
        "steps": [
            {"kind": "warmup", "duration_s": 300, "power_start": 0.4, "power_end": 0.6},
            {"kind": "steady", "duration_s": 1200, "power_start": 0.65, "power_end": 0.65},
            {"kind": "cooldown", "duration_s": 300, "power_start": 0.6, "power_end": 0.4},
        ],
    }

def test_valid_parsed_xml():
    d = plan_dict()
    plan = QuickWorkout.model_validate(d)
    xml = to_zwo(plan)
    root = ET.fromstring(xml)
    assert root.tag == "workout_file"
    assert root.find("author").text == "AI Coach"
    assert root.find("name").text == d["title"]
    assert any(caveat in root.find("description").text for caveat in d["caveats"])
    workout = root.find("workout")
    assert workout is not None
    nodes = list(workout)
    assert len(nodes) == 3
    assert nodes[0].tag == "Warmup"
    assert nodes[1].tag == "SteadyState"
    assert nodes[2].tag == "Cooldown"
    assert nodes[0].get("Duration") == "300"
    assert float(nodes[2].get("PowerHigh")) == 0.4
    assert float(nodes[1].get("Power")) == 0.65
    assert plan.duration_s == 1800

@pytest.mark.parametrize("duration,expected", [
    (True, "300"),
    ("300", "300"),
    (29, "29"),
    (1801, "1801"),
])
def test_duration_validation(duration, expected):
    d = plan_dict()
    d["steps"][0]["duration_s"] = duration
    with pytest.raises(ValidationError):
        QuickWorkout(**d)

@pytest.mark.parametrize("power,expected", [
    (True, "0.5"),
    ("0.5", "0.5"),
    (math.nan, "nan"),
    (math.inf, "inf"),
    (0.29, "0.29"),
    (1.21, "1.21"),
])
def test_power_validation(power, expected):
    d = plan_dict()
    d["steps"][0]["power_start"] = power
    with pytest.raises(ValidationError):
        WorkoutStep(**d["steps"][0])

@pytest.mark.parametrize("extra", [
    {"extra": "value"},
    {"steps": [{"kind": "warmup", "duration_s": 300, "power_start": 0.4, "power_end": 0.6, "extra": "x"}]},
])
def test_extras_invalid(extra):
    d = plan_dict()
    d.update(extra)
    with pytest.raises(ValidationError):
        QuickWorkout(**d)

@pytest.mark.parametrize("title", [
    "\x01",
    "\ud800",
    "\ufffe",
])
def test_invalid_title(title):
    d = plan_dict()
    d["title"] = title
    with pytest.raises(ValidationError):
        QuickWorkout(**d)

def test_rest_invalid():
    d = plan_dict()
    d.update(decision="rest", steps=[])
    plan = QuickWorkout(**d)
    with pytest.raises(ValueError):
        to_zwo(plan)
    d["steps"] = [{"kind": "warmup", "duration_s": 300, "power_start": 0.4, "power_end": 0.6}]
    with pytest.raises(ValidationError):
        QuickWorkout(**d)

def test_bad_ramps_and_lengths():
    d = plan_dict()
    # bad ramp warmup
    d["steps"][0]["power_start"] = 0.8
    with pytest.raises(ValidationError):
        WorkoutStep(**d["steps"][0])
    # bad ramp cooldown
    d["steps"][2]["power_start"] = 0.6
    d["steps"][2]["power_end"] = 0.8
    with pytest.raises(ValidationError):
        WorkoutStep(**d["steps"][2])
    # steady unequal
    d["steps"][1]["power_start"] = 0.6
    d["steps"][1]["power_end"] = 0.7
    with pytest.raises(ValidationError):
        WorkoutStep(**d["steps"][1])
    # empty workout
    d["steps"] = []
    with pytest.raises(ValidationError):
        QuickWorkout(**d)
    # missing ramps
    d["steps"] = [
        {"kind": "warmup", "duration_s": 300, "power_start": 0.4, "power_end": 0.6},
        {"kind": "steady", "duration_s": 1200, "power_start": 0.65, "power_end": 0.65},
    ]
    with pytest.raises(ValidationError):
        QuickWorkout(**d)
    # too short
    d["steps"] = [
        {"kind": "warmup", "duration_s": 60, "power_start": 0.4, "power_end": 0.6},
        {"kind": "steady", "duration_s": 60, "power_start": 0.65, "power_end": 0.65},
        {"kind": "cooldown", "duration_s": 60, "power_start": 0.6, "power_end": 0.4},
    ]
    with pytest.raises(ValidationError):
        QuickWorkout(**d)
    # too long
    d["steps"] = [
        {"kind": "warmup", "duration_s": 1800, "power_start": 0.4, "power_end": 0.6},
        {"kind": "steady", "duration_s": 1800, "power_start": 0.65, "power_end": 0.65},
        {"kind": "steady", "duration_s": 1800, "power_start": 0.65, "power_end": 0.65},
        {"kind": "cooldown", "duration_s": 1800, "power_start": 0.6, "power_end": 0.4},
    ]
    with pytest.raises(ValidationError):
        QuickWorkout(**d)
