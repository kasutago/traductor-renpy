"""
MarianMT (Helsinki-NLP/opus-mt-tc-big-en-es) en CPU con CTranslate2.

Todo vive en herramientas/marian/:
  venv/      Python aislado con ctranslate2 + sentencepiece (para traducir)
  modelo/    el modelo convertido a CTranslate2 (int8)

La instalación (setup) se hace una sola vez: crea un entorno temporal con
PyTorch (CPU) + transformers para convertir el modelo oficial y luego lo
borra junto con las cachés. Nada se instala fuera de la carpeta.

La traducción se hace en un proceso aparte (marian_worker.py) que se
comunica por stdin/stdout con JSON, para no mezclar paquetes con este Python.
"""

import json
import os
import shutil
import subprocess
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.join(HERE, "herramientas", "marian")
VENV = os.path.join(BASE, "venv")
MODEL = os.path.join(BASE, "modelo")
TMP = os.path.join(BASE, "_convertir")
WORKER = os.path.join(HERE, "marian_worker.py")

HF_MODEL = "Helsinki-NLP/opus-mt-tc-big-en-es"


WINDOWS = os.name == "nt"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _bin(venv, name):
    """
    Ejecutable dentro de un venv (Scripts\\x.exe en Windows, bin/x en Linux).
    """
    if WINDOWS:
        return os.path.join(venv, "Scripts", name + ".exe")
    return os.path.join(venv, "bin", name)


def _py(venv):
    return _bin(venv, "python")


def is_installed():
    return (os.path.isfile(_py(VENV)) and os.path.isfile(os.path.join(MODEL, "model.bin"))
            and os.path.isfile(os.path.join(MODEL, "source.spm")))


def _env():
    env = dict(os.environ)
    for k in ("PYTHONPATH", "PYTHONHOME"):
        env.pop(k, None)
    env["HF_HOME"] = os.path.join(TMP, "hf_cache")
    env["HF_HUB_DISABLE_TELEMETRY"] = "1"
    env["PIP_NO_CACHE_DIR"] = "1"
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _run(cmd, log):
    log("  > " + " ".join(os.path.basename(c) if i == 0 else c for i, c in enumerate(cmd)))
    proc = subprocess.Popen(cmd, env=_env(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            creationflags=NO_WINDOW)
    tail = []
    for raw in proc.stdout:
        line = raw.decode("utf-8", "replace").rstrip()
        tail = (tail + [line])[-30:]
    if proc.wait() != 0:
        raise RuntimeError("Falló: {}\n{}".format(" ".join(cmd), "\n".join(tail)))


def setup(log=print):
    """
    Instala MarianMT. Tarda unos minutos y descarga ~1 GB (temporal).
    """
    if is_installed():
        log("MarianMT ya está instalado.")
        return

    os.makedirs(BASE, exist_ok=True)

    if not os.path.isfile(_py(VENV)):
        log("Creando entorno de MarianMT...")
        _run([sys.executable, "-m", "venv", VENV], log)
        _run([_py(VENV), "-m", "pip", "install", "ctranslate2", "sentencepiece"], log)

    log("Preparando conversión del modelo (temporal, se borrará al terminar)...")
    if os.path.isdir(TMP):
        shutil.rmtree(TMP, ignore_errors=True)
    tvenv = os.path.join(TMP, "venv")
    _run([sys.executable, "-m", "venv", tvenv], log)
    _run([_py(tvenv), "-m", "pip", "install", "torch", "--index-url", "https://download.pytorch.org/whl/cpu"], log)
    _run([_py(tvenv), "-m", "pip", "install", "transformers", "sentencepiece", "safetensors", "ctranslate2"], log)

    log("Descargando y convirtiendo {} ...".format(HF_MODEL))
    if os.path.isdir(MODEL):
        shutil.rmtree(MODEL)
    converter = _bin(tvenv, "ct2-transformers-converter")
    _run([converter, "--model", HF_MODEL, "--output_dir", MODEL, "--quantization", "int8",
          "--copy_files", "source.spm", "target.spm"], log)

    log("Limpiando archivos temporales...")
    shutil.rmtree(TMP, ignore_errors=True)

    if not is_installed():
        raise RuntimeError("La instalación de MarianMT no se completó.")
    log("MarianMT instalado.")


class MarianTranslator(object):
    """
    Traductor por lotes. Arranca varios workers (uno por cada ~3 hilos de la
    CPU: un solo proceso no escala con más hilos) y reparte entre ellos cada
    lote. Se reutilizan mientras dure el objeto.
    """

    def __init__(self, threads=0, workers=0):
        if not is_installed():
            raise RuntimeError("MarianMT no está instalado.")
        cpus = os.cpu_count() or 4
        if not workers:
            workers = 4 if cpus >= 12 else (2 if cpus >= 6 else 1)
        if not threads:
            threads = max(1, cpus // workers)
        self.threads = threads
        self.procs = []
        try:
            for _ in range(workers):
                self.procs.append(self._start(threads))
        except Exception:
            self.close()
            raise

    @staticmethod
    def _start(threads):
        proc = subprocess.Popen(
            [_py(VENV), "-E", "-s", WORKER, MODEL, str(threads)],
            env=_env(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            creationflags=NO_WINDOW)
        ready = proc.stdout.readline()
        if not ready:
            raise RuntimeError("No se pudo iniciar MarianMT.")
        return proc

    @staticmethod
    def _ask(proc, texts):
        proc.stdin.write((json.dumps(texts, ensure_ascii=False) + "\n").encode("utf-8"))
        proc.stdin.flush()
        line = proc.stdout.readline()
        if not line:
            raise RuntimeError("MarianMT se cerró inesperadamente.")
        return json.loads(line.decode("utf-8"))

    def _ask_retry(self, k, texts):
        """
        Pregunta al worker k. Si se ha caído (cerrado, sin memoria...), lo arranca
        de nuevo y reintenta una vez.
        """
        try:
            return self._ask(self.procs[k], texts)
        except Exception:
            dead = self.procs[k]
            for action in (dead.kill, dead.stdin.close, dead.stdout.close):
                try:
                    action()
                except Exception:
                    pass
            self.procs[k] = self._start(self.threads)
            return self._ask(self.procs[k], texts)

    def translate(self, texts):
        n = len(self.procs)
        if n == 1 or len(texts) < 4 * n:
            return self._ask_retry(0, texts)
        # Reparto equilibrado: las frases largas se reparten entre todos.
        order = sorted(range(len(texts)), key=lambda i: -len(texts[i]))
        groups = [order[k::n] for k in range(n)]
        out = [None] * len(texts)
        errors = []

        def work(k, idx):
            try:
                for i, t in zip(idx, self._ask_retry(k, [texts[i] for i in idx])):
                    out[i] = t
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=work, args=(k, g)) for k, g in enumerate(groups) if g]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        if errors:
            raise errors[0]
        return out

    def close(self):
        for proc in self.procs:
            try:
                proc.stdin.close()
                proc.wait(timeout=10)
            except Exception:
                proc.kill()


if __name__ == "__main__":
    setup()
