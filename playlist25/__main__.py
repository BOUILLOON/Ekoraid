from __future__ import annotations
import argparse
import getpass
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

from .config import ROOT, SOURCE_ROOT, initialize_local_config, load_settings
from .db import init_db
from .processes import background_process_options


def service_command(*arguments):
    """Run a private service through Python or the executable's dispatch entry."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--service", *arguments]
    return [sys.executable, "-m", "playlist25", *arguments]


def _same_path(first, second):
    return os.path.normcase(str(Path(first).resolve())) == os.path.normcase(str(Path(second).resolve()))


def _matches_service(process, record, cfg):
    """A recycled PID or an unrelated executable must never receive a signal."""
    if not isinstance(record.get("service"), str):
        return False
    service = record["service"].split()
    if service not in (["serve", "guest"], ["serve", "admin"], ["worker"]):
        return False
    try:
        if abs(process.create_time() - float(record["started"])) >= 0.01:
            return False
        if not _same_path(process.exe(), sys.executable):
            return False
        if record.get("executable") and not _same_path(record["executable"], sys.executable):
            return False
        if record.get("home") and not _same_path(record["home"], cfg.root):
            return False
        if record.get("data_dir") and not _same_path(record["data_dir"], cfg.data_dir):
            return False
        return process.cmdline()[1:] == service_command(*service)[1:]
    except (KeyError, TypeError, ValueError, OSError):
        return False


def _process_records(path):
    try:
        records = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(records, list) or any(not isinstance(item, dict) or not isinstance(item.get("pid"), int)
                                              or isinstance(item.get("pid"), bool) or item["pid"] <= 0 for item in records):
            raise ValueError("invalid process records")
        return records
    except (ValueError, OSError) as exc:
        raise RuntimeError("Le registre des processus est invalide. Vérifiez processes.json avant de relancer.") from exc


def doctor(cfg):
    import importlib.metadata
    import shutil
    result = {"python":sys.version.split()[0],"data_dir":str(cfg.data_dir),"ffmpeg":bool(shutil.which(cfg.ffmpeg_path)),"ffprobe":bool(shutil.which(cfg.ffprobe_path)),"javascript":bool(shutil.which(cfg.js_runtime)),"free_mb":shutil.disk_usage(cfg.data_dir).free//1024**2}
    for package in ("yt-dlp","yt-dlp-ejs","fastapi"):
        try: result[package]=importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError: result[package]=None
    probe=cfg.temp_dir / ".write-check"
    try:
        probe.write_text("ok",encoding="utf-8")
        probe.unlink()
        result["writeable"]=True
    except OSError: result["writeable"]=False
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if all(result.get(k) for k in ("ffmpeg","ffprobe","javascript","writeable","yt-dlp","yt-dlp-ejs")) else 1


def _terminate(process):
    import psutil
    try:
        if os.name == 'nt':
            try:
                process.send_signal(signal.CTRL_BREAK_EVENT)
            except OSError:
                # A GUI executable may have no console to receive CTRL_BREAK.
                # End its own children too, so no yt-dlp subprocess is orphaned.
                parent=psutil.Process(process.pid)
                for child in parent.children(recursive=True):
                    try: child.terminate()
                    except psutil.NoSuchProcess: pass
                process.terminate()
        else: process.terminate()
        try: process.wait(timeout=10)
        except (subprocess.TimeoutExpired,psutil.TimeoutExpired):
            parent=psutil.Process(process.pid)
            for child in parent.children(recursive=True):
                try: child.kill()
                except psutil.NoSuchProcess: pass
            process.kill()
            process.wait(timeout=5)
    except (ProcessLookupError,psutil.NoSuchProcess): pass


def stop(cfg):
    import psutil
    pid_file=cfg.data_dir / "processes.json"
    if not pid_file.exists():
        print("Aucun lancement enregistré.")
        return
    records=_process_records(pid_file)
    (cfg.data_dir / ".stop-requested").touch()
    services=[]
    for item in records:
        try:
            process=psutil.Process(item["pid"])
            if _matches_service(process,item,cfg):
                services.append(process)
        except (psutil.NoSuchProcess,psutil.AccessDenied): pass
    # Let the supervisor notice the marker before falling back to PID cleanup.
    deadline=time.monotonic()+2
    while services and time.monotonic()<deadline:
        remaining=[]
        for process in services:
            try:
                if process.is_running() and process.status()!=psutil.STATUS_ZOMBIE: remaining.append(process)
            except psutil.NoSuchProcess: pass
            except psutil.AccessDenied: remaining.append(process)
        services=remaining
        if services: time.sleep(0.15)
    for process in services:
        _terminate(process)
    pid_file.unlink(missing_ok=True)
    print("Services arrêtés ; données et fichiers audio conservés.")


def start(cfg):
    import psutil
    pid_file=cfg.data_dir / "processes.json"
    if pid_file.exists():
        for record in _process_records(pid_file):
            try:
                p=psutil.Process(record["pid"])
                if _matches_service(p,record,cfg):
                    raise RuntimeError("Un service est déjà lancé. Utilisez la commande stop avant de relancer.")
            except psutil.NoSuchProcess: pass
            except psutil.AccessDenied as exc:
                raise RuntimeError("Impossible de vérifier un service déjà enregistré. Arrêtez son lancement avant de relancer.") from exc
    (cfg.data_dir / ".stop-requested").unlink(missing_ok=True)
    children=[]
    try:
        records=[]
        for command in (("serve","guest"),("serve","admin"),("worker",)):
            environment=os.environ.copy()
            environment.update(PLAYLIST25_HOME=str(cfg.root),DATA_DIR=str(cfg.data_dir))
            options={"cwd":str(cfg.root if getattr(sys,"frozen",False) else SOURCE_ROOT),"env":environment}
            if os.name=="nt":
                # Windowed bootloaders do not restore child standard handles
                # automatically. The dispatch entry has reopened these streams.
                options.update(background_process_options(new_process_group=True, windows=True))
                options.update(stdout=sys.stdout,stderr=sys.stderr,stdin=subprocess.DEVNULL)
            process=subprocess.Popen(service_command(*command),**options)
            children.append(process)
            records.append({"pid":process.pid,"started":psutil.Process(process.pid).create_time(),"service":" ".join(command),
                            "executable":str(Path(sys.executable).resolve()),"home":str(cfg.root),"data_dir":str(cfg.data_dir)})
        pid_file.write_text(json.dumps(records),encoding="utf-8")
        print(f"Invités : http://127.0.0.1:{cfg.guest_port} | Administration privée : http://127.0.0.1:{cfg.admin_port}",flush=True)
        print("Mot de passe dans .env.local, modifiable avec configure-admin. Ctrl+C arrête les services.",flush=True)
        while True:
            if (cfg.data_dir / ".stop-requested").exists(): return
            for child in children:
                if child.poll() is not None:
                    if (cfg.data_dir / ".stop-requested").exists(): return
                    raise RuntimeError(f"Un service s’est arrêté (code {child.returncode}). Consultez son message ci-dessus.")
            time.sleep(0.15)
    except KeyboardInterrupt:
        pass
    finally:
        for child in children:
            if child.poll() is None: _terminate(child)
        pid_file.unlink(missing_ok=True)
        (cfg.data_dir / ".stop-requested").unlink(missing_ok=True)


def _backup_summary(command, result):
    """Only the documented inventory, never configuration or private keys."""
    fields = ("archive", "created_at", "include_audio", "confidential", "file_count",
              "uncompressed_bytes", "archive_bytes", "manifest_sha256", "home")
    summary = {"command": command}
    for field in fields:
        if field in result:
            value = result[field]
            summary[field] = str(value) if isinstance(value, Path) else value
    counts = result.get("counts", {})
    summary["counts"] = {name: counts[name] for name in
                         ("sources", "proposals", "guests", "votes", "photos", "audio_files") if name in counts}
    if "missing_audio" in result:
        value = result["missing_audio"]
        summary["missing_audio"] = len(value) if isinstance(value, (list, tuple)) else value
    return summary


def _backup_command(args, parser):
    # These operations must not initialize, load or migrate the selected current
    # studio. They read only the explicitly supplied source/archive and target.
    if args.surface:
        parser.error("Ces commandes ne prennent pas de surface guest/admin.")
    if args.command == "backup":
        if args.home is None or args.destination is None:
            parser.error("backup exige --home et --destination.")
        from .backups import create_backup
        result = create_backup(args.home, args.destination, include_audio=not args.without_audio)
    elif args.command == "inspect-backup":
        if args.archive is None:
            parser.error("inspect-backup exige --archive.")
        from .backups import inspect_backup
        result = inspect_backup(args.archive)
    else:
        if args.archive is None or args.home is None:
            parser.error("restore-backup exige --archive et --home vers un nouveau dossier vide.")
        expected = args.expected_manifest_sha256
        if expected is not None and not re.fullmatch(r"[a-f0-9]{64}", expected):
            parser.error("L'empreinte de manifeste doit contenir 64 caractères SHA256 hexadécimaux.")
        from .backups import restore_backup
        result = restore_backup(args.archive, args.home, expected_manifest_sha256=expected)
    print(json.dumps(_backup_summary(args.command, result), ensure_ascii=False, indent=2))
    return 0


def main():
    parser=argparse.ArgumentParser(description="La playlist des 25 ans")
    parser.add_argument("command",choices=["init","configure-admin","start","stop","serve","worker","doctor",
                                          "backup","inspect-backup","restore-backup"])
    parser.add_argument("surface",nargs="?",choices=["guest","admin"])
    parser.add_argument("--once",action="store_true",help="Worker : traiter au plus une tâche")
    parser.add_argument("--home",type=Path,help="Sauvegarde : studio source ; restauration : nouveau dossier vide")
    parser.add_argument("--destination",type=Path,help="Nouvelle archive ZIP de sauvegarde, hors du studio")
    parser.add_argument("--archive",type=Path,help="Archive ZIP de sauvegarde à inspecter ou restaurer")
    parser.add_argument("--without-audio",action="store_true",help="Sauvegarde : omettre les fichiers audio (inclus par défaut)")
    parser.add_argument("--expected-manifest-sha256",help="Restauration : empreinte obtenue lors de l'inspection")
    args=parser.parse_args()
    if args.command in {"backup", "inspect-backup", "restore-backup"}:
        return _backup_command(args, parser)
    if args.command in {"init","configure-admin","start"}:
        initialize_local_config()
    if args.command=="configure-admin":
        from dotenv import set_key
        password=getpass.getpass("Nouveau mot de passe administrateur (12 caractères minimum) : ")
        if len(password)<12: parser.error("Choisissez au moins 12 caractères.")
        if getpass.getpass("Confirmation : ")!=password: parser.error("Les mots de passe sont différents.")
        set_key(ROOT / ".env.local","ADMIN_PASSWORD",password)
        print("Mot de passe enregistré dans .env.local. Redémarrez les services pour l’utiliser.")
        return 0
    cfg=load_settings()
    init_db(cfg)
    if args.command=="init":
        print("Base créée ; identifiants privés dans .env.local. Lancez configure-admin pour choisir votre mot de passe.")
    elif args.command=="doctor": return doctor(cfg)
    elif args.command=="start": start(cfg)
    elif args.command=="stop": stop(cfg)
    elif args.command=="worker":
        from .worker import run
        run(cfg,once=args.once)
    elif args.command=="serve":
        if not args.surface: parser.error("Indiquez serve guest ou serve admin.")
        import uvicorn
        from .app import create_app
        uvicorn.run(create_app(args.surface,cfg),host=cfg.guest_host if args.surface=="guest" else cfg.admin_host,port=cfg.guest_port if args.surface=="guest" else cfg.admin_port,access_log=False)
    return 0

if __name__=="__main__":
    try: raise SystemExit(main())
    except (ValueError,RuntimeError) as error:
        print(f"Configuration : {error}",file=sys.stderr)
        raise SystemExit(1)
