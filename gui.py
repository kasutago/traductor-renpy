"""
Interfaz gráfica del Traductor Ren'Py (tkinter).
"""

import io
import json
import os
import queue
import subprocess
import sys
import threading
import traceback
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import glosario_auto  # noqa: E402
import marian  # noqa: E402
import revision  # noqa: E402
import tl_editor  # noqa: E402
import traducir_auto  # noqa: E402
import traductor  # noqa: E402

CONFIG_FILE = os.path.join(HERE, "config.json")
LANGUAGE = "spanish"


def open_path(path):
    """
    Abre un archivo o carpeta con el programa del sistema.
    """
    if os.name == "nt":
        os.startfile(path)
    else:
        subprocess.Popen(["xdg-open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def load_config():
    try:
        with io.open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_config(cfg):
    with io.open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


class App(object):

    def __init__(self, root):
        self.root = root
        self.cfg = load_config()
        self.game = None
        self.worker = None
        self.control = None
        self.events = queue.Queue()

        root.title("Traductor Ren'Py " + traductor.VERSION)
        root.geometry("820x640")
        root.minsize(700, 520)

        pad = {"padx": 8, "pady": 4}
        main = ttk.Frame(root, padding=10)
        main.pack(fill="both", expand=True)

        # --- Juego ----------------------------------------------------------
        f = ttk.LabelFrame(main, text="Juego", padding=8)
        f.pack(fill="x")
        self.game_var = tk.StringVar(value=self.cfg.get("juego", ""))
        ttk.Entry(f, textvariable=self.game_var).grid(row=0, column=0, sticky="ew", **pad)
        ttk.Button(f, text="Examinar...", command=self.browse).grid(row=0, column=1, **pad)
        self.game_info = ttk.Label(f, text="Elige el ejecutable (.exe / .sh) o la carpeta del juego.")
        self.game_info.grid(row=1, column=0, columnspan=2, sticky="w", padx=8)
        f.columnconfigure(0, weight=1)
        self.game_var.trace_add("write", lambda *a: self.root.after(300, self.refresh_game))

        # --- Paso 1 ---------------------------------------------------------
        f = ttk.LabelFrame(main, text="1. Preparar el juego", padding=8)
        f.pack(fill="x", pady=(8, 0))
        self.btn_prepare = ttk.Button(f, text="Extraer y crear botón", command=self.prepare)
        self.btn_prepare.pack(side="left", **pad)
        ttk.Label(f, text="Extrae .rpa/.rpyc, crea tl/spanish y añade el botón de idioma.").pack(side="left")

        # --- Paso 2 ---------------------------------------------------------
        f = ttk.LabelFrame(main, text="2. Traducción automática (MarianMT, en tu PC)", padding=8)
        f.pack(fill="x", pady=(8, 0))
        self.marian_info = ttk.Label(f, text="")
        self.marian_info.grid(row=0, column=0, sticky="w", padx=8)

        bar = ttk.Frame(f)
        bar.grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.btn_translate = ttk.Button(bar, text="Traducir", command=self.translate)
        self.btn_translate.pack(side="left", **pad)
        self.btn_pause = ttk.Button(bar, text="Pausar", command=self.toggle_pause, state="disabled")
        self.btn_pause.pack(side="left", **pad)
        self.btn_cancel = ttk.Button(bar, text="Cancelar", command=self.cancel, state="disabled")
        self.btn_cancel.pack(side="left", **pad)
        ttk.Button(bar, text="Editar glosario", command=self.edit_glossary).pack(side="left", **pad)
        self.btn_suggest = ttk.Button(bar, text="Sugerir glosario...", command=self.suggest_glossary)
        self.btn_suggest.pack(side="left", **pad)
        self.btn_review = ttk.Button(bar, text="Revisar traducción...", command=self.review)
        self.btn_review.pack(side="left", **pad)

        # --- Paso 3 ---------------------------------------------------------
        f = ttk.LabelFrame(main, text="3. Compartir", padding=8)
        f.pack(fill="x", pady=(8, 0))
        self.btn_export = ttk.Button(f, text="Exportar traducción", command=self.export)
        self.btn_export.pack(side="left", **pad)
        ttk.Label(f, text="Crea un .zip con la traducción y el botón (sin el juego) para compartirlo.").pack(side="left")

        # --- Progreso y registro --------------------------------------------
        f = ttk.Frame(main)
        f.pack(fill="x", pady=(10, 0))
        self.progress = ttk.Progressbar(f, mode="determinate")
        self.progress.pack(side="left", fill="x", expand=True)
        self.progress_label = ttk.Label(f, text="", width=16, anchor="e")
        self.progress_label.pack(side="left", padx=(8, 0))

        f = ttk.Frame(main)
        f.pack(fill="both", expand=True, pady=(8, 0))
        self.logbox = tk.Text(f, height=12, wrap="word", state="disabled", font=("Consolas", 9))
        sb = ttk.Scrollbar(f, command=self.logbox.yview)
        self.logbox.configure(yscrollcommand=sb.set)
        self.logbox.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        # Los mensajes de traductor.py van al registro.
        traductor.log = self.log

        self.refresh_game()
        self.refresh_marian()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(100, self.poll)

    # --- utilidades --------------------------------------------------------

    def log(self, msg=""):
        self.events.put(("log", str(msg)))

    def set_progress(self, done, total):
        self.events.put(("progress", (done, total)))

    def poll(self):
        try:
            while True:
                kind, data = self.events.get_nowait()
                if kind == "log":
                    self.logbox.configure(state="normal")
                    self.logbox.insert("end", data + "\n")
                    self.logbox.see("end")
                    self.logbox.configure(state="disabled")
                elif kind == "progress":
                    done, total = data
                    self.progress["maximum"] = max(total, 1)
                    self.progress["value"] = done
                    self.progress_label["text"] = "{}/{}".format(done, total)
                elif kind == "call":
                    data()
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def ui(self, fn):
        self.events.put(("call", fn))

    def busy(self):
        return self.worker is not None and self.worker.is_alive()

    def run(self, target, on_done=None):
        def wrapper():
            try:
                target()
            except SystemExit as e:
                self.log("ERROR: {}".format(e))
            except Exception as e:
                self.log("ERROR: {}".format(e))
                self.log(traceback.format_exc())
            finally:
                self.ui(self.finish)
                if on_done:
                    self.ui(on_done)

        self.set_buttons(True)
        self.worker = threading.Thread(target=wrapper, daemon=True)
        self.worker.start()

    def finish(self):
        self.control = None
        self.set_buttons(False)
        self.refresh_game()

    def set_buttons(self, working):
        state = "disabled" if working else "normal"
        self.btn_prepare["state"] = state if self.game else "disabled"
        self.btn_translate["state"] = state if self.has_tl() else "disabled"
        self.btn_export["state"] = state if self.has_tl() else "disabled"
        self.btn_suggest["state"] = state if self.has_tl() else "disabled"
        self.btn_review["state"] = state if self.has_tl() else "disabled"
        self.btn_pause["state"] = "normal" if working and self.control else "disabled"
        self.btn_cancel["state"] = "normal" if working and self.control else "disabled"
        self.btn_pause["text"] = "Pausar"

    # --- juego -------------------------------------------------------------

    def browse(self):
        path = filedialog.askopenfilename(title="Elige el ejecutable del juego (.exe / .sh)",
                                          filetypes=[("Juego", "*.exe *.sh *.py"), ("Todos", "*.*")])
        if path:
            self.game_var.set(path)

    def refresh_game(self):
        path = self.game_var.get().strip().strip('"')
        self.game = None
        if path and os.path.exists(path):
            try:
                self.game = traductor.Game(path)
            except SystemExit as e:
                self.game_info["text"] = str(e)
        if self.game:
            state = "con traducción instalada" if self.has_tl() else "sin preparar"
            self.game_info["text"] = "{}  ·  Ren'Py {}  ·  {}".format(self.game.name, self.game.version(), state)
            self.cfg["juego"] = path
        elif path:
            if not str(self.game_info["text"]).startswith("No "):
                self.game_info["text"] = "No es un juego de Ren'Py válido."
        if not self.busy():
            self.set_buttons(False)

    def has_tl(self):
        return bool(self.game) and os.path.isdir(os.path.join(self.game.gamedir, "tl", LANGUAGE))

    def prepare(self):
        game = self.game

        def job():
            traductor.run_all(game, traductor.DEFAULT_OUT, LANGUAGE)
            self.log("Ya puedes usar la traducción automática.")

        self.run(job)

    def export(self):
        game = self.game

        def job():
            pkg, _zip = traductor.cmd_export(game, traductor.DEFAULT_OUT, LANGUAGE)
            self.ui(lambda: open_path(os.path.dirname(pkg)))

        self.run(job)

    # --- MarianMT -----------------------------------------------------------

    def refresh_marian(self):
        if marian.is_installed():
            self.marian_info["text"] = "MarianMT instalado. Traduce en la CPU, sin internet."
        else:
            self.marian_info["text"] = ("MarianMT se instalará la primera vez que traduzcas "
                                        "(unos minutos, ~1 GB temporal).")

    def edit_glossary(self):
        traducir_auto.load_glossary()  # crea el archivo si no existe
        open_path(traducir_auto.GLOSSARY_FILE)

    # --- glosario automático y revisión -----------------------------------------

    def tl_dir(self):
        return os.path.join(self.game.gamedir, "tl", LANGUAGE)

    def _background(self, work, done):
        """
        Calcula work() en un hilo (para no congelar la ventana con juegos grandes)
        y luego llama a done(resultado) desde la interfaz.
        """
        def runner():
            try:
                rv = work()
            except Exception as e:
                self.log("ERROR: {}".format(e))
                self.log(traceback.format_exc())
                return
            self.ui(lambda: done(rv))
        threading.Thread(target=runner, daemon=True).start()

    def suggest_glossary(self):
        self.log("Buscando nombres y palabras sin traducir...")
        tl = self.tl_dir()
        self._background(lambda: glosario_auto.sugerencias(tl, traducir_auto.load_glossary()),
                         lambda rv: self._glossary_window(*rv))

    def _glossary_window(self, text, n_names, n_left):
        win = tk.Toplevel(self.root)
        win.title("Sugerencias de glosario")
        win.geometry("640x540")
        ttk.Label(win, wraplength=600, justify="left", text=(
            "{} nombres y {} palabras sin traducir. Edita o borra lo que no quieras y pulsa "
            "Añadir. Las líneas que empiezan por # no se guardan: quítales el # para usarlas."
        ).format(n_names, n_left)).pack(fill="x", padx=10, pady=8)
        f = ttk.Frame(win)
        f.pack(fill="both", expand=True, padx=10)
        box = tk.Text(f, wrap="none", font=("Consolas", 10))
        sb = ttk.Scrollbar(f, command=box.yview)
        box.configure(yscrollcommand=sb.set)
        box.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        box.insert("1.0", text)

        def add():
            lines = [l.strip() for l in box.get("1.0", "end").splitlines()
                     if l.strip() and not l.strip().startswith("#") and "=" in l]
            if not lines:
                messagebox.showinfo("Glosario", "No hay ninguna línea activa para añadir.", parent=win)
                return
            with io.open(traducir_auto.GLOSSARY_FILE, "a", encoding="utf-8") as g:
                g.write("\n# Añadido desde las sugerencias\n" + "\n".join(lines) + "\n")
            self.log("Glosario: {} entradas añadidas.".format(len(lines)))
            win.destroy()

        bar = ttk.Frame(win)
        bar.pack(fill="x", padx=10, pady=8)
        ttk.Button(bar, text="Añadir al glosario", command=add).pack(side="right", padx=4)
        ttk.Button(bar, text="Cerrar", command=win.destroy).pack(side="right", padx=4)

    def review(self):
        self.log("Cargando la traducción para revisarla...")
        tl = self.tl_dir()
        self._background(lambda: revision.collect(tl), self._review_window)

    def _review_window(self, entries):
        index = {id(e): i for i, e in enumerate(entries)}
        win = tk.Toplevel(self.root)
        win.title("Revisar traducción")
        win.geometry("1060x700")

        top = ttk.Frame(win)
        top.pack(fill="x", padx=10, pady=8)
        ttk.Label(top, text="Mostrar:").pack(side="left")
        filtros = [("Todas las que tienen problemas", None)] + [(n, k) for k, n in revision.PROBLEMAS] + \
                  [("Todas las líneas", "todas")]
        combo = ttk.Combobox(top, state="readonly", width=32, values=[n for n, _k in filtros])
        combo.current(0)
        combo.pack(side="left", padx=6)
        ttk.Label(top, text="Buscar:").pack(side="left", padx=(12, 0))
        search = tk.StringVar()
        search_entry = ttk.Entry(top, textvariable=search, width=26)
        search_entry.pack(side="left", padx=6)
        count = ttk.Label(top, text="")
        count.pack(side="left", padx=8)

        body = ttk.PanedWindow(win, orient="horizontal")
        body.pack(fill="both", expand=True, padx=10)
        left = ttk.Frame(body)
        tree = ttk.Treeview(left, columns=("pos", "prob"), show="headings", selectmode="browse")
        tree.heading("pos", text="Archivo : línea")
        tree.heading("prob", text="Problema")
        tree.column("pos", width=200)
        tree.column("prob", width=170)
        sb = ttk.Scrollbar(left, command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        body.add(left, weight=2)

        right = ttk.Frame(body)
        ttk.Label(right, text="Contexto (líneas de alrededor)").pack(anchor="w")
        ctx_box = tk.Text(right, height=7, wrap="word", state="disabled", font=("Consolas", 9), foreground="#666")
        ctx_box.pack(fill="x")
        ttk.Label(right, text="Original").pack(anchor="w", pady=(8, 0))
        src_box = tk.Text(right, height=5, wrap="word", state="disabled", font=("Consolas", 10))
        src_box.pack(fill="x")
        ttk.Label(right, text="Traducción (editable)").pack(anchor="w", pady=(8, 0))
        dst_box = tk.Text(right, height=7, wrap="word", font=("Consolas", 10), undo=True)
        dst_box.pack(fill="both", expand=True)
        status = ttk.Label(right, text="", foreground="#a33", wraplength=420, justify="left")
        status.pack(anchor="w", pady=4)
        body.add(right, weight=3)

        state = {"shown": [], "cur": None, "tr": None, "busy": False}

        def label(e):
            return ", ".join(revision.NOMBRES[p] for p in e.problemas) or "-"

        def refilter(*_a):
            key = filtros[combo.current()][1]
            if key is None:
                shown = [e for e in entries if e.problemas]
            elif key == "todas":
                shown = list(entries)
            else:
                shown = [e for e in entries if key in e.problemas]
            q = search.get().strip().lower()
            if q:
                shown = [e for e in shown if q in e.src.lower() or q in e.dst.lower()]
            state["shown"] = shown
            state["cur"] = None
            tree.delete(*tree.get_children())
            for i, e in enumerate(shown):
                tree.insert("", "end", iid=str(i), values=("{} : {}".format(e.rel, e.lineno + 1), label(e)))
            count.configure(text="{} líneas".format(len(shown)))
            for box in (ctx_box, src_box):
                box.configure(state="normal")
                box.delete("1.0", "end")
                box.configure(state="disabled")
            dst_box.delete("1.0", "end")
            if shown:
                tree.selection_set("0")
                tree.focus("0")

        def set_text(box, text):
            box.configure(state="normal")
            box.delete("1.0", "end")
            box.insert("1.0", text)
            box.configure(state="disabled")

        def context(e):
            j = index[id(e)]
            near = [k for k in range(j - 2, j + 3) if 0 <= k < len(entries) and entries[k].path == e.path]
            out = []
            for k in near:
                n = entries[k]
                who = (n.who + ": ") if n.who else ""
                if k == j:
                    out.append(u"►  (esta línea)")
                else:
                    out.append(u"   {}{}\n   → {}".format(who, n.src, n.dst))
            return "\n".join(out)

        def show(_ev=None):
            sel = tree.selection()
            if not sel:
                return
            e = state["shown"][int(sel[0])]
            state["cur"] = int(sel[0])
            set_text(ctx_box, context(e))
            set_text(src_box, (e.who + ": " if e.who else "") + e.src)
            dst_box.delete("1.0", "end")
            dst_box.insert("1.0", e.dst)
            dst_box.edit_reset()
            status.configure(text="")

        def save(next_=True):
            i = state["cur"]
            if i is None:
                return
            e = state["shown"][i]
            err = revision.guardar(e, dst_box.get("1.0", "end").rstrip("\n"))
            if err:
                status.configure(text=err)
                return
            tree.set(str(i), "prob", label(e))
            if next_ and i + 1 < len(state["shown"]):
                tree.selection_set(str(i + 1))
                tree.focus(str(i + 1))
                tree.see(str(i + 1))
            else:
                status.configure(text="Guardado.")

        def restore():
            i = state["cur"]
            if i is not None:
                dst_box.delete("1.0", "end")
                dst_box.insert("1.0", state["shown"][i].dst)

        def retranslate():
            i = state["cur"]
            if i is None or state["busy"]:
                return
            if not marian.is_installed():
                status.configure(text="MarianMT no está instalado: traduce el juego una vez primero.")
                return
            e = state["shown"][i]
            state["busy"] = True
            status.configure(text="Traduciendo con MarianMT...")

            def work():
                if state["tr"] is None:
                    state["tr"] = marian.MarianTranslator(workers=1)
                backend = traducir_auto.MarianBackend(state["tr"], traducir_auto.load_glossary())
                it = tl_editor.Item(e.path, e.lineno, e.start, e.end, e.src, e.who, "say")
                out = backend.translate([it])[0]
                return tl_editor.unmask(it, out) if out else None

            def done(body):
                state["busy"] = False
                if not win.winfo_exists():
                    return
                if state["cur"] != i:
                    return
                if body is None:
                    status.configure(text="MarianMT no pudo traducir esta línea.")
                    return
                dst_box.delete("1.0", "end")
                dst_box.insert("1.0", body)
                status.configure(text="Traducción nueva (aún sin guardar).")

            def fail_safe():
                try:
                    return work()
                finally:
                    state["busy"] = False
            self._background(fail_safe, done)

        def close():
            if state["tr"] is not None:
                state["tr"].close()
            win.destroy()

        bar = ttk.Frame(right)
        bar.pack(fill="x", pady=4)
        ttk.Button(bar, text="Guardar y siguiente (Ctrl+Enter)", command=save).pack(side="left", padx=2)
        ttk.Button(bar, text="Restaurar", command=restore).pack(side="left", padx=2)
        ttk.Button(bar, text="Traducir de nuevo", command=retranslate).pack(side="left", padx=2)
        dst_box.bind("<Control-Return>", lambda ev: (save(), "break")[1])
        tree.bind("<<TreeviewSelect>>", show)
        combo.bind("<<ComboboxSelected>>", refilter)
        search_entry.bind("<Return>", refilter)
        win.protocol("WM_DELETE_WINDOW", close)
        refilter()
        self.review_win = {"win": win, "tree": tree, "dst": dst_box, "save": save, "combo": combo,
                           "refilter": refilter, "state": state, "status": status, "search": search,
                           "context": ctx_box, "retranslate": retranslate, "close": close}

    # --- traducción ----------------------------------------------------------

    def translate(self):
        game = self.game
        save_config(self.cfg)

        tl_dir = os.path.join(game.gamedir, "tl", LANGUAGE)
        work_dir = traductor.out_dir_for(game, traductor.DEFAULT_OUT)
        self.control = traducir_auto.Control()
        control = self.control

        def job():
            glossary = traducir_auto.load_glossary()
            if not marian.is_installed():
                self.log("Instalando MarianMT (solo la primera vez)...")
                marian.setup(self.log)
                self.ui(self.refresh_marian)
            tr = marian.MarianTranslator()
            self.log("Traduciendo con MarianMT (CPU).")
            backend = traducir_auto.MarianBackend(tr, glossary)

            try:
                ok, failed, cancelled = traducir_auto.translate_folder(
                    tl_dir, backend, work_dir, self.log, self.set_progress, control)
            finally:
                tr.close()

            self.log("Comprobando con el motor del juego que todo compila...")
            good, err = traductor.lint_check(game, traductor.DEFAULT_OUT)
            if good:
                self.log("Comprobación correcta: el juego carga sin errores.")
                msg = "Traducción terminada.\n\nTraducidas: {}\nSin traducir: {}".format(ok, len(failed))
                if cancelled:
                    msg = "Traducción cancelada. Lo hecho se ha guardado.\n\nTraducidas: {}".format(ok)
                self.ui(lambda: messagebox.showinfo("Traductor Ren'Py", msg))
            else:
                self.log("ERROR en la comprobación:\n" + err)
                self.ui(lambda: messagebox.showerror(
                    "Traductor Ren'Py",
                    "El juego da un error con la traducción. Mira el registro.\n"
                    "Hay una copia de seguridad en salida/<juego>/copias_tl."))

        self.run(job)
        self.set_buttons(True)

    def toggle_pause(self):
        if not self.control:
            return
        if self.control.running.is_set():
            self.control.running.clear()
            self.btn_pause["text"] = "Reanudar"
            self.log("En pausa.")
        else:
            self.control.running.set()
            self.btn_pause["text"] = "Pausar"
            self.log("Reanudado.")

    def cancel(self):
        if self.control:
            self.control.cancelled.set()
            self.control.running.set()
            self.log("Cancelando al terminar la tanda actual...")

    def on_close(self):
        if self.busy():
            if not messagebox.askyesno("Traductor Ren'Py", "Hay un trabajo en curso. ¿Salir igualmente?"):
                return
        save_config(self.cfg)
        self.root.destroy()


def main():
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
