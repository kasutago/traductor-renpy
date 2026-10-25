"""
Traductor de juegos Ren'Py - fase 1.

  1. extraer   : descomprime los .rpa del juego en una carpeta aparte y
                 decompila los .rpyc que no tengan su .rpy (unrpyc).
  2. traducir  : genera game/tl/<idioma>/ con el comando "translate" del propio
                 motor Ren'Py que trae el juego (los IDs salen siempre bien).
                 Si el juego solo trae .rpyc, se decompilan antes en la copia
                 de trabajo, porque translate solo lee archivos .rpy.

El juego original no se modifica: todo se hace en una copia de trabajo dentro
de salida/<juego>/. Solo con --instalar se copia la carpeta tl/<idioma> al
juego (y si ya existía, antes se guarda una copia de seguridad).

Uso:
  python traductor.py                       (menú interactivo)
  python traductor.py extraer  "<juego>"
  python traductor.py traducir "<juego>" --idioma spanish [--instalar]
  python traductor.py todo     "<juego>" --idioma spanish [--instalar]
"""

import argparse
import datetime
import os
import re
import shutil
import subprocess
import sys

import decompilar
import rpa
import textos_pantalla

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(HERE, "salida")

# Extensiones que no hacen falta para generar la traducción. No se copian a la
# carpeta de trabajo para ahorrar espacio y tiempo.
MEDIA_EXT = {
    ".png", ".jpg", ".jpeg", ".webp", ".avif", ".gif", ".bmp", ".tga", ".svg",
    ".mp3", ".ogg", ".opus", ".wav", ".flac", ".m4a",
    ".webm", ".mp4", ".mkv", ".avi", ".ogv", ".mpg", ".mpeg", ".m4v",
}

# Carpetas de game/ que no se copian a la carpeta de trabajo.
SKIP_DIRS = {"cache", "saves", "__pycache__"}


def log(msg=""):
    print(msg, flush=True)


# ---------------------------------------------------------------------------
# Localizar el juego y su motor


