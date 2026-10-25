"""
Lector/extractor de archivos .rpa de Ren'Py (formatos RPA-1.0, RPA-2.0, RPA-3.0
y RPA-3.2).

Formato (ver renpy/loader.py en el código de Ren'Py):
  RPA-3.0 <offset hex 16> <clave hex 8>\n
  ...datos de los archivos...
  [en <offset>] índice = zlib(pickle({nombre: [(offset, longitud, prefijo), ...]}))
  En 3.x, offset y longitud están ofuscados con XOR de la clave.

El índice se lee con un Unpickler restringido para no ejecutar código del
archivo (un .rpa es un dato no confiable).
"""

import io
import os
import pickle
import zlib


class RPAError(Exception):
    pass


class _SafeUnpickler(pickle.Unpickler):
    # El índice solo contiene dict, list, tuple, int, str y bytes; no hace
    # falta ninguna clase.
    def find_class(self, module, name):
        if module in ("builtins", "__builtin__") and name in ("set", "frozenset", "bytearray"):
            import builtins
            return getattr(builtins, name)
        if module == "_codecs" and name == "encode":
            import codecs
            return codecs.encode
        raise RPAError("El índice del .rpa intenta cargar {}.{}; se rechaza.".format(module, name))


def _loads(data):
    return _SafeUnpickler(io.BytesIO(data), encoding="bytes").load()


def _to_str(s):
    if isinstance(s, bytes):
        return s.decode("utf-8", "replace")
    return s


def _to_bytes(s):
    if s is None:
        return b""
    if isinstance(s, str):
        return s.encode("latin-1")
    return bytes(s)


class RPAArchive(object):
    """
    Archivo .rpa abierto. `index` es {nombre: [(offset, longitud, prefijo), ...]}.
    """

    def __init__(self, path):
        self.path = path
        self.version, self.index = self._read_index(path)

    @staticmethod
    def _read_index(path):
        with open(path, "rb") as f:
            header = f.readline()

            if header.startswith(b"RPA-3.2 "):
                parts = header.split()
                offset = int(parts[1], 16)
                key = 0
                for p in parts[2:]:
                    key ^= int(p, 16)
                version = "RPA-3.2"
            elif header.startswith(b"RPA-3.0 "):
                parts = header.split()
                offset = int(parts[1], 16)
                key = 0
                for p in parts[2:]:
                    key ^= int(p, 16)
                version = "RPA-3.0"
            elif header.startswith(b"RPA-2.0 "):
                offset = int(header[8:], 16)
                key = None
                version = "RPA-2.0"
            else:
                # RPA-1.0: el índice está en un archivo .rpi aparte.
                rpi = os.path.splitext(path)[0] + ".rpi"
                if not os.path.exists(rpi):
                    raise RPAError("Formato .rpa no reconocido: {!r}".format(header[:16]))
                with open(rpi, "rb") as fi:
                    raw = _loads(zlib.decompress(fi.read()))
                return "RPA-1.0", RPAArchive._normalize(raw, None)

            f.seek(offset)
            raw = _loads(zlib.decompress(f.read()))

        return version, RPAArchive._normalize(raw, key)

    @staticmethod
    def _normalize(raw, key):
        index = {}
        for name, entries in raw.items():
            name = _to_str(name)
            out = []
            for e in entries:
                if len(e) == 2:
                    off, length = e
                    prefix = b""
                else:
                    off, length, prefix = e[0], e[1], e[2]
                if key is not None:
                    off ^= key
                    length ^= key
                out.append((off, length, _to_bytes(prefix)))
            index[name] = out
        return index

    def names(self):
        return sorted(self.index)

    def read(self, name):
        parts = []
        with open(self.path, "rb") as f:
            for off, length, prefix in self.index[name]:
                f.seek(off)
                parts.append(prefix)
                parts.append(f.read(length - len(prefix)))
        return b"".join(parts)

    def extract_to(self, name, dest_root, chunk=1 << 20):
        """
        Extrae `name` debajo de `dest_root` y devuelve la ruta escrita.
        """
        dest = safe_join(dest_root, name)
        os.makedirs(os.path.dirname(dest), exist_ok=True)

        with open(self.path, "rb") as f, open(dest, "wb") as out:
            for off, length, prefix in self.index[name]:
                out.write(prefix)
                remaining = length - len(prefix)
                f.seek(off)
                while remaining > 0:
                    data = f.read(min(chunk, remaining))
                    if not data:
                        raise RPAError("{}: datos truncados en el .rpa".format(name))
                    out.write(data)
                    remaining -= len(data)
        return dest


def safe_join(root, name):
    """
    Une `root` y `name` impidiendo que el nombre escape de `root`
    (rutas absolutas o con '..').
    """
    name = name.replace("\\", "/").lstrip("/")
    parts = [p for p in name.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts) or (parts and ":" in parts[0]):
        raise RPAError("Ruta peligrosa dentro del .rpa: {!r}".format(name))
    root = os.path.abspath(root)
    dest = os.path.abspath(os.path.join(root, *parts))
    if os.path.commonpath([root, dest]) != root:
        raise RPAError("Ruta peligrosa dentro del .rpa: {!r}".format(name))
    return dest


def find_archives(gamedir):
    """
    Devuelve los .rpa que hay en `gamedir` (recursivo), ordenados.
    """
    rv = []
    for dirpath, _dirnames, filenames in os.walk(gamedir):
        for fn in filenames:
            if fn.lower().endswith(".rpa"):
                rv.append(os.path.join(dirpath, fn))
    return sorted(rv)
