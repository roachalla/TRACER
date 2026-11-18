"""Tests for the pure text checks (scripts/checks.py). No API key, models or video needed.
Run from the project folder:  python -m unittest discover tests"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
from checks import covered, eq_norm, incomplete, nums, split_md, spoken_numbers  # noqa: E402


class SpokenNumbers(unittest.TestCase):
    def test_compound(self):
        self.assertEqual(spoken_numbers("twenty five hundred metres"), {"2500"})
        self.assertEqual(spoken_numbers("one hundred and ten"), {"110"})
        self.assertEqual(spoken_numbers("three thousand two hundred"), {"3200"})

    def test_decimal(self):
        self.assertEqual(spoken_numbers("point nine"), {"0.9"})
        self.assertEqual(spoken_numbers("two point five seconds"), {"2.5"})

    def test_bare_point_is_not_a_number(self):
        self.assertEqual(spoken_numbers("at that point we stop"), set())

    def test_separate_numbers(self):
        self.assertEqual(spoken_numbers("two waves and three crests"), {"2", "3"})

    def test_nums_mixes_digits_and_words(self):
        self.assertEqual(nums("T = 8.5 s, depth twenty metres"), {"8.5", "20"})


class SplitMd(unittest.TestCase):
    def test_display_and_inline_equations(self):
        md = "Dispersion relation\n$$\\omega^2 = gk\\tanh(kh)$$\n$L_0 = g T^2 / 2\\pi$ deep water\nshort $x$"
        text, eqs = split_md(md)
        self.assertEqual(eqs[0], "\\omega^2 = gk\\tanh(kh)")
        self.assertIn("\\text{deep water}", eqs[1])
        self.assertEqual(text, ["Dispersion relation", "short $x$"])  # short inline math stays in the text


class EquationCompare(unittest.TestCase):
    def test_same_relation_written_differently(self):
        self.assertEqual(eq_norm("a \\approx b"), eq_norm("a = b"))
        self.assertEqual(eq_norm("x \\to 0"), eq_norm("x \\rightarrow 0"))
        self.assertEqual(eq_norm("\\left( a \\right)"), "(a)")

    def test_covered(self):
        self.assertEqual(covered("", "anything"), 1.0)
        self.assertEqual(covered("P=I", "P=IV"), 1.0)
        self.assertLess(covered("P=IV", "P=I^2R"), 0.9)


class Incomplete(unittest.TestCase):
    def test_unfinished(self):
        for e in ("C_0 = L_0/T =", "h =", "", "  ", "\\quad", "a \\approx", "\\text{ }"):
            self.assertTrue(incomplete(e), e)

    def test_finished(self):
        for e in ("h = 10", "C_0 = L_0/T", "x \\to 0", "\\omega^2 = gk"):
            self.assertFalse(incomplete(e), e)


if __name__ == "__main__":
    unittest.main()
