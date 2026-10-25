"""
Decompila .rpyc/.rpymc a .rpy/.rpym con unrpyc
(https://github.com/CensoredUsername/unrpyc), que está en herramientas/.

  - Ren'Py 8 (Python 3): unrpyc v2 con el Python de este programa.
  - Ren'Py 6/7 (Python 2): unrpyc v1 con el python.exe que trae el juego.

Si falta, unrpyc se descarga de GitHub la primera vez que hace falta.

Solo se decompilan los compilados que no tienen ya su fuente al lado.
"""

import io
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(HERE, "herramientas")
UNRPYC_V2 = os.path.join(TOOLS, "unrpyc-v2.0.4", "unrpyc.py")
UNRPYC_V1 = os.path.join(TOOLS, "unrpyc-v1.3.2", "unrpyc.py")

UNRPYC_URL = "https://github.com/CensoredUsername/unrpyc/archive/refs/tags/{tag}.zip"

COMPILED_EXT = (".rpyc", ".rpymc")


def ensure_unrpyc(script, log=print):
    """
    Descarga la versión de unrpyc de `script` (herramientas/unrpyc-<tag>/)
    si no está. Solo se guarda lo necesario para decompilar.
    """
    if os.path.isfile(script):
        return
    dest = os.path.dirname(script)
    tag = os.path.basename(dest)[len("unrpyc-"):]
    log("  Descargando unrpyc {} (solo la primera vez)...".format(tag))
    with urllib.request.urlopen(UNRPYC_URL.format(tag=tag), timeout=120) as r:
        data = r.read()

    tmp = dest + "_tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for name in z.namelist():
            # "unrpyc-1.3.2/decompiler/x.py" -> "decompiler/x.py"
            rel = name.split("/", 1)[1] if "/" in name else ""
            top = rel.split("/", 1)[0]
            if not rel or rel.endswith("/"):
                continue
            if top not in ("decompiler", "unrpyc.py", "deobfuscate.py", "un.rpyc", "LICENSE"):
                continue
            target = os.path.join(tmp, *rel.split("/"))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as f:
                f.write(z.read(name))
    shutil.rmtree(dest, ignore_errors=True)
    os.replace(tmp, dest)
    if not os.path.isfile(script):
        raise SystemExit("No se pudo instalar unrpyc {}.".format(tag))


def find_missing_sources(root, skip_tl=True):
    """
    Devuelve los .rpyc/.rpymc de `root` que no tienen su .rpy/.rpym.
    """
    rv = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel = os.path.relpath(dirpath, root).replace("\\", "/")
        if skip_tl and rel.split("/")[0] == "tl":
            dirnames[:] = []
            continue
        if rel == ".":
            dirnames[:] = [d for d in dirnames if d.lower() not in ("cache", "saves", "__pycache__")]
        names = set(filenames)
        for fn in filenames:
            if fn.endswith(COMPILED_EXT) and fn[:-1] not in names:
                rv.append(os.path.join(dirpath, fn))
    return sorted(rv)


def _runner(game):
    """
    Devuelve (comando_python, entorno, script_unrpyc, versión).
    """
    major = game.version().split(".")[0]
    if major.isdigit() and int(major) < 8:
        # Python 2 del propio juego, con sus opciones (ver Game.python_cmd).
        env = game.python_env()
        env["PYTHONIOENCODING"] = "utf-8"
        return game.python_cmd(), env, UNRPYC_V1, "v1.3.2"

    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env["PYTHONIOENCODING"] = "utf-8"
    # -E -s: ignora variables de entorno y site-packages del usuario.
    return [sys.executable, "-E", "-s", "-B"], env, UNRPYC_V2, "v2.0.4"


def _run(runner, files, extra):
    python_cmd, env, script, _version = runner
    cmd = python_cmd + [script, "--clobber"] + extra + files
    proc = subprocess.run(cmd, cwd=os.path.dirname(script), env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return proc.stdout.decode("utf-8", "replace")


def decompile(game, root, log=print):
    """
    Decompila en su sitio los compilados de `root` que no tienen fuente.
    Devuelve (decompilados, lista_de_fallidos).
    """
    pending = find_missing_sources(root)
    if not pending:
        return 0, []

    runner = _runner(game)
    script, version = runner[2], runner[3]
    ensure_unrpyc(script, log)

    log("  Decompilando {} archivos .rpyc con unrpyc {}...".format(len(pending), version))

    output = ""
    # Por tandas para no pasarse del límite de longitud de línea de Windows.
    for i in range(0, len(pending), 100):
        output += _run(runner, pending[i:i + 100], [])

    failed = [f for f in pending if not os.path.exists(f[:-1])]
    if failed:
        log("  {} archivos fallaron; reintentando con --try-harder (anti-ofuscación)...".format(len(failed)))
        for i in range(0, len(failed), 100):
            output += _run(runner, failed[i:i + 100], ["--try-harder"])
        failed = [f for f in pending if not os.path.exists(f[:-1])]

    with open(os.path.join(os.path.dirname(os.path.abspath(root)), "_unrpyc.log"), "w", encoding="utf-8") as f:
        f.write(output)

    done = len(pending) - len(failed)
    log("    {} decompilados, {} fallidos.".format(done, len(failed)))
    return done, failed
