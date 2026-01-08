#!/usr/bin/env python3
"""
Actualizar/convertir enlaces AceStream dentro de M3U(s).
Versión reforzada: retries, cabeceras, fallbacks para gateways IPFS, mejor logging de errores.
"""
import argparse
import re
import sys
from pathlib import Path
import os
import logging
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Config logging simple
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

HEX40 = r"[0-9A-Fa-f]{40}"
PAT_GENERIC_HASH = re.compile(
    r"(?:acestream://|https?://[^/\s:]+:\d{1,5}/(?:ace/getstream\?id=)?)(%s)" % HEX40,
    re.IGNORECASE,
)

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko)",
    "Accept": "*/*",
    "Accept-Language": "es-ES,es;q=0.9",
}

# Create a requests.Session with retries
def make_session(total_retries=3, backoff=1):
    session = requests.Session()
    retries = Retry(
        total=total_retries,
        backoff_factor=backoff,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(['GET', 'HEAD'])
    )
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session

def fetch_url_content(url: str, timeout: int, session: requests.Session):
    """
    Intenta descargar `url` y devuelve el contenido text. Lanza RuntimeError si falla.
    Trata respuestas 4xx/5xx como error y muestra preview del body.
    """
    logging.info(f"Intentando descargar: {url}")
    try:
        resp = session.get(url, headers=DEFAULT_HEADERS, timeout=timeout, allow_redirects=True)
    except requests.RequestException as e:
        raise RuntimeError(f"RequestException al conectar '{url}': {e}")

    # Si la respuesta está vacía o status >= 400 lo consideramos fallo
    if resp.status_code >= 400:
        preview = resp.text[:400].replace("\n", "\\n")
        raise RuntimeError(f"HTTP {resp.status_code} '{url}' -> preview: {preview}")
    if len(resp.content) == 0:
        raise RuntimeError(f"Respuesta vacía (0 bytes) al descargar '{url}' (status {resp.status_code})")

    # Intentar decodificar con encoding detectado y fallback latin-1
    encoding = resp.encoding or "utf-8"
    try:
        return resp.content.decode(encoding)
    except Exception:
        return resp.content.decode("latin-1", errors="ignore")

def ipfs_gateways_for(url: str):
    """
    Genera candidatos para URL IPFS/IPNS (fallbacks).
    Mantiene el orden de preferencia.
    """
    # normalizar
    if url.startswith("ipfs://") or url.startswith("ipns://"):
        # convertir a https ipfs.io por defecto
        url = url.replace("ipfs://", "https://ipfs.io/ipfs/").replace("ipns://", "https://ipfs.io/ipns/")
    # Si es ipfs.io, añadir candidatos
    if "ipfs.io" in url:
        yield url
        yield url.replace("https://ipfs.io", "https://cloudflare-ipfs.com")
        yield url.replace("https://ipfs.io", "https://dweb.link")
    elif "/ipns/" in url or "/ipfs/" in url:
        yield url
        # construir alternativas reemplazando host
        if url.startswith("https://"):
            yield url.replace("https://", "https://cloudflare-ipfs.com/")
            yield url.replace("https://", "https://dweb.link/")
        elif url.startswith("http://"):
            yield url.replace("http://", "http://cloudflare-ipfs.com/")
            yield url.replace("http://", "http://dweb.link/")
        else:
            yield "https://cloudflare-ipfs.com" + url if url.startswith("/") else url
            yield "https://dweb.link" + url if url.startswith("/") else url
    else:
        yield url

def replace_hashes_in_text(text: str, new_host: str, new_port: int):
    def _repl(m):
        h = m.group(1)
        return f"http://{new_host}:{new_port}/ace/getstream?id={h}"
    return PAT_GENERIC_HASH.sub(_repl, text)

def combine_sources(urls, inputs, timeout):
    pieces = []
    errors = []
    session = make_session()

    # Procesar URLs remotas con fallbacks
    for u in urls:
        success = False
        last_error = None
        for candidate in ipfs_gateways_for(u):
            try:
                content = fetch_url_content(candidate, timeout=timeout, session=session)
                pieces.append(content)
                logging.info(f"Descargado OK: {candidate} (size {len(content)} bytes)")
                success = True
                break
            except Exception as e:
                last_error = str(e)
                logging.warning(f"Fallo con {candidate}: {e}")
                errors.append(f"{candidate}: {e}")
        if not success:
            errors.append(f"No se pudo descargar ninguna réplica para {u} -> último error: {last_error}")

    # Procesar archivos locales
    for p in inputs:
        pth = Path(p)
        if not pth.exists():
            errors.append(f"Archivo local no encontrado: {p}")
            continue
        try:
            pieces.append(pth.read_text(encoding="utf-8"))
        except UnicodeDecodeError:
            pieces.append(pth.read_text(encoding="latin-1"))

    return "\n".join(pieces), errors

def write_backup_if_needed(output_path: Path, do_backup: bool):
    if do_backup and output_path.exists():
        i = 1
        while True:
            bak = output_path.with_name(output_path.name + f".bak{i}")
            if not bak.exists():
                output_path.replace(bak)
                return bak
            i += 1
    return None

def main():
    parser = argparse.ArgumentParser(description="Actualizar/convertir enlaces AceStream dentro de M3U(s).")
    parser.add_argument("--url", action="append", default=[], help="URL pública con M3U (repetible).")
    parser.add_argument("--input", action="append", default=[], help="Archivo local M3U (repetible).")
    parser.add_argument("--out-dir", default=".", help="Directorio de salida.")
    parser.add_argument("--combined-name", default="playlist.m3u", help="Nombre del fichero combinado.")
    parser.add_argument("--host", default="127.0.0.1", help="Host destino para ace/getstream.")
    parser.add_argument("--port", type=int, default=6878, help="Puerto destino para ace/getstream.")
    parser.add_argument("--no-backup", dest="backup", action="store_false", help="No crear backups.")
    parser.add_argument("--timeout", type=int, default=30, help="Timeout descargas (segundos).")
    args = parser.parse_args()

    if not args.url and not args.input:
        logging.error("Debes proporcionar al menos una fuente --url o --input.")
        sys.exit(2)

    combined_text, errors = combine_sources(args.url, args.input, timeout=args.timeout)
    if not combined_text.strip():
        logging.error("No se pudo leer ningún contenido válido de las fuentes proporcionadas.")
        for e in errors:
            logging.error("  - " + str(e))
        sys.exit(3)

    updated = replace_hashes_in_text(combined_text, args.host, args.port)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / args.combined_name

    try:
        bak = write_backup_if_needed(out_path, args.backup)
    except Exception as e:
        logging.error(f"Error creando backup: {e}")
        sys.exit(4)

    try:
        out_path.write_text(updated, encoding="utf-8")
    except Exception as e:
        logging.error(f"Error escribiendo fichero de salida '{out_path}': {e}")
        if bak and bak.exists():
            bak.replace(out_path)
        sys.exit(5)

    logging.info(f"Archivo M3U actualizado: {out_path}")
    if errors:
        logging.warning("Se produjeron algunos errores no fatales al obtener fuentes:")
        for e in errors:
            logging.warning("  - " + str(e))

if __name__ == "__main__":
    main()
