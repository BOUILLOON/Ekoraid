"""Expose pytest failures as GitHub annotations when full CI logs are unavailable."""
from __future__ import annotations

from pathlib import Path
import sys
from xml.etree import ElementTree


def escape_message(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    report = Path(sys.argv[1])
    if not report.is_file():
        print("::error title=Windows test diagnostics::pytest did not produce its JUnit report.")
        return
    for case in ElementTree.parse(report).getroot().iter("testcase"):
        for failure in (*case.findall("failure"), *case.findall("error")):
            title = "Windows test: " + case.get("name", "unknown")
            title = escape_message(title).replace(",", "%2C").replace(":", "%3A")
            details = failure.text or failure.get("message", "Test failed without a diagnostic message.")
            print(f"::error title={title}::{escape_message(details[:12000])}")


if __name__ == "__main__":
    main()