class Game(object):

    def __init__(self, path):
        path = os.path.abspath(path.strip().lstrip("﻿").strip('"'))

        if os.path.isfile(path):
            path = os.path.dirname(path)
        if os.path.basename(path).lower() == "game" and os.path.isdir(os.path.join(os.path.dirname(path), "renpy")):
            path = os.path.dirname(path)

        if not os.path.isdir(path):
            raise SystemExit("No existe la carpeta: {}".format(path))

        self.root = path
        self.name = os.path.basename(path)
        self.launcher = self._find_launcher()
        self.python = self._find_python()
        self.gamedir = self._find_gamedir()

    def _find_launcher(self):
        # El .py de arranque es el que importa renpy.bootstrap (p. ej. Juego.py).
        for fn in sorted(os.listdir(self.root)):
            if not fn.lower().endswith(".py"):
                continue
            full = os.path.join(self.root, fn)
            try:
                with open(full, "r", encoding="utf-8", errors="replace") as f:
                    if "renpy.bootstrap" in f.read():
                        return full
            except OSError:
                pass
        raise SystemExit("No encuentro el .py de arranque de Ren'Py en {}".format(self.root))

    def _find_python(self):
        lib = os.path.join(self.root, "lib")
        if os.name == "nt":
            plat, exe_name = "windows", "python.exe"
            preferred = ["py3-windows-x86_64", "windows-x86_64", "py2-windows-x86_64", "windows-i686", "py2-windows-i686"]
        else:
            plat, exe_name = "linux", "python"
            preferred = ["py3-linux-x86_64", "linux-x86_64", "py2-linux-x86_64", "linux-i686", "py2-linux-i686"]
        for d in preferred:
            exe = os.path.join(lib, d, exe_name)
            if os.path.isfile(exe):
                return exe
        for dirpath, _dirs, files in os.walk(lib):
            if exe_name in files and plat in os.path.basename(dirpath):
                return os.path.join(dirpath, exe_name)
        raise SystemExit("No encuentro el {} del juego dentro de {} (¿trae versión para {}?)".format(
            exe_name, lib, "Windows" if os.name == "nt" else "Linux"))

    def _find_gamedir(self):
        # Misma lógica que path_to_gamedir() del .py de arranque.
        name = os.path.splitext(os.path.basename(self.launcher))[0]
        candidates = [name]
        n = name
        while n:
            prefix, n = n[0], n[1:]
            if prefix in " _":
                candidates.append(n)
        candidates += ["game", "data"]
        for c in candidates:
            if c == "renpy":
                continue
            d = os.path.join(self.root, c)
            if os.path.isdir(d):
                return d
        raise SystemExit("No encuentro la carpeta game/ en {}".format(self.root))

    def _old_pythonlib(self):
        """
        Ren'Py 6/7 antiguos traen la biblioteca de Python aparte, en
        lib/pythonlib2.7 (compilada en .pyo y sin site.py). Devuelve esa ruta
        o None.
        """
        lib = os.path.join(self.root, "lib")
        try:
            names = sorted(os.listdir(lib))
        except OSError:
            return None
        for n in names:
            if n.startswith("pythonlib2") and os.path.isdir(os.path.join(lib, n)):
                return os.path.join(lib, n)
        return None

    def python_cmd(self):
        """
        Comando para ejecutar el Python del juego. En los antiguos hace falta
        -S (no hay site.py) y -O (la biblioteca está en .pyo).
        """
        if self._old_pythonlib():
            return [self.python, "-S", "-O", "-B"]
        return [self.python, "-B"]

    def python_env(self, saves=None):
        """
        Entorno para ejecutar el Python del juego. Con `saves`, todo lo que el
        motor guarda (persistent, copias de scripts) va a esa carpeta y no a
        %APPDATA%\\RenPy, que comparte con el juego real del usuario.
        """
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        env.pop("PYTHONHOME", None)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        old = self._old_pythonlib()
        if old:
            env["PYTHONPATH"] = old
        if saves:
            os.makedirs(saves, exist_ok=True)
            # Ren'Py 7.x/8 usa RENPY_PATH_TO_SAVES; los antiguos solo APPDATA.
            env["RENPY_PATH_TO_SAVES"] = saves
            env["APPDATA"] = saves
            env["RENPY_DISABLE_BACKUPS"] = "I take responsibility for this."
        return env

    def version(self):
        # Ren'Py 7.4+ / 8: vc_version.py tiene  version = '8.5.3.26021301'
        vc = os.path.join(self.root, "renpy", "vc_version.py")
        vc_number = ""
        try:
            with open(vc, "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("version"):
                        return line.split("=", 1)[1].strip().lstrip("uU").strip("'\"")
                    if line.startswith("vc_version"):
                        vc_number = line.split("=", 1)[1].strip()
        except OSError:
            pass

        # Ren'Py 6 / 7 antiguos: renpy/__init__.py tiene
        #   version_tuple = (6, 99, 12, 4, vc_version)
        init = os.path.join(self.root, "renpy", "__init__.py")
        try:
            with open(init, "r", encoding="utf-8", errors="replace") as f:
                m = re.search(r"^\s*version_tuple\s*=\s*\(([^)]*)\)", f.read(), re.M)
            if m:
                parts = [p.strip() for p in m.group(1).split(",") if p.strip()]
                nums = [p for p in parts if p.isdigit()]
                if nums:
                    if vc_number.isdigit() and len(nums) < len(parts):
                        nums.append(vc_number)
                    return ".".join(nums)
        except OSError:
            pass
        return "desconocida"


def out_dir_for(game, out_root):
    return os.path.join(out_root, game.name)


# ---------------------------------------------------------------------------
# 1. Extraer


def extract(game, dest, only_scripts=False):
    """
    Extrae todos los .rpa de game.gamedir dentro de `dest`, respetando la ruta
    interna de cada archivo. Devuelve el número de archivos extraídos.
    """
    archives = rpa.find_archives(game.gamedir)
    if not archives:
        log("  El juego no tiene archivos .rpa.")
        return 0

    total = 0
    for path in archives:
        arc = rpa.RPAArchive(path)
        names = arc.names()
        if only_scripts:
            names = [n for n in names if os.path.splitext(n)[1].lower() not in MEDIA_EXT]
        log("  {} ({}, {} archivos)".format(os.path.relpath(path, game.root), arc.version, len(names)))
        for i, name in enumerate(names, 1):
            arc.extract_to(name, dest)
            if i % 200 == 0 or i == len(names):
                log("    {}/{}".format(i, len(names)))
        total += len(names)
    return total


def cmd_extract(game, out_root):
    dest = os.path.join(out_dir_for(game, out_root), "extraido")
    log("Extrayendo .rpa en: {}".format(dest))
    n = extract(game, dest)
    log("  {} archivos extraídos.".format(n))

    # Los .rpyc sueltos del juego sin su .rpy también se decompilan aquí.
    loose = decompilar.find_missing_sources(game.gamedir)
    if loose:
        log("  Copiando {} .rpyc sueltos sin .rpy para decompilarlos...".format(len(loose)))
        for src in loose:
            dst = os.path.join(dest, os.path.relpath(src, game.gamedir))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)

    _done, failed = decompilar.decompile(game, dest, log)
    report_failed(failed, dest)
    log("Listo: archivos en {}".format(dest))
    return dest


