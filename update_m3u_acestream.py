#!/usr/bin/env python3
"""
update_m3u_acestream.py

Soporta:
  --url <URL>   (repetible)  : URL pública que contiene un .m3u (se descarga)
  --input <FILE> (repetible) : fichero local .m3u
  --out-dir DIR               : directorio de salida (por defecto ./)
  --host HOST                 : host destino (por defecto 127.0.0.1)
  --port PORT                 : puerto destino (por defecto 6878)
  --no-backup                 : no crear backup de ficheros de salida
  --no-commit                 : no hacer git add/commit/push
  --combined-name NAME        : nombre del fichero combinado (por defecto combined_converted.m3u)
"""
import re
import argparse
from pathlib import Path
import shutil
import datetime
import subprocess
import sys
import urllib.request
import urllib.error
from urllib.parse import urlparse

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--url", action="append", help="URL pública de M3U (repetible)")
    p.add_argument("--input", action="append", help="Archivo local M3U (repetible)")
    p.add_argument("--out-dir", default=".", help="Directorio de salida")
    p.add_argument("--host", default="127.0.0.1", help="Host destino")
    p.add_argument("--port", type=int, default=6878, help="Puerto destino")
    p.add_argument("--no-backup", dest="backup", action="store_false", help="No crear backups")
    p.add_argument("--no-commit", dest="commit", action="store_false", help="No hacer commit/push")
    p.add_argument("--combined-name", default="combined_converted.m3u", help="Nombre del archivo combinado")
    args = p.parse_args()

    sources = []
    if args.url:
        for u in args.url:
            sources.append(("url", u))
    if args.input:
        for f in args.input:
            sources.append(("file", f))

def download_url(url: str, timeout: int = 30) -> str:
    """
    Descarga la URL con cabeceras tipo navegador. Lanza RuntimeError en fallo con snippet del body si es HTTPError.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0 Safari/537.36",
        "Accept": "text/html,application/vnd.apple.mpegurl,application/x-mpegURL,*/*",
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
        "Connection": "close",
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            content = response.read().decode('utf-8')
            return content
    except (urllib.error.HTTPError, urllib.error.URLError) as e:
        raise RuntimeError(f"Error al descargar {url}: {e}")

def load_m3u(file_path: str) -> list[str]:
    with open(file_path, 'r', encoding='utf-8') as file:
        lines = file.readlines()
    return [line.strip() for line in lines if not line.startswith('#') and line]

def process_sources(sources):
    processed_files = []
    backup_dir = Path(args.out_dir) / "backup"
    backup_dir.mkdir(parents=True, exist_ok=True)

    for source_type, source in sources:
        if source_type == "url":
            content = download_url(source)
            file_path = Path(f"{args.out_dir}/{args.combined_name}")
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            processed_files.append((file_path, False))
        elif source_type == "file":
            file_path = Path(source)
            if not file_path.exists():
                raise FileNotFoundError(f"El archivo {source} no existe.")
            processed_files.append((file_path, True))

    return processed_files

def commit_changes(file_paths):
    if args.commit:
        for file_path, is_backup in file_paths:
            git_commit(file_path)

def git_commit(file_path: Path) -> None:
    subprocess.run(['git', 'add', str(file_path)])
    print(f"Se agregó {file_path} al repositorio.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Actualiza un archivo M3U desde una URL o archivo local.")
    parser.add_argument("--url", action="append", help="URL pública de M3U (repetible)")
    parser.add_argument("--input", action="append", help="Archivo local M3U (repetible)")
    parser.add_argument("--out-dir", default=".", help="Directorio de salida")
    parser.add_argument("--host", default="127.0.0.1", help="Host destino")
    parser.add_argument("--port", type=int, default=6878, help="Puerto destino")
    parser.add_argument("--no-backup", dest="backup", action="store_false", help="No crear backup de ficheros de salida")
    parser.add_argument("--no-commit", dest="commit", action="store_false", help="No hacer commit/push")
    parser.add_argument("--combined-name", default="combined_converted.m3u", help="Nombre del archivo combinado")

    args = parser.parse_args()

    if not (args.url or args.input):
        print("Debe especificar una URL o un archivo M3U.")
        sys.exit(1)

    try:
        processed_files = process_sources(args)
        commit_changes(processed_files)
        print("Proceso completado exitosamente.")

    except Exception as e:
        print(f"Error: {e}")
