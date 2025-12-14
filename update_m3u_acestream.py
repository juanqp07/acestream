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
#!/usr/bin/env python3
"""
M3U Playlist Updater for Acestream

This script processes M3U playlists, converting Acestream links to HTTP streams
that can be played through an Acestream server. It supports downloading from
multiple sources, including IPFS/IPNS gateways, and can automatically commit
changes to a Git repository.
"""

import argparse
import datetime
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.parse import urlparse

__version__ = "1.1.0"

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

def download_url(url: str, timeout: int = 30, max_retries: int = 3) -> str:
    """
    Download URL with browser-like headers. Raises RuntimeError on failure with response body snippet for HTTPError.
    
    Args:
        url: URL to download
        timeout: Request timeout in seconds
        max_retries: Maximum number of retry attempts
        
    Returns:
        str: Downloaded content
        
    Raises:
        RuntimeError: If download fails after all retries
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/vnd.apple.mpegurl,application/x-mpegURL,*/*",
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
        "Connection": "close",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache"
    }
    last_error = None
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                content_type = r.headers.get('Content-Type', '')
                if 'charset=' in content_type:
                    charset = content_type.split('charset=')[-1].split(';')[0].strip()
                else:
                    charset = 'utf-8'
                return r.read().decode(charset, errors="replace")
                
        except urllib.error.HTTPError as e:
            # Read error response for debugging
            body = b""
            try:
                body = e.read(2048)
                last_error = f"HTTP {e.code} {e.reason}. Response: {body.decode('utf-8', errors='replace')[:500]}"
            except Exception as e_read:
                last_error = f"HTTP {e.code} {e.reason} (Failed to read response: {str(e_read)})"
                
            # Don't retry on client errors (4xx) except 429 (Too Many Requests)
            if 400 <= e.code < 500 and e.code != 429:
                break
                
        except (urllib.error.URLError, socket.timeout) as e:
            last_error = str(e)
            # For timeouts or connection errors, we'll retry
            
        except Exception as e:
            last_error = str(e)
            break
            
        # Exponential backoff before retry
        if attempt < max_retries - 1:
            wait_time = (2 ** attempt) * 0.5  # 0.5, 1, 2, 4, ... seconds
            print(f"Attempt {attempt + 1} failed: {last_error}. Retrying in {wait_time:.1f}s...")
            time.sleep(wait_time)
    
    raise RuntimeError(f"Failed to download {url} after {max_retries} attempts. Last error: {last_error}")

def try_download_with_gateways(original_url: str, timeout: int = 30) -> str:
    """
    Try to download from the original URL; if it contains /ipfs/ or /ipns/ paths, it will try alternative gateways.
    
    Strategy:
      1. If the URL is from ipfs.io, try alternative gateways first (as ipfs.io often blocks bots).
      2. If the URL contains /ipfs/ or /ipns/, try each gateway.
      3. Finally, try the original URL.
      
    Args:
        original_url: The URL to download
        timeout: Timeout in seconds for each attempt
        
    Returns:
        str: The downloaded content
        
    Raises:
        RuntimeError: If all download attempts fail
    """
    parsed = urlparse(original_url)
    path = parsed.path or ""
    netloc = (parsed.netloc or "").lower()
    query = f"?{parsed.query}" if parsed.query else ""
    fragment = f"#{parsed.fragment}" if parsed.fragment else ""
    
    # Check if this is an IPFS/IPNS URL
    is_ipfs = "/ipfs/" in original_url
    is_ipns = "/ipns/" in original_url
    is_ipfs_path = is_ipfs or is_ipns
    
    # Extract the IPFS/IPNS path if present
    ipfs_path = ""
    if is_ipfs_path:
        idx = original_url.find("/ipfs/" if is_ipfs else "/ipns/")
        ipfs_path = original_url[idx:] if idx != -1 else path
    
    # If the original URL is from ipfs.io, try alternative gateways first
    if "ipfs.io" in netloc and is_ipfs_path:
        for gw in ALTERNATIVE_GATEWAYS:
            candidate = f"{gw.rstrip('/')}{ipfs_path}{query}{fragment}"
            try:
                print(f"Trying gateway {gw}...")
                return download_url(candidate, timeout=timeout)
            except RuntimeError as e:
                print(f"Failed with {gw}: {str(e)}")
    
    # If it's an IPFS/IPNS path (regardless of original domain), try all gateways
    if is_ipfs_path:
        for gw in ALTERNATIVE_GATEWAYS:
            # Skip if we already tried this gateway
            if gw in (parsed.scheme + "://" + parsed.netloc):
                continue
                
            candidate = f"{gw.rstrip('/')}{ipfs_path}{query}{fragment}"
            try:
                print(f"Trying gateway {gw}...")
                return download_url(candidate, timeout=timeout)
            except RuntimeError as e:
                print(f"Failed with {gw}: {str(e)}")
    
    # Try the original URL if it has http/https scheme
    if parsed.scheme in ("http", "https"):
        try:
            print(f"Trying original URL: {original_url}")
            return download_url(original_url, timeout=timeout)
        except RuntimeError as e:
            print(f"Original URL failed: {str(e)}")
    
    # If we have an IPFS path but no scheme, try with https://ipfs.io
    if is_ipfs_path and not parsed.scheme:
        try:
            candidate = f"https://ipfs.io{ipfs_path}{query}{fragment}"
            print(f"Trying with default IPFS gateway: {candidate}")
            return download_url(candidate, timeout=timeout)
        except RuntimeError as e:
            print(f"Default IPFS gateway failed: {str(e)}")
    
    # Final attempt with the original URL (in case it was modified)
    if original_url != (parsed.scheme or '') + '://' + (parsed.netloc or '') + (parsed.path or ''):
        try:
            print(f"Final attempt with original URL: {original_url}")
            return download_url(original_url, timeout=timeout)
        except RuntimeError as e:
            print(f"Final attempt failed: {str(e)}")
    
    raise RuntimeError("Failed to download from any gateway or the original URL.")

def backup_file(path: Path) -> Path:
    ts = now_ts()
    dest = path.with_name(path.name + f".bak.{ts}")
    shutil.copy2(path, dest)
    return dest

def is_git_tracked(file_path: Path) -> bool:
    """Check if a file is tracked by git."""
    try:
        cmd = ["git", "ls-files", "--error-unmatch", str(file_path)]
        result = subprocess.run(cmd, capture_output=True, text=True)
        return result.returncode == 0
    except Exception:
        return False

def git_commit_and_push(paths, message: str) -> None:
    """Improved git commit and push with better error handling."""
    try:
        # Filter out non-tracked files and check if they exist
        tracked_paths = [p for p in paths if p.exists() and is_git_tracked(p)]
        if not tracked_paths:
            print("No tracked files to commit.")
            return

        # Configure git user
        subprocess.run(
            ["git", "config", "user.name", "github-actions[bot]"],
            check=False,
            capture_output=True
        )
        subprocess.run(
            ["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"],
            check=False,
            capture_output=True
        )
        
        # Add files
        cmd = ["git", "add"] + [str(p) for p in tracked_paths]
        add_result = subprocess.run(cmd, capture_output=True, text=True)
        if add_result.returncode != 0:
            print(f"Warning: Failed to add files to git: {add_result.stderr}")
            return

        # Check for changes
        diff_result = subprocess.run(
            ["git", "diff", "--cached", "--exit-code"],
            capture_output=True,
            text=True
        )
        if diff_result.returncode == 0:
            print("No changes to commit.")
            return

        # Commit and push
        commit_result = subprocess.run(
            ["git", "commit", "-m", message],
            capture_output=True,
            text=True
        )
        if commit_result.returncode != 0:
            print(f"Warning: Failed to commit: {commit_result.stderr}")
            return

        push_result = subprocess.run(["git", "push"], capture_output=True, text=True)
        if push_result.returncode != 0:
            print(f"Warning: Failed to push: {push_result.stderr}")
            return

        print("Successfully committed and pushed changes.")
        
    except Exception as e:
        print(f"Error in git operations: {str(e)}")
        # Don't exit with error to allow the script to continue

def safe_name_from_url(url: str, default: str) -> str:
    try:
        p = urlparse(url)
        name = Path(p.path).name
        if not name:
            return default
        return name
    except Exception:
        return default



def parse_arguments():
    """Parse and validate command line arguments."""
    p = argparse.ArgumentParser(
        description='Update M3U playlists with Acestream links',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    
    # Input sources
    source_group = p.add_argument_group('Input Sources')
    source_group.add_argument(
        "--url", 
        action="append", 
        default=[],
        help="URL pública que contiene un .m3u (puede usarse múltiples veces)"
    )
    source_group.add_argument(
        "--input", 
        action="append", 
        default=[],
        help="Archivo local .m3u (puede usarse múltiples veces)"
    )
    
    # Output options
    output_group = p.add_argument_group('Output Options')
    output_group.add_argument(
        "--out-dir", 
        default=".", 
        help="Directorio de salida"
    )
    output_group.add_argument(
        "--combined-name", 
        default="playlist.m3u", 
        help="Nombre del archivo combinado"
    )
    output_group.add_argument(
        "--no-backup", 
        dest="backup", 
        action="store_false",
        help="No crear backups de los archivos existentes"
    )
    
    # Acestream settings
    acestream_group = p.add_argument_group('Acestream Settings')
    acestream_group.add_argument(
        "--host", 
        default="127.0.0.1", 
        help="Host de Acestream"
    )
    acestream_group.add_argument(
        "--port", 
        type=int, 
        default=6878, 
        help="Puerto de Acestream"
    )
    
    # Git options
    git_group = p.add_argument_group('Git Options')
    git_group.add_argument(
        "--no-commit", 
        dest="commit", 
        action="store_false",
        help="No hacer commit/push de los cambios"
    )
    git_group.add_argument(
        "--git-remote",
        default="origin",
        help="Nombre del repositorio remoto de Git"
    )
    git_group.add_argument(
        "--git-branch",
        default="main",
        help="Rama de Git a la que hacer push"
    )
    
    # Network settings
    network_group = p.add_argument_group('Network Settings')
    network_group.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Tiempo de espera para descargas en segundos"
    )
    network_group.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="Número máximo de reintentos por URL"
    )
    
    # Debugging
    debug_group = p.add_argument_group('Debugging')
    debug_group.add_argument(
        "--verbose", 
        "-v", 
        action="count", 
        default=0,
        help="Aumentar verbosidad (puede usarse múltiples veces, ej: -vvv)"
    )
    debug_group.add_argument(
        "--dry-run",
        action="store_true",
        help="Mostrar qué haría sin hacer cambios reales"
    )
    
    args = p.parse_args()
    
    # Validations
    if not args.url and not args.input:
        p.error("Se requiere al menos una fuente (--url o --input)")
    
    # Convert relative paths to absolute
    if args.out_dir:
        args.out_dir = os.path.abspath(args.out_dir)
    
    return args

def setup_logging(verbosity: int):
    """Configure logging based on verbosity level."""
    log_level = {
        0: logging.WARNING,
        1: logging.INFO,
        2: logging.DEBUG
    }.get(min(verbosity, 2), logging.DEBUG)
    
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler('m3u_updater.log')
        ]
    )
    
    # Set lower log level for urllib3
    logging.getLogger('urllib3').setLevel(logging.WARNING if verbosity < 2 else logging.INFO)

def main():
    # Parse command line arguments
    args = parse_arguments()
    
    # Configure logging
    setup_logging(args.verbose)
    logger = logging.getLogger(__name__)
    
    if args.dry_run:
        logger.info("Modo de prueba activado. No se realizarán cambios reales.")
    
    # Ensure output directory exists
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    args = p.parse_args()

    # Process all input sources
    generated_paths = []
    combined_parts = []
    
    # Process URLs
    for idx, url in enumerate(args.url, 1):
        try:
            logger.info(f"Processing URL {idx}/{len(args.url)}: {url}")
            
            # Download the M3U content
            logger.debug(f"Downloading {url}")
            try:
                text = try_download_with_gateways(url, timeout=args.timeout)
            except Exception as e:
                logger.error(f"Failed to download {url}: {str(e)}")
                continue
                
            # Generate output filename
            base_name = safe_name_from_url(url, f"source_{idx}.m3u")
            out_name = f"{Path(base_name).stem}_converted.m3u"
            out_path = out_dir / out_name
            
            # Process the content
            new_text = replace_acestream_and_existing(text, args.host, args.port)
            
            # Write the file
            if not args.dry_run:
                if out_path.exists() and args.backup:
                    backup_path = backup_file(out_path)
                    logger.info(f"Created backup: {backup_path}")
                
                out_path.write_text(new_text, encoding="utf-8")
                logger.info(f"Saved: {out_path}")
            else:
                logger.info(f"[DRY RUN] Would save to: {out_path}")
            
            generated_paths.append(out_path)
            combined_parts.append(new_text)
            
        except Exception as e:
            logger.error(f"Error processing URL {url}: {str(e)}", exc_info=args.verbose > 1)
    
    # Process local files
    for file_path in args.input:
        try:
            logger.info(f"Processing local file: {file_path}")
            
            fp = Path(file_path)
            if not fp.exists():
                logger.warning(f"Input file not found, skipping: {file_path}")
                continue
                
            # Read and process the file
            text = fp.read_text(encoding="utf-8")
            new_text = replace_acestream_and_existing(text, args.host, args.port)
            
            # Generate output filename
            out_name = f"{fp.stem}_converted.m3u"
            out_path = out_dir / out_name
            
            # Write the file
            if not args.dry_run:
                if out_path.exists() and args.backup:
                    backup_path = backup_file(out_path)
                    logger.info(f"Created backup: {backup_path}")
                
                out_path.write_text(new_text, encoding="utf-8")
                logger.info(f"Saved: {out_path}")
            else:
                logger.info(f"[DRY RUN] Would save to: {out_path}")
            
            generated_paths.append(out_path)
            combined_parts.append(new_text)
            
        except Exception as e:
            logger.error(f"Error processing file {file_path}: {str(e)}", exc_info=args.verbose > 1)
    
    # Create combined playlist if we have any content
    if combined_parts:
        combined = "\n\n".join(part.strip() for part in combined_parts if part.strip())
        combined_path = out_dir / args.combined_name
        
        if not args.dry_run:
            if combined_path.exists() and args.backup:
                backup_path = backup_file(combined_path)
                logger.info(f"Created combined backup: {backup_path}")
            
            combined_path.write_text(combined, encoding="utf-8")
            logger.info(f"Combined playlist saved to: {combined_path}")
            generated_paths.append(combined_path)
        else:
            logger.info(f"[DRY RUN] Would save combined playlist to: {combined_path}")
    
    # Git operations if enabled
    if args.commit and generated_paths and not args.dry_run:
        try:
            msg = f"Update M3U playlists ({now_ts()})"
            git_commit_and_push(generated_paths, msg)
            logger.info("Successfully committed and pushed changes")
        except Exception as e:
            logger.error(f"Git operation failed: {str(e)}", exc_info=args.verbose > 0)
            sys.exit(3)
    elif args.dry_run and args.commit:
        logger.info("[DRY RUN] Would commit and push changes")
    
    logger.info("Processing completed successfully")

if __name__ == "__main__":
    main()
