import unittest

from src.analytics import packaging_underperforming


class AnalyticsSampleTests(unittest.TestCase):
    def test_sparse_or_missing_impressions_never_trigger_changes(self):
        self.assertFalse(packaging_underperforming({"videoThumbnailImpressionsClickRate": 0.01}, 0.04))
        self.assertFalse(packaging_underperforming({"videoThumbnailImpressions": 30,
                                                    "videoThumbnailImpressionsClickRate": 0.01}, 0.04))

    def test_mature_low_ctr_sample_can_trigger_review(self):
        self.assertTrue(packaging_underperforming({"videoThumbnailImpressions": 100,
                                                   "videoThumbnailImpressionsClickRate": 2.0}, 0.04))
        self.assertFalse(packaging_underperforming({"videoThumbnailImpressions": 300,
                                                    "videoThumbnailImpressionsClickRate": 7.0}, 0.04))


if __name__ == "__main__":
    unittest.main()
