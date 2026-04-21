import unittest
from datetime import date

from find_toi import (
    compile_matchers,
    is_target_day_post,
    should_scan_message_date,
)


class CompileMatchersTests(unittest.TestCase):
    def test_matches_existing_formats(self):
        base_regex, date_regex = compile_matchers(["TOI", "TOIH"], "19-04-2026")

        self.assertTrue(base_regex.search("TOIH - Hyderabad Times - 19-04-2026.pdf"))
        self.assertTrue(date_regex.search("TOIH - Hyderabad Times - 19-04-2026.pdf"))
        self.assertTrue(base_regex.search("ToI Hyderabad Times 19.04.2026.pdf"))
        self.assertTrue(date_regex.search("ToI Hyderabad Times 19.04.2026.pdf"))
        self.assertTrue(base_regex.search("TOI_Hyderabad_19-04-2026.pdf"))
        self.assertTrue(date_regex.search("TOI_Hyderabad_19-04-2026.pdf"))

    def test_matches_new_apostrophe_date_format(self):
        base_regex, date_regex = compile_matchers(["TOI", "TOIH"], "19-04-2026")

        self.assertTrue(base_regex.search("ToI Hyderabad 19'04'2026.pdf"))
        self.assertTrue(date_regex.search("ToI Hyderabad 19'04'2026.pdf"))

    def test_rejects_non_hyderabad_files(self):
        base_regex, date_regex = compile_matchers(["TOI", "TOIH"], "19-04-2026")

        self.assertFalse(base_regex.search("ToI Chennai 19'04'2026.pdf"))
        self.assertTrue(date_regex.search("ToI Chennai 19'04'2026.pdf"))

    def test_allows_nearby_message_dates(self):
        target_date = date(2026, 4, 19)

        self.assertTrue(should_scan_message_date(date(2026, 4, 17), target_date))
        self.assertTrue(should_scan_message_date(date(2026, 4, 18), target_date))
        self.assertTrue(should_scan_message_date(date(2026, 4, 19), target_date))
        self.assertTrue(should_scan_message_date(date(2026, 4, 20), target_date))
        self.assertFalse(should_scan_message_date(date(2026, 4, 16), target_date))
        self.assertFalse(should_scan_message_date(date(2026, 4, 21), target_date))

    def test_target_day_post_is_an_additional_match_path(self):
        target_date = date(2026, 4, 19)
        base_regex, date_regex = compile_matchers(["TOI", "TOIH"], "19-04-2026")
        filename = "ToI Hyderabad.pdf"

        self.assertTrue(base_regex.search(filename))
        self.assertFalse(date_regex.search(filename))
        self.assertTrue(is_target_day_post(date(2026, 4, 19), target_date))
        self.assertFalse(is_target_day_post(date(2026, 4, 18), target_date))


if __name__ == "__main__":
    unittest.main()
