from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import shutil
import subprocess
import sys
import threading
from unittest.mock import Mock, patch
import wave

import pytest

from playlist25.config import Settings
from playlist25.db import connect, init_db, now, set_setting, upsert_source
from playlist25.providers import SourceError, analyze, analyze_links, parse_youtube_url, search, yt_dlp_command
from playlist25.queue import claim_job, enqueue_sources, queue_snapshot, recover_jobs
from playlist25.worker import TaskFailure, WorkerLock, _check_space, _perform_job, run

VIDEO = "BaW_jenozKc"
PLAYLIST = "PL1234567890abc"


@pytest.fixture
def cfg(tmp_path):
    settings = Settings(root=tmp_path, data_dir=tmp_path / "data", session_secret="test-only", admin_password="test-only", min_free_mb=0, max_upload_mb=1)
    init_db(settings)
    return settings


def add_source(conn):
    return upsert_source(conn, {"video_id": VIDEO, "url": f"https://www.youtube.com/watch?v={VIDEO}", "title": "Été : soirée 🎉", "duration": 1})


def generated_wav(path: Path):
    """One second of test silence, created locally and freely usable."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(8000)
        output.writeframes(b"\x00\x00" * 8000)
    return path


@pytest.mark.parametrize("url", [f"https://youtu.be/{VIDEO}?t=9", f"http://m.youtube.com/watch?v={VIDEO}&feature=share", f"https://www.youtube.com/shorts/{VIDEO}"])
def test_url_variants_are_one_source(url):
    assert parse_youtube_url(url)["url"] == f"https://www.youtube.com/watch?v={VIDEO}"


@pytest.mark.parametrize("url", ["file:///etc/passwd", "http://127.0.0.1/watch?v=BaW_jenozKc", "http://169.254.169.254/latest/meta-data", "https://youtube.com.evil.example/watch?v=BaW_jenozKc", "https://youtube.com@127.0.0.1/watch?v=BaW_jenozKc", "https://www.youtube.com/channel/anything", f"https://www.youtube.com/watch?v={VIDEO}&v=aaaaaaaaaaa", f"https://www.youtube.com/watch?v={VIDEO}&list=RD{VIDEO}", f"https://www.youtube.com:9999/watch?v={VIDEO}", f"https://www.youtube.com/watch?v={VIDEO}\\evil", "$(touch /tmp/test-injection)"])
def test_refuse_arbitrary_network_shell_radio_and_channels(url):
    with pytest.raises(SourceError):
        parse_youtube_url(url)


def test_video_with_playlist_requires_an_explicit_choice(cfg):
    url = f"https://www.youtube.com/watch?v={VIDEO}&list={PLAYLIST}"
    assert parse_youtube_url(url)["kind"] == "choice"
    assert parse_youtube_url(url, "video")["url"] == f"https://www.youtube.com/watch?v={VIDEO}"
    assert parse_youtube_url(url, "playlist")["url"] == f"https://www.youtube.com/playlist?list={PLAYLIST}"
    with pytest.raises(SourceError) as error:
        analyze(url, cfg)
    assert error.value.need_choice and error.value.status == 409


def test_analysis_keeps_duplicates_private_entries_order_and_missing_metadata(cfg):
    info = {"title": "Fête", "playlist_count": 4, "entries": [{"id": VIDEO, "title": "Été", "channel": "Chaîne"}, {"id": VIDEO, "title": "Été"}, {"id": "aaaaaaaaaaa", "title": "[Private video]"}, None]}
    with patch("playlist25.providers._metadata", return_value=(info, "")):
        result = analyze(f"https://youtube.com/playlist?list={PLAYLIST}", cfg)
    assert not result["complete"] and any("non résolus" in warning for warning in result["warnings"])
    assert [e["position"] for e in result["entries"]] == [1, 2, 3, 4]
    assert result["entries"][0]["artist"] == ""  # Channel is not a certified artist.
    assert result["entries"][0]["duration"] is None
    assert result["entries"][0]["video_id"] == result["entries"][1]["video_id"]
    assert not result["entries"][2]["available"] and not result["entries"][3]["available"]


def test_cap_plus_one_does_not_claim_a_complete_playlist(cfg):
    cfg.max_playlist_items = 2
    info = {"title": "Fête", "entries": [{"id": VIDEO}] * 3}
    with patch("playlist25.providers._metadata", return_value=(info, "")) as metadata:
        result = analyze(f"https://youtube.com/playlist?list={PLAYLIST}", cfg)
    assert metadata.call_args.kwargs["limit"] == 3
    assert len(result["entries"]) == 2 and not result["complete"] and result["warnings"]


def test_playlist_keeps_original_positions_and_reports_missing_ranges(cfg):
    info = {"title": "Ordre", "playlist_count": 5, "entries": [{"id": VIDEO, "playlist_index": 1}, {"id": "aaaaaaaaaaa", "playlist_index": 4}]}
    with patch("playlist25.providers._metadata", return_value=(info, "")):
        result = analyze(f"https://youtube.com/playlist?list={PLAYLIST}", cfg)
    assert [entry["position"] for entry in result["entries"]] == [1, 4]
    assert not result["complete"]
    assert any("2 à 3" in warning for warning in result["warnings"])
    assert any("finales non résolues : 5" in warning for warning in result["warnings"])


def test_partial_metadata_nonzero_exit_never_claims_complete(cfg):
    response = Mock(returncode=2, stdout='{"title":"Partielle","entries":[{"id":"BaW_jenozKc","playlist_index":1}]}', stderr="warning without error keywords")
    with patch("playlist25.providers.subprocess.run", return_value=response):
        result = analyze(f"https://youtube.com/playlist?list={PLAYLIST}", cfg)
    assert result["entries"][0]["position"] == 1
    assert not result["complete"] and any("code 2" in warning for warning in result["warnings"])


@pytest.mark.parametrize("indices", [[1, 1], [4, 1], [1, True], [1, 0], [1, "invalide"]])
def test_inconsistent_playlist_positions_are_not_silently_renumbered(cfg, indices):
    info = {"entries": [{"id": VIDEO, "playlist_index": index} for index in indices]}
    with patch("playlist25.providers._metadata", return_value=(info, "")):
        with pytest.raises(SourceError, match="position") as error:
            analyze(f"https://youtube.com/playlist?list={PLAYLIST}", cfg)
    assert error.value.status == 502


def test_mixed_known_and_unknown_playlist_positions_remain_honest(cfg):
    info = {"entries": [{"id": VIDEO, "playlist_index": 4}, {"id": "aaaaaaaaaaa"}]}
    with patch("playlist25.providers._metadata", return_value=(info, "")):
        result = analyze(f"https://youtube.com/playlist?list={PLAYLIST}", cfg)
    assert [entry["position"] for entry in result["entries"]] == [4, 5]
    assert not result["complete"] and any("déduits" in warning for warning in result["warnings"])


def test_live_and_overlong_search_results_are_not_available(cfg):
    info = {"entries": [{"id": VIDEO, "title": "Direct", "is_live": True}, {"id": "aaaaaaaaaaa", "title": "Long", "duration": cfg.max_duration + 1}, {"id": "bbbbbbbbbbb", "title": "Studio", "channel": "Auteur"}]}
    with patch("playlist25.providers._metadata", return_value=(info, "")):
        results = search("Titre artiste", cfg)
    assert [result["title"] for result in results] == ["Studio"]


def test_links_keep_failures_without_fetching_arbitrary_urls(cfg):
    good = {"kind": "video", "title": "Morceau", "source_url": f"https://youtube.com/watch?v={VIDEO}", "complete": True, "warnings": [], "entries": [{"position": 1, "video_id": VIDEO, "available": True}]}
    with patch("playlist25.providers.analyze", return_value=good) as analyze_one:
        result = analyze_links(f"https://youtu.be/{VIDEO}\nhttp://127.0.0.1/private", cfg)
    assert analyze_one.call_count == 1
    assert result["entries"][1]["position"] == 2 and not result["entries"][1]["available"]
    assert not result["complete"]


def test_commands_use_bundled_js_components_and_no_shell(cfg):
    command = yt_dlp_command(cfg)
    assert command[1:3] == ["-m", "yt_dlp"]
    assert "--no-remote-components" in command and "--no-plugin-dirs" in command
    assert command[command.index("--js-runtimes") + 1] == "node"
    response = Mock(returncode=0, stdout='{"id":"BaW_jenozKc","title":"Test"}', stderr="")
    with patch("playlist25.providers.subprocess.run", return_value=response) as subprocess_run:
        analyze(f"https://youtu.be/{VIDEO}", cfg)
    assert subprocess_run.call_args.kwargs.get("shell", False) is False
    assert subprocess_run.call_args.args[0][-1] == f"https://www.youtube.com/watch?v={VIDEO}"


def test_provider_timeout_has_an_honest_error(cfg):
    with patch("playlist25.providers.subprocess.run", side_effect=subprocess.TimeoutExpired("yt-dlp", 120)):
        with pytest.raises(SourceError, match="délai") as error:
            analyze(f"https://youtu.be/{VIDEO}", cfg)
    assert error.value.status == 503


def test_concurrent_enqueue_and_claim_only_create_one_active_job(cfg):
    with connect(cfg) as conn:
        source_id = add_source(conn)
    def enqueue(_):
        with connect(cfg) as conn:
            return enqueue_sources(conn, cfg, [source_id])
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(enqueue, range(4)))
    assert sum(len(r["queued"]) for r in results) == 1
    def claim(_):
        with connect(cfg) as conn:
            return claim_job(conn)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(claim, range(4)))
    assert sum(r is not None for r in results) == 1


def test_worker_lock_prevents_duplicate_workers(cfg):
    path = cfg.temp_dir / "test-worker.lock"
    with WorkerLock(path):
        with pytest.raises(RuntimeError, match="déjà actif"):
            with WorkerLock(path):
                pass
    with WorkerLock(path):
        pass


def test_pause_and_offline_do_not_claim_a_job(cfg):
    with connect(cfg) as conn:
        enqueue_sources(conn, cfg, [add_source(conn)])
        conn.commit()
        for key in ("queue_paused", "offline_mode"):
            set_setting(conn, key, True)
            conn.commit()
            assert claim_job(conn) is None
            set_setting(conn, key, False)
            conn.commit()
        assert claim_job(conn) is not None


def test_interrupted_jobs_resume_with_bounded_attempts(cfg):
    with connect(cfg) as conn:
        enqueue_sources(conn, cfg, [add_source(conn)])
        conn.commit()
        job = claim_job(conn)
        assert recover_jobs(conn, cfg)["interrupted"] == [job["source_id"]]
        assert queue_snapshot(conn)[0]["status"] == "queued"
        claim_job(conn)
        recover_jobs(conn, cfg)
        assert queue_snapshot(conn)[0]["status"] == "failed"
        result = enqueue_sources(conn, cfg, [job["source_id"]], retry=True)
        assert result["queued"] == [job["source_id"]]
        assert queue_snapshot(conn)[0]["attempts"] == 0


@pytest.mark.skipif(shutil.which("ffprobe") is None, reason="ffprobe required for real audio verification")
def test_available_file_is_verified_and_not_downloaded_again(cfg):
    with connect(cfg) as conn:
        source_id = add_source(conn)
        file = generated_wav(cfg.audio_dir / "authorized-test.wav")
        conn.execute("UPDATE sources SET file_path=?,status='available' WHERE id=?", (file.name, source_id))
        result = enqueue_sources(conn, cfg, [source_id])
        assert not result["queued"] and "vérifié" in result["skipped"][0]["reason"]
        assert conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0
        file.unlink()
        recover_jobs(conn, cfg)
        assert conn.execute("SELECT status FROM sources WHERE id=?", (source_id,)).fetchone()[0] == "interrupted"


@pytest.mark.skipif(shutil.which("ffprobe") is None, reason="ffprobe required for real audio verification")
def test_real_ffprobe_before_publication_and_restart_recovery(cfg):
    with connect(cfg) as conn:
        source_id = add_source(conn)
        enqueue_sources(conn, cfg, [source_id])
        conn.commit()
        job = claim_job(conn)
        wav = generated_wav(cfg.temp_dir / f"job-{job['id']}" / "track.wav")
        with patch("playlist25.worker._download", return_value=(wav, {"duration": 1, "codec": "pcm_s16le"})):
            _perform_job(conn, cfg, job, threading.Event())
        source = conn.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
        assert source["status"] == "available" and (cfg.audio_dir / source["file_path"]).is_file()
        # Simulate crash after installed file but before available state commit.
        conn.execute("UPDATE jobs SET status='verifying' WHERE id=?", (job["id"],))
        conn.execute("UPDATE sources SET status='verifying' WHERE id=?", (source_id,))
        conn.commit()
        recover_jobs(conn, cfg)
        assert queue_snapshot(conn)[0]["status"] == "available"
        assert not enqueue_sources(conn, cfg, [source_id])["queued"]


@pytest.mark.skipif(shutil.which("ffprobe") is None, reason="ffprobe required for real audio verification")
def test_metadata_corrected_during_download_is_used_in_filename_and_tags(cfg):
    from mutagen import File
    with connect(cfg) as conn:
        source_id = add_source(conn)
        enqueue_sources(conn, cfg, [source_id])
        conn.commit()
        job = claim_job(conn)
        wav = generated_wav(cfg.temp_dir / f"job-{job['id']}" / "track.wav")
        def corrected_download(*args):
            conn.execute("UPDATE sources SET title=?,artist=? WHERE id=?", ("Titre corrigé", "Artiste connu", source_id))
            conn.commit()
            return wav, {"duration": 1, "codec": "pcm_s16le"}
        with patch("playlist25.worker._download", side_effect=corrected_download):
            _perform_job(conn, cfg, job, threading.Event())
        source = conn.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
        assert source["status"] == "available" and "Titre corrigé" in source["file_path"]
        audio = File(cfg.audio_dir / source["file_path"])
        assert audio.tags["TIT2"].text == ["Titre corrigé"]
        assert audio.tags["TPE1"].text == ["Artiste connu"]


@pytest.mark.parametrize("failure,expected", [(TaskFailure("Erreur de conversion FFmpeg"), "failed"), (TaskFailure("Vidéo privée", unavailable=True), "unavailable"), (TaskFailure("Délai dépassé", transient=True), "queued"), (TaskFailure("Espace disque insuffisant"), "failed")])
def test_conversion_unavailable_network_and_disk_failures_are_independent(cfg, failure, expected):
    with connect(cfg) as conn:
        source_id = add_source(conn)
        enqueue_sources(conn, cfg, [source_id])
        conn.commit()
        job = claim_job(conn)
        with patch("playlist25.worker._download", side_effect=failure):
            _perform_job(conn, cfg, job, threading.Event())
        snapshot = queue_snapshot(conn)[0]
        assert snapshot["status"] == expected and snapshot["error"]
        assert snapshot["events"][-1]["message"]
        assert conn.execute("SELECT file_path FROM sources WHERE id=?", (source_id,)).fetchone()[0] is None


def test_browser_is_irrelevant_to_the_separate_worker(cfg):
    with connect(cfg) as conn:
        enqueue_sources(conn, cfg, [add_source(conn)])
    with patch("playlist25.worker._perform_job") as perform:
        assert run(cfg, once=True) == 0
    assert perform.call_count == 1


@pytest.mark.skipif(shutil.which("ffprobe") is None, reason="ffprobe required for real audio verification")
def test_subprocess_progress_conversion_verification_and_completion(cfg, tmp_path):
    simulator = tmp_path / "simulate_download.py"
    simulator.write_text('''import sys, json, wave
from pathlib import Path
output = Path(sys.argv[sys.argv.index("--output") + 1].replace("%(ext)s", "wav"))
print('__PLAYLIST25_INFO__' + json.dumps({"id": "BaW_jenozKc", "duration": 1}), flush=True)
print('__PLAYLIST25_PROGRESS__' + json.dumps({"downloaded": 8000, "total": 16000, "status": "downloading"}), flush=True)
with wave.open(str(output), "wb") as audio:
    audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(8000); audio.writeframes(b"\\x00\\x00" * 8000)
print('__PLAYLIST25_PROGRESS__' + json.dumps({"downloaded": 16000, "total": 16000, "status": "finished"}), flush=True)
print('__PLAYLIST25_POST__started', flush=True)
print('__PLAYLIST25_FILE__' + json.dumps(str(output)), flush=True)
''', encoding="utf-8")
    with connect(cfg) as conn:
        source_id = add_source(conn)
        enqueue_sources(conn, cfg, [source_id])
    with patch("playlist25.worker.yt_dlp_command", return_value=[sys.executable, str(simulator)]):
        run(cfg, once=True)
    with connect(cfg) as conn:
        job = queue_snapshot(conn)[0]
        assert job["status"] == "available" and job["progress"] == 100
        assert [event["status"] for event in job["events"]] == ["queued", "downloading", "converting", "verifying", "available"]
        source = conn.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
        assert source["file_path"] and (cfg.audio_dir / source["file_path"]).is_file()


def test_real_subprocess_timeout_is_killed_and_bounded(cfg, tmp_path):
    cfg.task_timeout = 1
    cfg.max_attempts = 1
    simulator = tmp_path / "simulate_slow_download.py"
    simulator.write_text("import time\ntime.sleep(10)\n", encoding="utf-8")
    with connect(cfg) as conn:
        enqueue_sources(conn, cfg, [add_source(conn)])
    with patch("playlist25.worker.yt_dlp_command", return_value=[sys.executable, str(simulator)]):
        run(cfg, once=True)
    with connect(cfg) as conn:
        job = queue_snapshot(conn)[0]
        assert job["status"] == "failed" and "délai" in job["error"]


def test_insufficient_space_is_detected_before_launch(cfg):
    staging = cfg.temp_dir / "job-1"
    staging.mkdir()
    with patch("playlist25.worker.shutil.disk_usage", return_value=Mock(free=0)):
        with pytest.raises(TaskFailure, match="disque insuffisant"):
            _check_space(cfg, staging, initial=True)
