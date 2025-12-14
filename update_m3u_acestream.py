#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
update_m3u_acestream.py
Convierte enlaces:
  - acestream://<HASH>
  - http[s]://<host>:<port>/<HASH>
  - http[s]://<host>:<port>/ace/getstream?id=<HASH>
hacia:
  http://<HOST_ARG>:<PORT_ARG>/ace/getstream?id=<HASH>
"""
from __future__ import annotations
import argparse
import logging
import re
import sys
from pathlib import Path
import urllib.request
import urllib.error
import datetime
import shutil
import subprocess

HEX40 = r"[0-9A-Fa-f]{40}"

# 1) acestream://HASH
PAT_ACESTREAM = re.compile(r"acestream://(" + HEX40 + r")", re.IGNORECASE)

# 2) host:port/... where ... can be HASH or ace/getstream?id=HASH (captures the HASH)
PAT_HOST_PORT_HASH = re.compile(
    r"(?:https?://)?(?P<host>[^/\s:]+):(?P<port>\d{1,5})/(?:(?:ace/getstream\?id=)?(?P<h>" + HEX40 + r"))",
    re.IGNORECASE,
)

# (Optional) bare query id=HASH anywhere (to catch some odd forms)
PAT_QUERY_ID = re.compile(r"([?&]id=)(" + HEX40 + r")", re.IGNORECASE)


def now_ts() -> str:
    return datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")


def download_url(url: str, timeout: int = 30) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/x-mpegURL,application/vnd.apple.mpegurl,text/plain,*/*",
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            # try to decode with charset if present
            ct = r.headers.get("Content-Type", "") or ""
            if "charset=" in ct:
                charset = ct.split("charset=")[-1].split(";")[0].strip()
            else:
                charset = "utf-8"
            return raw.decode(charset, errors="replace")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTPError {e.code} downloading {url}: {e.reason}")
    except Exception as e:
        raise RuntimeError(f"Error downloading {url}: {e}")


def replace_links(text: str, target_host: str, target_port: int) -> tuple[str, int]:
    """
    Reemplaza las variantes encontradas y devuelve (nuevo_texto, numero_de_reemplazos)
    """
    total = 0

    def repl_acestream(m):
        nonlocal total
        h = m.group(1)
        total += 1
        return f"http://{target_host}:{target_port}/ace/getstream?id={h}"

    text = PAT_ACESTREAM.sub(repl_acestream, text)

    def repl_hostport(m):
        nonlocal total
        h = m.group("h")
        if not h:
            return m.group(0)
        total += 1
        return f"http://{target_host}:{target_port}/ace/getstream?id={h}"

    text = PAT_HOST_PORT_HASH.sub(repl_hostport, text)

    # Catch occurrences like "?id=HASH" or "&id=HASH" that might remain (counts as one replacement)
    def repl_query(m):
        nonlocal total
        h = m.group(2)
        total += 1
        # preserve leading ? or &
        prefix = m.group(1)
        # replace whole URL piece with the standard form (we return only the query part; caller's URL likely fixes)
        return f"{prefix}{h}"  # kept as-is but counted; usually handled by previous rules

    text = PAT_QUERY_ID.sub(repl_query, text)

    return text, total


def backup_file(path: Path):
    stamp = now_ts()
    dest = path.with_name(path.name + f".bak.{stamp}")
    shutil.copy2(path, dest)
    return dest


def git_commit_and_push(paths, message):
    # Uses repo's configured credentials (GITHUB_TOKEN via checkout persist-credentials)
    try:
        subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=False)
        subprocess.run(
            ["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"],
            check=False,
        )
        subprocess.run(["git", "add", *map(str, paths)], check=False)
        # only commit if there are changes staged
        res = subprocess.run(["git", "diff", "--cached", "--exit-code"], capture_output=True)
        if res.returncode == 0:
            logging.info("No hay cambios para commitear.")
            return
        subprocess.run(["git", "commit", "-m", message], check=True)
        subprocess.run(["git", "push"], check=True)
        logging.info("Commit y push realizados.")
    except Exception as e:
        logging.warning("Error en git commit/push: %s", e)


def parse_args():
    p = argparse.ArgumentParser(description="Actualizar M3U (acestream -> http).")
    p.add_argument("--url", action="append", default=[], help="URL pública con M3U (repetible).")
    p.add_argument("--input", action="append", default=[], help="Archivo local M3U (repetible).")
    p.add_argument("--out-dir", default=".", help="Directorio de salida.")
    p.add_argument("--combined-name", default="playlist.m3u", help="Nombre del fichero combinado.")
    p.add_argument("--host", default="127.0.0.1", help="Host destino para ace/getstream.")
    p.add_argument("--port", type=int, default=6878, help="Puerto destino para ace/getstream.")
    p.add_argument("--no-backup", dest="backup", action="store_false", help="No crear backups.")
    p.add_argument("--no-commit", dest="commit", action="store_false", help="No hacer commit/push.")
    p.add_argument("--timeout", type=int, default=30, help="Timeout descargas (segundos).")
    p.add_argument("--verbose", "-v", action="count", default=0, help="Verbosity.")
    return p.parse_args()


def main():
    args = parse_args()
    log_level = logging.WARNING
    if args.verbose == 1:
        log_level = logging.INFO
    elif args.verbose >= 2:
        log_level = logging.DEBUG
    logging.basicConfig(level=log_level, format="%(asctime)s %(levelname)s: %(message)s")

    if not args.url and not args.input:
        logging.error("Se necesita --url o --input")
        sys.exit(1)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    combined_parts = []
    written_files = []

    # Process URLs
    for u in args.url:
        logging.info("Descargando %s", u)
        try:
            text = download_url(u, timeout=args.timeout)
        except Exception as e:
            logging.error("Error descargando %s: %s", u, e)
            continue
        new_text, changed = replace_links(text, args.host, args.port)
        logging.info("Reemplazos en %s: %d", u, changed)
        combined_parts.append(new_text)

        # Save individual converted file (optional)
        fname = Path(u.split("/")[-1] or "source.m3u")
        out_name = f"{fname.stem}_converted.m3u"
        out_path = out_dir / out_name
        if out_path.exists() and args.backup:
            b = backup_file(out_path)
            logging.info("Backup creado: %s", b)
        out_path.write_text(new_text, encoding="utf-8")
        written_files.append(out_path)

    # Process local files
    for f in args.input:
        p = Path(f)
        if not p.exists():
            logging.warning("Archivo local no encontrado: %s", f)
            continue
        text = p.read_text(encoding="utf-8")
        new_text, changed = replace_links(text, args.host, args.port)
        logging.info("Reemplazos en %s: %d", f, changed)
        combined_parts.append(new_text)
        out_name = f"{p.stem}_converted.m3u"
        out_path = out_dir / out_name
        if out_path.exists() and args.backup:
            b = backup_file(out_path)
            logging.info("Backup creado: %s", b)
        out_path.write_text(new_text, encoding="utf-8")
        written_files.append(out_path)

    # Write combined playlist
    if combined_parts:
        combined_text = "\n\n".join(part.strip() for part in combined_parts if part.strip())
        combined_path = out_dir / args.combined_name
        if combined_path.exists() and args.backup:
            b = backup_file(combined_path)
            logging.info("Backup combinado creado: %s", b)
        combined_path.write_text(combined_text, encoding="utf-8")
        written_files.append(combined_path)
        logging.info("Playlist combinada guardada en %s", combined_path)
    else:
        logging.warning("No se generó contenido combinado (no hay partes).")

    # Git commit & push (si está activado)
    if args.commit and written_files:
        git_commit_and_push(written_files, f"Actualizar playlist {now_ts()}")

    logging.info("Fin del proceso.")


if __name__ == "__main__":
    main()
