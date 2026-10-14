"""Prepared, synthetic ZIP fixtures only: no audio downloads or network requests."""
from datetime import datetime, timedelta, timezone
import hashlib
import io
from pathlib import Path
import socket
from types import SimpleNamespace
from urllib.parse import urlsplit
import uuid
import zipfile

from fastapi.testclient import TestClient
import pytest

from playlist25.admin import create_admin_app
from playlist25.config import Settings
from playlist25.db import connect, init_db, now
from playlist25.guest import create_guest_app
from playlist25 import transfers

HOST = "192.168.10.5"
REAL_ADDRESSES = transfers.transfer_addresses


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    cfg = Settings(data_dir=tmp_path / "data", session_secret="transfer-test-only-secret", admin_password="transfer-test-only-password", min_free_mb=0)
    init_db(cfg)
    with connect(cfg) as conn:
        conn.executescript(transfers.TRANSFERS_SCHEMA)
    monkeypatch.setattr(transfers, "transfer_addresses", lambda _: [{"host": HOST, "label": "Wi-Fi test"}])
    return cfg


def prepared_zip(cfg, *, make_zip=True):
    export_id = uuid.uuid4().hex
    directory = cfg.export_dir / export_id
    directory.mkdir()
    archive = cfg.export_dir / f"{export_id}.zip" if make_zip else None
    if archive:
        with zipfile.ZipFile(archive, "w") as output:
            output.writestr("Anniversaire25/authorized-fixture.txt", "Test autorisé, créé localement.")
    with connect(cfg) as conn:
        conn.execute("INSERT INTO exports(id,directory,archive,created_at) VALUES(?,?,?,?)", (export_id, str(directory), str(archive) if archive else None, now()))
    return export_id, archive


def share(cfg):
    export_id, archive = prepared_zip(cfg)
    with connect(cfg) as conn:
        result = transfers.create_transfer(conn, cfg, export_id, HOST)
    return result, archive


@pytest.mark.parametrize("large_id", [2 ** 63, 2 ** 64 - 1, 2 ** 96 + 37, 2 ** 128 - 1])
def test_large_windows_file_ids_roundtrip_exactly_and_reject_replacement(cfg, monkeypatch, large_id):
    original_signature = transfers._signature
    file_identity = {"inode": large_id}
    def windows_signature(metadata):
        # All named/descriptor checks see the same synthetic NTFS identity;
        # ZIP bytes, SQLite storage, authorization and HTTP streaming are real.
        return original_signature(SimpleNamespace(st_size=metadata.st_size, st_mtime_ns=metadata.st_mtime_ns,
                                                  st_dev=2 ** 64 + 3, st_ino=file_identity["inode"]))
    monkeypatch.setattr(transfers, "_signature", windows_signature)
    result, archive = share(cfg)
    with connect(cfg) as conn:
        row = conn.execute("SELECT device,inode,typeof(device),typeof(inode) FROM transfers WHERE id=?", (result["id"],)).fetchone()
    assert tuple(row) == (f"file-id:{2 ** 64 + 3:x}", f"file-id:{large_id:x}", "text", "text")
    client = guest_client(cfg)
    url = urlsplit(result["url"]).path + "/download"
    first = client.get(url, headers={"Range": "bytes=0-24"})
    rest = client.get(url, headers={"Range": "bytes=25-", "If-Range": first.headers["etag"]})
    assert first.status_code == rest.status_code == 206
    assert first.content + rest.content == archive.read_bytes()
    file_identity["inode"] += 1
    assert client.get(url).status_code == 404


def guest_client(cfg, *, client_ip="192.168.10.6", host=HOST):
    app = create_guest_app(cfg)
    transfers.attach_guest_routes(app, cfg)
    return TestClient(app, base_url=f"http://{host}:{cfg.guest_port}", client=(client_ip, 12345))


def admin_client(cfg):
    app = create_admin_app(cfg)
    transfers.attach_admin_routes(app, cfg)
    return TestClient(app)


