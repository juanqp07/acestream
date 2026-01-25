#!/usr/bin/env python3
import argparse
import re
import sys
import os
from pathlib import Path
import logging
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import glob

# Config logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

HEX40 = r"[0-9A-Fa-f]{40}"
PAT_GENERIC_HASH = re.compile(
    r"(?:acestream://|https?://[^/\s:]+:\d{1,5}/(?:ace/getstream\?id=)?)(%s)" % HEX40,
    re.IGNORECASE,
)

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko)",
    "Accept": "*/*",
}

def make_session(total_retries=3, backoff=1):
    session = requests.Session()
    retries = Retry(total=total_retries, backoff_factor=backoff, status_forcelist=(429, 500, 502, 503, 504))
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session

def fetch_url_content(url: str, timeout: int, session: requests.Session):
    logging.info(f"Descargando: {url}")
    resp = session.get(url, headers=DEFAULT_HEADERS, timeout=timeout, allow_redirects=True)
    resp.raise_for_status()
    encoding = resp.encoding or "utf-8"
    return resp.content.decode(encoding, errors="ignore")

def replace_hashes_in_text(text: str, new_host: str, new_port: int, mode: str, password: str):
    def _repl(m):
        content_id = m.group(1)
        if mode == "mediaflow":
            return f"http://{new_host}:{new_port}/proxy/acestream/stream?id={content_id}&api_password={password}"
        else: # acexy
            return f"http://{new_host}:{new_port}/ace/getstream?id={content_id}"
    return PAT_GENERIC_HASH.sub(_repl, text)

def process_content(content, output_path, args):
    """Lógica para transformar y guardar el contenido"""
    password = os.environ.get("MEDIAFLOW_PASSWORD", "")
    updated = replace_hashes_in_text(content, args.host, args.port, args.mode, password)
    
    if args.backup and output_path.exists():
        bak = output_path.with_suffix(output_path.suffix + ".bak")
        output_path.replace(bak)
        
    output_path.write_text(updated, encoding="utf-8")
    logging.info(f"Guardado: {output_path} (Modo: {args.mode})")

def main():
    parser = argparse.ArgumentParser(description="Procesador de Listas AceStream")
    parser.add_argument("--url", action="append", default=[], help="URLs de listas.")
    parser.add_argument("--input", action="append", default=[], help="Archivos locales o patrones (ej: *.m3u).")
    parser.add_argument("--out-dir", default="procesados", help="Carpeta de salida.")
    parser.add_argument("--host", default="127.0.0.1", help="IP destino AceStream.")
    parser.add_argument("--port", type=int, default=6878, help="Puerto AceStream.")
    parser.add_argument("--mode", choices=["acexy", "mediaflow"], default="acexy", help="Modo de modificación (acexy o mediaflow).")
    parser.add_argument("--no-backup", dest="backup", action="store_false", help="No crear backups.")
    parser.add_argument("--timeout", type=int, default=20, help="Timeout segundos.")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    session = make_session()

    # 1. PROCESAR ARCHIVOS LOCALES (Soporta múltiples y comodines)
    all_inputs = []
    for pattern in args.input:
        all_inputs.extend(glob.glob(pattern))

    for item in all_inputs:
        path = Path(item)
        if path.is_file():
            try:
                logging.info(f"Procesando archivo: {path.name}")
                content = path.read_text(encoding="utf-8", errors="ignore")
                process_content(content, out_dir / path.name, args)
            except Exception as e:
                logging.error(f"Error con {path.name}: {e}")

    # 2. PROCESAR URLS
    for i, url in enumerate(args.url):
        try:
            content = fetch_url_content(url, args.timeout, session)
            # Generar un nombre basado en la URL o un índice
            name = url.split("/")[-1] or f"lista_{i}.m3u"
            if not name.endswith(".m3u"): name += ".m3u"
            
            process_content(content, out_dir / name, args)
        except Exception as e:
            logging.error(f"Error descargando {url}: {e}")

if __name__ == "__main__":
    main()

