import unittest


class CompleteSetTests(unittest.TestCase):
    def test_partition_portfolio_pays_one(self):
        for state in ("below", "bucket", "above"):
            lower_no = state == "below"
            bucket_yes = state == "bucket"
            upper_yes = state == "above"
            self.assertEqual(int(lower_no) + int(bucket_yes) + int(upper_yes), 1)

    def test_complement_portfolio_pays_two(self):
        for state in ("below", "bucket", "above"):
            lower_yes = state != "below"
            bucket_no = state != "bucket"
            upper_no = state != "above"
            self.assertEqual(int(lower_yes) + int(bucket_no) + int(upper_no), 2)


if __name__ == "__main__":
    unittest.main()
