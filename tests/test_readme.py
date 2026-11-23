"""The README shows every picture in docs/screenshots/, and only pictures that exist."""
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent


def test_every_screenshot_is_in_the_readme_and_every_readme_picture_exists():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    shown = set(re.findall(r"\(docs/screenshots/([\w-]+\.png)\)", readme))
    saved = {path.name for path in (ROOT / "docs" / "screenshots").glob("*.png")}
    assert shown == saved, {"missing from the README": saved - shown, "missing files": shown - saved}


def test_the_readme_counts_the_tests_roughly_right():
    """The Tools table says how many tests there are: keep it near the truth."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    claimed = int(re.search(r"The tests \((\d+) of them\)", readme).group(1))
    actual = sum(len(re.findall(r"^def test_|^    def test_", path.read_text(encoding="utf-8"), re.MULTILINE))
                 for path in (ROOT / "tests").glob("test_*.py"))
    assert actual * 0.8 <= claimed <= actual * 2.5, (claimed, actual)