def login(client, cfg):
    csrf = client.get("/api/session").json()["csrf_token"]
    response = client.post("/api/login", json={"password": cfg.admin_password}, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def test_direct_lan_phone_can_get_only_the_selected_prepared_zip(cfg):
    result, archive = share(cfg)
    client = guest_client(cfg)
    path = urlsplit(result["url"]).path
    page = client.get(path)
    assert page.status_code == 200 and "Télécharger le pack ZIP" in page.text
    response = client.get(path + "/download")
    assert response.status_code == 200 and response.content == archive.read_bytes()
    assert response.headers["content-type"] == "application/zip"
    assert int(response.headers["content-length"]) == archive.stat().st_size
    with zipfile.ZipFile(io.BytesIO(response.content)) as downloaded:
        assert downloaded.read("Anniversaire25/authorized-fixture.txt").decode().startswith("Test autorisé")
    for route in ("/api/transfers", "/api/transfers/addresses", "/api/library", "/api/library/1/audio", f"/api/exports/{archive.stem}/zip", "/admin", "/transfer/"):
        assert client.get(route).status_code == 404
    assert str(cfg.export_dir) not in page.text


def test_raw_token_is_never_stored_and_expiry_is_ten_minutes(cfg):
    result, _ = share(cfg)
    token = urlsplit(result["url"]).path.rsplit("/", 1)[1]
    assert len(token) == 43 and result["qr_data_url"].startswith("data:image/png;base64,")
    with connect(cfg) as conn:
        row = dict(conn.execute("SELECT * FROM transfers WHERE id=?", (result["id"],)).fetchone())
    assert token not in " ".join(str(value) for value in row.values())
    assert row["token_hash"] == hashlib.sha256(token.encode("ascii")).hexdigest()
    assert datetime.fromisoformat(row["expires_at"]) - datetime.fromisoformat(row["created_at"]) == timedelta(minutes=10)
    assert not Path(row["archive_relative"]).is_absolute()


@pytest.mark.parametrize("state", ["expired", "revoked", "unknown", "malformed"])
def test_expired_revoked_unknown_or_malformed_token_all_fail_without_disclosure(cfg, state):
    result, _ = share(cfg)
    path = urlsplit(result["url"]).path
    with connect(cfg) as conn:
        if state == "expired":
            conn.execute("UPDATE transfers SET expires_at=? WHERE id=?", ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), result["id"]))
        elif state == "revoked":
            assert transfers.revoke_transfer(conn, result["id"])
            assert not transfers.revoke_transfer(conn, result["id"])
        elif state == "unknown":
            path = "/transfer/" + "a" * 43
        elif state == "malformed":
            path = "/transfer/not-a-token"
    client = guest_client(cfg)
    for route in (path, path + "/download"):
        response = client.get(route)
        assert response.status_code == 404 and "même Wi-Fi" in response.json()["detail"]
        assert "no-store" in response.headers["cache-control"]
        assert response.headers["referrer-policy"] == "no-referrer"


@pytest.mark.parametrize("header", ["Forwarded", "X-Forwarded-For", "X-Forwarded-Host", "X-Forwarded-Proto", "X-Original-Forwarded-For", "X-Real-IP", "Via", "CF-Connecting-IP", "CF-Ray", "CF-Visitor", "X-Proxy-Anything", "True-Client-IP"])
def test_proxy_and_tunnel_headers_cannot_turn_loopback_into_public_zip_access(cfg, header):
    result, _ = share(cfg)
    # A tunnel connects locally; its provenance headers must forbid the transfer.
    client = guest_client(cfg, client_ip="127.0.0.1")
    path = urlsplit(result["url"]).path
    assert client.get(path + "/download", headers={header: "192.168.10.6"}).status_code == 404


@pytest.mark.parametrize("client_ip", ["8.8.8.8", "203.0.113.1", "169.254.169.254", "2001:4860:4860::8888"])
def test_public_documentation_and_metadata_addresses_are_not_direct_private_clients(cfg, client_ip):
    result, _ = share(cfg)
    response = guest_client(cfg, client_ip=client_ip).get(urlsplit(result["url"]).path + "/download")
    assert response.status_code == 404


