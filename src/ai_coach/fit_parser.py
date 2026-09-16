"""Strict, bounded FIT decoding for an archive that retains the source bytes.

Values keep the FIT profile units (for example, speed in m/s and coordinates in
semicircles). The ordered ``_fields`` in each message are authoritative; the
top-level field keys are a convenience containing the first value of each name.
No missing measurements, pause durations, time zones, or laps are inferred.
"""

from __future__ import annotations

import base64
import gzip
import io
import math
import re
import zlib
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from enum import Enum
from typing import Any

import fitdecode

PARSER_VERSION = "1"
MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_MESSAGES = 200_000
MAX_DECODED_FIELDS = 1_000_000
MAX_FIT_FILES = 1024
MAX_ARRAY_ITEMS = 65_536
MAX_VALUE_DEPTH = 16


class ActivityParseError(ValueError):
    """Base error: retain the archived original and record a parsing failure."""

    code = "activity_parse_error"


class InvalidActivityFile(ActivityParseError):
    code = "invalid_activity_file"


class ActivityFileTooLarge(ActivityParseError):
    code = "activity_file_too_large"


class UnsupportedActivityFormat(ActivityParseError):
    code = "unsupported_activity_format"

    def __init__(self, format_name: str):
        self.format_name = format_name
        super().__init__(f"Activity format {format_name!r} is not supported yet.")


@dataclass
class ParsedActivity:
    records: list[dict[str, Any]] = field(default_factory=list)
    laps: list[dict[str, Any]] = field(default_factory=list)
    sessions: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class _ArchiveDataProcessor(fitdecode.DefaultDataProcessor):
    def process_type_local_date_time(self, reader: Any, field_data: Any) -> None:
        # fitdecode 0.11 assigns UTC to device-local wall time. The source does
        # not establish its time zone: emit an explicitly naive ISO value.
        if field_data.value is not None:
            field_data.value = datetime.fromtimestamp(
                fitdecode.FIT_UTC_REFERENCE + field_data.value, timezone.utc
            ).replace(tzinfo=None)
            field_data.units = None


def _json_safe(value: Any, *, depth: int = 0) -> Any:
    if depth > MAX_VALUE_DEPTH:
        raise ActivityFileTooLarge("Decoded value nesting exceeds the limit.")
    if isinstance(value, Enum):
        return _json_safe(value.value, depth=depth + 1)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        # JSON has no NaN/infinity values. Preserve them explicitly instead of
        # generating invalid JSON or silently substituting a measured zero.
        return value if math.isfinite(value) else {"non_finite": str(value)}
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray, memoryview)):
        if len(value) > MAX_ARRAY_ITEMS:
            raise ActivityFileTooLarge("Decoded byte array exceeds the limit.")
        return {"encoding": "base64", "data": base64.b64encode(value).decode("ascii")}
    if isinstance(value, (tuple, list)):
        if len(value) > MAX_ARRAY_ITEMS:
            raise ActivityFileTooLarge("Decoded array exceeds the limit.")
        return [_json_safe(item, depth=depth + 1) for item in value]
    if isinstance(value, dict):
        if len(value) > MAX_ARRAY_ITEMS:
            raise ActivityFileTooLarge("Decoded mapping exceeds the limit.")
        if not all(isinstance(key, str) for key in value):
            raise InvalidActivityFile("Decoded mapping contains non-string keys.")
        return {key: _json_safe(item, depth=depth + 1) for key, item in value.items()}
    raise InvalidActivityFile(f"Unsupported decoded value type: {type(value).__name__}.")


def _unpack(data: bytes) -> tuple[bytes, bool]:
    if not isinstance(data, bytes):
        raise TypeError("data must be bytes")
    if len(data) > MAX_FILE_BYTES:
        raise ActivityFileTooLarge("Source file exceeds the 50 MiB limit.")
    compressed = data.startswith(b"\x1f\x8b")
    if compressed:
        try:
            # read() with a size prevents gzip bombs from allocating the full
            # decompressed payload, including concatenated gzip members.
            with gzip.GzipFile(fileobj=io.BytesIO(data), mode="rb") as stream:
                data = stream.read(MAX_FILE_BYTES + 1)
        except (OSError, EOFError, zlib.error) as exc:
            raise InvalidActivityFile("Invalid or truncated gzip stream.") from exc
        if len(data) > MAX_FILE_BYTES:
            raise ActivityFileTooLarge("Decompressed file exceeds the 50 MiB limit.")
    if not data:
        raise InvalidActivityFile("Activity file is empty.")
    return data, compressed


