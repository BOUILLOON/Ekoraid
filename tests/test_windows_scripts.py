"""Windows PowerShell 5.1 needs BOM detection; parse scripts without executing them."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WINDOWS_SCRIPTS = ("install-windows.ps1", "Start-Windows.ps1", "Stop-Windows.ps1")


@pytest.mark.parametrize("name", WINDOWS_SCRIPTS)
def test_windows_scripts_are_unambiguous_utf8_for_powershell51(name):
    data = (ROOT / name).read_bytes()
    assert data.startswith(b"\xef\xbb\xbf"), "Windows PowerShell 5.1 otherwise interprets UTF-8 script text as the local ANSI encoding"
    data[3:].decode("utf-8", errors="strict")


def test_windows_scripts_parse_without_running_application():
    runtime = shutil.which("powershell.exe") or shutil.which("powershell") or shutil.which("pwsh")
    if not runtime:
        pytest.skip("No native PowerShell runtime; execute this parser check on Windows/PowerShell CI")
    quoted = ["'" + str(ROOT / name).replace("'", "''") + "'" for name in WINDOWS_SCRIPTS]
    command = """
$PlaylistParseFailed = $false
foreach ($PlaylistScript in @(%s)) {
    $PlaylistTokens = $null
    $PlaylistParseErrors = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile($PlaylistScript, [ref]$PlaylistTokens, [ref]$PlaylistParseErrors)
    foreach ($PlaylistParseError in $PlaylistParseErrors) {
        Write-Output ($PlaylistScript + ':' + $PlaylistParseError.Extent.StartLineNumber + ': ' + $PlaylistParseError.Message)
        $PlaylistParseFailed = $true
    }
}
if ($PlaylistParseFailed) { exit 1 }
""" % ",".join(quoted)
    result = subprocess.run([runtime, "-NoProfile", "-NonInteractive", "-Command", command],
                            capture_output=True, text=True, errors="replace", timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
