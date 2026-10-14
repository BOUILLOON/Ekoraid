"""Expose pytest failures as GitHub annotations when full CI logs are unavailable."""
from __future__ import annotations

from pathlib import Path
import json
import math
import re
import sys
from xml.etree import ElementTree


def escape_message(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _seconds(value: str | None) -> float:
    try:
        seconds = float(value or 0)
    except (ValueError, TypeError):
        return 0.0
    return seconds if math.isfinite(seconds) and seconds >= 0 else 0.0


def failure_message(failure: ElementTree.Element) -> str:
    """Group the exception message, not varying fixture names or traceback paths."""
    if failure.get("message", "").strip():
        return failure.get("message").strip()
    for line in reversed((failure.text or "").splitlines()):
        text = re.sub(r"^\s*E\s+", "", line).strip()
        if re.match(r"^(?:[\w.]+(?:Error|Exception|Failure)|Failed|SystemExit|KeyboardInterrupt):\s*\S", text):
            return text
    return (failure.text or "Test failed without a diagnostic message.").strip()


def summarize(root: ElementTree.Element) -> tuple[dict, dict]:
    cases = list(root.iter("testcase"))
    summary = {"report_available": True, "tests": len(cases), "passed": 0, "failed": 0,
               "errors": 0, "skipped": 0, "duration_seconds": 0.0, "failure_groups": 0}
    groups = {}
    for case in cases:
        failures = [*case.findall("failure"), *case.findall("error")]
        status = "errors" if case.find("error") is not None else "failed" if failures else "skipped" if case.find("skipped") is not None else "passed"
        summary[status] += 1
        for failure in failures:
            message = failure_message(failure)
            group = groups.setdefault(message, {"count": 0, "representative": case.get("name", "unknown"),
                                               "details": failure.text or failure.get("message", "Test failed without a diagnostic message.")})
            group["count"] += 1
    if root.get("time") is not None:
        elapsed = _seconds(root.get("time"))
    elif root.tag == "testsuites" and root.findall("testsuite"):
        elapsed = sum(_seconds(suite.get("time")) for suite in root.findall("testsuite"))
    else:
        elapsed = sum(_seconds(case.get("time")) for case in cases)
    summary["duration_seconds"] = round(elapsed, 6)
    summary["failure_groups"] = len(groups)
    return summary, groups


def write_summary(report: Path, summary: dict) -> None:
    # Counts only: no test names, paths, fixture values, passwords or tracebacks.
    destination = report.with_name("pytest-summary.json")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def annotate(report: Path) -> None:
    unavailable = {"report_available": False, "tests": None, "passed": None, "failed": None,
                   "errors": None, "skipped": None, "duration_seconds": None, "failure_groups": None}
    if not report.is_file():
        write_summary(report, unavailable)
        print("::error title=Windows test diagnostics::pytest did not produce its JUnit report.")
        return
    try:
        root = ElementTree.parse(report).getroot()
    except (ElementTree.ParseError, OSError):
        write_summary(report, unavailable)
        print("::error title=Windows test diagnostics::pytest produced an unreadable JUnit report.")
        return
    summary, groups = summarize(root)
    write_summary(report, summary)
    counts = ", ".join(f"{key}={value}" for key, value in summary.items() if key != "report_available")
    print(f"::notice title=Windows pytest summary::{escape_message(counts)}")
    for message, group in groups.items():
        title = escape_message(f"Windows tests ({group['count']}): {message[:200]}").replace(",", "%2C").replace(":", "%3A")
        details = f"{group['count']} failure/error occurrence(s). Representative: {group['representative']}\n{group['details']}"
        print(f"::error title={title}::{escape_message(details)}")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    annotate(Path(sys.argv[1]))


if __name__ == "__main__":
    main()
