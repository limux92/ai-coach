import json
import unittest

from ai_coach.normalize import (NormalizationError, is_direct_garmin, normalize_activity,
                       normalize_calendar_event, normalize_planned_workout,
                       normalize_wellness, source_hash, is_garmin_upload_candidate,
                       is_verified_garmin_fit)


def garmin_fit_metadata():
    return {"format": "FIT", "crc_verified": True, "fit_file_count": 1,
            "other_messages": {"file_id": [{"_file_index": 0, "manufacturer": "garmin",
                "_fields": [{"definition_number": 0, "raw_value": 4, "developer_data_index": None},
                            {"definition_number": 1, "raw_value": 1, "developer_data_index": None}]}]}}


class NormalizationTests(unittest.TestCase):
    def test_manual_garmin_upload_requires_native_fit_proof_and_keeps_source(self):
        payload = self.activity(source="UPLOAD", file_type="fit", device_name="GARMIN FR970", strava_only=None)
        self.assertTrue(is_garmin_upload_candidate(payload))
        with self.assertRaises(NormalizationError):
            normalize_activity(payload, athlete_id="123")
        doc = normalize_activity(payload, athlete_id="123", upload_fit_metadata=garmin_fit_metadata())
        self.assertEqual(doc["provider_source"], "UPLOAD")
        self.assertEqual(doc["import_method"], "manual_upload")
        self.assertEqual(doc["source_verification"]["method"], "native_fit_file_id")
        self.assertIn("manually uploaded", doc["garmin_attribution"])
        self.assertFalse(is_direct_garmin(payload))

    def test_candidate_predicate_still_excludes_strava_unknown_and_non_garmin(self):
        for changes in ({"source": "STRAVA"}, {"source": "UNKNOWN"}, {"strava_only": True},
                        {"device_name": "Garminish device"}, {"device_name": "Polar"},
                        {"device_name": None}, {"file_type": "tcx"}):
            payload = self.activity(source="UPLOAD", file_type="fit", device_name="GARMIN FR970")
            payload.update(changes)
            self.assertFalse(is_garmin_upload_candidate(payload))
            with self.assertRaises(NormalizationError):
                normalize_activity(payload, athlete_id="123", upload_fit_metadata=garmin_fit_metadata())

    def test_file_proof_rejects_accessories_developer_spoof_and_missing_chained_id(self):
        metadata = garmin_fit_metadata()
        self.assertTrue(is_verified_garmin_fit(metadata))
        metadata["other_messages"]["file_id"][0]["_fields"][1]["developer_data_index"] = 0
        self.assertFalse(is_verified_garmin_fit(metadata))
        metadata = garmin_fit_metadata()
        metadata["other_messages"]["file_id"][0]["_fields"][1]["raw_value"] = 32
        self.assertFalse(is_verified_garmin_fit(metadata))
        metadata = garmin_fit_metadata()
        metadata["other_messages"]["device_info"] = metadata["other_messages"].pop("file_id")
        self.assertFalse(is_verified_garmin_fit(metadata))
        metadata = garmin_fit_metadata()
        metadata["fit_file_count"] = 2
        self.assertFalse(is_verified_garmin_fit(metadata))
        metadata = garmin_fit_metadata()
        metadata["other_messages"]["file_id"][0]["_fields"][0]["raw_value"] = 6
        self.assertFalse(is_verified_garmin_fit(metadata))

    def activity(self, **changes):
        return {"id": "i123", "source": "GARMIN_CONNECT", "start_date_local": "2026-09-15T07:30:00",
                "start_date": "2026-09-15T05:30:00Z", "type": "Run", **changes}

    def test_direct_origin_allowlist_is_not_based_on_strava_id_or_device(self):
        self.assertTrue(is_direct_garmin(self.activity(strava_id="99")))
        self.assertFalse(is_direct_garmin(self.activity(source="STRAVA", device_name="Garmin 970")))
        self.assertFalse(is_direct_garmin(self.activity(source=None)))
        self.assertFalse(is_direct_garmin(self.activity(strava_only=True)))
        with self.assertRaises(NormalizationError):
            normalize_activity(self.activity(source="STRAVA"), athlete_id="123")

    def test_timezone_units_missing_values_and_plan_link(self):
        doc = normalize_activity(self.activity(distance=10000, icu_distance=9950, average_speed=3.2,
                                              icu_training_load=50, paired_event_id=12,
                                              icu_average_watts=250, icu_rpe=4, session_rpe=120), athlete_id="123")
        self.assertEqual(doc["start_date_local"], "2026-09-15T07:30:00")
        self.assertEqual(doc["start_date_utc"], "2026-09-15T05:30:00.000Z")
        self.assertEqual(doc["metrics"]["distance_m"], 9950)
        self.assertEqual(doc["metrics"]["recorded_distance_m"], 10000)
        self.assertIsNone(doc["metrics"]["average_heart_rate_bpm"])
        self.assertEqual(doc["analysis"]["training_load"], 50)
        self.assertEqual(doc["metrics"]["average_power_w"], 250)
        self.assertEqual(doc["observations"]["rpe"], 4)
        self.assertEqual(doc["analysis"]["session_rpe_load"], 120)
        self.assertEqual(doc["paired_planned_workout_id"], "intervals_12")
        with self.assertRaises(NormalizationError):
            normalize_activity(self.activity(start_date="2026-09-15T05:30:00"), athlete_id="123")

    def test_normalize_activity_preserves_ftp_and_power_model_cp(self):
        doc = normalize_activity(self.activity(icu_ftp=285, icu_pm_cp=275, icu_pm_w_prime=18500),
                                 athlete_id="123")
        self.assertEqual(doc["analysis"]["ftp_w"], 285)
        self.assertEqual(doc["analysis"]["model_cp_w"], 275)
        self.assertEqual(doc["analysis"]["model_w_prime_j"], 18500)

    def test_non_workouts_are_separate_calendar_events(self):
        for category in ["NOTE", "RACE_A", "RACE", None]:
            payload = {"id": 1, "category": category, "start_date_local": "2026-09-15T00:00:00"}
            self.assertIsNone(normalize_planned_workout(payload, athlete_id="123"))
            self.assertEqual(normalize_calendar_event(payload, athlete_id="123")["category"], category)

    def test_workout_projects_provider_zones_with_versioned_definitions(self):
        doc = normalize_activity(self.activity(icu_hr_zones=[120, 150, 190],
                                               icu_hr_zone_times=[600, 1200, 0]), athlete_id="123")
        self.assertEqual(doc["schema_version"], 2)
        self.assertEqual(doc["zone_summary"]["calculation_version"], 1)
        self.assertEqual(doc["zone_summary"]["heart_rate"]["classified_seconds"], 1800)
        self.assertEqual(doc["zone_summary"]["heart_rate"]["boundaries_bpm"], [120, 150, 190])
        doc = normalize_activity(self.activity(), athlete_id="123")
        self.assertEqual(doc["zone_summary"]["heart_rate"]["status"], "missing")

    def test_planned_structure_preserves_relative_targets_and_past_status(self):
        structure = {"steps": [{"reps": 4, "steps": [{"duration": 300,
                              "hr": {"start": 80, "end": 85, "units": "%lthr"}}]}], "duration": 1200}
        doc = normalize_planned_workout({"id": 2, "category": "WORKOUT",
                                        "start_date_local": "2020-01-01T00:00:00",
                                        "workout_doc": structure}, athlete_id="123")
        self.assertEqual(doc["status"], "planned")
        self.assertEqual(json.loads(doc["structured_workout_json"]), structure)
        self.assertEqual(doc["metrics"]["duration_s"], 1200)

    def test_large_structure_requires_raw_archive(self):
        doc = normalize_planned_workout({"id": 2, "category": "WORKOUT",
                                        "start_date_local": "2026-09-15T00:00:00",
                                        "workout_doc": {"description": "x" * (300 * 1024)}}, athlete_id="123")
        self.assertTrue(doc["structure_stored_in_raw_payload"])
        self.assertIsNone(doc["structured_workout_json"])

    def test_wellness_does_not_fabricate_garmin_provenance(self):
        doc = normalize_wellness({"id": "2026-09-15", "hrv": 60, "sleepSecs": 28000,
                                  "restingHR": 45, "tempRestingHR": True, "lactate": 1.4}, athlete_id="123")
        self.assertEqual(doc["id"], "2026-09-15")
        self.assertEqual(doc["provider_source"], "INTERVALS_MERGED_WELLNESS")
        self.assertEqual(doc["metrics"]["hrv_rmssd_ms"], 60)
        self.assertEqual(doc["observations"]["lactate_mmol_l"], 1.4)
        self.assertTrue(doc["resting_heart_rate_is_temporary"])

    def test_payload_hash_is_order_independent_and_rejects_nan(self):
        self.assertEqual(source_hash({"x": 1, "y": 2}), source_hash({"y": 2, "x": 1}))
        with self.assertRaises(NormalizationError):
            source_hash({"invalid": float("nan")})


if __name__ == "__main__":
    unittest.main()
