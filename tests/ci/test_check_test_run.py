import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_test_run.py"
spec = importlib.util.spec_from_file_location("check_test_run", SCRIPT)
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


def junit(tmp_path, tests, skipped=0, failures=0, errors=0):
    path = tmp_path / "junit.xml"
    path.write_text(
        f'<testsuites><testsuite name="pytest" tests="{tests}" skipped="{skipped}" '
        f'failures="{failures}" errors="{errors}"/></testsuites>'
    )
    return str(path)


@pytest.mark.parametrize(
    "counts, ok, text",
    [
        ({"tests": 1600, "skipped": 2}, True, "1600 tests, 2 skipped, 0 failed"),
        ({"tests": 0}, False, "no tests ran"),
        ({"tests": 100, "skipped": 3}, False, "more than 2% skipped"),
        ({"tests": 100, "errors": 1}, False, "failures"),
    ],
)
def test_verdict(tmp_path, counts, ok, text):
    result, message = check.verdict(junit(tmp_path, **counts))
    assert result is ok and text in message