def test_host_must_match_selected_interface_and_port_without_duplicate_authorities(cfg):
    result, _ = share(cfg)
    path = urlsplit(result["url"]).path
    assert guest_client(cfg, host="rebinding.example").get(path).status_code == 404
    client = guest_client(cfg)
    assert client.get(path, headers={"Host": HOST}).status_code == 404
    assert client.get(path, headers={"Host": f"{HOST}:9999"}).status_code == 404
    assert client.get(path, headers=[("Host", f"{HOST}:8000"), ("Host", "evil.example:8000")]).status_code == 404


def test_zip_changed_or_removed_after_share_is_refused(cfg):
    result, archive = share(cfg)
    client = guest_client(cfg)
    path = urlsplit(result["url"]).path + "/download"
    with archive.open("ab") as output:
        output.write(b"changed")
    assert client.get(path).status_code == 404
    archive.unlink()
    assert client.get(path).status_code == 404


def test_symlink_zip_and_symlink_parent_are_refused(cfg, tmp_path):
    export_id, archive = prepared_zip(cfg)
    target = tmp_path / "outside.zip"
    target.write_bytes(archive.read_bytes())
    archive.unlink()
    try:
        archive.symlink_to(target)
    except OSError:
        pytest.skip("Symbolic links are not available to this test user")
    with connect(cfg) as conn:
        with pytest.raises(transfers.TransferError, match="symbolique"):
            transfers.create_transfer(conn, cfg, export_id, HOST)
    linked = cfg.export_dir / "linked"
    linked.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(transfers.TransferError, match="symbolique"):
        transfers._archive_path(cfg, "linked/outside.zip")


@pytest.mark.parametrize("path", ["../outside.zip", "/etc/secrets.zip", "nested\\outside.zip", "a\x00.zip"])
def test_archive_paths_cannot_escape_export_directory(cfg, path):
    with pytest.raises(transfers.TransferError):
        transfers._archive_path(cfg, path)


def test_creation_requires_real_prepared_zip_and_known_private_interface(cfg):
    export_id, _ = prepared_zip(cfg, make_zip=False)
    with connect(cfg) as conn:
        with pytest.raises(transfers.TransferError, match="ZIP"):
            transfers.create_transfer(conn, cfg, export_id, HOST)
    export_id, archive = prepared_zip(cfg)
    with connect(cfg) as conn:
        for host in ("127.0.0.1", "192.168.99.99", "8.8.8.8", "localhost", "https://example.com", "169.254.169.254"):
            with pytest.raises(transfers.TransferError, match="adresse privée"):
                transfers.create_transfer(conn, cfg, export_id, host)
        archive.write_text("a .zip suffix is insufficient", encoding="utf-8")
        with pytest.raises(transfers.TransferError, match="invalide"):
            transfers.create_transfer(conn, cfg, export_id, HOST)


def test_security_headers_apply_to_page_archive_and_errors(cfg):
    result, _ = share(cfg)
    client = guest_client(cfg)
    path = urlsplit(result["url"]).path
    for route in (path, path + "/download", "/transfer/invalid"):
        response = client.get(route)
        assert response.headers["referrer-policy"] == "no-referrer"
        assert "no-store" in response.headers["cache-control"]
        assert "script-src" not in response.headers["content-security-policy"]
        assert "default-src 'none'" in response.headers["content-security-policy"]
        assert response.headers["x-frame-options"] == "DENY"


