"""Streaming FIT inspection validates full files without retaining sample JSON."""
import gzip
import json
import struct
from unittest.mock import patch

import pytest

from ai_coach import fit_parser
from ai_coach.fit_parser import (
    ActivityFileTooLarge, InvalidActivityFile, UnsupportedActivityFormat,
    inspect_activity_file, parse_activity_file,
)
from ai_coach.normalize import is_verified_activity_fit
from test_fit_parser import definition, fit_file, workout_body


def identity_body(manufacturer=260):
    return definition(5, 0, [(0, 1, 0), (1, 2, 0x84)]) + struct.pack("<BBH", 5, 4, manufacturer)


def test_counts_records_without_materializing_samples_and_retains_native_identity():
    data = fit_file(identity_body() + workout_body())
    message = fit_parser._message
    retained_types = []

    def record_identity(frame, **kwargs):
        retained_types.append(frame.name)
        return message(frame, **kwargs)

    with patch("ai_coach.fit_parser._message", side_effect=record_identity):
        result = inspect_activity_file(data)
    assert retained_types == ["file_id"]
    assert set(result) == {"metadata", "record_count", "lap_count"}
    assert result["record_count"] == 2
    assert result["lap_count"] == 1
    metadata = result["metadata"]
    assert metadata["inspection_version"] == 1
    assert metadata["sample_storage"] == "original_only"
    assert set(metadata["other_messages"]) == {"file_id"}
    assert metadata["source_size_bytes"] == metadata["decoded_size_bytes"] == len(data)
    assert is_verified_activity_fit(metadata, "zwift")
    assert not is_verified_activity_fit(metadata, "garmin")
    json.dumps(result, allow_nan=False)


def test_chained_gzip_files_retain_identity_per_segment():
    one = fit_file(identity_body() + workout_body(), header_size=12)
    result = inspect_activity_file(gzip.compress(one + one), filename="rides.fit.gz")
    assert result["record_count"] == 4
    assert result["lap_count"] == 2
    metadata = result["metadata"]
    assert metadata["gzip_compressed"] is True
    assert metadata["crc_verified"] is True
    assert metadata["fit_file_count"] == 2
    assert [row["_file_index"] for row in metadata["other_messages"]["file_id"]] == [0, 1]
    assert is_verified_activity_fit(metadata, "zwift")


@pytest.mark.parametrize("second", [workout_body(), identity_body(1) + workout_body()])
def test_valid_crc_does_not_hide_missing_or_mixed_segment_provenance(second):
    data = fit_file(identity_body() + workout_body()) + fit_file(second)
    metadata = inspect_activity_file(data)["metadata"]
    assert metadata["crc_verified"] is True
    assert not is_verified_activity_fit(metadata, "zwift")
    assert not is_verified_activity_fit(metadata, "garmin")


def test_corrupt_final_segment_truncated_input_and_garbage_never_succeed():
    one = fit_file(identity_body() + workout_body())
    damaged = bytearray(one + one)
    damaged[-1] ^= 1
    for bad in (bytes(damaged), one[:-1], one[:-10], one + b"garbage", gzip.compress(one)[:-4]):
        with pytest.raises(InvalidActivityFile):
            inspect_activity_file(bad)


@pytest.mark.parametrize("constant", ["MAX_INSPECTION_MESSAGES", "MAX_INSPECTION_FIELDS"])
def test_streaming_limits_fail_closed(constant):
    with patch(f"ai_coach.fit_parser.{constant}", 1), pytest.raises(ActivityFileTooLarge):
        inspect_activity_file(fit_file(identity_body() + workout_body()))


def test_segment_and_identity_limits_fail_closed():
    one = fit_file(identity_body())
    with patch("ai_coach.fit_parser.MAX_FIT_FILES", 1), pytest.raises(ActivityFileTooLarge):
        inspect_activity_file(one + one)
    with patch("ai_coach.fit_parser.MAX_INSPECTION_FILE_IDS", 1), pytest.raises(ActivityFileTooLarge):
        inspect_activity_file(one + one)


def test_source_and_decompression_limits_remain_enforced():
    with patch("ai_coach.fit_parser.MAX_FILE_BYTES", 1000):
        for data in (b"x" * 1001, gzip.compress(b"x" * 1001)):
            with pytest.raises(ActivityFileTooLarge):
                inspect_activity_file(data)


def test_streaming_fallback_has_own_bounds_without_relaxing_full_parser():
    data = fit_file(identity_body() + workout_body())
    with patch("ai_coach.fit_parser.MAX_DECODED_FIELDS", 1):
        with pytest.raises(ActivityFileTooLarge):
            parse_activity_file(data)
        assert inspect_activity_file(data)["record_count"] == 2


def test_empty_invalid_and_unsupported_files_are_explicit():
    with pytest.raises(InvalidActivityFile):
        inspect_activity_file(b"")
    with pytest.raises(InvalidActivityFile):
        inspect_activity_file(fit_file(b""))
    with pytest.raises(InvalidActivityFile):
        inspect_activity_file(b"bad", filename="activity.fit")
    with pytest.raises(UnsupportedActivityFormat):
        inspect_activity_file(b'<gpx version="1.1"/>')
