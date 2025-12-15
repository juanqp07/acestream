import argparse
import re
import sys
from pathlib import Path
import urllib.request
import urllib.error

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

def replace_link(url, new_host, new_port):
    # Check if the URL is in acestream format
    if re.match(PAT_ACESTREAM, url):
        hash_match = re.search(PAT_ACESTREAM, url)
        hash_value = hash_match.group(1)
        return f"{new_host}:{new_port}/{hash_value}"

    # Check if the URL is in host:port format
    if re.match(PAT_HOST_PORT_HASH, url):
        host_match = re.search(PAT_HOST_PORT_HASH, url)
        host_value = host_match.group('host')
        port_value = host_match.group('port')
        return f"{new_host}:{new_port}/{host_value}"

    # If the URL is neither, return it as is
    return url

def update_m3u(input_file, output_file, new_host, new_port):
    if not input_file.exists():
        print(f"El archivo M3U {input_file} no existe.")
        return

    with open(input_file, "r") as file:
        m3u_content = file.read()

    # Replace all URLs in the M3U content
    updated_m3u_content = re.sub(
        r"(acestream://|https?://(?:[^/\s:]+):\d{1,5}/(?:ace/getstream\?id=)?)([0-9A-Fa-f]{40})",
        lambda match: replace_link(match.group(0), new_host, new_port),
        m3u_content,
    )

    # Save the updated M3U content to the output file
    with open(output_file, "w") as file:
        file.write(updated_m3u_content)

    print(f"Archivo M3U actualizado: {output_file}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Actualizar M3U (acestream -> http).")
    parser.add_argument("--url", action="append", default=[], help="URL pública con M3U (repetible).")
    parser.add_argument("--input", action="append", default=[], help="Archivo local M3U (repetible).")
    parser.add_argument("--out-dir", default=".", help="Directorio de salida.")
    parser.add_argument("--combined-name", default="playlist.m3u", help="Nombre del fichero combinado.")
    parser.add_argument("--host", default="127.0.0.1", help="Host destino para ace/getstream.")
    parser.add_argument("--port", type=int, default=6878, help="Puerto destino para ace/getstream.")
    parser.add_argument("--no-backup", dest="backup", action="store_false", help="No crear backups.")
    parser.add_argument("--no-commit", dest="commit", action="store_false", help="No hacer commit/push.")
    parser.add_argument("--timeout", type=int, default=30, help="Timeout descargas (segundos).")

    args = parser.parse_args()

    if len(args.url) != 2:
        print("Debes proporcionar dos enlaces URL.")
        sys.exit(1)

    new_host = args.host
    new_port = args.port

    if args.url:
        update_m3u(args.url[0], args.url[1], new_host, new_port)
    else:
        update_m3u(args.input[0], args.input[1], new_host, new_port)
