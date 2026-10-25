"""
Revisión de lo ya traducido: recorre las parejas original / traducción de
tl/<idioma>, marca las líneas sospechosas y guarda correcciones a mano.

Solo se cambia el contenido del literal entre comillas de una línea, igual que
hace tl_editor (el archivo nunca cambia de número de líneas).
"""

import io
import os
import re

import tl_editor
import traducir_auto
from tl_editor import MARK_RE, OLD_NEW_RE, PROTECT_RE, TRANSLATE_RE

# Nombre mostrado de cada tipo de problema (el orden es el del filtro).
PROBLEMAS = [
    ("sin_traducir", "Sin traducir"),
    ("formal", "Usted / su (trato formal)"),
    ("marcadores", "Variables o etiquetas distintas"),
    ("basura", "Texto basura / repetición"),
    ("corta", "Traducción muy corta"),
    ("larga", "Traducción muy larga"),
    ("ingles", "Palabras en inglés"),
    ("signos", "Faltan ¿ ¡"),
]
NOMBRES = dict(PROBLEMAS)

ENGLISH_RE = re.compile(r"\b(?:the|and|with|that|this|have|would|your|there|what|because)\b")


class Entry(object):
    __slots__ = ("path", "rel", "lineno", "start", "end", "who", "src", "dst", "problemas")

    def __init__(self, path, rel, lineno, start, end, who, src, dst):
        self.path, self.rel, self.lineno = path, rel, lineno
        self.start, self.end, self.who = start, end, who
        self.src, self.dst = src, dst
        self.problemas = diagnosticar(src, dst)


def _plain(body):
    """Texto sin variables, etiquetas ni escapes (para comparar)."""
    masked, _marks = tl_editor.mask(body)
    return MARK_RE.sub("", masked).strip()


def _marks(body):
    """Variables, etiquetas y formatos (sin contar comillas escapadas)."""
    return sorted(m for m in PROTECT_RE.findall(body) if m not in ('\\"', "\\'"))


def diagnosticar(src, dst):
    rv = []
    ps, pd = _plain(src), _plain(dst)
    if not re.search(r"[A-Za-z]", ps):
        return rv
    if src == dst:
        words = traducir_auto.translatable_words(ps)
        # Un nombre suelto ("Sarah...", "(Jack...)") se queda igual.
        if words and not (len(words) <= 2 and all(w[:1].isupper() for w in words) and not ps.startswith("(")):
            rv.append("sin_traducir")
        return rv
    if _marks(src) != _marks(dst):
        rv.append("marcadores")
    if traducir_auto.JUNK_RE.search(pd) and not traducir_auto.JUNK_SRC_RE.search(ps) \
            or traducir_auto.runaway(ps, pd):
        rv.append("basura")
    hard, soft = traducir_auto.MarianBackend._problems(traducir_auto.MarianBackend, ps, pd)
    if hard or soft:
        rv.append("formal")
    if len(ps) > 40 and len(pd) < 0.45 * len(ps):
        rv.append("corta")
    if len(pd) > 2.4 * len(ps) + 30:
        rv.append("larga")
    if len(ENGLISH_RE.findall(pd.lower())) >= 2:
        rv.append("ingles")
    if ("?" in pd and "¿" not in pd and "?" in ps) or ("!" in pd and "¡" not in pd and "!" in ps):
        rv.append("signos")
    return rv


def collect(tl_dir):
    """
    Todas las parejas (original, traducción) de tl_dir, en orden de archivo.
    """
    entries = []
    for path in traducir_auto.collect(tl_dir):
        rel = os.path.relpath(path, tl_dir)
        with io.open(path, "r", encoding="utf-8", newline="") as f:
            text = f.read()
        if text.startswith("﻿"):
            text = text[1:]
        mode = None
        last_comment = last_old = None
        for i, raw in enumerate(text.splitlines(True)):
            line = raw.rstrip("\r\n")
            if line and not line[0].isspace():
                m = TRANSLATE_RE.match(line)
                mode = ("strings" if m.group(1) == "strings" else "say") if m else None
                last_comment = last_old = None
                continue
            stripped = line.strip()
            if mode == "say":
                if stripped.startswith("#"):
                    last_comment = stripped[1:].strip()
                    continue
                lit, who = tl_editor._say_literal(line)
                if lit is None or last_comment is None:
                    continue
                clit, _w = tl_editor._say_literal("    " + last_comment)
                if clit is not None:
                    entries.append(Entry(path, rel, i, lit.start() + 1, lit.end() - 1, who,
                                         clit.group(0)[1:-1], lit.group(0)[1:-1]))
                last_comment = None
            elif mode == "strings":
                m = OLD_NEW_RE.match(line)
                if not m:
                    continue
                if m.group(2) == "old":
                    last_old = m.group(4)
                elif last_old is not None:
                    start = len(m.group(1)) + 3 + len(m.group(3)) + 1
                    entries.append(Entry(path, rel, i, start, start + len(m.group(4)) - 2, "",
                                         last_old[1:-1], m.group(4)[1:-1]))
                    last_old = None
    return entries


def validar(texto):
    """Devuelve un mensaje de error si `texto` no sirve como contenido de un literal."""
    if "\n" in texto or "\r" in texto:
        return "El texto no puede tener saltos de línea (usa \\n)."
    if re.search(r'(?<!\\)"', re.sub(r"\\\\", "", texto)):
        return 'Las comillas dentro del texto deben escribirse como \\".'
    if texto.count("{") != texto.count("}") and "{{" not in texto:
        return "Las llaves { } de las etiquetas no están balanceadas."
    return None


def guardar(entry, nuevo):
    """
    Escribe `nuevo` como traducción de `entry`. Devuelve None si salió bien o un
    mensaje de error. Comprueba antes que la línea del archivo sigue igual.
    """
    err = validar(nuevo)
    if err:
        return err
    with io.open(entry.path, "r", encoding="utf-8", newline="") as f:
        text = f.read()
    bom = text.startswith("﻿")
    if bom:
        text = text[1:]
    lines = text.splitlines(True)
    raw = lines[entry.lineno]
    if raw[entry.start:entry.end] != entry.dst:
        return "El archivo ha cambiado desde que se cargó. Vuelve a cargar la revisión."
    lines[entry.lineno] = raw[:entry.start] + nuevo + raw[entry.end:]
    tl_editor.write(entry.path, lines, bom)
    entry.end = entry.start + len(nuevo)
    entry.dst = nuevo
    entry.problemas = diagnosticar(entry.src, nuevo)
    return None
