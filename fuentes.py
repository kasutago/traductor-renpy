"""
Detecta las fuentes del juego que no tienen las letras del español
(á é í ó ú ü ñ ¿ ¡ y sus mayúsculas).

Lee directamente la tabla cmap de los .ttf/.otf/.ttc, sin dependencias. Las
fuentes que fallan se cambian por DejaVuSans (incluida en Ren'Py) solo
mientras el juego está en el idioma de la traducción (ver plantillas/).
"""

import os
import struct

FONT_EXT = (".ttf", ".otf", ".ttc")

SPANISH = u"áéíóúüñÁÉÍÓÚÜÑ¿¡"


def _cmap_codepoints(data):
    """
    Conjunto de caracteres que cubre la fuente (formatos 0, 4, 6 y 12 de cmap).
    """
    offset = 0
    if data[:4] == b"ttcf":
        # Colección: basta con la primera fuente.
        offset = struct.unpack_from(">I", data, 12)[0]

    num_tables = struct.unpack_from(">H", data, offset + 4)[0]
    cmap = None
    for i in range(num_tables):
        tag, _checksum, toff, _length = struct.unpack_from(">4sIII", data, offset + 12 + 16 * i)
        if tag == b"cmap":
            cmap = toff
            break
    if cmap is None:
        return set()

    rv = set()
    count = struct.unpack_from(">H", data, cmap + 2)[0]
    for i in range(count):
        platform, _encoding, sub = struct.unpack_from(">HHI", data, cmap + 4 + 8 * i)
        if platform not in (0, 3):
            continue
        sub += cmap
        fmt = struct.unpack_from(">H", data, sub)[0]

        if fmt == 0:
            glyphs = data[sub + 6:sub + 6 + 256]
            rv.update(c for c in range(256) if glyphs[c])

        elif fmt == 4:
            segx2 = struct.unpack_from(">H", data, sub + 6)[0]
            n = segx2 // 2
            ends = struct.unpack_from(">%dH" % n, data, sub + 14)
            starts = struct.unpack_from(">%dH" % n, data, sub + 16 + segx2)
            deltas = struct.unpack_from(">%dh" % n, data, sub + 16 + 2 * segx2)
            ro_pos = sub + 16 + 3 * segx2
            ranges = struct.unpack_from(">%dH" % n, data, ro_pos)
            for k in range(n):
                for c in range(starts[k], ends[k] + 1):
                    if c == 0xFFFF:
                        continue
                    if ranges[k] == 0:
                        glyph = (c + deltas[k]) & 0xFFFF
                    else:
                        pos = ro_pos + 2 * k + ranges[k] + 2 * (c - starts[k])
                        if pos + 2 > len(data):
                            continue
                        glyph = struct.unpack_from(">H", data, pos)[0]
                        if glyph:
                            glyph = (glyph + deltas[k]) & 0xFFFF
                    if glyph:
                        rv.add(c)

        elif fmt == 6:
            first, n = struct.unpack_from(">HH", data, sub + 6)
            glyphs = struct.unpack_from(">%dH" % n, data, sub + 10)
            rv.update(first + k for k in range(n) if glyphs[k])

        elif fmt == 12:
            n = struct.unpack_from(">I", data, sub + 12)[0]
            for k in range(n):
                start, end, glyph = struct.unpack_from(">III", data, sub + 16 + 12 * k)
                if glyph:
                    rv.update(range(start, end + 1))
                else:
                    rv.update(range(start + 1, end + 1))

    return rv


def missing_chars(path, chars=SPANISH):
    """
    Letras de `chars` que no tiene la fuente, o None si no se pudo leer.
    """
    try:
        with open(path, "rb") as f:
            data = f.read()
        cps = _cmap_codepoints(data)
    except (OSError, struct.error, IndexError):
        return None
    if not cps:
        return None
    return u"".join(c for c in chars if ord(c) not in cps)


def find_fonts(*roots):
    """
    Rutas de las fuentes de las carpetas `roots` (sin tl/).
    """
    rv = []
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            if os.path.relpath(dirpath, root).replace("\\", "/").split("/")[0] in ("tl", "cache", "saves"):
                dirnames[:] = []
                continue
            rv.extend(os.path.join(dirpath, fn) for fn in filenames if fn.lower().endswith(FONT_EXT))
    return sorted(rv)


def fonts_without_spanish(*roots):
    """
    {nombre_de_archivo: letras_que_faltan} de las fuentes incompletas.
    Las fuentes que no se pueden leer se dejan como están.
    """
    rv = {}
    for path in find_fonts(*roots):
        missing = missing_chars(path)
        if missing:
            rv[os.path.basename(path)] = missing
    return rv
