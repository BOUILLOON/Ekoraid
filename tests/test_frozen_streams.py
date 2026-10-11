"""No-console launch and inherited-handle ownership at the frozen entry point."""
import ctypes
from ctypes import wintypes
from importlib.util import module_from_spec, spec_from_file_location
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "studio_entry.py"
SPEC = spec_from_file_location("studio_frozen_streams", SCRIPT)
entry = module_from_spec(SPEC)
SPEC.loader.exec_module(entry)


class Function:
    def __init__(self, action):
        self.action = action

    def __call__(self, *args):
        return self.action(*args)


def win32_fixture(monkeypatch, *, handle=101, file_type=3, duplicate_ok=True, fail_open=False):
    closed, opened, duplicate_calls = [], [], []
    def duplicate(process, original, target, result, access, inherit, options):
        duplicate_calls.append((original, inherit, options))
        if duplicate_ok:
            ctypes.cast(result, ctypes.POINTER(wintypes.HANDLE))[0] = 202
        return duplicate_ok
    def open_handle(value, flags):
        opened.append((value, flags))
        if fail_open:
            raise OSError(6, "The handle is invalid")
        return 303
    kernel = SimpleNamespace(
        GetStdHandle=Function(lambda *args: handle),
        GetFileType=Function(lambda *args: file_type),
        GetCurrentProcess=Function(lambda: 404),
        DuplicateHandle=Function(duplicate),
        CloseHandle=Function(lambda value: closed.append(value) or True),
    )
    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: kernel, raising=False)
    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(open_osfhandle=open_handle))
    return closed, opened, duplicate_calls


@pytest.mark.parametrize("handle,file_type", [(None, 0), (0, 0), (ctypes.c_void_p(-1).value, 0), (101, 0)])
def test_absent_or_invalid_explorer_handles_never_become_crt_streams(monkeypatch, handle, file_type):
    closed, opened, duplicates = win32_fixture(monkeypatch, handle=handle, file_type=file_type)
    assert entry._windows_standard_descriptor(-11, "w") is None
    assert opened == duplicates == closed == []


@pytest.mark.parametrize("file_type", [1, 2, 3])
def test_valid_inherited_handle_is_duplicated_without_taking_original_ownership(monkeypatch, file_type):
    closed, opened, duplicates = win32_fixture(monkeypatch, file_type=file_type)
    assert entry._windows_standard_descriptor(-11, "w") == 303
    assert opened[0][0] == 202
    assert duplicates == [(101, False, 2)]
    assert closed == []


def test_crt_rejection_closes_owned_duplicate_and_preserves_original(monkeypatch):
    closed, opened, duplicates = win32_fixture(monkeypatch, fail_open=True)
    assert entry._windows_standard_descriptor(-10, "r") is None
    assert opened[0][0] == 202
    assert closed == [202]


def test_failed_handle_duplication_does_not_transfer_original_to_crt(monkeypatch):
    closed, opened, duplicates = win32_fixture(monkeypatch, duplicate_ok=False)
    assert entry._windows_standard_descriptor(-11, "w") is None
    assert opened == closed == []
    assert duplicates == [(101, False, 2)]


@pytest.mark.parametrize("mode,handle_id", [("r", -10), ("w", -11), ("w", -12)])
def test_explorer_no_console_restores_usable_null_stream(monkeypatch, mode, handle_id):
    monkeypatch.setattr(entry, "_standard_descriptor", lambda *args: None)
    with entry._restored_stream(0 if mode == "r" else 1, handle_id, mode) as stream:
        if mode == "r":
            assert stream.read() == ""
        else:
            stream.write("Le studio de Fanny\n")
            stream.flush()
        os.fstat(stream.fileno())


def test_invalid_crt_descriptor_falls_back_without_winerror6(monkeypatch):
    monkeypatch.setattr(entry, "_standard_descriptor", lambda *args: -100)
    with entry._restored_stream(1, -11, "w") as stream:
        stream.write("desktop can open")
        stream.flush()


def test_failure_during_fdopen_closes_duplicate_then_uses_null(monkeypatch, tmp_path):
    descriptor = os.open(tmp_path / "fixture.log", os.O_WRONLY | os.O_CREAT)
    monkeypatch.setattr(entry, "_standard_descriptor", lambda *args: descriptor)
    def invalid_wrapper(*args, **kwargs):
        raise OSError(6, "The handle is invalid")
    monkeypatch.setattr(entry.os, "fdopen", invalid_wrapper)
    with entry._restored_stream(1, -11, "w") as stream:
        stream.write("desktop can open")
        stream.flush()
    with pytest.raises(OSError):
        os.fstat(descriptor)


def test_restored_redirected_log_remains_usable_and_leaves_original_open(tmp_path, monkeypatch):
    path = tmp_path / "service.log"
    original = os.open(path, os.O_WRONLY | os.O_CREAT)
    try:
        monkeypatch.setattr(entry, "_standard_descriptor", lambda *args: os.dup(original))
        with entry._restored_stream(1, -11, "w") as stream:
            stream.write("doctor JSON remains capturable\n")
            stream.flush()
        os.write(original, b"original still owns its handle\n")
    finally:
        os.close(original)
    assert "doctor JSON remains capturable" in path.read_text(encoding="utf-8")
    assert "original still owns its handle" in path.read_text(encoding="utf-8")