def test_admin_authentication_and_csrf_are_required_for_share_and_revoke(cfg):
    export_id, _ = prepared_zip(cfg)
    client = admin_client(cfg)
    assert client.get("/api/transfers/addresses").status_code == 401
    assert client.post("/api/transfers", json={"export_id": export_id, "host": HOST}).status_code == 401
    headers = login(client, cfg)
    assert client.get("/api/transfers/addresses").json()["addresses"] == [{"host": HOST, "label": "Wi-Fi test"}]
    assert client.post("/api/transfers", json={"export_id": export_id, "host": HOST}).status_code == 403
    created = client.post("/api/transfers", json={"export_id": export_id, "host": HOST}, headers=headers)
    assert created.status_code == 200, created.text
    assert "no-store" in created.headers["cache-control"]
    result = created.json()
    assert client.post("/api/transfers/revoke", json={"id": result["id"]}).status_code == 403
    assert client.post("/api/transfers/revoke", json={"id": result["id"]}, headers=headers).json() == {"revoked": True}
    assert guest_client(cfg).get(urlsplit(result["url"]).path).status_code == 404
    assert client.post("/api/transfers", json={"export_id": export_id, "host": HOST, "expires_in": 99999}, headers=headers).status_code == 422


def test_address_candidates_are_real_up_private_interfaces_matching_guest_binding(cfg, monkeypatch):
    cfg.local_url = "http://192.168.20.7:8000"
    records = {
        "Wi-Fi": [SimpleNamespace(family=socket.AF_INET, address="192.168.20.7"), SimpleNamespace(family=socket.AF_INET6, address="fd12::7")],
        "Ethernet": [SimpleNamespace(family=socket.AF_INET, address="10.0.0.8")],
        "docker0": [SimpleNamespace(family=socket.AF_INET, address="172.17.0.1")],
        "tun0": [SimpleNamespace(family=socket.AF_INET, address="10.99.0.1")],
        "Inactive": [SimpleNamespace(family=socket.AF_INET, address="192.168.1.2")],
        "Loopback": [SimpleNamespace(family=socket.AF_INET, address="127.0.0.1")],
        "Internet": [SimpleNamespace(family=socket.AF_INET, address="8.8.8.8")],
        "Documented": [SimpleNamespace(family=socket.AF_INET, address="192.0.2.2")],
    }
    statuses = {name: SimpleNamespace(isup=name != "Inactive") for name in records}
    monkeypatch.setattr(transfers.psutil, "net_if_addrs", lambda: records)
    monkeypatch.setattr(transfers.psutil, "net_if_stats", lambda: statuses)
    assert [item["host"] for item in REAL_ADDRESSES(cfg)] == ["192.168.20.7", "10.0.0.8"]
    cfg.guest_host = "::"
    assert [item["host"] for item in REAL_ADDRESSES(cfg)] == ["fd12::7"]
    cfg.guest_host = "127.0.0.1"
    assert REAL_ADDRESSES(cfg) == []


def test_ula_ipv6_lan_transfer_url_and_host_check(cfg, monkeypatch):
    cfg.guest_host = "::"
    host = "fd12::5"
    monkeypatch.setattr(transfers, "transfer_addresses", lambda _: [{"host": host, "label": "IPv6 Wi-Fi"}])
    export_id, archive = prepared_zip(cfg)
    with connect(cfg) as conn:
        result = transfers.create_transfer(conn, cfg, export_id, host)
    assert result["url"].startswith("http://[fd12::5]:8000/transfer/")
    app = create_guest_app(cfg)
    transfers.attach_guest_routes(app, cfg)
    client = TestClient(app, base_url="http://[fd12::5]:8000", client=("fd12::6", 12345))
    assert client.get(urlsplit(result["url"]).path + "/download").content == archive.read_bytes()


def test_existing_active_transfer_quota_is_bounded(cfg, monkeypatch):
    monkeypatch.setattr(transfers, "MAX_ACTIVE_TRANSFERS", 2)
    export_id, _ = prepared_zip(cfg)
    with connect(cfg) as conn:
        first = transfers.create_transfer(conn, cfg, export_id, HOST)
        transfers.create_transfer(conn, cfg, export_id, HOST)
        with pytest.raises(transfers.TransferError, match="Trop de partages"):
            transfers.create_transfer(conn, cfg, export_id, HOST)
        transfers.revoke_transfer(conn, first["id"])
        assert transfers.create_transfer(conn, cfg, export_id, HOST)["id"]


