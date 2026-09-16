import copy
import unittest

from ai_coach.zones import build_zone_summary


class ZoneSummaryTests(unittest.TestCase):
    def payload(self, **changes):
        return {"icu_hr_zones": [120, 140, 160, 180, 200],
                "icu_hr_zone_times": [300, 900, 600, 120, 0], **changes}

    def heart_rate(self, **changes):
        return build_zone_summary(self.payload(**changes))["heart_rate"]

    def test_classified_time_and_percentages_are_provider_calculations(self):
        summary = self.heart_rate(moving_time=2000, elapsed_time=2200, icu_recording_time=2020)
        self.assertEqual(summary["status"], "available")
        self.assertEqual(summary["source"], "intervals.icu")
        self.assertEqual(summary["basis"], "provider_zone_times")
        self.assertEqual(summary["seconds"], [300, 900, 600, 120, 0])
        self.assertEqual(summary["classified_seconds"], 1920)
        self.assertEqual(summary["percentages"], [15.625, 46.875, 31.25, 6.25, 0.0])
        self.assertEqual(summary["boundaries_bpm"], [120, 140, 160, 180, 200])
        self.assertEqual(summary["boundary_semantics"], "inclusive_upper_bpm")

    def test_equal_moving_or_recording_time_does_not_prove_hr_coverage(self):
        for changes in ({"moving_time": 1920}, {"elapsed_time": 1920},
                        {"icu_recording_time": 1920}):
            coverage = self.heart_rate(**changes)["coverage"]
            self.assertEqual(coverage["status"], "unknown_provider_duration_basis")
            self.assertIsNone(coverage["unclassified_seconds"])
            self.assertIsNone(coverage["denominator_seconds"])
            self.assertIsNone(coverage["classified_fraction"])

    def test_snapshot_does_not_mutate_when_source_arrays_change(self):
        payload = self.payload()
        original = copy.deepcopy(payload)
        result = build_zone_summary(payload)
        self.assertEqual(payload, original)
        payload["icu_hr_zones"][0] = 121
        payload["icu_hr_zone_times"][0] = 1
        self.assertEqual(result["heart_rate"]["boundaries_bpm"][0], 120)
        self.assertEqual(result["heart_rate"]["seconds"][0], 300)

    def test_definition_id_tracks_bins_not_activity_or_profile_metadata(self):
        first = self.heart_rate(lthr=180, icu_resting_hr=50)
        same = self.heart_rate(id="i456", lthr=181, icu_resting_hr=45,
                               icu_hr_zones=[120.0, 140, 160, 180, 200],
                               icu_hr_zone_times=[5, 4, 3, 2, 1])
        changed = self.heart_rate(icu_hr_zones=[121, 140, 160, 180, 200])
        self.assertEqual(first["zone_definition_id"], same["zone_definition_id"])
        self.assertNotEqual(first["zone_definition_id"], changed["zone_definition_id"])

    def test_zero_time_is_available_without_division_or_fake_percentages(self):
        summary = self.heart_rate(icu_hr_zone_times=[0, 0, 0, 0, 0])
        self.assertEqual(summary["status"], "available")
        self.assertEqual(summary["classified_seconds"], 0)
        self.assertEqual(summary["percentages"], [None] * 5)

    def test_missing_data_does_not_fabricate_zero_zone_time(self):
        for changes, reason in (({"icu_hr_zones": None, "icu_hr_zone_times": None}, "zone_data_missing"),
                                ({"icu_hr_zones": None}, "zone_boundaries_missing"),
                                ({"icu_hr_zone_times": None}, "zone_times_missing")):
            with self.subTest(reason=reason):
                summary = self.heart_rate(**changes)
                self.assertEqual(summary["status"], "missing")
                self.assertEqual(summary["reason"], reason)
                self.assertEqual(summary["seconds"], [])
                self.assertIsNone(summary["classified_seconds"])

    def test_ignored_hr_never_contributes_zone_times(self):
        summary = self.heart_rate(icu_ignore_hr=True)
        self.assertEqual(summary["status"], "ignored")
        self.assertEqual(summary["seconds"], [])
        self.assertEqual(summary["coverage"]["status"], "not_applicable")
        self.assertEqual(self.heart_rate(icu_ignore_hr=False)["status"], "available")
        for value in (1, 0, "true", [], {}):
            self.assertEqual(self.heart_rate(icu_ignore_hr=value)["status"], "invalid")

    def test_malformed_boundaries_cannot_be_paired_with_valid_times(self):
        for value in ([], [120, 120], [140, 120], [0], [-10], [120.5],
                      [True], [float("nan")], [float("inf")], [10**1000],
                      "120,140", {"1": 120}, ["120"], list(range(1, 34))):
            with self.subTest(value_type=type(value).__name__):
                summary = self.heart_rate(icu_hr_zones=value)
                self.assertEqual(summary["status"], "invalid")
                self.assertEqual(summary["reason"], "invalid_zone_boundaries")
                self.assertIsNone(summary["zone_definition_id"])
                self.assertEqual(summary["seconds"], [])

    def test_malformed_time_array_cannot_publish_partial_results(self):
        for value in ([], [1, 2], "300,900,600,120,0", {"1": 100},
                      [300, 900, 600, 120, None], [300, 900, 600, 120, True],
                      [300, 900, 600, 120, -1], [300, 900, 600, 120, "0"],
                      [300, 900, 600, 120, float("nan")],
                      [300, 900, 600, 120, float("inf")],
                      [300, 900, 600, 120, 10**1000]):
            with self.subTest(value_type=type(value).__name__):
                summary = self.heart_rate(icu_hr_zone_times=value)
                self.assertEqual(summary["status"], "invalid")
                self.assertEqual(summary["seconds"], [])
                self.assertIsNone(summary["classified_seconds"])

    def test_fractional_durations_are_preserved_without_sample_count_rounding(self):
        summary = self.heart_rate(icu_hr_zone_times=[0.25, 0.5, 1.75, 0, 0])
        self.assertEqual(summary["seconds"], [0.25, 0.5, 1.75, 0, 0])
        self.assertEqual(summary["classified_seconds"], 2.5)
        self.assertEqual(summary["percentages"], [10, 20, 70, 0, 0])


if __name__ == "__main__":
    unittest.main()
