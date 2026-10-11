"""Packaging safety with synthetic archives; the CI workflow tests real Windows."""
import base64
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import stat
import zipfile

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_windows.py"
spec = importlib.util.spec_from_file_location("studio_build", SCRIPT)
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


def synthetic_archive(entries=None):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as output:
        for name, data in (entries or {"runtime/bin/ffmpeg.exe": b"synthetic test fixture, never executed", "runtime/LICENSE.txt": b"Test fixture license"}).items():
            # Windows ZipInfo converts backslashes during construction. Keep
            # the malicious raw spelling in both ZIP headers for this fixture.
            member = zipfile.ZipInfo(name)
            member.filename = name
            member.orig_filename = name
            output.writestr(member, data)
    return stream.getvalue()


def runtime(payload):
    return {"url": "https://trusted.example/runtime.zip", "filename": "runtime.zip", "version": "test", "sha256": hashlib.sha256(payload).hexdigest(), "members": {"bin/ffmpeg.exe": "ffmpeg.exe"}}


def test_download_requires_https_and_pinned_checksum(tmp_path):
    item = runtime(b"test")
    item["url"] = "http://trusted.example/runtime.zip"
    with pytest.raises(ValueError, match="HTTPS"):
        build.download_verified(item, tmp_path)
    item["url"] = "https://trusted.example/runtime.zip"
    item["sha256"] = "unverified"
    with pytest.raises(ValueError, match="SHA256"):
        build.download_verified(item, tmp_path)


def test_matching_download_is_cached_only_after_verification(tmp_path, monkeypatch):
    payload = synthetic_archive()
    calls = []
    def response(request, **kwargs):
        calls.append(request)
        return io.BytesIO(payload)
    monkeypatch.setattr(build, "urlopen", response)
    item = runtime(payload)
    archive = build.download_verified(item, tmp_path)
    assert archive.read_bytes() == payload
    assert build.download_verified(item, tmp_path) == archive
    assert len(calls) == 1
    assert not list(tmp_path.glob("*.part"))


def test_checksum_failure_never_leaves_a_usable_archive(tmp_path, monkeypatch):
    item = runtime(b"expected bytes")
    monkeypatch.setattr(build, "urlopen", lambda *args, **kwargs: io.BytesIO(b"tampered"))
    with pytest.raises(ValueError, match="checksum mismatch"):
        build.download_verified(item, tmp_path)
    assert not list(tmp_path.iterdir())
    (tmp_path / item["filename"]).write_bytes(b"wrong cache")
    with pytest.raises(ValueError, match="Cached archive checksum"):
        build.download_verified(item, tmp_path)


def test_vendor_only_selected_binaries_and_license(tmp_path):
    payload = synthetic_archive({"runtime/bin/ffmpeg.exe": b"fixture", "runtime/bin/unwanted.exe": b"not bundled", "runtime/LICENSE.txt": b"license"})
    archive = tmp_path / "fixture.zip"
    archive.write_bytes(payload)
    build.vendor_runtime(archive, runtime(payload), tmp_path / "tools", tmp_path / "licenses")
    assert [path.name for path in (tmp_path / "tools").iterdir()] == ["ffmpeg.exe"]
    assert (tmp_path / "licenses" / "runtime.zip.LICENSE.txt").read_bytes() == b"license"


@pytest.mark.parametrize("name", ["../escaped.exe", "/escaped.exe", "C:/escaped.exe", "runtime\\escaped.exe"])
def test_vendor_rejects_archive_traversal_before_writing_binaries(tmp_path, name):
    payload = synthetic_archive({"runtime/bin/ffmpeg.exe": b"fixture", "runtime/LICENSE.txt": b"license", name: b"unsafe"})
    assert name.encode("utf-8") in payload
    archive = tmp_path / "fixture.zip"
    archive.write_bytes(payload)
    with pytest.raises(ValueError, match="unsafe path"):
        build.vendor_runtime(archive, runtime(payload), tmp_path / "tools", tmp_path / "licenses")
    assert not list((tmp_path / "tools").glob("*.exe"))


