"""Running prescriptions with open warmup/cooldown and timed effort intervals."""
import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .quick_workout_schema import _validate_xml_string


class RunningStep(BaseModel):
    kind: Literal['warmup', 'run', 'recovery', 'cooldown']
    duration_type: Literal['time', 'lap_press']
    duration_s: Annotated[int | None, Field(strict=True, ge=30, le=1800)]
    effort: Literal['easy', 'steady', 'hard']

    model_config = ConfigDict(extra='forbid')

    @model_validator(mode='after')
    def check_step(self):
        if self.kind in {'warmup', 'cooldown'}:
            if self.duration_type != 'lap_press' or self.duration_s is not None or self.effort != 'easy':
                raise ValueError('Warmup/cooldown require easy effort and lap_press with null duration_s')
        else:
            if self.duration_type != 'time' or self.duration_s is None:
                raise ValueError('Run/recovery intervals require a timed duration')
            if self.kind == 'recovery' and self.effort != 'easy':
                raise ValueError('Recovery effort must be easy')
        return self


class RunningWorkout(BaseModel):
    schema_version: Literal[1] = 1
    sport: Literal['running'] = 'running'
    day: datetime.date
    decision: Literal['workout', 'rest']
    title: Annotated[str, Field(min_length=1, max_length=80)]
    rationale: Annotated[str, Field(min_length=1, max_length=1200)]
    caveats: Annotated[list[Annotated[str, Field(max_length=240)]], Field(default_factory=list, max_length=8)]
    steps: Annotated[list[RunningStep], Field(default_factory=list, max_length=50)]

    model_config = ConfigDict(extra='forbid')

    @field_validator('title', 'rationale')
    @classmethod
    def validate_text(cls, value):
        return _validate_xml_string(value)

    @field_validator('caveats')
    @classmethod
    def validate_caveats(cls, values):
        return [_validate_xml_string(value) for value in values]

    @model_validator(mode='after')
    def check_workout(self):
        if self.decision == 'rest':
            if self.steps:
                raise ValueError('Rest recommendations have no steps')
            return self
        if len(self.steps) < 3 or self.steps[0].kind != 'warmup' or self.steps[-1].kind != 'cooldown':
            raise ValueError('Running workouts require warmup, main set and cooldown')
        if not any(step.kind == 'run' for step in self.steps):
            raise ValueError('At least one run interval is required')
        if any(step.kind not in {'run', 'recovery'} for step in self.steps[1:-1]):
            raise ValueError('Only run/recovery steps belong in the main set')
        if not 300 <= self.duration_s <= 2400:
            raise ValueError('Timed main set must be 300..2400 seconds')
        return self

    @property
    def duration_s(self):
        """Timed main set only; never a total for the lap-controlled workout."""
        return sum(step.duration_s for step in self.steps if step.duration_type == 'time')
