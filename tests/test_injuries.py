import unittest

from nfl_edge.board import short_injury


class ShortInjuryTest(unittest.TestCase):
    def test_summaries(self):
        cases = {"Knee": "Knee", "Left Knee": "Knee", "Hamstring, Ankle": "Hamstring",
                 "Not injury related - resting player": "Rest",
                 "Not injury related - personal matter": "Personal",
                 "Not injury related": "Non-injury", "": "Undisclosed",
                 "right shoulder sprain": "Shoulder Sprain"}
        for text, want in cases.items():
            self.assertEqual(short_injury(text), want, text)
            self.assertLessEqual(len(short_injury(text).split()), 2)


if __name__ == "__main__":
    unittest.main()
