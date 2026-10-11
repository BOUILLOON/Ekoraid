"""Build a guest-only static Pages site; never copies local data/admin/secrets."""
from __future__ import annotations

import argparse
import base64
import json
import re
import shutil
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
PUBLIC_ASSETS = ("styles.css", "guest.js", "cloud.js", "placeholder.svg")


def check_public_key(value: str):
    if value.startswith("sb_publishable_") and len(value) >= 25 and re.fullmatch(r"[A-Za-z0-9_-]+", value):
        return value
    try:
        encoded = value.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        if claims.get("role") == "anon" and len(value.split(".")) == 3:
            return value
    except (ValueError, IndexError, UnicodeError):
        pass
    raise ValueError("Utilisez uniquement la clé publishable ou anon. Une clé service_role/secret ne doit jamais être publiée.")


def build(url: str, public_key: str, output: Path):
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or not re.fullmatch(r"[a-z0-9-]+\.supabase\.co", parsed.hostname or "")
            or parsed.username or parsed.password or parsed.port or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
        raise ValueError("L’URL doit être https://REFERENCE.supabase.co, sans chemin ni paramètres.")
    public_key = check_public_key(public_key)
    output = output.resolve()
    if (output == ROOT or output in ROOT.parents or output in {ROOT / "playlist25", ROOT / "data"}
            or ROOT / "playlist25" in output.parents or ROOT / "data" in output.parents):
        raise ValueError("Choisissez un dossier de sortie distinct du projet et des données locales.")
    marker = output / ".playlist25-generated"
    if output.exists() and any(output.iterdir()) and not marker.is_file():
        raise ValueError("Le dossier contient déjà des fichiers. Choisissez un dossier vide pour préserver vos fichiers.")
    permitted = {"index.html", ".nojekyll", ".playlist25-generated", "static/cloud-config.js", *("static/" + name for name in PUBLIC_ASSETS)}
    if output.exists():
        for path in output.rglob("*"):
            if path.is_symlink() or (path.is_file() and path.relative_to(output).as_posix() not in permitted):
                raise ValueError("Le dossier contient un fichier étranger au site invités. Choisissez un nouveau dossier vide.")
    static = ROOT / "playlist25" / "static"
    for name in ("guest.html", *PUBLIC_ASSETS):
        if not (static / name).is_file() or (static / name).is_symlink():
            raise ValueError(f"Ressource locale manquante/non autorisée : {name}")
    (output / "static").mkdir(parents=True, exist_ok=True)
    page = (static / "guest.html").read_text(encoding="utf-8")
    policy = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src " + url.rstrip("/") + "; object-src 'none'; base-uri 'none'; form-action 'none'"
    page = page.replace("<head>", '<head>\n  <meta http-equiv="Content-Security-Policy" content="' + policy + '">', 1)
    (output / "index.html").write_text(page, encoding="utf-8")
    for name in PUBLIC_ASSETS:
        shutil.copyfile(static / name, output / "static" / name)
    (output / "static" / "cloud-config.js").write_text(
        '"use strict";\nwindow.PLAYLIST_CLOUD = Object.freeze(' + json.dumps(
            {"url": url.rstrip("/"), "publicKey": public_key}, ensure_ascii=True) + ');\n', encoding="utf-8")
    (output / ".nojekyll").touch()
    marker.write_text("Playlist25 guest-only build v1\n", encoding="utf-8")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "cloud" / "public-config.json", help="JSON contenant url/publicKey (publics)")
    parser.add_argument("--url", help="Remplace l’URL publique du fichier de configuration")
    parser.add_argument("--public-key", help="Remplace la clé publique publishable/anon du fichier, jamais secret/service_role")
    parser.add_argument("--output", type=Path, default=ROOT / "_site")
    args = parser.parse_args()
    try:
        saved = json.loads(args.config.read_text(encoding="utf-8")) if args.config.is_file() else {}
        if not isinstance(saved, dict):
            raise ValueError("Le fichier de configuration publique doit être un objet JSON.")
        url, public_key = args.url or saved.get("url"), args.public_key or saved.get("publicKey")
        if not url or not public_key:
            raise ValueError("Ajoutez url/publicKey dans cloud/public-config.json ou passez --url et --public-key.")
        result = build(url, public_key, args.output)
    except (ValueError, OSError) as error:
        parser.exit(1, str(error) + "\n")
    print(f"Site invités préparé dans {result}. Aucune publication effectuée.")


if __name__ == "__main__":
    main()
