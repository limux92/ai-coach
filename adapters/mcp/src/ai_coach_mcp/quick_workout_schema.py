"""Validated model-independent workout prescription and deterministic Zwift export."""
import datetime
import xml.etree.ElementTree as ET
from typing import Annotated, List, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator

# Helper for XML valid characters
def _is_valid_xml_char(ch: str) -> bool:
    code = ord(ch)
    return (
        ch in ("\t", "\n", "\r")
        or 0x20 <= code <= 0xD7FF
        or 0xE000 <= code <= 0xFFFD
        or 0x10000 <= code <= 0x10FFFF
    )

def _validate_xml_string(value: str) -> str:
    if not all(_is_valid_xml_char(c) for c in value):
        raise ValueError("String contains invalid XML characters")
    return value

class WorkoutStep(BaseModel):
    kind: Literal["warmup", "steady", "cooldown"]
    duration_s: Annotated[int, Field(..., strict=True, ge=30, le=1800)]
    power_start: Annotated[float, Field(..., strict=True, ge=0.3, le=1.2, allow_inf_nan=False)]
    power_end: Annotated[float, Field(..., strict=True, ge=0.3, le=1.2, allow_inf_nan=False)]

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _check_step_logic(self) -> "WorkoutStep":
        if self.kind == "steady" and self.power_start != self.power_end:
            raise ValueError("Steady steps must have equal start and end power")
        if self.kind == "warmup" and self.power_end < self.power_start:
            raise ValueError("Warmup end power must be >= start power")
        if self.kind == "cooldown" and self.power_end > self.power_start:
            raise ValueError("Cooldown end power must be <= start power")
        return self

class QuickWorkout(BaseModel):
    schema_version: Literal[1] = 1
    day: datetime.date
    decision: Literal["workout", "rest"]
    title: Annotated[str, Field(..., min_length=1, max_length=80)]
    rationale: Annotated[str, Field(..., min_length=1, max_length=1200)]
    caveats: Annotated[List[Annotated[str, Field(max_length=240)]], Field(default_factory=list, max_length=8)]
    steps: Annotated[List[WorkoutStep], Field(default_factory=list, max_length=60)]

    model_config = ConfigDict(extra="forbid")

    @field_validator("title", "rationale")
    def _validate_xml_title(cls, v: str) -> str:
        return _validate_xml_string(v)

    @field_validator("caveats")
    def _validate_xml_caveats(cls, v: List[str]) -> List[str]:
        return [_validate_xml_string(c) for c in v]

    @model_validator(mode="after")
    def _check_workout(self) -> "QuickWorkout":
        if self.decision == "rest":
            if self.steps:
                raise ValueError("Rest workouts must have no steps")
            return self
        # workout
        if len(self.steps) < 3:
            raise ValueError("Workout must have at least 3 steps")
        if self.steps[0].kind != "warmup":
            raise ValueError("First step must be warmup")
        if self.steps[-1].kind != "cooldown":
            raise ValueError("Last step must be cooldown")
        for step in self.steps[1:-1]:
            if step.kind != "steady":
                raise ValueError("Middle steps must be steady")
        total = sum(s.duration_s for s in self.steps)
        if not (600 <= total <= 5400):
            raise ValueError("Total duration must be between 600 and 5400 seconds")
        return self

    @property
    def duration_s(self) -> int:
        return sum(s.duration_s for s in self.steps)

def to_zwo(plan: QuickWorkout) -> str:
    if plan.decision == "rest":
        raise ValueError("Cannot export rest workout to ZWO")

    root = ET.Element("workout_file")
    ET.SubElement(root, "author").text = "AI Coach"
    ET.SubElement(root, "name").text = plan.title
    description = plan.rationale
    if plan.caveats:
        description += "\n\nCaveats:\n" + "\n".join(f"- {c}" for c in plan.caveats)
    ET.SubElement(root, "description").text = description
    ET.SubElement(root, "sportType").text = "bike"

    workout_el = ET.SubElement(root, "workout")

    for step in plan.steps:
        if step.kind == "warmup":
            el = ET.SubElement(workout_el, "Warmup")
            el.set("Duration", str(step.duration_s))
            el.set("PowerLow", f"{step.power_start:.3f}")
            el.set("PowerHigh", f"{step.power_end:.3f}")
        elif step.kind == "cooldown":
            el = ET.SubElement(workout_el, "Cooldown")
            el.set("Duration", str(step.duration_s))
            el.set("PowerLow", f"{step.power_start:.3f}")
            el.set("PowerHigh", f"{step.power_end:.3f}")
        else:  # steady
            el = ET.SubElement(workout_el, "SteadyState")
            el.set("Duration", str(step.duration_s))
            el.set("Power", f"{step.power_start:.3f}")

    xml_bytes = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    return xml_bytes.decode("utf-8")