def _detect_format(data: bytes, filename: str | None) -> str:
    if len(data) >= 12 and data[8:12] == b".FIT":
        return "FIT"
    prefix = data[:65_536]
    encoding = "utf-16" if prefix.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
    text = prefix.decode(encoding, errors="replace").lstrip()
    if text.startswith("<"):
        if re.search(r"<(?:[\w.-]+:)?TrainingCenterDatabase(?:\s|/?>)", text):
            return "TCX"
        if re.search(r"<(?:[\w.-]+:)?gpx(?:\s|/?>)", text, flags=re.IGNORECASE):
            return "GPX"
        return "XML"
    if data.startswith(b"PK\x03\x04"):
        return "ZIP"
    if data.startswith(b"\x1f\x8b"):
        return "nested gzip"
    if filename and filename.lower().removesuffix(".gz").endswith(".fit"):
        raise InvalidActivityFile("File named FIT has no valid FIT signature.")
    return "unknown"


def _message(frame: Any, *, sequence: int, file_index: int) -> dict[str, Any]:
    fields: list[dict[str, Any]] = []
    result: dict[str, Any] = {
        "_message": frame.name,
        "_global_message_number": frame.global_mesg_num,
        "_sequence": sequence,
        "_file_index": file_index,
        "_fields": fields,
    }
    for item in frame.fields:
        definition = item.field_def
        descriptor = {
            "name": item.name,
            "definition_number": item.def_num,
            "value": _json_safe(item.value),
            "raw_value": _json_safe(item.raw_value),
            "units": item.units,
            "type": getattr(item.type, "name", None),
            "expanded": item.is_expanded,
            "developer_data_index": getattr(definition, "dev_data_index", None),
            "native_field_number": getattr(item.field, "native_field_num", None),
        }
        fields.append(descriptor)
        # A developer can reuse a native name or reserved key. Ordered fields
        # preserve every value without allowing a collision to erase metadata.
        result.setdefault(item.name, descriptor["value"])
    return result


def parse_activity_file(data: bytes, *, filename: str | None = None) -> ParsedActivity:
    """Decode a FIT or gzip-wrapped FIT file, rejecting corrupt/oversized input.

    TCX/GPX and unknown inputs raise UnsupportedActivityFormat, allowing the
    importer to retain the source with an explicit status and retry after an
    appropriate parser is added. No partial parsed result is returned on error.

    ``metadata['other_messages']`` retains every other FIT data-message type,
    including device information, developer-field descriptions and HRV data.
    ``_sequence`` preserves ordering across the separated message collections.
    """
    source_size = len(data) if isinstance(data, bytes) else 0
    payload, compressed = _unpack(data)
    file_format = _detect_format(payload, filename)
    if file_format != "FIT":
        raise UnsupportedActivityFormat(file_format)

    result = ParsedActivity(metadata={
        "format": "FIT",
        "parser": "fitdecode",
        "parser_version": fitdecode.__version__,
        "schema_version": PARSER_VERSION,
        "filename": filename,
        "gzip_compressed": compressed,
        "source_size_bytes": source_size,
        "decoded_size_bytes": len(payload),
        "headers": [],
        "other_messages": {},
        "time_semantics": "FIT UTC date_time retains offset; local_date_time has no assumed zone",
        "units": "FIT profile units; coordinates remain semicircles",
    })
    destinations = {
        "record": result.records,
        "lap": result.laps,
        "session": result.sessions,
        "event": result.events,
    }
    message_count = field_count = crc_count = 0
    file_index = -1
    try:
        with fitdecode.FitReader(
            io.BytesIO(payload),
            processor=_ArchiveDataProcessor(),
            check_crc=fitdecode.CrcCheck.RAISE,
            error_handling=fitdecode.ErrorHandling.RAISE,
        ) as reader:
            for frame in reader:
                if frame.frame_type == fitdecode.FIT_FRAME_HEADER:
                    file_index += 1
                    if file_index >= MAX_FIT_FILES:
                        raise ActivityFileTooLarge("Chained FIT file count exceeds the limit.")
                    result.metadata["headers"].append({
                        "header_size": frame.header_size,
                        "protocol_version": frame.proto_ver,
                        "profile_version": frame.profile_ver,
                        "body_size": frame.body_size,
                        "header_crc_matched": frame.crc_matched,
                    })
                elif frame.frame_type == fitdecode.FIT_FRAME_CRC:
                    crc_count += 1
                    if not frame.matched:
                        raise InvalidActivityFile("FIT checksum did not match.")
                elif frame.frame_type == fitdecode.FIT_FRAME_DATA:
                    message_count += 1
                    field_count += len(frame.fields)
                    if message_count > MAX_MESSAGES or field_count > MAX_DECODED_FIELDS:
                        raise ActivityFileTooLarge("Decoded FIT message or field limit exceeded.")
                    message = _message(frame, sequence=message_count - 1, file_index=file_index)
                    if frame.name in destinations:
                        destinations[frame.name].append(message)
                    else:
                        result.metadata["other_messages"].setdefault(frame.name, []).append(message)
    except ActivityParseError:
        raise
    except Exception as exc:
        # Library format/CRC exceptions and malformed-field conversion failures
        # become a stable boundary error; never pass a partial success upward.
        raise InvalidActivityFile(f"FIT decoding failed ({type(exc).__name__}).") from exc

    if file_index < 0 or crc_count != file_index + 1:
        raise InvalidActivityFile("FIT file is missing a complete header or checksum.")
    if not message_count:
        raise InvalidActivityFile("FIT file contains no data messages.")
    result.metadata.update({
        "fit_file_count": file_index + 1,
        "crc_verified": True,
        "message_count": message_count,
        "decoded_field_count": field_count,
    })
    return result


