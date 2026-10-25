"""
Lectura y reescritura segura de los archivos de tl/<idioma>/.

Solo se cambia el CONTENIDO de un literal entre comillas en dos tipos de
línea, siempre dentro de la misma línea (el archivo nunca cambia de número de
líneas):

  translate <idioma> <id>:          (bloque de diálogo)
      # lois "Chris, honey!"        <- comentario: no se toca
      lois "Chris, honey!"          <- se traduce el texto de las comillas

  translate <idioma> strings:
      old "New Game"                <- no se toca
      new "New Game"                <- se traduce el texto de las comillas

Una línea solo se considera "pendiente" si sigue igual que el original (la
del comentario o el old). Así se respetan las traducciones ya hechas y se
puede reanudar.

Antes de traducir, las etiquetas {i}, variables [nombre], escapes \\n, \\" y
formatos %s se sustituyen por marcadores ⟦0⟧, ⟦1⟧... que se restauran
después. Si falta o sobra algún marcador, la traducción se descarta.
"""

import io
import re

STR_RE = re.compile(r'"(?:[^"\\]|\\.)*"')
TRANSLATE_RE = re.compile(r'^translate\s+\S+\s+(\S+?)\s*:\s*$')
OLD_NEW_RE = re.compile(r'^(\s*)(old|new)(\s+)("(?:[^"\\]|\\.)*")\s*$')

# Primeras palabras de líneas de un bloque que no son diálogo.
NOT_SAY = {
    "voice", "play", "queue", "stop", "show", "hide", "scene", "with", "window",
    "nvl", "pause", "call", "jump", "return", "$", "python", "camera", "at",
    "define", "default", "image", "translate", "label", "menu", "if", "elif",
    "else", "while", "pass", "init", "transform", "screen", "style", "renpy",
}

# Lo que hay que proteger dentro del literal (en su forma escrita).
PROTECT_RE = re.compile(
    r'\{\{|\[\['                    # llaves/corchetes escapados
    r'|\{[^{}]*\}'                  # etiquetas de texto {i} {color=#f00} {w=.5}
    r'|\[[^\[\]]*\]'                # interpolación [nombre]
    r'|\\.'                         # escapes \n \" \\ \t
    r'|%\([^)]*\)[-#0 +]*\d*(?:\.\d+)?[sdifr]'   # %(nombre)s
    r'|%[-#0 +]*\d*(?:\.\d+)?[sdifr%]'           # %s %d %%
    r'|%[aAbBcHIjmMpSUwWxXyYZ](?![a-z])'         # fechas: %b %H %M
)

MARK_RE = re.compile(r'⟦(\d+)⟧')

FILE_RE = re.compile(r'^[\w./\\-]+\.(png|jpe?g|webp|gif|ogg|opus|mp3|wav|ttf|otf|rpy|webm|mp4|txt|json)$', re.I)


class Item(object):
    """
    Un texto traducible. `body` es el contenido del literal (sin comillas,
    con los escapes tal cual están escritos).
    """
    __slots__ = ("path", "lineno", "start", "end", "body", "who", "kind", "masked", "marks")

    def __init__(self, path, lineno, start, end, body, who, kind):
        self.path = path
        self.lineno = lineno      # índice 0 dentro de la lista de líneas
        self.start = start        # posición del contenido dentro de la línea
        self.end = end
        self.body = body
        self.who = who
        self.kind = kind          # "say" o "string"
        self.masked, self.marks = mask(body)


def mask(body):
    marks = []

    def repl(m):
        marks.append(m.group(0))
        return "⟦{}⟧".format(len(marks) - 1)

    return PROTECT_RE.sub(repl, body), marks


def is_translatable(item):
    plain = MARK_RE.sub("", item.masked)
    if not re.search(r"[A-Za-z]", plain):
        return False
    if FILE_RE.match(item.body.strip()):
        return False
    return True


