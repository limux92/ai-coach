"""End-to-end decoding tests using independently generated FIT binary fixtures."""

from __future__ import annotations

import gzip
import json
import math
import struct
import unittest
from dataclasses import asdict
from datetime import date, datetime, time, timezone
from enum import Enum
from unittest.mock import patch

from ai_coach.fit_parser import (
    ActivityFileTooLarge,
    InvalidActivityFile,
    UnsupportedActivityFormat,
    _json_safe,
    parse_activity_file,
)


def crc16(data: bytes) -> int:
    """FIT CRC: reflected polynomial 0xA001, zero initial state."""
    value = 0
    for octet in data:
        value ^= octet
        for _ in range(8):
            value = (value >> 1) ^ (0xA001 if value & 1 else 0)
    return value


def fit_file(body: bytes, *, header_size: int = 14) -> bytes:
    header = struct.pack("<BBHI4s", header_size, 0x20, 2171, len(body), b".FIT")
    if header_size == 14:
        header += struct.pack("<H", crc16(header))
    data = header + body
    return data + struct.pack("<H", crc16(data))


def definition(local: int, global_number: int, fields: list[tuple[int, int, int]],
               dev_fields: list[tuple[int, int, int]] | None = None) -> bytes:
    header = 0x40 | local | (0x20 if dev_fields else 0)
    output = struct.pack("<BBBHB", header, 0, 0, global_number, len(fields))
    output += b"".join(bytes(item) for item in fields)
    if dev_fields:
        output += bytes([len(dev_fields)]) + b"".join(bytes(item) for item in dev_fields)
    return output


STAMP = 1_100_000_000  # FIT epoch, greater than the power-on-relative threshold.


def workout_body() -> bytes:
    # A tiny interval-like activity with pause/resume events, deliberately
    # unequal elapsed and timer durations, missing heart rate and a local clock.
    body = definition(0, 20, [(253, 4, 0x86), (3, 1, 2), (6, 2, 0x84), (250, 3, 2)])
    body += struct.pack("<BIBH3B", 0, STAMP, 150, 3500, 4, 5, 6)
    body += struct.pack("<BIBH3B", 0, STAMP + 10, 255, 0, 7, 8, 9)
    body += definition(1, 21, [(253, 4, 0x86), (0, 1, 0), (1, 1, 0)])
    for offset, event_type in [(0, 0), (5, 1), (8, 0), (10, 4)]:
        body += struct.pack("<BIBB", 1, STAMP + offset, 0, event_type)
    for local, global_number in [(2, 19), (3, 18)]:
        body += definition(local, global_number, [
            (253, 4, 0x86), (2, 4, 0x86), (7, 4, 0x86), (8, 4, 0x86)
        ])
        body += struct.pack("<BIIII", local, STAMP + 10, STAMP, 10000, 7000)
    body += definition(4, 34, [(253, 4, 0x86), (5, 4, 0x86)])
    body += struct.pack("<BII", 4, STAMP + 10, STAMP + 7210)
    return body


def developer_body() -> bytes:
    # Developer data is identified before it is referenced by a record. The
    # developer chooses the native field's name to exercise a real collision.
    body = definition(0, 207, [(3, 1, 2), (1, 16, 13)])
    body += bytes([0, 0]) + bytes(range(16))
    body += definition(1, 206, [(0, 1, 2), (1, 1, 2), (2, 1, 2), (3, 12, 7), (8, 4, 7)])
    body += bytes([1, 0, 0, 2]) + b"heart_rate\x00\x00" + b"bpm\x00"
    body += definition(2, 20, [(253, 4, 0x86), (3, 1, 2)], [(0, 1, 0)])
    body += struct.pack("<BIBB", 2, STAMP, 140, 149)
    return body