def test_vendor_checks_original_name_before_windows_separator_normalization():
    member = zipfile.ZipInfo("runtime/escaped.exe")
    member.orig_filename = "runtime\\escaped.exe"
    assert not build._safe_zip_member(member)


def test_vendor_refuses_symlinks_and_unverified_archive(tmp_path):
    archive = tmp_path / "fixture.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("runtime/bin/ffmpeg.exe", b"fixture")
        link = zipfile.ZipInfo("runtime/linked")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        output.writestr(link, "../../outside")
    item = runtime(archive.read_bytes())
    with pytest.raises(ValueError, match="symbolic link"):
        build.vendor_runtime(archive, item, tmp_path / "tools", tmp_path / "licenses")
    item["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="integrity"):
        build.vendor_runtime(archive, item, tmp_path / "tools", tmp_path / "licenses")


@pytest.mark.parametrize("name", [".env.local", "playlist.sqlite3", "private.db", "processes.json"])
def test_distribution_refuses_private_configuration_and_data(tmp_path, name):
    directory = tmp_path / "bundle"
    directory.mkdir()
    (directory / name).write_text("private fixture", encoding="utf-8")
    with pytest.raises(ValueError, match="Private configuration"):
        build.bundle_zip(directory, tmp_path / "output.zip")
    assert not (tmp_path / "output.zip").exists()


def test_zip_has_simple_top_level_directory_and_matching_sha256(tmp_path):
    directory = tmp_path / "bundle"
    (directory / "_internal" / "tools").mkdir(parents=True)
    (directory / "Studio-Fanny.exe").write_bytes(b"synthetic fixture, never executed")
    (directory / "_internal" / "tools" / "node.exe").write_bytes(b"synthetic runtime")
    archive = tmp_path / "Studio-Fanny-Windows.zip"
    checksum = build.bundle_zip(directory, archive)
    assert checksum == hashlib.sha256(archive.read_bytes()).hexdigest()
    assert archive.with_suffix(".zip.sha256").read_text().startswith(checksum + "  Studio-Fanny-Windows.zip")
    with zipfile.ZipFile(archive) as output:
        assert output.namelist() == ["Studio-Fanny/Studio-Fanny.exe", "Studio-Fanny/_internal/tools/node.exe"]


@pytest.mark.parametrize("config", [{"url": "https://fixture.supabase.co", "publicKey": "sb_secret_not_public"}, {"url": "http://fixture.supabase.co", "publicKey": "sb_publishable_fixture"}, {"url": "https://fixture.supabase.co", "publicKey": "sb_publishable_fixture", "service_role_key": "private"}])
def test_public_config_never_accepts_private_keys(tmp_path, config):
    path = tmp_path / "public-config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(ValueError):
        build.validate_public_config(path)


def test_legacy_supabase_service_role_jwt_is_rejected(tmp_path):
    payload = base64.urlsafe_b64encode(json.dumps({"role": "service_role"}).encode()).decode().rstrip("=")
    path = tmp_path / "public-config.json"
    path.write_text(json.dumps({"url": "https://fixture.supabase.co", "publicKey": "header." + payload + ".signature"}), encoding="utf-8")
    with pytest.raises(ValueError, match="private Supabase"):
        build.validate_public_config(path)
    payload = base64.urlsafe_b64encode(json.dumps({"role": "anon"}).encode()).decode().rstrip("=")
    path.write_text(json.dumps({"url": "https://fixture.supabase.co", "publicKey": "header." + payload + ".signature"}), encoding="utf-8")
    build.validate_public_config(path)


def test_actual_committed_public_config_is_safe_to_bundle():
    build.validate_public_config(SCRIPT.parent.parent / "cloud" / "public-config.json")


def test_production_runtime_pins_are_https_archives_with_stable_versions():
    for item in build.RUNTIMES.values():
        assert item["url"].startswith("https://")
        assert item["version"] in item["url"]
        assert len(item["sha256"]) == 64
        assert "latest" not in item["url"]
