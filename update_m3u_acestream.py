#!/usr/bin/env python3
import argparse
import re
import sys
from pathlib import Path
import urllib.request
import urllib.error
import os

HEX40 = r"[0-9A-Fa-f]{40}"

# Regex que captura el HASH final (funciona para:
#   - acestream://HASH
#   - http(s)://host:port/.../HASH
#   - http(s)://host:port/ace/getstream?id=HASH
PAT_GENERIC_HASH = re.compile(
    r"(?:acestream://|https?://[^/\s:]+:\d{1,5}/(?:ace/getstream\?id=)?)(%s)" % HEX40,
    re.IGNORECASE,
)

def fetch_url_content(url: str, timeout: int):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            raw = resp.read()
            # Intentamos decodificar como utf-8, si falla, fallback a latin-1
            try:
                return raw.decode("utf-8")
            except UnicodeDecodeError:
                return raw.decode("latin-1")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Error descargando '{url}': {e}")

def replace_hashes_in_text(text: str, new_host: str, new_port: int):
    """
    Reemplaza todas las apariciones detectadas por:
      http://{new_host}:{new_port}/ace/getstream?id={HASH}
    """
    def _repl(m):
        h = m.group(1)
        return f"http://{new_host}:{new_port}/ace/getstream?id={h}"

    return PAT_GENERIC_HASH.sub(_repl, text)

def combine_sources(urls, inputs, timeout):
    pieces = []
    errors = []
    # Procesar URLs remotas
    for u in urls:
        try:
            pieces.append(fetch_url_content(u, timeout=timeout))
        except Exception as e:
            errors.append(str(e))

    # Procesar archivos locales
    for p in inputs:
        pth = Path(p)
        if not pth.exists():
            errors.append(f"Archivo local no encontrado: {p}")
            continue
        try:
            pieces.append(pth.read_text(encoding="utf-8"))
        except UnicodeDecodeError:
            # Intentar con latin1
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
    parser.add_argument("--no-commit", dest="commit", action="store_false", help="No hacer commit/push (no usado por ahora).")
    parser.add_argument("--timeout", type=int, default=30, help="Timeout descargas (segundos).")

    args = parser.parse_args()

    # Validación básica: al menos una fuente (url o input)
    if not args.url and not args.input:
        print("Error: debes proporcionar al menos una fuente --url o --input.", file=sys.stderr)
        sys.exit(2)

    combined_text, errors = combine_sources(args.url, args.input, timeout=args.timeout)

    if not combined_text.strip():
        print("Error: No se pudo leer ningún contenido válido de las fuentes proporcionadas.", file=sys.stderr)
        for e in errors:
            print("  -", e, file=sys.stderr)
        sys.exit(3)

    # Reemplazar hashes por la URL de streaming destino
    updated = replace_hashes_in_text(combined_text, args.host, args.port)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / args.combined_name

    # Hacer backup si procede
    try:
        bak = write_backup_if_needed(out_path, args.backup)
    except Exception as e:
        print(f"Error creando backup: {e}", file=sys.stderr)
        sys.exit(4)

    try:
        out_path.write_text(updated, encoding="utf-8")
    except Exception as e:
        print(f"Error escribiendo fichero de salida '{out_path}': {e}", file=sys.stderr)
        # si hubo un backup, intentar restaurarlo (silencioso)
        if bak and bak.exists():
            bak.replace(out_path)
        sys.exit(5)

    print(f"Archivo M3U actualizado: {out_path}")
    if errors:
        print("Se produjeron algunos errores no fatales al obtener fuentes:", file=sys.stderr)
        for e in errors:
            print("  -", e, file=sys.stderr)

if __name__ == "__main__":
    main()