class FitParserTests(unittest.TestCase):
    def test_actual_fit_samples_laps_events_and_time_semantics(self):
        result = parse_activity_file(fit_file(workout_body()), filename="intervals.fit")
        self.assertEqual(len(result.records), 2)
        self.assertEqual(result.records[0]["heart_rate"], 150)
        self.assertIsNone(result.records[1]["heart_rate"])
        self.assertEqual(result.records[0]["speed"], 3.5)
        self.assertEqual(result.records[0]["unknown_250"], [4, 5, 6])
        self.assertEqual(result.laps[0]["total_elapsed_time"], 10.0)
        self.assertEqual(result.laps[0]["total_timer_time"], 7.0)
        self.assertEqual(result.sessions[0]["total_timer_time"], 7.0)
        self.assertEqual([row["event_type"] for row in result.events],
                         ["start", "stop", "start", "stop_all"])
        self.assertTrue(result.records[0]["timestamp"].endswith("+00:00"))
        activity = result.metadata["other_messages"]["activity"][0]
        self.assertNotIn("+", activity["local_timestamp"])
        self.assertEqual(result.metadata["fit_file_count"], 1)
        self.assertTrue(result.metadata["crc_verified"])
        json.dumps(asdict(result), allow_nan=False)

    def test_developer_and_native_field_name_collision_is_lossless(self):
        result = parse_activity_file(fit_file(developer_body()))
        row = result.records[0]
        self.assertEqual(row["heart_rate"], 140)
        fields = [item for item in row["_fields"] if item["name"] == "heart_rate"]
        self.assertEqual([item["value"] for item in fields], [140, 149])
        self.assertEqual([item["developer_data_index"] for item in fields], [None, 0])
        self.assertIn("field_description", result.metadata["other_messages"])
        self.assertIn("developer_data_id", result.metadata["other_messages"])

    def test_duplicate_native_fields_preserved(self):
        body = definition(0, 20, [(3, 1, 2), (3, 1, 2)]) + bytes([0, 133, 134])
        row = parse_activity_file(fit_file(body)).records[0]
        self.assertEqual(row["heart_rate"], 133)
        self.assertEqual([field["value"] for field in row["_fields"]], [133, 134])

    def test_gzip_and_chained_fit_are_supported(self):
        plain = fit_file(workout_body(), header_size=12) + fit_file(workout_body())
        result = parse_activity_file(gzip.compress(plain), filename="workouts.fit.gz")
        self.assertEqual(result.metadata["fit_file_count"], 2)
        self.assertEqual([item["_file_index"] for item in result.records], [0, 0, 1, 1])
        self.assertTrue(result.metadata["gzip_compressed"])

    def test_corrupt_crc_and_truncated_data_never_return_partial_result(self):
        valid = fit_file(workout_body())
        corrupt = bytearray(valid)
        corrupt[-1] ^= 1
        for invalid in [bytes(corrupt), valid[:-1], valid[:-10], valid + b"garbage"]:
            with self.subTest(size=len(invalid)), self.assertRaises(InvalidActivityFile):
                parse_activity_file(invalid)

    def test_gzip_integrity_and_size_bounds(self):
        compressed = gzip.compress(fit_file(workout_body()))
        with self.assertRaises(InvalidActivityFile):
            parse_activity_file(compressed[:-4])
        with self.assertRaises(InvalidActivityFile):
            parse_activity_file(bytes.fromhex("1f8b0800000000000003") + b"\xff" * 16)
        with patch("ai_coach.fit_parser.MAX_FILE_BYTES", 1000):
            with self.assertRaises(ActivityFileTooLarge):
                parse_activity_file(gzip.compress(b"x" * 1001))
            with self.assertRaises(ActivityFileTooLarge):
                parse_activity_file(b"x" * 1001)
            with self.assertRaises(ActivityFileTooLarge):
                parse_activity_file(gzip.compress(b"x" * 800) + gzip.compress(b"x" * 800))

    def test_decoded_message_and_field_bounds(self):
        data = fit_file(workout_body())
        with patch("ai_coach.fit_parser.MAX_MESSAGES", 1), self.assertRaises(ActivityFileTooLarge):
            parse_activity_file(data)
        with patch("ai_coach.fit_parser.MAX_DECODED_FIELDS", 1), self.assertRaises(ActivityFileTooLarge):
            parse_activity_file(data)
        with patch("ai_coach.fit_parser.MAX_FIT_FILES", 1), self.assertRaises(ActivityFileTooLarge):
            parse_activity_file(data + data)

    def test_formats_and_empty_inputs_have_explicit_errors(self):
        for content, expected in [
            (b'<?xml version="1.0"?><TrainingCenterDatabase xmlns="urn:garmin"/>', "TCX"),
            (b'<?xml version="1.0"?><gpx version="1.1"/>', "GPX"),
            ('<gpx version="1.1"/>'.encode("utf-16"), "GPX"),
            (b"PK\x03\x04data", "ZIP"),
            (b"something", "unknown"),
        ]:
            with self.subTest(format=expected), self.assertRaises(UnsupportedActivityFormat) as error:
                parse_activity_file(content)
            self.assertEqual(error.exception.format_name, expected)
        with self.assertRaises(InvalidActivityFile):
            parse_activity_file(b"")
        with self.assertRaises(InvalidActivityFile):
            parse_activity_file(b"corrupt", filename="activity.FIT.gz")

    def test_json_safe_types_preserve_special_values(self):
        class Metric(Enum):
            RUN = "run"
        values = [
            datetime(2026, 1, 1, tzinfo=timezone.utc), date(2026, 1, 1),
            time(7, 30), b"\x00\xff", (1, 2), Metric.RUN,
            math.nan, math.inf, -math.inf,
        ]
        safe = _json_safe(values)
        self.assertEqual(safe[3], {"encoding": "base64", "data": "AP8="})
        self.assertEqual(safe[4], [1, 2])
        self.assertEqual(safe[5], "run")
        self.assertEqual(safe[6], {"non_finite": "nan"})
        json.dumps(safe, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
