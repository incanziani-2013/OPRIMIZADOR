"""
OptimizadorPC - Optimizador para Windows con interfaz moderna.
Dependencias: customtkinter, psutil
"""
import ctypes
import os
import subprocess
import sys
import threading
import time
import tkinter as tk
from collections import deque
from tkinter import ttk, messagebox

import customtkinter as ctk
import psutil

IS_WIN = os.name == "nt"
NO_WINDOW = 0x08000000 if IS_WIN else 0

# ----------------------------------------------------------------- tema
C = dict(
    bg="#0A0E1A", side="#0D1221", card="#121A2E", card2="#1A2440", line="#222D4A",
    track="#1E2846", text="#E8ECF8", mute="#8A94B3",
    a1="#22D3EE", a2="#8B5CF6", ok="#34D399", warn="#FBBF24", bad="#F87171",
)
FONT = "Segoe UI"

ctk.set_appearance_mode("dark")


# ----------------------------------------------------------------- utilidades
def fmt(n):
    n = float(n)
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} PB"


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def run_cmd(cmd):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, shell=True,
                           timeout=30, creationflags=NO_WINDOW)
        return r.returncode == 0
    except Exception:
        return False


def env(name, *parts):
    base = os.environ.get(name)
    return os.path.join(base, *parts) if base else None


def dir_size(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except Exception:
                pass
    return total


def clean_path(path):
    """Borra el contenido de una carpeta (sin borrar la carpeta). Devuelve bytes liberados."""
    freed = 0
    for root, dirs, files in os.walk(path, topdown=False):
        for f in files:
            p = os.path.join(root, f)
            try:
                size = os.path.getsize(p)
                os.remove(p)
                freed += size
            except Exception:
                pass  # en uso: se saltea
        for d in dirs:
            try:
                os.rmdir(os.path.join(root, d))
            except Exception:
                pass
    return freed


class _RBInfo(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("cbSize", ctypes.c_ulong), ("i64Size", ctypes.c_longlong),
                ("i64NumItems", ctypes.c_longlong)]


def bin_size():
    try:
        info = _RBInfo()
        info.cbSize = ctypes.sizeof(info)
        ctypes.windll.shell32.SHQueryRecycleBinW(None, ctypes.byref(info))
        return max(0, info.i64Size)
    except Exception:
        return 0


def empty_bin():
    try:
        ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, 0x7)
        return True
    except Exception:
        return False


def free_ram():
    """Libera memoria no usada de los procesos. Devuelve bytes recuperados."""
    if not IS_WIN:
        return 0
    before = psutil.virtual_memory().available
    k32, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
    for p in psutil.process_iter(["pid"]):
        try:
            h = k32.OpenProcess(0x1F0FFF, False, p.info["pid"])
            if h:
                psapi.EmptyWorkingSet(h)
                k32.CloseHandle(h)
        except Exception:
            pass
    time.sleep(0.4)
    return max(0, psutil.virtual_memory().available - before)


# categorías de limpieza: (clave, título, descripción, función que devuelve carpetas)
CATS = [
    ("temp", "Temporales de usuario", "Archivos temporales que dejan las aplicaciones",
     lambda: [os.environ.get("TEMP")]),
    ("wintemp", "Temporales de Windows", "Carpeta Temp del sistema",
     lambda: [env("WINDIR", "Temp")]),
    ("crash", "Informes de errores", "Volcados de memoria y reportes de fallos",
     lambda: [env("LOCALAPPDATA", "CrashDumps"),
              env("PROGRAMDATA", "Microsoft", "Windows", "WER", "ReportQueue")]),
    ("chrome", "Caché de Google Chrome", "Se vuelve a generar sola al navegar",
     lambda: [env("LOCALAPPDATA", "Google", "Chrome", "User Data", "Default", "Cache"),
              env("LOCALAPPDATA", "Google", "Chrome", "User Data", "Default", "Code Cache")]),
    ("edge", "Caché de Microsoft Edge", "Se vuelve a generar sola al navegar",
     lambda: [env("LOCALAPPDATA", "Microsoft", "Edge", "User Data", "Default", "Cache"),
              env("LOCALAPPDATA", "Microsoft", "Edge", "User Data", "Default", "Code Cache")]),
    ("wu", "Descargas de Windows Update", "Instaladores ya descargados",
     lambda: [env("WINDIR", "SoftwareDistribution", "Download")]),
    ("bin", "Papelera de reciclaje", "Archivos que ya borraste", None),
]
QUICK = ("temp", "wintemp", "crash", "chrome", "edge")


