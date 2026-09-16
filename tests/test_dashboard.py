"""Dashboard HTTP contract: bounded projections of the private source records."""

from copy import deepcopy
from datetime import date, timedelta

import pytest

from ai_coach.dashboard import MAX_RESPONSE_BYTES
from ai_coach.zones import build_zone_summary
from test_api import api


WINDOW = {"oldest": "2026-09-01", "newest": "2026-09-30"}


def workout(identifier="run", **changes):
    return {
        "id": identifier, "source_id": identifier,
        "local_date": "2026-09-16", "start_date_local": "2026-09-16T00:15:00",
        "start_date_utc": "2026-09-15T22:15:00Z", "timezone": "Europe/Oslo",
        "name": "Morning run", "sport": "Run", "provider_source": "UPLOAD",
        "garmin_attribution": "Garmin device data, manually uploaded to Intervals.icu",
        "parse_status": "parsed", "parsed_artifact": {"object": "private/parsed"},
        "original_artifact": {"object": "private/original"},
        "metrics": {"distance_m": 6100, "moving_time_s": 2340, "elevation_gain_m": 49,
                    "average_heart_rate_bpm": 130, "average_power_w": None},
        "analysis": {"training_load": 23, "intensity_percent": 70, "ctl": 18, "atl": 21},
        "observations": {"rpe": 2, "feel": 3},
        "zone_summary": build_zone_summary({"icu_hr_zones": [140, 160, 190],
                                            "icu_hr_zone_times": [1700, 500, 100]}),
        **changes,
    }


def test_calendar_projection_preserves_source_local_date_metrics_and_zone_definition(api):
    client, memory = api
    doc = workout()
    memory.put("workouts", "run", doc)
    result = client.get("/v1/dashboard/workouts", params=WINDOW)
    assert result.status_code == 200
    row = result.json()["items"][0]
    assert row["local_date"] == "2026-09-16"
    assert row["start_date_local"] == "2026-09-16T00:15:00"
    assert row["start_date_utc"] == "2026-09-15T22:15:00Z"
    assert row["timezone"] == "Europe/Oslo"
    assert row["metrics"]["distance_m"] == 6100
    assert row["metrics"]["average_power_w"] is None
    assert row["metrics"]["energy_kcal"] is None
    assert row["analysis"]["ctl"] == 18 and row["observations"]["rpe"] == 2
    assert row["zone_summary"] == doc["zone_summary"]
    assert row["sample_availability"] == "available"
    assert memory.get("workouts", "run") == doc


def test_list_allowlist_excludes_details_and_originals_even_for_large_laps(api):
    client, memory = api
    for number in range(50):
        identifier = f"run-{number:02}"
        memory.put("workouts", identifier, workout(identifier, zone_summary=None,
            description="private-description" * 100,
            api_key="secret-never-return", raw_payload={"private": "raw-private"},
            laps_summary=[{"total_distance": 1000, "_fields": ["huge-raw-fields"] * 100}] * 100))
    response = client.get("/v1/dashboard/workouts", params={**WINDOW, "limit": 50})
    assert response.status_code == 200
    page = response.json()
    # Even a 50-item request with huge source laps fits the gateway response cap.
    assert len(response.content) <= MAX_RESPONSE_BYTES < 64_000
    assert 1 <= len(page["items"]) <= 50
    for forbidden in ("private-description", "secret-never-return", "raw-private", "huge-raw-fields",
                      "laps_summary", "original_artifact", "parsed_artifact", "private/parsed"):
        assert forbidden not in response.text
    allowed = {"id", "source_id", "local_date", "start_date_local", "start_date_utc", "timezone",
               "sport", "name", "provider_source", "recording_platform", "distance_type",
               "source_attribution", "garmin_attribution", "parse_status", "sample_availability",
               "metrics", "analysis", "observations", "zone_summary"}
    assert set(page["items"][0]) == allowed


def test_byte_limited_pages_cover_all_visible_records_and_skip_tombstones(api):
    client, memory = api
    expected = []
    for number in range(90):
        identifier = f"run-{number:03}"
        retired = number % 7 == 0
        memory.put("workouts", identifier, workout(identifier,
            name="界" * 160, source_deleted=retired and number % 2 == 0,
            source_excluded=retired and number % 2 == 1))
        if not retired:
            expected.append(identifier)
    actual = []
    query = {**WINDOW, "limit": 50}
    saw_short_page = False
    for _ in range(10):
        response = client.get("/v1/dashboard/workouts", params=query)
        assert response.status_code == 200 and len(response.content) <= MAX_RESPONSE_BYTES
        page = response.json()
        actual.extend(row["id"] for row in page["items"])
        if page["next_cursor"] is None:
            break
        assert page["items"] and page["next_cursor"] == page["items"][-1]["id"]
        saw_short_page |= len(page["items"]) < 50
        query["after"] = page["next_cursor"]
    assert actual == expected and len(actual) == len(set(actual))
    assert saw_short_page
    # Reading a hidden record directly cannot bypass existing retirement rules.
    assert client.get("/v1/dashboard/workouts/run-000").status_code == 410
    assert client.get("/v1/dashboard/workouts/run-007").status_code == 410


