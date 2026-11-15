"""Fail a CI run whose pytest result is not evidence: nothing ran, or too much was skipped."""

import sys

from defusedxml import ElementTree as ET

MAX_SKIP_RATIO = 0.02


def verdict(junit_path: str, max_skip_ratio: float = MAX_SKIP_RATIO) -> tuple[bool, str]:
    root = ET.parse(junit_path).getroot()
    suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
    total = sum(int(s.get("tests", 0)) for s in suites)
    skipped = sum(int(s.get("skipped", 0)) for s in suites)
    failed = sum(int(s.get("failures", 0)) + int(s.get("errors", 0)) for s in suites)
    summary = f"{total} tests, {skipped} skipped, {failed} failed"
    if total == 0:
        return False, f"no tests ran ({summary})"
    if failed:
        return False, f"failures ({summary})"
    if skipped / total > max_skip_ratio:
        return False, f"more than {max_skip_ratio:.0%} skipped ({summary})"
    return True, summary


if __name__ == "__main__":
    ok, message = verdict(sys.argv[1])
    print(("ok: " if ok else "FAIL: ") + message)
    sys.exit(0 if ok else 1)