def cat_paths(fn):
    return [p for p in (fn() if fn else []) if p and os.path.isdir(p)]


def cat_size(key):
    for k, _t, _d, fn in CATS:
        if k == key:
            return bin_size() if key == "bin" else sum(dir_size(p) for p in cat_paths(fn))
    return 0


def cat_clean(key):
    for k, _t, _d, fn in CATS:
        if k == key:
            if key == "bin":
                before = bin_size()
                empty_bin()
                return before
            return sum(clean_path(p) for p in cat_paths(fn))
    return 0


# ----------------------------------------------------------------- inicio (registro)
RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"
BACKUP = r"Software\OptimizadorPC\Disabled"


def _enum(hive, path):
    import winreg
    out = []
    try:
        with winreg.OpenKey(hive, path) as k:
            i = 0
            while True:
                try:
                    n, v, _ = winreg.EnumValue(k, i)
                    out.append((n, v))
                    i += 1
                except OSError:
                    break
    except OSError:
        pass
    return out


def startup_list():
    if not IS_WIN:
        return []
    import winreg
    rows = []
    for origin, hive in (("Usuario", winreg.HKEY_CURRENT_USER), ("Sistema", winreg.HKEY_LOCAL_MACHINE)):
        for n, v in _enum(hive, RUN):
            rows.append((origin, n, v, "Activo"))
        for n, v in _enum(winreg.HKEY_CURRENT_USER, BACKUP + "\\" + origin):
            rows.append((origin, n, v, "Desactivado"))
    return rows


def startup_toggle(origin, name, cmd, state):
    import winreg
    hive = winreg.HKEY_CURRENT_USER if origin == "Usuario" else winreg.HKEY_LOCAL_MACHINE
    bk = BACKUP + "\\" + origin
    if state == "Activo":  # desactivar: primero respaldo, después borrar
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, bk) as k:
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, cmd)
        with winreg.OpenKey(hive, RUN, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, name)
    else:  # reactivar
        with winreg.OpenKey(hive, RUN, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, cmd)
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, bk, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, name)


# ----------------------------------------------------------------- widgets propios
class Gauge(tk.Canvas):
    def __init__(self, master, title, size=190):
        super().__init__(master, width=size, height=size, bg=C["card"], highlightthickness=0)
        self.size, self.title = size, title
        self.set(0, "")

    def set(self, pct, sub):
        self.delete("all")
        s, p = self.size, 16
        box = (p, p, s - p, s - p)
        self.create_arc(*box, start=225, extent=-270, style="arc", outline=C["track"], width=13)
        col = C["bad"] if pct >= 90 else C["warn"] if pct >= 75 else C["a1"]
        if pct > 0.5:
            self.create_arc(*box, start=225, extent=-270 * min(pct, 100) / 100,
                            style="arc", outline=col, width=13)
        self.create_text(s / 2, s / 2 - 8, text=f"{pct:.0f}%", fill=C["text"], font=(FONT, 28, "bold"))
        self.create_text(s / 2, s / 2 + 22, text=self.title, fill=C["mute"], font=(FONT, 11))
        self.create_text(s / 2, s - 12, text=sub, fill=C["mute"], font=(FONT, 9))


