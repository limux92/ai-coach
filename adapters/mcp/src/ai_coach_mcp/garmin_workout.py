"""Small FIT running workout encoder: lap-controlled bookends and timed intervals.

FIT file_id/workout/workout_step messages follow Garmin's Workout File profile.
Warmup/cooldown duration is open (LAP press), not a fixed time or distance.
"""
from datetime import date
import hashlib
import struct

from .running_workout_schema import RunningWorkout


def _crc(data: bytes) -> int:
    value = 0
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = (value >> 1) ^ (0xA001 if value & 1 else 0)
    return value


def _text(value: str, limit=80) -> bytes:
    return value.encode('utf-8')[:limit].decode('utf-8', errors='ignore').encode('utf-8') + b'\0'


def _message(local, global_number, fields):
    # Each field is (profile field number, FIT base type, encoded value).
    definition = bytes([0x40 | local, 0, 0]) + struct.pack('<HB', global_number, len(fields))
    definition += b''.join(bytes([number, len(value), kind]) for number, kind, value in fields)
    return definition + bytes([local]) + b''.join(value for _, _, value in fields)


def to_fit(plan: RunningWorkout) -> bytes:
    """Revalidate a prescription and return a complete FIT file, including CRCs."""
    plan = RunningWorkout.model_validate(plan.model_dump())
    if plan.decision != 'workout':
        raise ValueError('Rest recommendations have no workout file')
    created = (plan.day - date(1989, 12, 31)).days * 86400
    if not 0 <= created < 0xFFFFFFFF:
        raise ValueError('Workout day is outside the FIT timestamp range')
    serial = int.from_bytes(hashlib.sha256(plan.model_dump_json().encode()).digest()[:4], 'little') or 1
    body = _message(0, 0, [
        (0, 0, b'\x05'),  # file = workout
        (1, 0x84, struct.pack('<H', 255)),  # development manufacturer
        (2, 0x84, struct.pack('<H', 0)),
        (3, 0x8C, struct.pack('<I', serial)),
        (4, 0x86, struct.pack('<I', created)),
    ])
    body += _message(1, 26, [
        (4, 0, b'\x01'),  # sport = running
        (6, 0x84, struct.pack('<H', len(plan.steps))),
        (8, 7, _text(plan.title)),
    ])
    for index, step in enumerate(plan.steps):
        lap = step.duration_type == 'lap_press'
        cue = {'easy': 'Easy, conversational effort', 'steady': 'Steady, controlled effort',
               'hard': 'Hard, controlled effort; not a sprint'}[step.effort]
        notes = f'Press LAP to finish {step.kind}. {cue}' if lap else cue
        label = f'{step.kind.title()} - LAP' if lap else f'{step.kind.title()} - {step.effort}'
        body += _message(2, 27, [
            (254, 0x84, struct.pack('<H', index)),
            (0, 7, _text(f'{index + 1}. {label}')),
            (1, 0, bytes([5 if lap else 0])),  # open or time
            (2, 0x86, struct.pack('<I', 0 if lap else step.duration_s * 1000)),
            (3, 0, b'\x02'),  # open target: effort cue, no invented pace/HR/power
            (4, 0x86, struct.pack('<I', 0)),
            (7, 0, bytes([{'warmup': 2, 'run': 0, 'recovery': 4, 'cooldown': 3}[step.kind]])),
            (8, 7, _text(notes)),
        ])
    header = struct.pack('<BBHI4s', 14, 0x10, 2100, len(body), b'.FIT')
    header += struct.pack('<H', _crc(header))
    content = header + body
    return content + struct.pack('<H', _crc(content))