def _say_literal(line):
    """
    Devuelve (match_del_literal_de_texto, quien) para una línea de diálogo, o
    (None, None) si la línea no es diálogo.
    """
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None, None
    first = re.match(r'[^\s(]+', stripped)
    if first and first.group(0) in NOT_SAY:
        return None, None

    strings = list(STR_RE.finditer(line))
    if not strings:
        return None, None

    s0 = strings[0]
    before = line[:s0.start()].strip()
    # "Nombre" "texto": el primer literal es quien habla.
    if not before and len(strings) > 1 and not line[s0.end():strings[1].start()].strip():
        return strings[1], s0.group(0)[1:-1]
    who = before.split()[0] if before else ""
    if who and not re.match(r'^[A-Za-z_][\w.]*$', who):
        return None, None
    return s0, who


def parse(path):
    """
    Lee un archivo de tl. Devuelve (lineas, items_pendientes, bom).
    `lineas` conserva los finales de línea originales.
    """
    with io.open(path, "r", encoding="utf-8", newline="") as f:
        text = f.read()
    bom = text.startswith("﻿")
    if bom:
        text = text[1:]
    lines = text.splitlines(True)

    items = []
    mode = None          # None, "say" o "strings"
    last_comment = None  # para bloques de diálogo
    last_old = None      # para bloques de strings

    for i, raw in enumerate(lines):
        line = raw.rstrip("\r\n")
        if line and not line[0].isspace():
            m = TRANSLATE_RE.match(line)
            if m:
                mode = "strings" if m.group(1) == "strings" else "say"
            else:
                mode = None
            last_comment = last_old = None
            continue

        stripped = line.strip()
        if mode == "say":
            if stripped.startswith("#"):
                last_comment = stripped[1:].strip()
                continue
            lit, who = _say_literal(line)
            if lit is None:
                continue
            # Solo si sigue igual que el original del comentario.
            if last_comment is None or stripped != last_comment:
                continue
            item = Item(path, i, lit.start() + 1, lit.end() - 1, lit.group(0)[1:-1], who, "say")
            if is_translatable(item):
                items.append(item)

        elif mode == "strings":
            m = OLD_NEW_RE.match(line)
            if not m:
                continue
            kind, lit = m.group(2), m.group(4)
            if kind == "old":
                last_old = lit
            elif last_old is not None:
                if lit == last_old:
                    start = len(m.group(1)) + 3 + len(m.group(3)) + 1
                    item = Item(path, i, start, start + len(lit) - 2, lit[1:-1], "", "string")
                    if is_translatable(item):
                        items.append(item)
                last_old = None

    return lines, items, bom


def escape_text(text):
    """
    Convierte texto plano (lo que devuelve el traductor, sin marcadores) en
    contenido válido de un literal de Ren'Py.
    """
    text = text.replace("\\", "\\\\").replace('"', '\\"')
    text = text.replace("{", "{{").replace("[", "[[")
    return text


def unmask(item, translated):
    """
    Restaura los marcadores en la traducción. Devuelve el nuevo contenido del
    literal, o None si la traducción no es válida.
    """
    if translated is None:
        return None
    translated = translated.replace("\r", " ").replace("\n", " ").strip()
    translated = re.sub(r"\s{2,}", " ", translated)
    if not translated:
        return None

    found = [int(n) for n in MARK_RE.findall(translated)]
    if sorted(found) != list(range(len(item.marks))):
        return None

    # Se escapa el texto entre marcadores y se insertan los originales.
    out = []
    pos = 0
    for m in MARK_RE.finditer(translated):
        out.append(escape_text(translated[pos:m.start()]))
        out.append(item.marks[int(m.group(1))])
        pos = m.end()
    out.append(escape_text(translated[pos:]))
    rv = "".join(out)

    # Conserva los espacios del principio y del final del original.
    lead = len(item.body) - len(item.body.lstrip(" "))
    trail = len(item.body) - len(item.body.rstrip(" "))
    return " " * lead + rv.strip(" ") + " " * trail


def apply(lines, item, new_body):
    raw = lines[item.lineno]
    lines[item.lineno] = raw[:item.start] + new_body + raw[item.end:]


def write(path, lines, bom):
    text = "".join(lines)
    with io.open(path, "w", encoding="utf-8", newline="") as f:
        if bom:
            f.write("﻿")
        f.write(text)
