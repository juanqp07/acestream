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

HEX40 = r"[0-9A-Fa-f]{40}"
PAT_ACESTREAM = re.compile(r"acestream://(" + HEX40 + r")")
# captura host:port/... con posible esquema y con /ace/getstream?id=hash o /hash
PAT_HOST_PORT_HASH = re.compile(
    r"(?:https?://)?(?P<host>[^/\s:]+):(?P<port>\d{1,5})/(?:(?:ace/getstream\?id=)?(?P<h>" + HEX40 + r"))"
)

# Ordenado: probar gateways más tolerantes primero (Cloudflare, dweb.link, pinata, ipfs.io)
ALTERNATIVE_GATEWAYS = [
    "https://cloudflare-ipfs.com",
    "https://dweb.link",
    "https://gateway.pinata.cloud",
    "https://ipfs.io",
]

def now_ts():
    return datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")

def replace_acestream_and_existing(text: str, host: str, port: int) -> str:
    # 1) reemplaza acestream://<hash>
    def repl_acestream(m):
        h = m.group(1)
        return f"http://{host}:{port}/ace/getstream?id={h}"
    text = PAT_ACESTREAM.sub(repl_acestream, text)

    # 2) reemplaza cualquier host:port/<hash> o host:port/ace/getstream?id=<hash>
    def repl_hostport(m):
        h = m.group("h")
        if not h:
            return m.group(0)  # no tocamos si no hay hash
        return f"http://{host}:{port}/ace/getstream?id={h}"
    text = PAT_HOST_PORT_HASH.sub(repl_hostport, text)

    return text

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
        with urllib.request.urlopen(req, timeout=timeout) as r:
            charset = r.headers.get_content_charset() or "utf-8"
            return r.read().decode(charset, errors="replace")
    except urllib.error.HTTPError as e:
        # leer un snippet del body para depuración
        body = b""
        try:
            body = e.read(2048)
        except Exception:
            pass
        snippet = ""
        try:
            snippet = body.decode("utf-8", errors="replace")
        except Exception:
            snippet = str(body)[:200]
        raise RuntimeError(f"Error descargando URL {url}: HTTP {e.code} {e.reason}. Response snippet: {snippet[:1000]}")
    except Exception as e:
        raise RuntimeError(f"Error descargando URL {url}: {e}")

def try_download_with_gateways(original_url: str, timeout: int = 30) -> str:
    """
    Intenta descargar la URL original; si detecta rutas /ipfs/ o /ipns/ probará gateways alternativos.
    Heurística:
      - Si la URL tiene host ipfs.io, probamos gateways alternativos primero (porque ipfs.io suele bloquear bots).
      - Si la URL contiene /ipfs/ o /ipns/, reconstruimos la URL con cada gateway y probamos.
      - Finalmente intentamos la original.
    """
    parsed = urlparse(original_url)
    path = parsed.path or ""
    netloc = parsed.netloc.lower()
    is_ipfs_path = "/ipfs/" in original_url or "/ipns/" in original_url

    # Si el host original es ipfs.io, intentamos gateways antes que la original
    if "ipfs.io" in netloc:
        if is_ipfs_path:
            # calcular ipfs/ipns path
            idx = original_url.find("/ipfs/")
            if idx == -1:
                idx = original_url.find("/ipns/")
            ipfs_path = original_url[idx:] if idx != -1 else path
            for gw in ALTERNATIVE_GATEWAYS:
                candidate = gw.rstrip("/") + ipfs_path
                try:
                    print(f"Intentando gateway {candidate} ...")
                    return download_url(candidate, timeout=timeout)
                except RuntimeError as e:
                    print(f"Fallo {candidate}: {e}")
        # si no funciona con gateways, intentar la original al final

    # Intentamos la original (si tiene esquema)
    if parsed.scheme in ("http", "https"):
        try:
            print(f"Descargando (original) {original_url} ...")
            return download_url(original_url, timeout=timeout)
        except RuntimeError as e:
            print(f"Fallo descarga original: {e}")

    # Si es path IPFS/IPNS y no hemos tenido éxito aún, probar gateways (incluso si original no era ipfs.io)
    if is_ipfs_path:
        idx = original_url.find("/ipfs/")
        if idx == -1:
            idx = original_url.find("/ipns/")
        ipfs_path = original_url[idx:] if idx != -1 else path
        for gw in ALTERNATIVE_GATEWAYS:
            candidate = gw.rstrip("/") + ipfs_path
            try:
                print(f"Intentando gateway {candidate} ...")
                return download_url(candidate, timeout=timeout)
            except RuntimeError as e:
                print(f"Fallo {candidate}: {e}")

    # Intento final con la original si no se intentó antes o por si no tenía schema
    try:
        print(f"Ultimo intento con original: {original_url} ...")
        return download_url(original_url, timeout=timeout)
    except RuntimeError as e:
        raise RuntimeError("No fue posible descargar la URL desde ningún gateway público.") from e

