"""
Textos de pantallas que el comando "translate" de Ren'Py no recoge.

Ren'Py solo extrae los textos marcados con _("..."), pero al jugar traduce
cualquier texto que se muestre con text / textbutton / label si existe una
traducción para él. Muchos juegos escriben `textbutton "New Game"` sin _(),
así que aquí se buscan esos textos y se añaden a tl/<idioma>/ en un archivo
propio, sin repetir los que Ren'Py ya generó (un "old" repetido es un error).

Si el archivo ya existía, se conservan las traducciones hechas a mano.
"""

import io
import os
import re

OUT_NAME = "zz_textos_pantallas.rpy"

_STR = r'"(?:[^"\\\n]|\\.)*"|\'(?:[^\'\\\n]|\\.)*\''

# text "..." / textbutton "..." / label "..." / tooltip "..." en una pantalla.
SCREEN_TEXT = re.compile(r'^\s*(?:text|textbutton|label|tooltip)\s+(' + _STR + r')')

OLD_NEW = re.compile(r'^\s*(old|new)\s+(' + _STR + r')\s*$')


def _to_double_quoted(lit):
    """
    Devuelve el literal con comillas dobles (Ren'Py compara el valor, no la
    forma de escribirlo).
    """
    if lit.startswith('"'):
        return lit
    body = lit[1:-1].replace("\\'", "'").replace('"', '\\"')
    return '"' + body + '"'


def _read(path):
    with io.open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        return f.read()


def _source_files(gamedir):
    for dirpath, dirnames, filenames in os.walk(gamedir):
        rel = os.path.relpath(dirpath, gamedir).replace("\\", "/")
        if rel.split("/")[0] == "tl":
            dirnames[:] = []
            continue
        for fn in sorted(filenames):
            if fn.endswith((".rpy", ".rpym")):
                yield os.path.join(dirpath, fn)


def collect_screen_texts(gamedir):
    """
    Devuelve [(literal, "archivo:línea"), ...] sin repetidos, en orden.
    """
    seen = set()
    rv = []
    for path in _source_files(gamedir):
        rel = os.path.relpath(path, os.path.dirname(gamedir)).replace("\\", "/")
        for n, line in enumerate(_read(path).splitlines(), 1):
            m = SCREEN_TEXT.match(line)
            if not m:
                continue
            lit = _to_double_quoted(m.group(1))
            if lit in ('""',) or lit in seen:
                continue
            # Textos sin letras (números, símbolos, "[variable]") no se traducen.
            plain = re.sub(r'\[[^\]]*\]|\{[^}]*\}', '', lit[1:-1])
            if not re.search(r'[^\W\d_]', plain):
                continue
            seen.add(lit)
            rv.append((lit, "{}:{}".format(rel, n)))
    return rv


def existing_translations(tl_dir):
    """
    Lee los pares old/new de tl_dir. Devuelve (olds_de_otros_archivos,
    {old: new} de nuestro archivo).
    """
    others = set()
    ours = {}
    for dirpath, _dirnames, filenames in os.walk(tl_dir):
        for fn in filenames:
            if not fn.endswith((".rpy", ".rpym")):
                continue
            path = os.path.join(dirpath, fn)
            is_ours = (fn == OUT_NAME and os.path.normpath(dirpath) == os.path.normpath(tl_dir))
            old = None
            for line in _read(path).splitlines():
                m = OLD_NEW.match(line)
                if not m:
                    continue
                kind, lit = m.group(1), _to_double_quoted(m.group(2))
                if kind == "old":
                    old = lit
                    if not is_ours:
                        others.add(lit)
                elif old is not None:
                    if is_ours:
                        ours[old] = lit
                    old = None
    return others, ours


def write_screen_strings(gamedir, language, empty=False):
    """
    Escribe tl/<idioma>/zz_textos_pantallas.rpy. Devuelve cuántos textos tiene.
    """
    tl_dir = os.path.join(gamedir, "tl", language)
    texts = collect_screen_texts(gamedir)
    others, ours = existing_translations(tl_dir)

    entries = [(lit, where) for lit, where in texts if lit not in others]
    path = os.path.join(tl_dir, OUT_NAME)

    if not entries:
        if os.path.exists(path):
            os.remove(path)
        return 0

    out = [
        "# Textos de pantallas sin _() que Ren'Py no extrae solo.",
        "# Generado por el Traductor Ren'Py. Se pueden traducir igual que el resto.",
        "",
        "translate {} strings:".format(language),
        "",
    ]
    for lit, where in entries:
        new = ours.get(lit, '""' if empty else lit)
        out.append("    # {}".format(where))
        out.append("    old {}".format(lit))
        out.append("    new {}".format(new))
        out.append("")

    os.makedirs(tl_dir, exist_ok=True)
    with io.open(path, "w", encoding="utf-8-sig", newline="\n") as f:
        f.write("\n".join(out))
    return len(entries)
