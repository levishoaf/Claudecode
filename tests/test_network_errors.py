import unittest
from datetime import datetime, timezone
from unittest import mock

from nfl_edge import data, weather
from nfl_edge.venues import STADIUMS


class TimeoutTest(unittest.TestCase):
    def setUp(self):
        weather._down_until = 0.0

    tearDown = setUp

    def test_weather_timeout_skips_weather_instead_of_crashing(self):
        stadium = next(iter(STADIUMS))
        kick = datetime(2026, 10, 11, 17, tzinfo=timezone.utc)
        with mock.patch("urllib.request.urlopen",
                        side_effect=TimeoutError("The read operation timed out")) as get:
            self.assertIsNone(weather.kickoff_forecast(stadium, kick))
            self.assertIsNone(weather.kickoff_forecast(stadium, kick))
        self.assertEqual(get.call_count, 1)  # the second game didn't wait on it again

    def test_download_timeout_is_a_data_error(self):
        with mock.patch("urllib.request.urlopen", side_effect=TimeoutError("timed out")):
            with self.assertRaises(data.DataError):
                data._download("https://example.com/x.csv")