def backup_file(path: Path) -> Path:
    ts = now_ts()
    dest = path.with_name(path.name + f".bak.{ts}")
    shutil.copy2(path, dest)
    return dest

def git_commit_and_push(paths, message: str) -> None:
    subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=False)
    subprocess.run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"], check=False)
    # add all paths
    cmd = ["git", "add"] + [str(p) for p in paths]
    subprocess.run(cmd, check=True)
    try:
        subprocess.run(["git", "diff", "--cached", "--exit-code"], check=True)
        print("No hay cambios para commitear.")
        return
    except subprocess.CalledProcessError:
        pass
    subprocess.run(["git", "commit", "-m", message], check=True)
    subprocess.run(["git", "push"], check=True)

def safe_name_from_url(url: str, default: str) -> str:
    try:
        p = urlparse(url)
        name = Path(p.path).name
        if not name:
            return default
        return name
    except Exception:
        return default

def normalize_m3u_header(text: str) -> str:
    # Asegura que el fichero empieza con una única línea #EXTM3U
    # También filtra líneas #EXTGRP que el usuario no quiere
    lines = text.strip().splitlines()
    body = [
        ln for ln in lines 
        if ln.strip() != "#EXTM3U" and not ln.strip().startswith("#EXTGRP")
    ]
    return "#EXTM3U\n" + "\n".join(body).strip() + "\n"

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

    if not sources:
        print("Error: no se proporcionó ninguna fuente (--url ni --input).")
        sys.exit(2)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    generated_paths = []
    combined_parts = []

    for idx, (typ, src) in enumerate(sources, start=1):
        try:
            if typ == "url":
                print(f"Descargando {src} ...")
                text = try_download_with_gateways(src)
                base = safe_name_from_url(src, f"source_{idx}.m3u")
                out_name = f"{Path(base).stem}_converted.m3u"
            else:
                fp = Path(src)
                if not fp.exists():
                    print(f"Advertencia: archivo de entrada no encontrado: {src}, se salta.")
                    continue
                text = fp.read_text(encoding="utf-8")
                out_name = f"{fp.stem}_converted.m3u"

            new_text = replace_acestream_and_existing(text, args.host, args.port)
            # Normaliza header
            new_text = normalize_m3u_header(new_text)

            out_path = out_dir / out_name
            if out_path.exists() and args.backup:
                b = backup_file(out_path)
                print(f"Backup creado: {b}")
            out_path.write_text(new_text, encoding="utf-8")
            print(f"Guardado: {out_path}")
            generated_paths.append(out_path)
            combined_parts.append(new_text)
        except Exception as e:
            print(f"Error procesando {src}: {e}")

    # Crear combinado
    if combined_parts:
        combined = "\n".join(part.strip() for part in combined_parts if part)
        combined = normalize_m3u_header(combined)
        combined_path = out_dir / args.combined_name
        if combined_path.exists() and args.backup:
            b = backup_file(combined_path)
            print(f"Backup combinado creado: {b}")
        combined_path.write_text(combined, encoding="utf-8")
        print(f"Archivo combinado guardado en: {combined_path}")
        generated_paths.append(combined_path)

    if args.commit and generated_paths:
        try:
            msg = f"Actualizar M3U convertidos ({now_ts()})"
            git_commit_and_push(generated_paths, msg)
            print("Commited & pushed.")
        except subprocess.CalledProcessError as e:
            print("Error al commitear/pushear:", e)
            sys.exit(3)

if __name__ == "__main__":
    main()