def report_failed(failed, root):
    if not failed:
        return
    log("  AVISO: {} archivos .rpyc no se pudieron decompilar y NO se traducirán:".format(len(failed)))
    for m in failed[:20]:
        log("    - {}".format(os.path.relpath(m, root)))
    if len(failed) > 20:
        log("    ... y {} más".format(len(failed) - 20))
    log("  Detalles en _unrpyc.log")


# ---------------------------------------------------------------------------
# 2. Traducir


def build_workspace(game, work):
    """
    Crea <work>/game con los scripts del juego: primero el contenido de los
    .rpa y encima los archivos sueltos (que en Ren'Py tienen prioridad sobre
    los archivados). No se copian imágenes, audio ni vídeo.
    """
    if os.path.isdir(work):
        shutil.rmtree(work)
    wgame = os.path.join(work, os.path.basename(game.gamedir))
    os.makedirs(wgame)

    log("  Extrayendo scripts de los .rpa...")
    extract(game, wgame, only_scripts=True)

    log("  Copiando archivos sueltos del juego...")
    copied = 0
    for dirpath, dirnames, filenames in os.walk(game.gamedir):
        rel = os.path.relpath(dirpath, game.gamedir)
        if rel == ".":
            dirnames[:] = [d for d in dirnames if d.lower() not in SKIP_DIRS]
        for fn in filenames:
            ext = os.path.splitext(fn)[1].lower()
            if ext == ".rpa" or ext == ".rpi" or ext in MEDIA_EXT:
                continue
            dst_dir = os.path.join(wgame, rel)
            os.makedirs(dst_dir, exist_ok=True)
            shutil.copy2(os.path.join(dirpath, fn), os.path.join(dst_dir, fn))
            copied += 1
    log("    {} archivos copiados.".format(copied))

    return wgame


def count_sources(wgame):
    count = 0
    for dirpath, dirnames, filenames in os.walk(wgame):
        if os.path.relpath(dirpath, wgame).replace("\\", "/").split("/")[0] == "tl":
            dirnames[:] = []
            continue
        count += sum(1 for fn in filenames if fn.endswith((".rpy", ".rpym")))
    return count