class Graph(tk.Canvas):
    def __init__(self, master, n=60):
        super().__init__(master, bg=C["card"], highlightthickness=0, height=190)
        self.cpu = deque([0] * n, maxlen=n)
        self.ram = deque([0] * n, maxlen=n)
        self.bind("<Configure>", lambda _e: self.redraw())

    def push(self, c, r):
        self.cpu.append(c)
        self.ram.append(r)
        self.redraw()

    def redraw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 40:
            return
        pad, top = 12, 30
        for i in range(5):
            y = top + (h - top - pad) * i / 4
            self.create_line(pad, y, w - pad, y, fill=C["line"])
        for data, col in ((self.ram, C["a2"]), (self.cpu, C["a1"])):
            n = len(data)
            pts = []
            for i, v in enumerate(data):
                pts += [pad + (w - 2 * pad) * i / (n - 1), h - pad - (h - top - pad) * v / 100]
            self.create_line(*pts, fill=col, width=2, smooth=True)
        self.create_text(pad, 14, anchor="w", text="●  CPU", fill=C["a1"], font=(FONT, 10, "bold"))
        self.create_text(pad + 70, 14, anchor="w", text="●  RAM", fill=C["a2"], font=(FONT, 10, "bold"))


def card(parent, **kw):
    return ctk.CTkFrame(parent, fg_color=C["card"], corner_radius=16,
                        border_width=1, border_color=C["line"], **kw)


def label(parent, text, size=13, color=None, bold=False, **kw):
    return ctk.CTkLabel(parent, text=text, text_color=color or C["text"],
                        font=ctk.CTkFont(FONT, size, "bold" if bold else "normal"), **kw)


def button(parent, text, cmd, primary=False, width=150, **kw):
    if primary:
        return ctk.CTkButton(parent, text=text, command=cmd, width=width, height=40, corner_radius=10,
                             fg_color=C["a1"], hover_color="#06B6D4", text_color="#04202A",
                             font=ctk.CTkFont(FONT, 13, "bold"), **kw)
    return ctk.CTkButton(parent, text=text, command=cmd, width=width, height=40, corner_radius=10,
                         fg_color=C["card2"], hover_color=C["line"], text_color=C["text"],
                         border_width=1, border_color=C["line"], font=ctk.CTkFont(FONT, 13), **kw)


def header(parent, title, sub):
    f = ctk.CTkFrame(parent, fg_color="transparent")
    label(f, title, 26, bold=True).pack(anchor="w")
    label(f, sub, 13, C["mute"]).pack(anchor="w", pady=(2, 0))
    return f


