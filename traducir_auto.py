"""
Traducción automática de la carpeta tl/<idioma> de un juego con MarianMT.

El traductor nunca ve los archivos: tl_editor extrae solo el texto de las
comillas, aquí se traduce por tandas, se valida y tl_editor lo vuelve a poner
en la misma línea.
"""

import datetime
import hashlib
import io
import json
import os
import re
import shutil
import threading

import tl_editor
from tl_editor import MARK_RE

HERE = os.path.dirname(os.path.abspath(__file__))
GLOSSARY_FILE = os.path.join(HERE, "glosario.txt")

GLOSSARY_HEADER = u"""# Glosario del traductor. Una entrada por línea:
#   texto en inglés = traducción
# Si la traducción se deja vacía, el texto se mantiene igual (útil para nombres):
#   Quahog =
# Las líneas que empiezan por # se ignoran.
"""


# ---------------------------------------------------------------------------
# Glosario y caché


def load_glossary(path=GLOSSARY_FILE):
    if not os.path.exists(path):
        with io.open(path, "w", encoding="utf-8") as f:
            f.write(GLOSSARY_HEADER)
        return []
    rv = []
    with io.open(path, "r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            src, dst = line.split("=", 1)
            src, dst = src.strip(), dst.strip()
            if src:
                rv.append((src, dst or src))
    # Primero los más largos, para que "Stewie's Missions" gane a "Stewie".
    rv.sort(key=lambda e: -len(e[0]))
    return rv


def _term_re(term):
    return re.compile(r"(?<!\w)" + re.escape(term) + r"(?!\w)", re.I)


class Cache(object):

    def __init__(self, path):
        self.path = path
        self.data = {}
        if path and os.path.exists(path):
            try:
                with io.open(path, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
            except ValueError:
                self.data = {}

    def get(self, key):
        return self.data.get(key)

    def put(self, key, value):
        self.data[key] = value

    def save(self):
        if not self.path:
            return
        tmp = self.path + ".tmp"
        with io.open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=0)
        os.replace(tmp, self.path)


# ---------------------------------------------------------------------------
# Adaptador de MarianMT. Recibe Items y devuelve el texto traducido con los
# marcadores ⟦n⟧ (o None si falla).


with io.open(os.path.abspath(__file__), "rb") as _f:
    _CODE_HASH = hashlib.sha1(_f.read()).hexdigest()[:8]


class MarianBackend(object):
    """
    MarianMT no sabe de marcadores: el texto se parte por los marcadores, se
    traducen los trozos y se vuelve a unir. Los términos del glosario se
    sustituyen por su traducción tras protegerlos con un marcador temporal.
    """

    # La caché va ligada a la versión del código: cualquier cambio en este archivo
    # crea una caché nueva (así una mejora nunca se queda tapada por la caché vieja).
    name = "marian-" + _CODE_HASH
    batch_size = 128

    # Trato al lector: "tu" (tú/tu/ti) o "usted" (se deja lo que dé el modelo).
    TRATO = "tu"

    def __init__(self, translator, glossary):
        self.tr = translator
        self.glossary = glossary
        self.stats = {"tu_detectadas": 0, "tu_corregidas": 0, "tartamudeos": 0, "signos": 0}

    # Símbolos que MarianMT no tiene en su vocabulario: los convierte en "⁇"
    # (p. ej. "*Cough*" -> "⁇ Tos ⁇"). Se protegen como marcadores.
    # Lo mismo con las palabras que llevan letras con tilde o diéresis (nombres
    # como "Götz", "Zoë"): se dejan tal cual, enteras.
    SYMBOLS_RE = re.compile(u"[*♥♡♪♫★☆~#@^|<>_]+|\\w*[^\\x00-\\x7f\\W]\\w*")

    # Nombres propios inventados: sustituyen a [variables] y términos del glosario
    # para traducir la frase entera (si se corta en el marcador salen cosas como
    # "[name]¿Verdad?"). El modelo los copia tal cual.
    PLACEHOLDERS = [u"Zorbek", u"Quilmar", u"Tavrion", u"Belkorn", u"Mirvash", u"Orsolk"]
    VARIABLE_RE = re.compile(r"^\[[^\[\]]*\]$")

    def _prepare(self, item, use_ph=False):
        text = item.masked
        extra = {}
        for src, dst in self.glossary:
            rx = _term_re(src)
            if rx.search(text):
                key = len(item.marks) + len(extra)
                extra[key] = dst
                text = rx.sub(u"⟦{}⟧".format(key), text)

        def protect(m):
            key = len(item.marks) + len(extra)
            extra[key] = m.group(0)
            return u"⟦{}⟧".format(key)

        # Fuera de los marcadores existentes, para no tocar "⟦0⟧".
        pieces = re.split(u"(⟦\\d+⟧)", text)
        text = "".join(p if MARK_RE.fullmatch(p) else self.SYMBOLS_RE.sub(protect, p) for p in pieces)

        # Etiquetas de formato alrededor de unas pocas palabras ("maybe {b}you{/b}
        # can"): se quitan para traducir la frase entera (cortarla ahí da "usted"
        # y frases rotas) y luego se vuelven a poner sobre la palabra traducida.
        wraps = []
        if use_ph:
            def unwrap(m):
                a, inner, b = int(m.group(1)), m.group(2), int(m.group(3))
                if a >= len(item.marks) or b >= len(item.marks):
                    return m.group(0)
                mo = self.TAG_OPEN_RE.match(item.marks[a])
                if not mo or item.marks[b] != u"{/" + mo.group(1) + u"}":
                    return m.group(0)
                if len(inner.split()) > 6 or not re.search(r"[A-Za-z]", inner):
                    return m.group(0)
                whole = not m.string[:m.start()].strip() and not m.string[m.end():].strip()
                wraps.append((a, b, inner.strip(), whole))
                return inner
            text = re.sub(u"⟦(\\d+)⟧([^⟦⟧]{1,60}?)⟦(\\d+)⟧", unwrap, text)

        ph = {}
        if use_ph:
            nouns = []
            for m in MARK_RE.finditer(text):
                k = int(m.group(1))
                val = extra[k] if k in extra else item.marks[k]
                noun = (re.search(r"[^\W\d_]", val) and k in extra) or (k < len(item.marks) and self.VARIABLE_RE.match(val))
                if noun and k not in nouns:
                    nouns.append(k)
            if 0 < len(nouns) <= len(self.PLACEHOLDERS):
                for name, k in zip(self.PLACEHOLDERS, nouns):
                    ph[name] = extra[k] if k in extra else u"⟦{}⟧".format(k)
                    text = text.replace(u"⟦{}⟧".format(k), name)

        parts = MARK_RE.split(text)  # [texto, n, texto, n, texto...]
        return parts, extra, ph, wraps

    TAG_OPEN_RE = re.compile(r"^\{(b|i|u|s|plain|color|size|font|alpha|outlinecolor|a|k)(?:=[^{}]*)?\}$")

    @staticmethod
    def _rewrap(t, wraps, inner_done):
        """
        Vuelve a poner cada etiqueta (a, b) alrededor de la traducción de su texto.
        Si no se encuentra exactamente una vez, la etiqueta queda vacía al final
        ("{b}{/b}" no se ve): se pierde el énfasis pero no se rompe nada.
        """
        for a, b, inner, whole in wraps:
            open_m, close_m = u"⟦{}⟧".format(a), u"⟦{}⟧".format(b)
            if whole:
                t = open_m + t.strip() + close_m
                continue
            placed = False
            for cand in (inner_done.get(inner), inner):
                if not cand:
                    continue
                cand = cand.strip(u" .,;:!?¡¿…\"“”")
                if not cand:
                    continue
                hits = [m for m in re.finditer(r"(?<!\w)" + re.escape(cand) + r"(?!\w)", t, re.I)]
                if len(hits) == 1:
                    m = hits[0]
                    t = t[:m.start()] + open_m + t[m.start():m.end()] + close_m + t[m.end():]
                    placed = True
                    break
            if not placed:
                m = re.search(u"[\\s.,;:!?…\"”)]*$", t)
                t = t[:m.start()] + open_m + close_m + t[m.start():]
        return t

    # MarianMT a veces se come frases cuando recibe varias juntas, así que
    # cada trozo se parte en frases y se traducen por separado.
    # Solo es fin de frase si lo que sigue empieza como una frase nueva: tras "..."
    # y minúscula ("so... passionate") se sigue en la misma; cortar ahí deja
    # fragmentos que el modelo rellena con basura ("Me gusta Cargando...").
    SENTENCE_RE = re.compile(u'(?<=[.!?…])\\s+(?=[A-ZÁÉÍÓÚÑ0-9"“‘\'(\\[¿¡⟦{*-])')

    # --- tratamiento de tú ---------------------------------------------------
    # MarianMT prefiere "usted/su" para el "you/your" inglés. Se detecta y, solo
    # en esas frases, se reintenta anteponiendo una frase de contexto en tú que
    # luego se recorta (si no cuadra el número de frases, se descarta).
    YOU_RE = re.compile(r"\byou(?:r|rs|rself)?\b", re.I)
    YOUR_RE = re.compile(r"\byours?\b", re.I)
    OTHER_POSS_RE = re.compile(r"\b(?:his|her|hers|their|its|our|my|mine|thy)\b", re.I)
    USTED_RE = re.compile(r"\b[Uu]sted\b")
    SU_RE = re.compile(r"\b[Ss]us?\b")
    # Imperativo de usted con "se" pegado: lleva tilde (quítese, siéntase); sin
    # tilde sería subjuntivo (quedase, fuese) y no se toca.
    IMPERATIVE_RE = re.compile(r"\b\w*[áéíóú]\w*(?:ese|ase)\b", re.I)
    PLURAL_YOU_RE = re.compile(r"\b(?:you guys|you all|y'all|both of you|all of you|you two|you both|each of you|"
                               r"you three|you girls|you boys|you people|you ladies|you kids)\b", re.I)
    TU_PRIMES = [
        u"My friend, you are my best friend and I trust you with your life.",
        u"Hey buddy, you know I love you and your friends.",
        u"Hey you! Do you want to come with me? Take your bag.",
    ]

    TU_RE = re.compile(r"\btus?\b", re.I)
    YOUR1_RE = re.compile(r"\byour\b", re.I)

    def _problems(self, src, dst):
        """
        (graves, leves): graves = "usted"/imperativo formal; leves = "su" donde
        el inglés dice "your" (si hay "her/his" a la vez, solo cuenta si faltan
        "tu", porque el "su" puede ser de otra persona).
        """
        if not self.YOU_RE.search(src):
            return 0, 0
        hard = len(self.USTED_RE.findall(dst)) + len(self.IMPERATIVE_RE.findall(dst))
        n = len(self.YOUR1_RE.findall(src))
        soft = 0
        if self.PLURAL_YOU_RE.search(src):
            return hard, 0          # "ustedes ... su(s)" es correcto en plural
        if n and self.SU_RE.search(dst):
            if self.OTHER_POSS_RE.search(src):
                soft = max(0, n - len(self.TU_RE.findall(dst)))
            else:
                soft = len(self.SU_RE.findall(dst))
        return hard, soft

    def _formal(self, src, dst):
        return any(self._problems(src, dst))

    THOU_AUX = {"are": "art", "were": "wert", "have": "hast", "had": "hadst", "will": "wilt",
                "shall": "shalt", "can": "canst", "could": "couldst", "would": "wouldst",
                "should": "shouldst", "do": "dost", "did": "didst"}
    THOU_CONTRACTIONS = [(u"you['’]re", "thou art"), (u"you['’]ve", "thou hast"), (u"you['’]ll", "thou wilt")]

    @classmethod
    def _thou(cls, s):
        """
        "thy/thou" no tienen versión formal: el modelo los traduce como tú/tu
        (aprendió "thou art" de los textos antiguos del corpus).
        """
        def case(src, word):
            if src.isupper() and len(src) > 1:
                return word.upper()
            return word.capitalize() if src[:1].isupper() else word

        for pat, rep in cls.THOU_CONTRACTIONS:
            s = re.sub(r"\b" + pat, lambda m, rep=rep: case(m.group(0), rep), s, flags=re.I)
        aux = "|".join(cls.THOU_AUX)
        s = re.sub(r"\b(you)\s+(" + aux + r")\b",
                   lambda m: case(m.group(1), "thou") + " " + cls.THOU_AUX[m.group(2).lower()], s, flags=re.I)
        s = re.sub(r"\byourself\b", lambda m: case(m.group(0), "thyself"), s, flags=re.I)
        s = re.sub(r"\byour\b", lambda m: case(m.group(0), "thy"), s, flags=re.I)
        return re.sub(r"\byou\b(?![\'’])", lambda m: case(m.group(0), "thou"), s, flags=re.I)

    @staticmethod
    def _contract(s):
        """
        "You have" -> "You've": el modelo trata distinto las formas contraídas.
        """
        s = re.sub(r"\b(you) (have|are|will|would)\b",
                   lambda m: m.group(1) + {"have": "'ve", "are": "'re", "will": "'ll", "would": "'d"}[m.group(2).lower()],
                   s, flags=re.I)
        return s

    def _variant(self, s, mode):
        return self._thou(s) if mode == "thou" else (self._contract(s) if mode == "contract" else s)

    # Imperativos formales sueltos ("Wait." -> "Espere.") sin "you" en el original:
    # no se detectan como error, así que se pasan a tú con una tabla segura.
    IMPERATIVES = {
        u"espere": u"espera", u"disculpe": u"disculpa", u"oiga": u"oye", u"venga": u"ven",
        u"recuerde": u"recuerda", u"mire": u"mira", u"escuche": u"escucha", u"perdone": u"perdona",
        u"siga": u"sigue", u"deje": u"deja", u"quédese": u"quédate", u"cállese": u"cállate",
        u"siéntese": u"siéntate", u"déjeme": u"déjame", u"relájese": u"relájate",
        u"apúrese": u"apúrate", u"olvide": u"olvida", u"créame": u"créeme", u"tome": u"toma",
        u"ponga": u"pon", u"escúcheme": u"escúchame", u"míreme": u"mírame",
    }
    IMPERATIVE_START_RE = re.compile(u"((?:^|[,;] ?)[¡¿(\"“]?)(" + u"|".join(IMPERATIVES) + u")\\b", re.I)
    NOT_IMPERATIVE_SRC = {"he", "she", "they", "we", "i", "it", "you", "that", "this", "there", "who", "if"}

    REFLEXIVE_IRREGULAR = {
        u"póngase": u"ponte", u"váyase": u"vete", u"hágase": u"hazte", u"deténgase": u"detente",
        u"manténgase": u"mantente", u"conténgase": u"contente", u"téngase": u"tente", u"dése": u"date",
    }

    @classmethod
    def _tu_reflexive(cls, word):
        """quítese -> quítate, siéntase -> siéntete, acérquese -> acércate, póngase -> ponte."""
        low = word.lower()
        if low in cls.REFLEXIVE_IRREGULAR:
            r = cls.REFLEXIVE_IRREGULAR[low]
        else:
            stem = low[:-2]
            for old, new in ((u"que", u"ca"), (u"gue", u"ga"), (u"ce", u"za"), (u"zca", u"ce"), (u"ja", u"ge")):
                if stem.endswith(old):
                    stem = stem[:-len(old)] + new
                    break
            else:
                stem = stem[:-1] + (u"a" if stem.endswith(u"e") else u"e")
            r = stem + u"te"
        if word.isupper():
            return r.upper()
        return r.capitalize() if word[:1].isupper() else r

    def _fix_reflexive(self, dst):
        if self.TRATO != "tu":
            return dst
        return self.IMPERATIVE_RE.sub(lambda m: self._tu_reflexive(m.group(0)), dst)

    # En frases largas solo se cambia la primera palabra, y solo si el inglés
    # empieza por el verbo que le corresponde ("Wait for..." -> "Espera a que...").
    IMPERATIVE_SRC = {
        u"espere": ("wait",), u"mire": ("look",), u"escuche": ("listen",), u"venga": ("come",),
        u"recuerde": ("remember",), u"disculpe": ("excuse",), u"perdone": ("forgive", "pardon"),
        u"siga": ("follow", "keep"), u"deje": ("leave", "let", "stop"), u"tome": ("take",),
        u"ponga": ("put",), u"olvide": ("forget",), u"créame": ("believe", "trust"), u"oiga": ("hey",),
    }

    def _fix_imperative(self, src, dst):
        if self.TRATO != "tu":
            return dst
        first = re.match(r"[\W_]*([A-Za-z']+)", src)
        if not first or first.group(1).lower() in self.NOT_IMPERATIVE_SRC:
            return dst
        if len(dst.split()) > 8:
            m = re.match(u"^([\\W_]*)(\\w+)", dst)
            if m and first.group(1).lower() in self.IMPERATIVE_SRC.get(m.group(2).lower(), ()):
                w = m.group(2)
                new = self.IMPERATIVES[w.lower()]
                return m.group(1) + (new.capitalize() if w[:1].isupper() else new) + dst[m.end():]
            return dst

        def repl(m):
            w = m.group(2)
            new = self.IMPERATIVES[w.lower()]
            return m.group(1) + (new.capitalize() if w[:1].isupper() else new)
        return self.IMPERATIVE_START_RE.sub(repl, dst)

    # --- rescate de basura ---------------------------------------------------
    # Con algunos fragmentos ("A while...", "(Mhhhh, ...)") el modelo suelta
    # texto basura o repite letras sin fin. Se reintenta con variantes de la frase
    # y se reponen después los paréntesis y los puntos suspensivos del original.
    ELLIPSIS_END_RE = re.compile(u"(?:\\.\\.\\.|…)\\s*$")

    @staticmethod
    def _garbage(src, dst):
        if not dst or u"⁇" in dst:
            return True
        if JUNK_RE.search(dst) and not JUNK_SRC_RE.search(src):
            return True
        return runaway(src, dst)

    @classmethod
    def _rescue_variants(cls, s):
        """[(texto_para_el_modelo, quitó_paréntesis, quitó_puntos_finales)]"""
        paren = bool(re.match(r"^\(.*\)$", s))
        inner = s[1:-1] if paren else s
        ell = bool(cls.ELLIPSIS_END_RE.search(inner))

        def dots(t):
            t = re.sub(u"\\s*(?:\\.\\.\\.|…)\\s*(?=\\S)", ", ", t)
            return cls.ELLIPSIS_END_RE.sub(".", t)

        def stretch(t):
            return re.sub(r"([A-Za-z])\1{2,}", r"\1\1", t)

        rv = []
        if paren:
            rv.append((inner, True, False))
        if ell or u"…" in s or "..." in s:
            rv.append((dots(inner) if paren else dots(s), paren, ell))
        if re.search(r"([A-Za-z])\1{2,}", s):
            rv.append((stretch(inner if paren else s), paren, False))
        rv.append((stretch(dots(inner)), paren, ell))
        seen, out = set(), []
        for v in rv:
            if v[0] not in seen and v[0] != s:
                seen.add(v[0])
                out.append(v)
        return out

    def _rescue(self, done):
        bad = [x for x, d in done.items() if self._garbage(x, d)]
        if not bad:
            return
        self.stats.setdefault("rescatadas", 0)
        for k in range(4):
            jobs = []
            for x in bad:
                vs = self._rescue_variants(x)
                if k < len(vs):
                    jobs.append((x, vs[k]))
            if not jobs:
                break
            outs = self.tr.translate([self._dash_in(v[0]) for _x, v in jobs])
            for (x, (_t, paren, ell)), o in zip(jobs, outs):
                o = self._dash_out(x, (o or "").strip())
                if u"⁇" in o:
                    o = self._fix_accents(o)
                if self._garbage(x, o):
                    continue
                if ell:
                    o = re.sub(u"[\\s.,;:]*$", "", o) + "..."
                if paren:
                    o = "(" + o.strip("() ") + ")"
                done[x] = self._clean_dash(x, o)
                self.stats["rescatadas"] += 1
            bad = [x for x in bad if self._garbage(x, done[x])]
            if not bad:
                break

    # --- frases que el modelo copia sin traducir --------------------------------
    # Entre paréntesis o con mayúscula inicial, algunas frases salen tal cual
    # ("(Crap.)" -> "(Crap.)", pero "Crap." -> "Mierda."). Se reintenta sin
    # paréntesis y en minúscula; si sigue igual se deja (será un nombre o similar).
    def _retry_copied(self, done):
        pending = [x for x, d in done.items()
                   if d and d.strip() == x.strip() and translatable_words(x)]
        for k in range(3):
            jobs = []
            for x in pending:
                paren = bool(re.match(r"^\(.*\)$", x))
                inner = x[1:-1] if paren else x
                low = inner[:1].lower() + inner[1:]
                variants = [v for v in ([inner] if paren else []) + [low] if v != x]
                if k < len(variants):
                    jobs.append((x, variants[k], paren))
            if not jobs:
                break
            outs = self.tr.translate([self._dash_in(v) for _x, v, _p in jobs])
            for (x, v, paren), o in zip(jobs, outs):
                o = self._dash_out(x, (o or "").strip())
                if not o or self._garbage(x, o) or o.strip().lower() in (v.strip().lower(), x.strip().lower()):
                    continue
                o = self._fix_first_case(x.lstrip("("), o)
                if paren:
                    o = "(" + o.strip("() ") + ")"
                done[x] = self._clean_dash(x, o)
                self.stats["copiadas"] = self.stats.get("copiadas", 0) + 1
            pending = [x for x in pending if done[x].strip() == x.strip()]

    def _retry_tu(self, done):
        if self.TRATO != "tu":
            return
        pending = [x for x, d in done.items() if d and self._formal(x, d)]
        self.stats["tu_detectadas"] += len(pending)
        best = {x: self._problems(x, done[x]) for x in pending}
        fixed = set()
        p0, p1, p2 = self.TU_PRIMES
        attempts = [("thou", ""), ("contract", ""), (None, p0), ("thou", p0), (None, p1), (None, p2)]
        for k, (mode, prime) in enumerate(attempts):
            pending = [x for x in pending if sum(best[x]) > 0]
            if not pending:
                break
            n_prime = len(self._sentences(prime)) if prime else 0
            texts = [((prime + " ") if prime else "") + self._dash_in(self._variant(x, mode))
                     for x in pending]
            for x, o in zip(pending, self.tr.translate(texts)):
                parts = self._sentences((o or "").strip())
                if len(parts) != n_prime + len(self._sentences(x)):
                    continue
                cand = self._dash_out(x, " ".join(parts[n_prime:]))
                if u"⁇" in cand:
                    cand = self._fix_accents(cand)
                old = done[x]
                if not cand or u"⁇" in cand or not 0.5 * len(old) <= len(cand) <= 2 * len(old) + 10:
                    continue
                pr = self._problems(x, cand)
                if sum(pr) < sum(best[x]):
                    done[x] = self._clean_dash(x, cand)
                    best[x] = pr
                    fixed.add(x)
        self.stats["tu_corregidas"] += len(fixed)

    # --- tartamudeo: "T-THAT'S OK" / "A-Alexis" -----------------------------
    MID_STUTTER_RE = re.compile(r"(?<![\w-])([A-Za-z])-(?=\1[A-Za-z])", re.I)

    @classmethod
    def _split_stutter(cls, s):
        """
        Devuelve (frase_sin_tartamudeo, n). Solo si cada "X-" repite la letra
        con la que empieza la palabra siguiente. El del principio se repone
        después; los de mitad de frase se quitan (no se sabe dónde caerían).
        """
        head, n = cls._split_lead_stutter(s)
        return cls.MID_STUTTER_RE.sub("", head), n

    # El modelo escribe "⁇" cuando la frase acaba en guion largo (interrupción):
    # se traduce con "..." y se repone el guion después.
    @staticmethod
    def _dash_in(s):
        return re.sub(u"\\s*[–—]+\\s*$", "...", s)

    @staticmethod
    def _dash_out(src, dst):
        if not re.search(u"[–—]+\\s*$", src):
            return dst
        dst = re.sub(u"\\s*(?:\\.{2,}|…)[?!]*\\s*$", "", dst)
        if u"¿" in dst and "?" not in dst:
            dst = dst.replace(u"¿", "")
        if u"¡" in dst and "!" not in dst:
            dst = dst.replace(u"¡", "")
        return dst.rstrip() + u"–"

    @staticmethod
    def _split_lead_stutter(s):
        n, i = 0, 0
        while i + 2 < len(s) and s[i].isalpha() and s[i + 1] == "-":
            n += 1
            i += 2
        if not n:
            return s, 0
        rest = s[i:]
        first = s[0].lower()
        if len(rest) >= 2 and rest[0].isalpha() and rest[0].lower() == first and all(
                s[k].lower() == first for k in range(0, i, 2)):
            return rest, n
        return s, 0

    @staticmethod
    def _add_stutter(dst, n):
        for i, c in enumerate(dst):
            if c.isalpha():
                letter = c.upper() if (dst[i:i + 2].isupper() or dst[i:i + 1].isupper()) else c
                return dst[:i] + (letter + "-") * n + dst[i:]
        return dst

    # --- signos de apertura ¿ ¡ -----------------------------------------------
    @staticmethod
    def _fix_inverted(t):
        bounds = [0] + [m.end() for m in re.finditer(u"[.!?…]+[\"”’)]*(?=\\s)", t)] + [len(t)]
        out = []
        for a, b in zip(bounds, bounds[1:]):
            chunk = t[a:b]
            m = re.search(u"([!?]+)[\"”’)\\s]*$", chunk)
            if m:
                run = m.group(1)
                opener = u""
                for c in run:
                    o = u"¡" if c == "!" else u"¿"
                    if not opener.endswith(o):
                        opener += o
                opener = u"".join(o for o in opener[:2] if o not in chunk)
                if opener:
                    # Si la exclamación/pregunta va entre comillas, el signo se abre dentro.
                    start = 0
                    if re.search(u"[\"”»]\\s*$", chunk):
                        q = max(chunk.rfind(c, 0, m.start(1)) for c in u"“\"«")
                        if q >= 0:
                            start = q + 1
                    k = re.search(u"[^\\W_]|⟦", chunk[start:])
                    if k:
                        at = start + k.start()
                        chunk = chunk[:at] + opener + chunk[at:]
            out.append(chunk)
        return u"".join(out)

    @staticmethod
    def _fix_first_case(src, dst):
        sa = next((c for c in src if c.isalpha()), "")
        if sa.isupper() and not src.lstrip().startswith(u"⟦"):
            for i, c in enumerate(dst):
                if c.isalpha():
                    return dst[:i] + c.upper() + dst[i + 1:] if c.islower() else dst
                if c == u"⟦":
                    break
        return dst

    @staticmethod
    def _clean_dash(src, dst):
        # Quita el guion de diálogo que el modelo añade a veces.
        if dst.startswith("- ") and not src.startswith("-"):
            return dst[2:]
        return dst

    # Palabras frecuentes que empiezan por mayúscula con tilde, que MarianMT
    # escribe como "⁇ltimamente", "⁇l", etc.
    ACCENT_FIXES = [
        (u"ltim", u"Ú"), (u"nic", u"Ú"), (u"til", u"Ú"), (u"ne", u"Ú"),
        (u"xit", u"É"), (u"poca", u"É"), (u"ste", u"É"), (u"sta", u"É"), (u"sto", u"É"), (u"l", u"É"),
        (u"rea", u"Á"), (u"rbol", u"Á"), (u"nimo", u"Á"), (u"ngel", u"Á"), (u"frica", u"Á"),
        (u"ndice", u"Í"), (u"ntim", u"Í"), (u"dolo", u"Í"),
        (u"rden", u"Ó"), (u"pera", u"Ó"),
    ]

    @classmethod
    def _fix_accents(cls, dst):
        # "ü" tampoco está en su vocabulario: averig⁇emos, verg⁇enza, ping⁇ino.
        dst = re.sub(u"([gG])\\s?⁇\\s?([eiEI])", u"\\1ü\\2", dst)

        def repl(m):
            rest = m.group(1)
            for start, letter in cls.ACCENT_FIXES:
                if rest.lower().startswith(start) and (start != u"l" or len(rest) == 1):
                    return letter + rest
            return m.group(0)
        return re.sub(u"⁇\\s?([^\\W\\d_]+)", repl, dst)

    @staticmethod
    def _restore_case(src, dst):
        letters = [c for c in src if c.isalpha()]
        if letters and all(c.isupper() for c in letters):
            return dst.upper()
        # Primera letra en mayúscula si el original la tenía (saltando ¡ ¿ " ...).
        first_src = next((c for c in src if c.isalpha()), "")
        if first_src.isupper():
            for i, c in enumerate(dst):
                if c.isalpha():
                    return dst[:i] + c.upper() + dst[i + 1:]
        return dst

    # "Ms. Oleander" no es el fin de una frase.
    ABBREV_RE = re.compile(r"(?:^|\s)(?:Mr|Mrs|Ms|Mx|Dr|Prof|St|Sr|Sra|Jr|vs|etc|No)\.$", re.I)

    # El modelo corta en silencio las entradas muy largas: una "frase" de más de
    # LONG caracteres (párrafos sin puntos) se parte tras comas, punto y coma o
    # dos puntos en trozos de hasta CHUNK.
    LONG = 600
    CHUNK = 300

    def _sentences(self, seg):
        rv = []
        for piece in self.SENTENCE_RE.split(seg.strip()):
            if not piece:
                continue
            if rv and self.ABBREV_RE.search(rv[-1]):
                rv[-1] += " " + piece
            else:
                rv.append(piece)
        out = []
        for piece in rv:
            if len(piece) <= self.LONG:
                out.append(piece)
                continue
            cur = ""
            for part in re.split(r"(?<=[,;:])\s+", piece):
                if cur and len(cur) + 1 + len(part) > self.CHUNK:
                    out.append(cur)
                    cur = part
                else:
                    cur = (cur + " " + part) if cur else part
            if cur:
                out.append(cur)
        return out

    def _translate_segments(self, segments):
        sentences = []
        for seg in segments:
            for s in self._sentences(seg):
                core = self._split_stutter(s)[0]
                if re.search(r"[^\W\d_]", core) and core not in sentences:
                    sentences.append(core)
        done = {}
        unknown = []
        for i in range(0, len(sentences), 256):
            chunk = sentences[i:i + 256]
            for src, dst in zip(chunk, self.tr.translate([self._dash_in(c) for c in chunk])):
                dst = self._dash_out(src, (dst or "").strip())
                # "⁇" = carácter que el modelo no conoce (a MarianMT le faltan
                # las mayúsculas con tilde y la "ü"). Primero se corrigen los
                # casos conocidos; si queda alguno, se reintenta en minúsculas
                # y luego se le devuelven las mayúsculas.
                if u"⁇" in dst:
                    dst = self._fix_accents(dst)
                if u"⁇" in dst:
                    unknown.append(src)
                    dst = ""
                done[src] = self._clean_dash(src, dst)

        if unknown:
            lowered = [self._dash_in(s.lower()) for s in unknown]
            for src, dst in zip(unknown, self.tr.translate(lowered)):
                dst = self._fix_accents(self._dash_out(src, (dst or "").strip()))
                if not dst or u"⁇" in dst:
                    continue
                done[src] = self._clean_dash(src, self._restore_case(src, dst))
        self._rescue(done)
        self._retry_copied(done)
        self._retry_tu(done)
        for src_s in list(done):
            if done[src_s]:
                done[src_s] = self._fix_reflexive(self._fix_imperative(src_s, done[src_s]))

        rv = {}
        for seg in segments:
            parts = []
            for s in self._sentences(seg):
                core, n_stutter = self._split_stutter(s)
                t = done.get(core, core)
                if t and t != core:
                    t = self._fix_first_case(core, t)
                if t and n_stutter:
                    t = self._add_stutter(t, n_stutter)
                    self.stats["tartamudeos"] += 1
                if not t:
                    parts = None   # una frase sin traducción invalida el trozo
                    break
                parts.append(t)
            rv[seg] = " ".join(parts) if parts is not None else None
        return rv

    def _translate(self, items, use_ph):
        prepared = [self._prepare(it, use_ph) for it in items]
        segments = []
        inners = []
        for parts, _extra, _ph, wraps in prepared:
            inners += [w[2] for w in wraps if w[2] not in inners]
            for k in range(0, len(parts), 2):
                seg = parts[k]
                if re.search(r"[^\W\d_]", seg):
                    segments.append(seg.strip())
        done = self._translate_segments(segments) if segments else {}
        inner_done = self._translate_segments(inners) if inners else {}

        out = []
        for it, (parts, extra, ph, wraps) in zip(items, prepared):
            rv = []
            seen = set()
            for k, piece in enumerate(parts):
                if k % 2 == 0:
                    if re.search(r"[^\W\d_]", piece):
                        seg = done.get(piece.strip())
                        if not seg:
                            rv = None
                            break
                        lead = piece[:len(piece) - len(piece.lstrip())]
                        trail = piece[len(piece.rstrip()):]
                        # Un trozo que sigue a una variable o etiqueta y empieza en
                        # minúscula continúa la frase: no debe salir en mayúscula.
                        if k > 0 and re.match(r"[^\W\d_]", piece.lstrip()[:1] or " ") and piece.lstrip()[:1].islower():
                            fa = next((i for i, c in enumerate(seg) if c.isalpha()), None)
                            if fa is not None and seg[fa].isupper() and not seg.isupper():
                                seg = seg[:fa] + seg[fa].lower() + seg[fa + 1:]
                        for name, back in ph.items():
                            if name in seg:
                                seen.add(name)
                                seg = seg.replace(name, back)
                        rv.append(lead + seg + trail)
                    else:
                        rv.append(piece)
                else:
                    n = int(piece)
                    rv.append(extra[n] if n in extra else u"⟦{}⟧".format(n))
            t = "".join(rv) if rv is not None else None
            if t and len(seen) < len(ph):
                t = None   # el modelo se comió o cambió un nombre: se reintenta sin sustitutos
            if t and wraps:
                t = self._rewrap(t, wraps, inner_done)
            # "(Wait for it.)" -> "(Espera)." : el punto vuelve dentro del paréntesis.
            if t and re.search(r"[.!?…]+\)\s*$", it.masked) and re.search(r"\)[.!?…]+\s*$", t):
                t = re.sub(r"\)([.!?…]+)(\s*)$", r"\1)\2", t)
            if t:
                fixed = self._fix_inverted(t)
                if fixed != t:
                    self.stats["signos"] += 1
                t = fixed
            out.append(t if valid(it, t) else None)
        return out

    def translate(self, items):
        out = self._translate(items, True)
        redo = [i for i, o in enumerate(out) if o is None and any(self._prepare(items[i], True)[2:])]
        if redo:
            for i, o in zip(redo, self._translate([items[i] for i in redo], False)):
                out[i] = o
        return out


# Texto de botones web que el modelo aprendió de sus datos y suelta al azar.
JUNK_RE = re.compile(r"\b(?:Cargando|Relacionado|Compartir en|Haz clic|Haga clic|Suscríbete|Seguir leyendo|"
                     r"Facebook|Twitter|Pinterest|WhatsApp)\b")
JUNK_SRC_RE = re.compile(r"loading|related|share|click|subscribe|facebook|twitter|pinterest|whatsapp|read more", re.I)
RUN_RE = re.compile(r"(.)\1{7,}")

# Interjecciones y onomatopeyas que se quedan igual en español.
INTERJECTIONS = {
    "oh", "ok", "okay", "no", "uh", "um", "umm", "eh", "ehh", "ah", "ahh", "hm", "hmm", "wow", "duh",
    "huh", "ugh", "yay", "ha", "haha", "hehe", "hehehe", "hey", "yeah", "yes", "zzz", "gulp", "phew",
    "ouch", "oops", "aww", "wha", "pfft", "shh", "tsk", "mmm", "yup", "nope",
}


def translatable_words(text):
    """Palabras que deberían traducirse (no interjecciones ni letras estiradas)."""
    rv = []
    for w in re.findall(r"[A-Za-z][A-Za-z'’-]*", text):
        low = w.lower().strip("'’-")
        if len(low) < 4 or low in INTERJECTIONS or re.search(r"([a-z])\1\1", low):
            continue
        if re.fullmatch(r"([a-z]{1,3})\1+", low):      # hehehe, hahaha
            continue
        rv.append(w)
    return rv


def runaway(src, dst):
    """
    True si el modelo se quedó repitiendo un carácter: una racha de 8 o más que
    sea claramente más larga que la más larga del original ("Sooooo" ->
    "Muuuuuuy" es normal; "Mhhhh" -> "Mhhhhhhhhhhhhhhhh..." no).
    """
    longest_src = max((len(m.group(0)) for m in re.finditer(r"(.)\1*", src)), default=0)
    return any(len(m.group(0)) > max(7, longest_src + 3) for m in re.finditer(r"(.)\1{7,}", dst))


def valid(item, translated):
    if not translated or not translated.strip():
        return False
    if JUNK_RE.search(translated) and not JUNK_SRC_RE.search(item.masked):
        return False
    if runaway(item.masked, translated):
        return False
    # "⁇" es como MarianMT escribe un carácter que no conoce.
    if u"⁇" in translated:
        return False
    if tl_editor.unmask(item, translated) is None:
        return False
    plain_src = MARK_RE.sub("", item.masked)
    plain = MARK_RE.sub("", translated)
    if len(plain) > len(plain_src) * 3 + 40:
        return False
    return True


# ---------------------------------------------------------------------------
# Proceso principal


class Control(object):
    """
    Pausa y cancelación desde la interfaz.
    """

    def __init__(self):
        self.cancelled = threading.Event()
        self.running = threading.Event()
        self.running.set()

    def wait(self):
        self.running.wait()
        return not self.cancelled.is_set()


def collect(tl_dir):
    files = []
    for dirpath, _dirnames, filenames in os.walk(tl_dir):
        for fn in sorted(filenames):
            if fn.endswith((".rpy", ".rpym")):
                files.append(os.path.join(dirpath, fn))
    return sorted(files)


def translate_folder(tl_dir, backend, work_dir, log=print, progress=None, control=None):
    """
    Traduce las líneas pendientes de `tl_dir`. `work_dir` es la carpeta del
    juego en salida/ (ahí van la copia de seguridad, la caché y el informe).
    Devuelve (traducidas, fallidas, cancelado).
    """
    control = control or Control()
    os.makedirs(work_dir, exist_ok=True)

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = os.path.join(work_dir, "copias_tl", "antes_auto-" + stamp)
    shutil.copytree(tl_dir, backup)
    log("Copia de seguridad: {}".format(backup))

    cache = Cache(os.path.join(work_dir, "cache_{}.json".format(backend.name)))

    parsed = []
    total = 0
    for path in collect(tl_dir):
        lines, items, bom = tl_editor.parse(path)
        if items:
            parsed.append((path, lines, items, bom))
            total += len(items)
    log("{} líneas pendientes de traducir en {} archivos.".format(total, len(parsed)))

    done = 0
    ok = 0
    failed = []
    cancelled = False
    if progress:
        progress(0, total)

    try:
        for path, lines, items, bom in parsed:
            rel = os.path.relpath(path, tl_dir)

            # Primero lo que ya está en la caché; el resto, por tandas.
            pending = []
            for it in items:
                hit = cache.get(it.masked)
                if hit is not None and valid(it, hit):
                    tl_editor.apply(lines, it, tl_editor.unmask(it, hit))
                    ok += 1
                    done += 1
                else:
                    pending.append(it)

            if pending:
                log("Traduciendo {} ({} líneas)...".format(rel, len(items)))

            for i in range(0, len(pending), backend.batch_size):
                if not control.wait():
                    cancelled = True
                    break
                batch = pending[i:i + backend.batch_size]
                try:
                    results = backend.translate(batch)
                except Exception as e:
                    log("  Error del traductor: {}".format(e))
                    results = [None] * len(batch)

                for it, t in zip(batch, results):
                    new_body = tl_editor.unmask(it, t) if t else None
                    if new_body is None:
                        failed.append((rel, it.lineno + 1, it.body))
                    else:
                        tl_editor.apply(lines, it, new_body)
                        cache.put(it.masked, t)
                        ok += 1
                done += len(batch)
                if progress:
                    progress(done, total)

            # Se guarda cada archivo al terminarlo (o al cancelar a mitad).
            tl_editor.write(path, lines, bom)
            cache.save()
            if progress:
                progress(done, total)
            if cancelled:
                break
    finally:
        cache.save()

    report = os.path.join(work_dir, "informe_traduccion.txt")
    with io.open(report, "w", encoding="utf-8") as f:
        f.write(u"Traducción automática con {} - {}\n".format(backend.name, stamp))
        f.write(u"Traducidas: {}  Fallidas: {}  {}\n\n".format(ok, len(failed), "(CANCELADO)" if cancelled else ""))
        if failed:
            f.write(u"Líneas que se quedaron sin traducir (revísalas a mano):\n")
            for rel, n, body in failed:
                f.write(u"  {}:{}  \"{}\"\n".format(rel, n, body))

    log("Traducidas: {}  Fallidas: {}{}".format(ok, len(failed), "  (cancelado)" if cancelled else ""))
    if failed:
        log("Informe de líneas fallidas: {}".format(report))
    return ok, failed, cancelled
