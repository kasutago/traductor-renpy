"""
Sugerencias de glosario a partir de tl/<idioma>:

  - nombres propios que se repiten (para que no se traduzcan ni se deformen);
  - palabras inglesas que el modelo dejó sin traducir (para que les pongas
    traducción).

Se pueden pedir antes de traducir (solo nombres) o después (también las
palabras sin traducir y los nombres que el modelo cambió).
"""

import collections
import re

import revision
import tl_editor
import traducir_auto

WORD_RE = re.compile(r"[A-Za-z][A-Za-z'’-]*")
# Una palabra con mayúscula inicial al principio de frase no cuenta como nombre.
SENTENCE_START_RE = re.compile(r"(?:^|[.!?…:]\s*[\"“‘'(¿¡]?\s*|[\"“(]\s*)$")

STOP_NAMES = {
    "I", "Mr", "Mrs", "Ms", "Mx", "Dr", "Sir", "Miss", "Mom", "Dad", "God", "Oh", "Hey", "Yes", "No",
    "Okay", "Ok", "Well", "Wow", "Hmm", "Ah", "Uh", "Um", "Yeah", "Please", "Thanks", "Sorry",
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
    "January", "February", "March", "April", "May", "June", "July", "August", "September",
    "October", "November", "December", "English", "Spanish", "French", "German", "Japanese",
}


def _texts(entries):
    for e in entries:
        yield e, revision._plain(e.src), revision._plain(e.dst)


def nombres(entries, min_mid=2):
    """
    [(nombre, veces, cambiado_en)] ordenado por frecuencia. `cambiado_en` = en
    cuántas líneas ya traducidas el nombre no aparece tal cual en el español.
    """
    mid = collections.Counter()
    total = collections.Counter()
    lower = collections.Counter()
    changed = collections.Counter()
    for e, ps, pd in _texts(entries):
        seen = set()
        for m in WORD_RE.finditer(ps):
            w = m.group(0).strip("'’-")
            w = re.sub(r"['’]s$", "", w)
            if len(w) < 3 or "'" in w or "’" in w:
                continue
            if w[0].isupper() and not w.isupper():
                total[w] += 1
                if not SENTENCE_START_RE.search(ps[:m.start()]):
                    mid[w] += 1
                seen.add(w)
            else:
                lower[w.lower()] += 1
        if e.src != e.dst:
            for w in seen:
                if w not in pd:
                    changed[w] += 1
    rv = []
    for w, n in mid.items():
        if w in STOP_NAMES or n < min_mid:
            continue
        if n < 0.5 * total[w] or lower[w.lower()] > max(1, 0.2 * n):
            continue
        rv.append((w, total[w], changed[w]))
    rv.sort(key=lambda x: (-x[1], x[0]))
    return rv


def sin_traducir(entries, min_veces=3):
    """
    [(palabra, veces)]: palabras inglesas que siguen en el español de líneas ya
    traducidas.
    """
    c = collections.Counter()
    for e, ps, pd in _texts(entries):
        if e.src == e.dst:
            continue
        src_words = {w.lower() for w in WORD_RE.findall(ps)}
        for w in set(WORD_RE.findall(pd)):
            if w[0].islower() and len(w) >= 4 and w.lower() in src_words:
                c[w.lower()] += 1
    return [(w, n) for w, n in c.most_common(40) if n >= min_veces]


def sugerencias(tl_dir, existentes=()):
    """
    Texto listo para pegar en glosario.txt. Las líneas de nombres van activas
    (se mantienen tal cual); las de palabras sin traducir van comentadas para
    que escribas tú la traducción.
    """
    entries = revision.collect(tl_dir)
    ya = {s.lower() for s, _d in existentes}
    out = ["# Nombres que se repiten: con \"=\" vacío se mantienen tal cual. Borra los que sí quieras traducir."]
    n_names = 0
    for w, veces, cambiado in nombres(entries):
        if w.lower() in ya:
            continue
        nota = "# {} veces".format(veces) + ("; el modelo lo cambió en {}".format(cambiado) if cambiado else "")
        out += [nota, "{} =".format(w)]
        n_names += 1
    out.append("")
    out.append("# Palabras que el modelo dejó en inglés: quita el # y escribe la traducción.")
    n_left = 0
    for w, veces in sin_traducir(entries):
        if w in ya:
            continue
        out += ["# {} veces".format(veces), "# {} = ".format(w)]
        n_left += 1
    return "\n".join(out) + "\n", n_names, n_left