def make_tree(parent, cols, widths):
    style = ttk.Style()
    style.theme_use("clam")
    style.configure("Pro.Treeview", background=C["card"], fieldbackground=C["card"],
                    foreground=C["text"], rowheight=32, borderwidth=0, font=(FONT, 10))
    style.configure("Pro.Treeview.Heading", background=C["card2"], foreground=C["mute"],
                    borderwidth=0, relief="flat", font=(FONT, 10, "bold"), padding=(8, 8))
    style.layout("Pro.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
    style.map("Pro.Treeview", background=[("selected", C["a2"])], foreground=[("selected", "#FFFFFF")])
    style.map("Pro.Treeview.Heading", background=[("active", C["line"])])
    wrap = ctk.CTkFrame(parent, fg_color=C["card"], corner_radius=16, border_width=1, border_color=C["line"])
    tree = ttk.Treeview(wrap, columns=[c[0] for c in cols], show="headings",
                        style="Pro.Treeview", selectmode="browse")
    for (cid, title), w in zip(cols, widths):
        tree.heading(cid, text=title, anchor="w")
        tree.column(cid, width=w, anchor="w")
    sb = ctk.CTkScrollbar(wrap, command=tree.yview, fg_color=C["card"],
                          button_color=C["line"], button_hover_color=C["mute"])
    tree.configure(yscrollcommand=sb.set, takefocus=0)
    tree.configure(style="Pro.Treeview")
    sb.pack(side="right", fill="y", padx=(0, 6), pady=10)
    tree.pack(side="left", fill="both", expand=True, padx=(10, 0), pady=10)
    return wrap, tree


# ----------------------------------------------------------------- aplicación
class App(ctk.CTk):
    def __init__(self):
        super().__init__(fg_color=C["bg"])
        self.title("OptimizadorPC")
        self.geometry("1120x730")
        self.minsize(1000, 660)
        self.busy = False
        self.pcache = {}
        self.cat_vars, self.cat_labels = {}, {}

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.build_sidebar()

        self.content = ctk.CTkFrame(self, fg_color="transparent")
        self.content.grid(row=0, column=1, sticky="nsew", padx=28, pady=24)
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=1)

        self.pages = {}
        for key, builder in (("panel", self.page_panel), ("limpieza", self.page_clean),
                             ("rendimiento", self.page_boost), ("procesos", self.page_proc),
                             ("inicio", self.page_start)):
            f = ctk.CTkFrame(self.content, fg_color="transparent")
            f.grid(row=0, column=0, sticky="nsew")
            builder(f)
            self.pages[key] = f
        self.show("panel")
        self.tick()

    # ---------- navegación
    def build_sidebar(self):
        side = ctk.CTkFrame(self, fg_color=C["side"], corner_radius=0, width=230)
        side.grid(row=0, column=0, sticky="nsew")
        side.grid_propagate(False)
        brand = ctk.CTkFrame(side, fg_color="transparent")
        brand.pack(fill="x", padx=22, pady=(28, 26))
        label(brand, "⚡", 28, C["a1"]).pack(side="left")
        col = ctk.CTkFrame(brand, fg_color="transparent")
        col.pack(side="left", padx=8)
        label(col, "Optimizador", 17, bold=True).pack(side="left")
        label(col, "PC", 17, C["a1"], bold=True).pack(side="left", padx=(4, 0))

        self.nav = {}
        for key, text in (("panel", "▣   Panel"), ("limpieza", "✦   Limpieza"),
                          ("rendimiento", "⚡   Rendimiento"), ("procesos", "☰   Procesos"),
                          ("inicio", "⏻   Inicio de Windows")):
            b = ctk.CTkButton(side, text=text, anchor="w", height=44, corner_radius=10,
                              fg_color="transparent", hover_color=C["card2"], text_color=C["mute"],
                              font=ctk.CTkFont(FONT, 14), command=lambda k=key: self.show(k))
            b.pack(fill="x", padx=14, pady=3)
            self.nav[key] = b

        adm = is_admin() or not IS_WIN
        foot = ctk.CTkFrame(side, fg_color=C["card"], corner_radius=12)
        foot.pack(side="bottom", fill="x", padx=14, pady=18)
        label(foot, "●  Administrador" if adm else "●  Sin permisos de admin", 12,
              C["ok"] if adm else C["warn"], bold=True).pack(anchor="w", padx=14, pady=(10, 0))
        label(foot, "Acceso completo" if adm else "Algunas limpiezas no estarán disponibles",
              10, C["mute"], wraplength=170, justify="left").pack(anchor="w", padx=14, pady=(0, 10))

    def show(self, key):
        self.pages[key].tkraise()
        for k, b in self.nav.items():
            on = k == key
            b.configure(fg_color=C["card2"] if on else "transparent",
                        text_color=C["a1"] if on else C["mute"])
        if key == "procesos":
            self.load_procs()
        elif key == "inicio":
            self.load_start()

    def ui(self, fn, *a, **kw):
        self.after(0, lambda: fn(*a, **kw))

    # ---------- PANEL
    def page_panel(self, f):
        f.grid_columnconfigure((0, 1, 2), weight=1, uniform="g")
        header(f, "Panel", "Estado de tu equipo en tiempo real").grid(row=0, column=0, columnspan=3, sticky="w")

        self.gauges = {}
        for i, name in enumerate(("CPU", "Memoria RAM", "Disco C:")):
            c = card(f)
            c.grid(row=1, column=i, sticky="nsew", padx=(0 if i == 0 else 8, 0 if i == 2 else 8), pady=(18, 14))
            g = Gauge(c, name)
            g.pack(pady=12)
            self.gauges[name] = g

        bottom = ctk.CTkFrame(f, fg_color="transparent")
        bottom.grid(row=2, column=0, columnspan=3, sticky="nsew")
        f.grid_rowconfigure(2, weight=1)
        bottom.grid_columnconfigure(0, weight=3)
        bottom.grid_columnconfigure(1, weight=2)
        bottom.grid_rowconfigure(0, weight=1)

        gc = card(bottom)
        gc.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        label(gc, "Uso en los últimos 60 segundos", 13, C["mute"], bold=True).pack(anchor="w", padx=18, pady=(14, 0))
        self.graph = Graph(gc)
        self.graph.pack(fill="both", expand=True, padx=8, pady=(0, 10))

        qc = card(bottom)
        qc.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        label(qc, "Optimización rápida", 16, bold=True).pack(anchor="w", padx=20, pady=(18, 2))
        label(qc, "Limpia temporales y cachés, libera RAM y vacía la caché DNS en un solo paso.",
              12, C["mute"], wraplength=300, justify="left").pack(anchor="w", padx=20)
        self.qbtn = button(qc, "⚡  Optimizar ahora", self.quick_optimize, primary=True, width=260)
        self.qbtn.pack(padx=20, pady=(16, 10), anchor="w")
        self.qbar = ctk.CTkProgressBar(qc, height=8, corner_radius=4, progress_color=C["a1"], fg_color=C["track"])
        self.qbar.set(0)
        self.qbar.pack(fill="x", padx=20)
        self.qres = label(qc, "", 13, C["ok"], bold=True, wraplength=300, justify="left")
        self.qres.pack(anchor="w", padx=20, pady=(10, 0))

        info = ctk.CTkFrame(f, fg_color="transparent")
        info.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(14, 0))
        info.grid_columnconfigure((0, 1, 2, 3), weight=1, uniform="i")
        self.chips = {}
        cores = f"{psutil.cpu_count(logical=False) or '?'} núcleos · {psutil.cpu_count()} hilos"
        for i, (k, t, v) in enumerate((("cpu", "PROCESADOR", cores),
                                        ("ram", "RAM TOTAL", fmt(psutil.virtual_memory().total)),
                                        ("disk", "DISCO LIBRE", "—"), ("up", "ENCENDIDO HACE", "—"))):
            c = card(info)
            c.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 6, 0 if i == 3 else 6))
            label(c, t, 10, C["mute"], bold=True).pack(anchor="w", padx=16, pady=(12, 0))
            lb = label(c, v, 15, bold=True)
            lb.pack(anchor="w", padx=16, pady=(0, 12))
            self.chips[k] = lb

    def tick(self):
        cpu = psutil.cpu_percent()
        vm = psutil.virtual_memory()
        try:
            import shutil
            du = shutil.disk_usage("C:\\" if IS_WIN else "/")
            dpct, dsub, free = du.used / du.total * 100, f"{fmt(du.used)} de {fmt(du.total)}", fmt(du.free)
        except Exception:
            dpct, dsub, free = 0, "n/d", "n/d"
        self.gauges["CPU"].set(cpu, f"{psutil.cpu_count()} hilos")
        self.gauges["Memoria RAM"].set(vm.percent, f"{fmt(vm.used)} de {fmt(vm.total)}")
        self.gauges["Disco C:"].set(dpct, dsub)
        self.graph.push(cpu, vm.percent)
        self.chips["disk"].configure(text=free)
        s = int(time.time() - psutil.boot_time())
        d, h, m = s // 86400, s % 86400 // 3600, s % 3600 // 60
        self.chips["up"].configure(text=(f"{d} d " if d else "") + f"{h} h {m} min")
        self.after(1000, self.tick)

    def quick_optimize(self):
        if self.busy:
            return
        self.busy = True
        self.qbtn.configure(state="disabled", text="Optimizando…")
        self.qres.configure(text="", text_color=C["ok"])
        threading.Thread(target=self._quick, daemon=True).start()

    def _quick(self):
        total, steps = 0, len(QUICK) + 2
        for i, key in enumerate(QUICK):
            total += cat_clean(key)
            self.ui(self.qbar.set, (i + 1) / steps)
        ram = free_ram()
        self.ui(self.qbar.set, (steps - 1) / steps)
        run_cmd("ipconfig /flushdns")
        self.ui(self.qbar.set, 1)
        msg = f"✓ {fmt(total)} liberados en disco\n✓ {fmt(ram)} de RAM recuperados\n✓ Caché DNS vaciada"
        self.ui(self._quick_done, msg)

    def _quick_done(self, msg):
        self.qres.configure(text=msg)
        self.qbtn.configure(state="normal", text="⚡  Optimizar ahora")
        self.busy = False

    # ---------- LIMPIEZA
    def page_clean(self, f):
        f.grid_columnconfigure(0, weight=1)
        header(f, "Limpieza", "Elegí qué querés borrar. Los archivos en uso se omiten solos.").grid(row=0, column=0, sticky="w")
        lst = card(f)
        lst.grid(row=1, column=0, sticky="nsew", pady=(18, 14))
        f.grid_rowconfigure(1, weight=1)
        for key, title, desc, _fn in CATS:
            row = ctk.CTkFrame(lst, fg_color="transparent")
            row.pack(fill="x", padx=18, pady=7)
            var = tk.BooleanVar(value=key in QUICK)
            self.cat_vars[key] = var
            ctk.CTkCheckBox(row, text="", variable=var, width=26, checkbox_width=22, checkbox_height=22,
                            corner_radius=6, fg_color=C["a1"], hover_color="#06B6D4",
                            border_color=C["mute"], checkmark_color="#04202A").pack(side="left")
            txt = ctk.CTkFrame(row, fg_color="transparent")
            txt.pack(side="left", padx=8)
            label(txt, title, 14, bold=True).pack(anchor="w")
            label(txt, desc, 11, C["mute"]).pack(anchor="w")
            lb = label(row, "—", 14, C["a1"], bold=True)
            lb.pack(side="right")
            self.cat_labels[key] = lb
        bar = ctk.CTkFrame(f, fg_color="transparent")
        bar.grid(row=2, column=0, sticky="ew")
        button(bar, "Analizar", self.scan_clean, width=130).pack(side="left")
        self.cbtn = button(bar, "Limpiar seleccionado", self.do_clean, primary=True, width=200)
        self.cbtn.pack(side="left", padx=10)
        self.ctotal = label(bar, "", 14, C["ok"], bold=True)
        self.ctotal.pack(side="right")

    def scan_clean(self):
        if self.busy:
            return
        self.busy = True
        self.ctotal.configure(text="Analizando…", text_color=C["mute"])
        threading.Thread(target=self._scan, daemon=True).start()

    def _scan(self):
        total = 0
        for key, *_ in CATS:
            s = cat_size(key)
            total += s if self.cat_vars[key].get() else 0
            self.ui(self.cat_labels[key].configure, text=fmt(s))
        self.ui(self._scan_done, total)

    def _scan_done(self, total):
        self.ctotal.configure(text=f"Se pueden liberar {fmt(total)}", text_color=C["a1"])
        self.busy = False

    def do_clean(self):
        keys = [k for k, v in self.cat_vars.items() if v.get()]
        if self.busy or not keys:
            return
        if "bin" in keys and not messagebox.askyesno("Confirmar", "Se vaciará la papelera de reciclaje. ¿Continuar?"):
            return
        self.busy = True
        self.cbtn.configure(state="disabled")
        self.ctotal.configure(text="Limpiando…", text_color=C["mute"])
        threading.Thread(target=self._clean, args=(keys,), daemon=True).start()

    def _clean(self, keys):
        total = 0
        for k in keys:
            total += cat_clean(k)
            self.ui(self.cat_labels[k].configure, text="✓ limpio")
        self.ui(self._clean_done, total)

    def _clean_done(self, total):
        self.ctotal.configure(text=f"✓ {fmt(total)} liberados", text_color=C["ok"])
        self.cbtn.configure(state="normal")
        self.busy = False

    # ---------- RENDIMIENTO
    def page_boost(self, f):
        f.grid_columnconfigure(0, weight=1)
        header(f, "Rendimiento", "Ajustes rápidos para sacarle más a tu PC (ideal para FL Studio y juegos)").grid(row=0, column=0, sticky="w")
        wrap = ctk.CTkFrame(f, fg_color="transparent")
        wrap.grid(row=1, column=0, sticky="nsew", pady=(18, 0))
        wrap.grid_columnconfigure(0, weight=1)
        self.boost_res = {}
        items = (
            ("ram", "Liberar memoria RAM", "Pide a las aplicaciones que devuelvan memoria que no usan.", "Liberar", self._b_ram),
            ("hp", "Plan de energía: Alto rendimiento", "Evita que el procesador baje la velocidad. Consume más batería.", "Activar", lambda: run_cmd("powercfg /setactive SCHEME_MIN")),
            ("bal", "Plan de energía: Equilibrado", "Vuelve al plan normal de Windows.", "Restaurar", lambda: run_cmd("powercfg /setactive SCHEME_BALANCED")),
            ("dns", "Vaciar caché DNS", "Soluciona sitios que no cargan o direcciones viejas.", "Vaciar", lambda: run_cmd("ipconfig /flushdns")),
        )
        for i, (key, title, desc, btn, fn) in enumerate(items):
            c = card(wrap)
            c.grid(row=i, column=0, sticky="ew", pady=6)
            c.grid_columnconfigure(0, weight=1)
            tx = ctk.CTkFrame(c, fg_color="transparent")
            tx.grid(row=0, column=0, sticky="w", padx=20, pady=14)
            label(tx, title, 15, bold=True).pack(anchor="w")
            label(tx, desc, 12, C["mute"]).pack(anchor="w")
            res = label(c, "", 12, C["ok"], bold=True)
            res.grid(row=0, column=1, padx=10)
            self.boost_res[key] = res
            button(c, btn, lambda k=key, fn=fn: self.run_boost(k, fn), width=120).grid(row=0, column=2, padx=20)

    def _b_ram(self):
        return f"✓ {fmt(free_ram())} recuperados"

    def run_boost(self, key, fn):
        self.boost_res[key].configure(text="…", text_color=C["mute"])

        def work():
            r = fn()
            ok = bool(r)
            txt = r if isinstance(r, str) else ("✓ Listo" if ok else "No se pudo aplicar")
            self.ui(self.boost_res[key].configure, text=txt, text_color=C["ok"] if ok else C["warn"])
        threading.Thread(target=work, daemon=True).start()

    # ---------- PROCESOS
    def page_proc(self, f):
        f.grid_columnconfigure(0, weight=1)
        f.grid_rowconfigure(2, weight=1)
        header(f, "Procesos", "Lo que más memoria está usando ahora mismo").grid(row=0, column=0, sticky="w")
        bar = ctk.CTkFrame(f, fg_color="transparent")
        bar.grid(row=1, column=0, sticky="ew", pady=(16, 12))
        self.psearch = ctk.CTkEntry(bar, placeholder_text="Buscar proceso…", width=260, height=40, corner_radius=10,
                                    fg_color=C["card"], border_color=C["line"], text_color=C["text"])
        self.psearch.pack(side="left")
        self.psearch.bind("<KeyRelease>", lambda _e: self.load_procs())
        button(bar, "Actualizar", self.load_procs, width=120).pack(side="left", padx=10)
        button(bar, "Cerrar proceso", self.kill_proc, width=150).pack(side="left")
        wrap, self.ptree = make_tree(f, (("pid", "PID"), ("name", "NOMBRE"), ("ram", "MEMORIA"), ("cpu", "CPU")),
                                      (80, 380, 140, 90))
        wrap.grid(row=2, column=0, sticky="nsew")

    def load_procs(self):
        q = self.psearch.get().strip().lower()
        ncpu = psutil.cpu_count() or 1
        rows, alive = [], set()
        for p in psutil.process_iter(["pid", "name", "memory_info"]):
            try:
                pid = p.info["pid"]
                alive.add(pid)
                obj = self.pcache.setdefault(pid, p)
                cpu = obj.cpu_percent(None) / ncpu
                name = p.info["name"] or "?"
                if q and q not in name.lower():
                    continue
                rows.append((pid, name, p.info["memory_info"].rss, cpu))
            except Exception:
                pass
        self.pcache = {k: v for k, v in self.pcache.items() if k in alive}
        self.ptree.delete(*self.ptree.get_children())
        for pid, name, rss, cpu in sorted(rows, key=lambda r: r[2], reverse=True)[:60]:
            self.ptree.insert("", "end", values=(pid, name, fmt(rss), f"{cpu:.0f}%"))

    def kill_proc(self):
        sel = self.ptree.selection()
        if not sel:
            return
        pid, name = self.ptree.item(sel[0])["values"][:2]
        if messagebox.askyesno("Cerrar proceso", f"¿Cerrar {name} (PID {pid})?\nPodés perder trabajo sin guardar.\nNo cierres procesos del sistema."):
            try:
                psutil.Process(int(pid)).terminate()
            except Exception as e:
                messagebox.showerror("Error", str(e))
            self.after(600, self.load_procs)

    # ---------- INICIO
    def page_start(self, f):
        f.grid_columnconfigure(0, weight=1)
        f.grid_rowconfigure(2, weight=1)
        header(f, "Inicio de Windows", "Programas que se abren solos al encender. Desactivar los que no usás acelera el arranque.").grid(row=0, column=0, sticky="w")
        bar = ctk.CTkFrame(f, fg_color="transparent")
        bar.grid(row=1, column=0, sticky="ew", pady=(16, 12))
        button(bar, "Activar / Desactivar", self.toggle_start, primary=True, width=190).pack(side="left")
        button(bar, "Actualizar", self.load_start, width=120).pack(side="left", padx=10)
        wrap, self.stree = make_tree(f, (("origen", "ORIGEN"), ("name", "PROGRAMA"), ("estado", "ESTADO"), ("cmd", "COMANDO")),
                                      (90, 200, 110, 420))
        wrap.grid(row=2, column=0, sticky="nsew")
        self.stree.tag_configure("off", foreground=C["mute"])

    def load_start(self):
        self.stree.delete(*self.stree.get_children())
        for origin, name, cmd, state in startup_list():
            self.stree.insert("", "end", values=(origin, name, state, cmd), tags=("off",) if state != "Activo" else ())

    def toggle_start(self):
        sel = self.stree.selection()
        if not sel:
            return
        origin, name, state, cmd = self.stree.item(sel[0])["values"][:4]
        try:
            startup_toggle(origin, str(name), str(cmd), state)
        except PermissionError:
            messagebox.showerror("Permiso denegado", "Ejecutá la aplicación como administrador para cambiar entradas del sistema.")
        except Exception as e:
            messagebox.showerror("Error", str(e))
        self.load_start()


if __name__ == "__main__":
    App().mainloop()
