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

from .config import ROOT, initialize_local_config, load_settings
from .db import init_db


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
        if os.name == 'nt': process.send_signal(signal.CTRL_BREAK_EVENT)
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
    (cfg.data_dir / ".stop-requested").touch()
    records=json.loads(pid_file.read_text(encoding="utf-8"))
    for item in records:
        try:
            process=psutil.Process(item["pid"])
            arguments=process.cmdline()
            if "playlist25" in arguments and abs(process.create_time()-item["started"])<2:
                _terminate(process)
        except (psutil.NoSuchProcess,psutil.AccessDenied): pass
    pid_file.unlink(missing_ok=True)
    print("Services arrêtés ; données et fichiers audio conservés.")


def start(cfg):
    import psutil
    pid_file=cfg.data_dir / "processes.json"
    if pid_file.exists():
        for record in json.loads(pid_file.read_text(encoding="utf-8")):
            try:
                p=psutil.Process(record["pid"])
                if "playlist25" in p.cmdline() and abs(p.create_time()-record["started"])<2:
                    raise RuntimeError("Un service est déjà lancé. Utilisez la commande stop avant de relancer.")
            except psutil.NoSuchProcess: pass
    (cfg.data_dir / ".stop-requested").unlink(missing_ok=True)
    children=[]
    try:
        records=[]
        for command in (("serve","guest"),("serve","admin"),("worker",)):
            options={"cwd":str(ROOT)}
            if os.name=="nt": options["creationflags"]=subprocess.CREATE_NEW_PROCESS_GROUP
            process=subprocess.Popen([sys.executable,"-m","playlist25",*command],**options)
            children.append(process)
            records.append({"pid":process.pid,"started":psutil.Process(process.pid).create_time(),"service":" ".join(command)})
        pid_file.write_text(json.dumps(records),encoding="utf-8")
        print(f"Invités : http://127.0.0.1:{cfg.guest_port} | Administration privée : http://127.0.0.1:{cfg.admin_port}",flush=True)
        print("Mot de passe dans .env.local, modifiable avec configure-admin. Ctrl+C arrête les services.",flush=True)
        while True:
            for child in children:
                if child.poll() is not None:
                    if (cfg.data_dir / ".stop-requested").exists(): return
                    raise RuntimeError(f"Un service s’est arrêté (code {child.returncode}). Consultez son message ci-dessus.")
            time.sleep(0.5)
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