@pytest.mark.parametrize("header", ["bytes=0-9", "bytes=10-", "bytes=-12", "bytes=0-9999999"])
def test_lan_zip_supports_exact_resume_ranges_without_loading_the_pack(cfg, header):
    result, archive = share(cfg)
    data = archive.read_bytes()
    response = guest_client(cfg).get(urlsplit(result["url"]).path + "/download", headers={"Range": header})
    assert response.status_code == 206
    if header == "bytes=0-9":
        start, end = 0, 9
    elif header == "bytes=10-":
        start, end = 10, len(data) - 1
    elif header == "bytes=-12":
        start, end = len(data) - 12, len(data) - 1
    else:
        start, end = 0, len(data) - 1
    assert response.content == data[start:end + 1]
    assert response.headers["content-range"] == f"bytes {start}-{end}/{len(data)}"
    assert int(response.headers["content-length"]) == len(response.content)
    assert response.headers["accept-ranges"] == "bytes"
    assert "no-store" in response.headers["cache-control"]


@pytest.mark.parametrize("header", ["bytes=9999999-", "bytes=10-2", "bytes=-0", "bytes=-", "bytes=0-1,5-7", "bytes=" + "9" * 300 + "-"])
def test_invalid_zip_ranges_do_not_leak_data_or_file_handles(cfg, header, monkeypatch):
    result, archive = share(cfg)
    opened = []
    actual = transfers._open_archive
    def recording(*args):
        path, file, metadata = actual(*args)
        opened.append(file)
        return path, file, metadata
    monkeypatch.setattr(transfers, "_open_archive", recording)
    response = guest_client(cfg).get(urlsplit(result["url"]).path + "/download", headers={"Range": header})
    assert response.status_code == 416 and response.content == b""
    assert response.headers["content-range"] == f"bytes */{archive.stat().st_size}"
    assert all(file.closed for file in opened)


def test_zip_head_and_if_range_use_the_same_authorized_immutable_file(cfg):
    result, archive = share(cfg)
    client = guest_client(cfg)
    path = urlsplit(result["url"]).path + "/download"
    head = client.head(path)
    assert head.status_code == 200 and head.content == b""
    assert int(head.headers["content-length"]) == archive.stat().st_size
    for validator in (head.headers["etag"], head.headers["last-modified"]):
        response = client.get(path, headers={"Range": "bytes=5-10", "If-Range": validator})
        assert response.status_code == 206 and response.content == archive.read_bytes()[5:11]
    for validator in ('"different-file"', "W/" + head.headers["etag"], "invalid date"):
        response = client.get(path, headers={"Range": "bytes=5-10", "If-Range": validator})
        assert response.status_code == 200 and response.content == archive.read_bytes()
    response = client.get(path, headers={"Range": "items=1-2"})
    assert response.status_code == 200 and response.content == archive.read_bytes()
    with connect(cfg) as conn:
        transfers.revoke_transfer(conn, result["id"])
    assert client.head(path).status_code == 404
    assert client.get(path, headers={"Range": "bytes=5-10", "If-Range": head.headers["etag"]}).status_code == 404


def test_copy_changed_between_path_validation_and_open_is_rejected(cfg, monkeypatch):
    result, archive = share(cfg)
    original_open = transfers.os.open
    def changed(path, *args, **kwargs):
        if Path(path) == archive:
            with archive.open("ab") as output:
                output.write(b"changed before descriptor open")
        return original_open(path, *args, **kwargs)
    # _open_archive must bind validation to its descriptor, rather than serving
    # a path whose contents were replaced between the two operations.
    original_path = transfers._archive_path
    def validate_then_change(*args):
        path = original_path(*args)
        monkeypatch.setattr(transfers.os, "open", changed)
        return path
    monkeypatch.setattr(transfers, "_archive_path", validate_then_change)
    response = guest_client(cfg).get(urlsplit(result["url"]).path + "/download")
    assert response.status_code == 404