def run_translate(game, work, language, empty):
    """
    Ejecuta el motor del juego:  python.exe -B <Juego.py> <work> translate <idioma>
    El motor se usa desde su ubicación original pero con la carpeta de trabajo
    como basedir, así que todo lo que escribe va a la carpeta de trabajo.
    """
    env = game.python_env(saves=os.path.join(work, "_saves"))

    cmd = game.python_cmd() + [game.launcher, work, "translate", language]
    if empty:
        cmd.append("--empty")

    log("  Ejecutando el motor Ren'Py {} del juego...".format(game.version()))
    proc = subprocess.run(cmd, cwd=work, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    output = proc.stdout.decode("utf-8", "replace")

    with open(os.path.join(work, "_motor.log"), "w", encoding="utf-8") as f:
        f.write(" ".join('"{}"'.format(c) for c in cmd) + "\n\n" + output)

    return proc.returncode, output


LANGUAGE_NAMES = {
    "spanish": u"Español", "español": u"Español", "espanol": u"Español",
    "english": u"English", "french": u"Français", "german": u"Deutsch",
    "italian": u"Italiano", "portuguese": u"Português", "russian": u"Русский",
}

BUTTON_FILE = "traductor_idioma.rpy"
BUTTON_TEMPLATE = os.path.join(HERE, "plantillas", BUTTON_FILE)


def write_button(game, language):
    """
    Añade game/traductor_idioma.rpy: un archivo nuevo e independiente que
    pone el botón de idioma. No toca ningún archivo del juego.
    """
    name = LANGUAGE_NAMES.get(language.lower(), language.replace("_", " ").title())
    with open(BUTTON_TEMPLATE, "r", encoding="utf-8") as f:
        code = f.read()
    code = code.replace("@@IDIOMA@@", language).replace("@@NOMBRE@@", name.replace('"', ""))

    dst = os.path.join(game.gamedir, BUTTON_FILE)
    with open(dst, "w", encoding="utf-8") as f:
        f.write(code)
    # Si había un .rpyc de una instalación anterior, se borra para que Ren'Py
    # lo vuelva a compilar.
    old_rpyc = dst + "c"
    if os.path.exists(old_rpyc):
        os.remove(old_rpyc)
    log("  Botón de idioma añadido: {}".format(dst))


def lint_check(game, out_root):
    """
    Comprueba con el lint del motor del juego que todo compila, usando una
    copia de trabajo (con el tl/ actual del juego) para no tocar el juego.
    Devuelve (ok, texto_del_error).
    """
    work = os.path.join(out_dir_for(game, out_root), "_trabajo")
    wgame = build_workspace(game, work)
    decompilar.decompile(game, wgame, log)

    env = game.python_env(saves=os.path.join(work, "_saves"))
    cmd = game.python_cmd() + [game.launcher, work, "lint"]
    proc = subprocess.run(cmd, cwd=work, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    output = proc.stdout.decode("utf-8", "replace")

    for name in ("errors.txt", "traceback.txt"):
        p = os.path.join(work, name)
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8-sig", errors="replace") as f:
                return False, f.read()
    if proc.returncode != 0:
        return False, output[-3000:]
    return True, ""


def install(tl_src, game, language, out_root, button=True):
    dst = os.path.join(game.gamedir, "tl", language)
    if os.path.exists(dst):
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = os.path.join(out_dir_for(game, out_root), "copias_tl", "{}-{}".format(language, stamp))
        log("  Ya existía {}; copia de seguridad en {}".format(dst, backup))
        shutil.copytree(dst, backup)
        shutil.rmtree(dst)
    shutil.copytree(tl_src, dst)
    log("  Traducción instalada en: {}".format(dst))
    if button:
        write_button(game, language)


EXPORT_README = u"""Traducción al español de {juego}
Generada con el Traductor Ren'Py para la versión de Ren'Py {version}.

CÓMO INSTALARLA
1. Copia la carpeta "game" de este paquete dentro de la carpeta del juego
   (donde está el .exe). Windows preguntará si quieres combinar las carpetas:
   di que sí.
2. Abre el juego. Arriba a la derecha del menú principal aparece el botón de
   idioma (también se cambia con Mayús+L).

No hace falta nada más: no se modifica ningún archivo del juego, solo se
añaden game/tl/{idioma}/ y game/traductor_idioma.rpy.

PARA QUITARLA
Borra la carpeta game/tl/{idioma} y los archivos game/traductor_idioma.rpy y
game/traductor_idioma.rpyc.

NOTA: si tu versión del juego es distinta, las frases nuevas o cambiadas
saldrán en inglés; el resto se verá traducido.
"""


def cmd_export(game, out_root, language):
    """
    Crea un paquete para compartir la traducción: una carpeta game/ con
    tl/<idioma> y el botón, un LEEME.txt y un .zip con todo. No incluye nada
    del juego original.
    """
    tl_src = os.path.join(game.gamedir, "tl", language)
    button_src = os.path.join(game.gamedir, BUTTON_FILE)
    if not os.path.isdir(tl_src):
        raise SystemExit("El juego no tiene la traducción instalada ({}).".format(tl_src))

    name = "{} - traduccion {}".format(game.name, language)
    base = os.path.join(out_dir_for(game, out_root), "exportar")
    pkg = os.path.join(base, name)
    if os.path.isdir(pkg):
        shutil.rmtree(pkg)

    gdir = os.path.join(pkg, os.path.basename(game.gamedir))
    # Solo los .rpy: los .rpyc los genera el juego al abrirse.
    shutil.copytree(tl_src, os.path.join(gdir, "tl", language),
                    ignore=shutil.ignore_patterns("*.rpyc", "*.rpymc", "__pycache__"))
    if os.path.exists(button_src):
        shutil.copy2(button_src, gdir)
    else:
        log("  Aviso: el juego no tiene el botón instalado; se exporta sin él.")

    with open(os.path.join(pkg, "LEEME.txt"), "w", encoding="utf-8") as f:
        f.write(EXPORT_README.format(juego=game.name, version=game.version(), idioma=language))

    zip_path = shutil.make_archive(pkg, "zip", root_dir=pkg)

    files = sum(len(f) for _, _, f in os.walk(pkg))
    size = os.path.getsize(zip_path)
    log("Exportado ({} archivos): {}".format(files, pkg))
    log("Zip para compartir ({:.1f} MB): {}".format(size / 1048576.0, zip_path))
    return pkg, zip_path


def cmd_install(game, out_root, language, button=True):
    src = os.path.join(out_dir_for(game, out_root), "tl", language)
    if not os.path.isdir(src):
        raise SystemExit("No hay traducción generada en {}. Usa antes 'traducir'.".format(src))
    log("Instalando traducción '{}' en el juego...".format(language))
    install(src, game, language, out_root, button)
    log("Listo. En el juego: botón arriba a la derecha o Mayús+L para cambiar de idioma.")


def cmd_translate(game, out_root, language, empty=False, do_install=False, hint=True):
    base = out_dir_for(game, out_root)
    work = os.path.join(base, "_trabajo")

    log("Preparando carpeta de trabajo: {}".format(work))
    wgame = build_workspace(game, work)

    # El comando translate solo lee .rpy: los .rpyc sin fuente se decompilan.
    _done, failed = decompilar.decompile(game, wgame, log)
    report_failed(failed, wgame)
    log("  {} archivos .rpy listos para traducir.".format(count_sources(wgame)))

    code, output = run_translate(game, work, language, empty)

    tl_work = os.path.join(wgame, "tl", language)
    if code != 0 or not os.path.isdir(tl_work):
        log(output[-3000:])
        tb = os.path.join(work, "traceback.txt")
        if os.path.exists(tb):
            log("Mira el error completo en: {}".format(tb))
        raise SystemExit("El motor de Ren'Py falló (código {}).".format(code))

    n = textos_pantalla.write_screen_strings(wgame, language, empty)
    if n:
        log("  {} textos de pantallas sin _() añadidos en {}.".format(n, textos_pantalla.OUT_NAME))

    dest = os.path.join(base, "tl", language)
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    shutil.copytree(tl_work, dest)

    files = sum(len(f) for _, _, f in os.walk(dest))
    log("Listo: traducción generada en {} ({} archivos).".format(dest, files))

    if do_install:
        install(dest, game, language, out_root)
    elif hint:
        log("Cuando la tengas traducida, instálala con la opción 'instalar'")
        log("(copia la carpeta a {} y añade el botón de idioma).".format(os.path.join(game.gamedir, "tl", language)))

    return dest


# ---------------------------------------------------------------------------
# Interfaz


def ask(prompt, default=None):
    suffix = " [{}]".format(default) if default else ""
    try:
        value = input("{}{}: ".format(prompt, suffix)).strip().strip('"')
    except EOFError:
        value = ""
    return value or (default or "")


def interactive():
    log("=== Traductor Ren'Py ===")
    path = ask("Arrastra aquí la carpeta (o el .exe) del juego")
    if not path:
        return
    game = Game(path)
    log("Juego: {}  (Ren'Py {})".format(game.name, game.version()))
    log("")
    log("  1) Extraer y crear botón")
    op = ask("Opción", "1")
    if op != "1":
        return
    log("")
    run_all(game, DEFAULT_OUT, "spanish")


def run_all(game, out_root, language):
    """
    Todo en orden: extraer .rpa y decompilar .rpyc, generar tl/<idioma> con el
    motor del juego e instalarlo en el juego junto con el botón de idioma.
    """
    log("[1/3] Extrayendo archivos")
    cmd_extract(game, out_root)
    log("")
    log("[2/3] Generando la carpeta de traducción '{}'".format(language))
    cmd_translate(game, out_root, language, hint=False)
    log("")
    log("[3/3] Instalando en el juego")
    cmd_install(game, out_root, language)
    log("")
    log("Para traducir, edita los archivos de: {}".format(os.path.join(game.gamedir, "tl", language)))


def main():
    if len(sys.argv) == 1:
        interactive()
        return

    ap = argparse.ArgumentParser(description="Traductor de juegos Ren'Py")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("juego", help="Carpeta del juego (o su .exe)")
        p.add_argument("--salida", default=DEFAULT_OUT, help="Carpeta de salida (por defecto: salida/)")

    p = sub.add_parser("completo", help="Todo en uno: extraer, generar tl/<idioma> e instalar con el botón")
    common(p)
    p.add_argument("--idioma", default="spanish", help="Nombre del idioma (por defecto: spanish)")

    p = sub.add_parser("extraer", help="Descomprime los .rpa del juego")
    common(p)

    p = sub.add_parser("exportar", help="Crea un .zip con la traducción y el botón para compartir")
    common(p)
    p.add_argument("--idioma", default="spanish", help="Nombre del idioma (por defecto: spanish)")

    for name, help_ in (("traducir", "Genera tl/<idioma> con el motor del juego"),
                        ("todo", "Extrae y genera la traducción")):
        p = sub.add_parser(name, help=help_)
        common(p)
        p.add_argument("--idioma", default="spanish", help="Nombre del idioma (por defecto: spanish)")
        p.add_argument("--vacio", action="store_true", help="Deja las líneas traducidas vacías en vez de copiar el original")
        p.add_argument("--instalar", action="store_true", help="Instala tl/<idioma> y el botón de idioma en el juego")

    p = sub.add_parser("instalar", help="Instala la traducción ya generada y el botón de idioma")
    common(p)
    p.add_argument("--idioma", default="spanish", help="Nombre del idioma (por defecto: spanish)")
    p.add_argument("--sin-boton", action="store_true", help="Solo copia tl/<idioma>, sin añadir el botón")

    args = ap.parse_args()
    game = Game(args.juego)
    log("Juego: {}  (Ren'Py {})".format(game.name, game.version()))

    if args.cmd == "exportar":
        cmd_export(game, args.salida, args.idioma)
    if args.cmd == "completo":
        run_all(game, args.salida, args.idioma)
    if args.cmd in ("extraer", "todo"):
        cmd_extract(game, args.salida)
    if args.cmd in ("traducir", "todo"):
        cmd_translate(game, args.salida, args.idioma, empty=args.vacio, do_install=args.instalar)
    if args.cmd == "instalar":
        cmd_install(game, args.salida, args.idioma, button=not args.sin_boton)


if __name__ == "__main__":
    try:
        main()
    finally:
        if len(sys.argv) == 1:
            ask("\nPulsa Enter para salir")
