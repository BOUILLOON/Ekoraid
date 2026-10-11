from __future__ import annotations
import argparse
import getpass
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from .config import ROOT, SOURCE_ROOT, initialize_local_config, load_settings
from .db import init_db


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
            if os.name=="nt": options["creationflags"]=subprocess.CREATE_NEW_PROCESS_GROUP
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


def main():
    parser=argparse.ArgumentParser(description="La playlist des 25 ans")
    parser.add_argument("command",choices=["init","configure-admin","start","stop","serve","worker","doctor"])
    parser.add_argument("surface",nargs="?",choices=["guest","admin"])
    parser.add_argument("--once",action="store_true",help="Worker : traiter au plus une tâche")
    args=parser.parse_args()
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