@pytest.mark.parametrize("path", ["/v1/dashboard/workouts", "/v1/dashboard/planned-workouts"])
def test_dashboard_validates_inclusive_range_and_page_inputs(api, path):
    client, _ = api
    oldest = date(2024, 1, 1)
    assert client.get(path, params={"oldest": oldest, "newest": oldest + timedelta(days=365)}).status_code == 200
    for changes in (
        {"oldest": "2026-02-30"}, {"oldest": "2026-10-01"},
        {"oldest": "2024-01-01", "newest": "2025-01-01"},
        {"limit": 0}, {"limit": 51}, {"after": ""}, {"after": "x" * 181},
        {"after": "unknown"}, {"after": "bad/id"},
    ):
        assert client.get(path, params={**WINDOW, **changes}).status_code == 422
    assert client.get(path, params={"oldest": "2026-09-16", "newest": "2026-09-16"}).status_code == 200


def test_planned_calendar_uses_saved_status_and_paginates_same_day_ties(api):
    client, memory = api
    for identifier in ("c", "a", "b"):
        memory.put("planned_workouts", identifier, {
            "id": identifier, "source_id": identifier, "local_date": "2026-09-16",
            "name": "Tempo", "sport": "Run", "status": "planned", "timezone": "Europe/Oslo",
            "metrics": {"duration_s": 1800, "distance_m": None, "training_load": 40},
            "structured_workout_json": "huge-plan-body", "description": "private-plan-notes",
        })
    query = {**WINDOW, "limit": 1}
    ids = []
    for _ in range(3):
        response = client.get("/v1/dashboard/planned-workouts", params=query)
        page = response.json()
        row = page["items"][0]
        ids.append(row["id"])
        assert row["status"] == "planned"  # A past date cannot imply completion.
        assert row["metrics"]["distance_m"] is None
        assert row["metrics"]["duration_s"] == 1800
        assert "huge-plan-body" not in response.text and "private-plan-notes" not in response.text
        query["after"] = page["next_cursor"]
    assert ids == ["a", "b", "c"] and page["next_cursor"] is None


def test_zwift_summary_only_preserves_virtual_distance_and_explicit_sample_availability(api):
    client, memory = api
    memory.put("workouts", "zwift", workout("zwift", sport="VirtualRide", recording_platform="zwift",
        distance_type="virtual", source_attribution="Zwift virtual activity, uploaded to Intervals.icu",
        garmin_attribution=None, parse_status="summary_only", parsed_artifact=None,
        sample_availability="original_only", lap_count=135, record_count=900_000,
        laps_summary=[], laps_summary_truncated=True))
    response = client.get("/v1/dashboard/workouts/zwift")
    row = response.json()
    assert response.status_code == 200
    assert row["sample_availability"] == "original_only"
    assert row["source_availability"] == {"summary": True, "samples": False, "original_archived": True}
    assert row["garmin_attribution"] is None and row["recording_platform"] == "zwift"
    assert row["distance_type"] == "virtual" and row["lap_count"] == 135
    assert row["laps_omitted"] == 135 and row["laps_summary_truncated"] is True
    assert client.get("/v1/workouts/zwift/samples").status_code == 409


def test_detail_limits_unicode_description_and_laps_without_exposing_raw_fields(api):
    client, memory = api
    lap = {"start_time": "2026-09-16T10:00:00Z", "total_timer_time": 300,
           "total_distance": 1000, "avg_heart_rate": 140, "avg_power": None,
           "raw_fields": ["secret-metadata"] * 500, "_fields": ["private"]}
    doc = workout(description="界" * 20_000, lap_count=100, laps_summary=[deepcopy(lap)] * 100)
    memory.put("workouts", "run", doc)
    response = client.get("/v1/dashboard/workouts/run")
    assert response.status_code == 200 and len(response.content) <= MAX_RESPONSE_BYTES
    row = response.json()
    assert len(row["description"]) == 4000 and row["description_truncated"] is True
    assert len(row["laps_summary"]) == 50 and row["laps_omitted"] == 50
    assert row["laps_summary_truncated"] is True
    assert row["laps_summary"][0] == {key: value for key, value in lap.items()
                                       if key not in {"raw_fields", "_fields"}}
    assert "secret-metadata" not in response.text and "artifact" not in response.text
    # Existing raw route is unchanged and separate from the compact dashboard.
    assert client.get("/v1/workouts/run").json()["description"] == doc["description"]
    assert client.get("/v1/dashboard/workouts/missing").status_code == 404
    assert client.get("/v1/dashboard/workouts/bad%20id").status_code == 422


def test_missing_and_invalid_measurements_remain_null_not_zero(api):
    client, memory = api
    memory.put("workouts", "run", workout(metrics={"moving_time_s": 0, "distance_m": True,
        "average_power_w": float("nan"), "max_power_w": float("inf"), "energy_kcal": 10**1000},
        analysis=None, observations="invalid", zone_summary=None, parsed_artifact=None,
        original_artifact=None, parse_status="pending"))
    row = client.get("/v1/dashboard/workouts", params=WINDOW).json()["items"][0]
    assert row["metrics"]["moving_time_s"] == 0
    assert all(value is None for key, value in row["metrics"].items() if key != "moving_time_s")
    assert all(value is None for value in row["analysis"].values())
    assert all(value is None for value in row["observations"].values())
    assert row["zone_summary"] is None and row["sample_availability"] == "unavailable"


def test_oversized_zone_definition_is_not_silently_truncated_or_combined(api):
    client, memory = api
    zone = build_zone_summary({"icu_hr_zones": [140, 160], "icu_hr_zone_times": [100, 200]})
    zone["heart_rate"]["seconds"] = [100] * 33
    memory.put("workouts", "run", workout(zone_summary=zone))
    hr = client.get("/v1/dashboard/workouts/run").json()["zone_summary"]["heart_rate"]
    assert hr == {"status": "invalid", "reason": "dashboard_projection_limits"}
