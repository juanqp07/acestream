#!/usr/bin/env python3
"""
update_m3u_acestream.py


Lee un archivo M3U (por defecto `playlist.m3u`) y reemplaza URIs del tipo:
acestream://2773b39926d15dd3d9495d94c4050604792d7031
por
http://127.0.0.1:6878/2773b39926d15dd3d9495d94c4050604792d7031


Opciones:
--input PATH fichero m3u (por defecto playlist.m3u)
--host HOST host para el proxy (por defecto 127.0.0.1)
--port PORT puerto para el proxy (por defecto 6878)
--backup / --no-backup crear copia de seguridad antes de sobrescribir (por defecto sí)
--commit / --no-commit si se debe realizar git add/commit/push (por defecto sí)


Diseñado para correr dentro del repositorio (GitHub Actions hará checkout).
"""


import re
import argparse
from pathlib import Path
import shutil
import datetime
import subprocess
import sys


PATTERN = re.compile(r"acestream://([0-9A-Fa-f]{40})")




def now_ts():
return datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")




def replace_acestream(text: str, host: str, port: int) -> str:
replacement = rf"http://{host}:{port}/\1"
return PATTERN.sub(replacement, text)




def backup_file(path: Path) -> Path:
ts = now_ts()
dest = path.with_name(path.name + f".bak.{ts}")
shutil.copy2(path, dest)
return dest




def git_commit_and_push(path: Path, message: str) -> None:
# Configurar usuario en caso de que no exista
subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=False)
subprocess.run([
"git",
"config",
"user.email",
"41898282+github-actions[bot]@users.noreply.github.com",
], check=False)


subprocess.run(["git", "add", str(path)], check=True)
# Comprueba si hay cambios staged
try:
subprocess.run(["git", "diff", "--cached", "--exit-code"], check=True)
# Si exit_code == 0 no hay cambios en staged => no commit
print("No hay cambios para commitear.")
return
except subprocess.CalledProcessError:
# diff --cached devolvió != 0 -> hay cambios
pass


subprocess.run(["git", "commit", "-m", message], check=True)
# push (en Actions el token GITHUB_TOKEN suele estar disponible)
subprocess.run(["git", "push"], check=True)




def main():
p = argparse.ArgumentParser()
main()
