#!/usr/bin/env python3
"""
update_m3u_acestream.py

Convierte enlaces acestream://HASH o http(s)://host:port/.../HASH
al formato:
  http://HOST:PORT/ace/getstream?id=HASH

Soporta múltiples URLs y ficheros locales, genera playlist combinada
y puede hacer commit automático en GitHub Actions.
"""

import argparse
import datetime
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path
from urllib.parse import urlparse

HEX40 = r"[0-9A-Fa-f]{40}"
PAT_ACESTREAM = re.compile(r"acestream://(" + HEX40 + r")")
PAT_HOST_PORT_HASH = re.compile(
    r"(?:https?://)?[^/\s:]+:\d{1,5}/(?:ace/getstream\\?id=)?(" + HEX40 + r")"
)


def now_ts():
    return datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")


def replace_links(text: str, host: str, port: int) -> str:
    text = PAT_ACESTREAM.sub(
        lambda m: f"http://{host}:{port}/ace/getstream?id={m.group(1)}",
        text,
    )
    text = PAT_HOST_PORT_HASH.sub(
        lambda m: f"http://{host}:{port}/ace/getstream?id={m.group(1)}",
        text,
    )
    return text


def download_url(url: str, timeout: int = 30) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/x-mpegURL,text/plain,*/*",
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Error descargando {url}: {e}")


def backup(path: Path):
    dest = path.with_suffix(path.suffix + f".bak.{now_ts()}")
    shutil.copy2(path, dest)


def git_commit(files, msg):
    subprocess.run(["git", "config", "user.name", "github-actions[bot]"])
    subprocess.run([
        "git",
        "config",
        "user.email",
        "41898282+github-actions[bot]@users.noreply.github.com",
    ])
    subprocess.run(["git", "add", *map(str, files)])
    if subprocess.run(["git", "diff", "--cached", "--quiet"]).returncode == 0:
        return
    subprocess.run(["git", "commit", "-m", msg], check=True)
    subprocess.run(["git", "push"], check=True)


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--url", action="append", default=[])
    p.add_argument("--input", action="append", default=[])
    p.add_argument("--out-dir", default=".")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=6878)
    p.add_argument("--combined-name", default="playlist.m3u")
    p.add_argument("--no-backup", dest="backup", action="store_false")
    p.add_argument("--no-commit", dest="commit", action="store_false")
    return p.parse_args()


def main():
    args = parse_args()

    if not args.url and not args.input:
        sys.exit("Debe indicar --url o --input")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    parts = []
    outputs = []

    for url in args.url:
        text = download_url(url)
        text = replace_links(text, args.host, args.port)
        parts.append(text)

    for f in args.input:
        p = Path(f)
        if not p.exists():
            continue
        text = replace_links(p.read_text(encoding="utf-8"), args.host, args.port)
        parts.append(text)

    combined = "\n\n".join(parts)
    out_path = out_dir / args.combined_name

    if out_path.exists() and args.backup:
        backup(out_path)

    out_path.write_text(combined, encoding="utf-8")
    outputs.append(out_path)

    if args.commit:
        git_commit(outputs, f"Actualizar playlist {now_ts()}")


if __name__ == "__main__":
    main()
