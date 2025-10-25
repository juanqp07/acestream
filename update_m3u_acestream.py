#!/usr/bin/env python3
"""
update_m3u_acestream.py

Descarga (opcional) un M3U desde una URL pública o procesa un archivo local,
reemplaza enlaces tipo:
  acestream://2773b39926d15dd3d9495d94c4050604792d7031
por:
  http://<host>:<port>/2773b39926d15dd3d9495d94c4050604792d7031

Uso en Actions (ejemplo):
  python3 update_m3u_acestream.py --url "https://raw.githubusercontent.com/Icastresana/lista1/refs/heads/main/eventos.m3u" --output playlist.m3u

Opciones:
  --url        URL pública del M3U (opcional). Si se pasa, se descarga.
  --output     Archivo de salida local (por defecto playlist.m3u)
  --host       Host para servir los hashes (por defecto 127.0.0.1)
  --port       Puerto (por defecto 6878)
  --no-backup  No crear copia de seguridad
  --no-commit  No realizar git add/commit/push
"""
import re
import argparse
from pathlib import Path
import shutil
import datetime
import subprocess
import sys
import urllib.request

PATTERN = re.compile(r"acestream://([0-9A-Fa-f]{40})")


def now_ts():
    return datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")


def replace_acestream(text: str, host: str, port: int) -> str:
    # Usamos una función para evitar problemas con backreferences en literales
    def repl(m):
        return f"http://{host}:{port}/{m.group(1)}"
    return PATTERN.sub(repl, text)


def backup_file(path: Path) -> Path:
    ts = now_ts()
    dest = path.with_name(path.name + f".bak.{ts}")
    shutil.copy2(path, dest)
    return dest


def download_url(url: str) -> str:
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            charset = r.headers.get_content_charset() or "utf-8"
            return r.read().decode(charset, errors="replace")
    except Exception as e:
        raise RuntimeError(f"Error descargando URL {url}: {e}")


def git_commit_and_push(path: Path, message: str) -> None:
    # Config user (en Actions puede no estar configurado)
    subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=False)
    subprocess.run(
        ["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"],
        check=False,
    )

    subprocess.run(["git", "add", str(path)], check=True)
    try:
        subprocess.run(["git", "diff", "--cached", "--exit-code"], check=True)
        print("No hay cambios para commitear.")
        return
    except subprocess.CalledProcessError:
        pass

    subprocess.run(["git", "commit", "-m", message], check=True)
    subprocess.run(["git", "push"], check=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--url", help="URL pública del archivo M3U (opcional)")
    p.add_argument("--output", "-o", default="playlist.m3u", help="Ruta de salida del M3U")
    p.add_argument("--host", default="127.0.0.1", help="Host para servir los hashes")
    p.add_argument("--port", type=int, default=6878, help="Puerto para servir los hashes")
    p.add_argument("--no-backup", dest="backup", action="store_false", help="No crear backup")
    p.add_argument("--no-commit", dest="commit", action="store_false", help="No hacer git commit/push")
    args = p.parse_args()

    out_path = Path(args.output)

    if args.url:
        print(f"Descargando {args.url} ...")
        try:
            text = download_url(args.url)
        except Exception as e:
            print(e)
            sys.exit(2)
    else:
        if not out_path.exists():
            print(f"Error: archivo no encontrado: {out_path}")
            sys.exit(2)
        text = out_path.read_text(encoding="utf-8")

    new_text = replace_acestream(text, args.host, args.port)

    if new_text == text:
        print("No se encontraron enlaces acestream que reemplazar.")
        return

    if args.backup and out_path.exists():
        b = backup_file(out_path)
        print(f"Backup creado: {b}")

    out_path.write_text(new_text, encoding="utf-8")
    print(f"Archivo actualizado: {out_path}")

    if args.commit:
        msg = f"Actualiza enlaces acestream -> http proxy ({now_ts()})"
        try:
            git_commit_and_push(out_path, msg)
            print("Cambios commiteados y pusheados.")
        except subprocess.CalledProcessError as e:
            print("Error al hacer commit/push:", e)
            sys.exit(3)


if __name__ == "__main__":
    main()
