from pathlib import Path
import unittest


class DependencyMetadataTests(unittest.TestCase):
    def test_dexter_controller_comes_from_pypi_extra(self):
        pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
        text = pyproject.read_text(encoding="utf-8")

        self.assertIn('"dexter-controller>=0.2.2"', text)
        self.assertNotIn("dexter-controller @", text)
        self.assertNotIn("git+", text)
        self.assertNotIn("github.com/fchampalimaud", text)


if __name__ == "__main__":
    unittest.main()