FIT_INSPECTION_VERSION = 1
MAX_INSPECTION_MESSAGES = 500_000
MAX_INSPECTION_FIELDS = 20_000_000
MAX_INSPECTION_FILE_IDS = 4096


def inspect_activity_file(data: bytes, *, filename: str | None = None) -> dict[str, Any]:
    """Fully validate a FIT while retaining only bounded native identity metadata.

    This inspection does not produce decoded samples or replace the original.
    All segments and CRCs are consumed before returning success. Callers must
    separately verify the recorded manufacturer in every segment before using
    source summary metrics; metadata is not cryptographic authentication.
    """
    source_size = len(data) if isinstance(data, bytes) else 0
    payload, compressed = _unpack(data)
    file_format = _detect_format(payload, filename)
    if file_format != "FIT":
        raise UnsupportedActivityFormat(file_format)
    metadata: dict[str, Any] = {
        "format": "FIT", "parser": "fitdecode", "parser_version": fitdecode.__version__,
        "inspection_version": FIT_INSPECTION_VERSION, "sample_storage": "original_only",
        "filename": filename, "gzip_compressed": compressed,
        "source_size_bytes": source_size, "decoded_size_bytes": len(payload),
        "headers": [], "other_messages": {"file_id": []},
    }
    message_count = field_count = crc_count = record_count = lap_count = 0
    file_index = -1
    try:
        with fitdecode.FitReader(
            io.BytesIO(payload), processor=_ArchiveDataProcessor(),
            check_crc=fitdecode.CrcCheck.RAISE, error_handling=fitdecode.ErrorHandling.RAISE,
        ) as reader:
            for frame in reader:
                if frame.frame_type == fitdecode.FIT_FRAME_HEADER:
                    file_index += 1
                    if file_index >= MAX_FIT_FILES:
                        raise ActivityFileTooLarge("Chained FIT file count exceeds the limit.")
                    metadata["headers"].append({
                        "header_size": frame.header_size, "protocol_version": frame.proto_ver,
                        "profile_version": frame.profile_ver, "body_size": frame.body_size,
                        "header_crc_matched": frame.crc_matched,
                    })
                elif frame.frame_type == fitdecode.FIT_FRAME_CRC:
                    crc_count += 1
                    if not frame.matched:
                        raise InvalidActivityFile("FIT checksum did not match.")
                elif frame.frame_type == fitdecode.FIT_FRAME_DATA:
                    message_count += 1
                    field_count += len(frame.fields)
                    if (message_count > MAX_INSPECTION_MESSAGES
                            or field_count > MAX_INSPECTION_FIELDS):
                        raise ActivityFileTooLarge("Inspected FIT message or field limit exceeded.")
                    if frame.name == "record":
                        record_count += 1
                    elif frame.name == "lap":
                        lap_count += 1
                    elif frame.name == "file_id":
                        identities = metadata["other_messages"]["file_id"]
                        if len(identities) >= MAX_INSPECTION_FILE_IDS:
                            raise ActivityFileTooLarge("Inspected FIT identity count exceeds the limit.")
                        identities.append(_message(frame, sequence=message_count - 1,
                                                   file_index=file_index))
    except ActivityParseError:
        raise
    except Exception as exc:
        raise InvalidActivityFile(f"FIT inspection failed ({type(exc).__name__}).") from exc
    if file_index < 0 or crc_count != file_index + 1:
        raise InvalidActivityFile("FIT file is missing a complete header or checksum.")
    if not message_count:
        raise InvalidActivityFile("FIT file contains no data messages.")
    metadata.update({"fit_file_count": file_index + 1, "crc_verified": True,
                     "message_count": message_count, "decoded_field_count": field_count})
    return {"metadata": metadata, "record_count": record_count, "lap_count": lap_count}
