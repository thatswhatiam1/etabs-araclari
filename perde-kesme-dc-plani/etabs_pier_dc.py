# -*- coding: utf-8 -*-
"""
ETABS  ->  Perde (Pier) kesme kuvveti  &  d/c = V / V_max  plan görseli
=====================================================================

Açık olan ETABS modeline API ile bağlanır, verdiğiniz Load Case / Load Pattern /
Load Combination için seçilen kattaki pier kesmelerini (|V2|) okur ve planda
renklendirilmiş d/c görseli + Excel tablosu üretir.

    V_max = 0.85 · A_ch · √f_ck            (TBDY-2018 Denk. 7.17 sınırı)
    d/c   = FAKTOR · |V2|max / V_max       (FAKTOR varsayılan = 1.0, çarpma yok)

Script kendi başına:
  * ismin case mi, pattern mı, combo mu olduğunu bulur,
  * gerekli case'lerin analizinin yapılıp yapılmadığını kontrol eder
    (yapılmamışsa uyarır; --run verilirse analizi başlatır),
  * Display/Show Tables seçiminden bağımsız olarak çıktı seçimini kendisi yapar
    (tüm case/combo seçimlerini kaldırır, sadece istenen ismi seçer),
  * Geometriyi (perdeler, kolonlar, kirişler, akslar), perde kalınlıklarını,
    beton sınıfını (f_ck) ve pier lokal eksen açısını modelden okur.

Kurulum (bir kez):
    pip install comtypes matplotlib pandas openpyxl

Kullanım:
    python etabs_pier_dc.py                          -> isim sorulur
    python etabs_pier_dc.py --case "ExEs"
    python etabs_pier_dc.py --case "ExEs" --story Story1 --loc Bottom
    python etabs_pier_dc.py --case "ExEs" --run       -> analiz yoksa çalıştır
    python etabs_pier_dc.py --case 12                 -> 1-3 haneli sayı = kombinasyon no
    python etabs_pier_dc.py --list                    -> kombinasyonları numaralı listele
    python etabs_pier_dc.py --case "ExEs" --title "Sandıkkaya Spektrum Çalışması — X Doğrultusu"
    python etabs_pier_dc.py --case "ExEs" --factor 3.0   (istenirse büyütme)

Etiket yerleri otomatik ayarlanır. Elle düzeltmek isterseniz offsets.json
({"P14":[0,2900], ...}  mm cinsinden) hazırlayıp --offsets offsets.json verin.
"""

# ---------------------------------------------------------------------------
# SORUMLULUK REDDİ
# Bu araç bir mühendislik yardımcısıdır. Hesap, tasarım ve kontrol sorumluluğu
# tamamen kullanıcıya aittir. Çıktılar, ilgili yönetmelik ve proje koşullarına
# göre kullanıcı tarafından doğrulanmadan hiçbir projede kullanılmamalıdır.
# Yazar, kullanımdan doğabilecek hiçbir zarardan sorumlu tutulamaz.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# SORUMLULUK REDDİ — çalıştırıldığında bir kez gösterilir.
# Otomasyonda uyarıyı atlamak için ortam değişkeni: SORUMLULUK_ONAY=1
# ---------------------------------------------------------------------------
_SORUMLULUK_METNI = (
    "SORUMLULUK REDDİ\n\n"
    "Bu araç bir mühendislik yardımcısıdır; hesap, tasarım ve kontrol\n"
    "sorumluluğunu üstlenmez. Üretilen sonuçlar, ilgili yönetmelik ve proje\n"
    "koşullarına göre kullanıcı tarafından doğrulanmadan hiçbir projede\n"
    "kullanılmamalıdır.\n\n"
    "Yazılım olduğu gibi sunulur; yazar, kullanımından doğabilecek doğrudan\n"
    "veya dolaylı hiçbir zarardan sorumlu tutulamaz.\n\n"
    "Devam ederek bu koşulları kabul etmiş olursunuz."
)


def _sorumluluk_uyarisi():
    import os as _os
    if _os.environ.get("SORUMLULUK_ONAY") == "1":
        return
    try:
        import tkinter as _tk
        from tkinter import messagebox as _mb
        _kok = _tk.Tk()
        _kok.withdraw()
        _kok.attributes("-topmost", True)
        _mb.showwarning("Sorumluluk Reddi", _SORUMLULUK_METNI, parent=_kok)
        _kok.destroy()
    except Exception:
        print("\n" + "=" * 70)
        print(_SORUMLULUK_METNI)
        print("=" * 70 + "\n")


_sorumluluk_uyarisi()

import argparse
import json
import math
import os
import sys
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.colors as mc
import matplotlib.patches as mp
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon

# ---------------------------------------------------------------------------
# Ayarlar (komut satırından verilmezse bunlar kullanılır)
# ---------------------------------------------------------------------------
DEFAULTS = dict(
    story="Story1",       # Hangi kat
    loc="Bottom",         # Pier kuvvet konumu: Bottom / Top
    factor=1.0,           # d/c hesabında V ile çarpılacak katsayı (1.0 = çarpma yok)
    dc_max=1.25,          # Renk skalası üst sınırı
    out_dir="pier_dc_out",
)

ANGLE_TOL = 10.0          # derece; pier eksenine paralel kol kabul toleransı


# ===========================================================================
# 1) ETABS BAĞLANTISI VE VERİ ÇEKME
# ===========================================================================
def attach_etabs():
    """Çalışan ETABS örneğine bağlanır (v18+ Helper; yoksa GetActiveObject)."""
    import comtypes.client
    try:
        helper = comtypes.client.CreateObject("ETABSv1.Helper")
        import comtypes.gen.ETABSv1 as ETABSv1
        helper = helper.QueryInterface(ETABSv1.cHelper)
        etabs = helper.GetObject("CSI.ETABS.API.ETABSObject")
    except Exception:
        etabs = comtypes.client.GetActiveObject("CSI.ETABS.API.ETABSObject")
    if etabs is None:
        sys.exit("HATA: Açık bir ETABS bulunamadı. Önce modeli ETABS'te açın.")
    return etabs, etabs.SapModel


def _ok(res, what):
    """comtypes dönüşü: [çıktılar..., ret]. ret != 0 ise hata."""
    if isinstance(res, (list, tuple)):
        ret = res[-1]
    else:
        ret = res
    if ret != 0:
        raise RuntimeError(f"ETABS API hatası ({what}), ret={ret}")
    return res


def resolve_name(Sap, name):
    """İsmin türünü belirler. ('case'|'combo', sonuç_adı, analiz_gereken_caseler)"""
    cases = list(_ok(Sap.LoadCases.GetNameList(0, []), "LoadCases.GetNameList")[1])
    combos = list(_ok(Sap.RespCombo.GetNameList(0, []), "RespCombo.GetNameList")[1])
    pats = list(_ok(Sap.LoadPatterns.GetNameList(0, []), "LoadPatterns.GetNameList")[1])

    # 1-3 haneli sayı -> kombinasyon numarası
    if name.isdigit() and 1 <= len(name) <= 3:
        num = int(name)
        if name in combos:                                   # adı doğrudan "12" olan combo
            pick = name
        else:
            import re
            pref = [c for c in combos if re.match(rf"^0*{num}(?!\d)", c)]  # örn. "12-1.4G+1.6Q"
            if len(pref) == 1:                               # adı bu numarayla başlayan tek combo
                pick = pref[0]
            elif 1 <= num <= len(combos):                    # ETABS listesindeki sıra no (1'den başlar)
                pick = combos[num - 1]
            else:
                sys.exit(f"HATA: {num} numaralı kombinasyon yok (toplam {len(combos)} combo). "
                         f"Listeyi görmek için --list kullanın.")
        print(f"  {num} numara -> kombinasyon '{pick}'")
        return "combo", pick, combo_cases(Sap, pick, cases)

    if name in cases:
        return "case", name, {name}
    if name in combos:
        return "combo", name, combo_cases(Sap, name, cases)
    if name in pats:
        # Pattern -> bu pattern'i tek başına (SF=1) içeren lineer statik case'i bul
        for c in cases:
            try:
                r = Sap.LoadCases.StaticLinear.GetLoads(c, 0, [], [], [])
                if r[-1] == 0 and r[0] == 1 and r[2][0] == name and abs(r[3][0] - 1) < 1e-6:
                    print(f"  '{name}' bir Load Pattern; sonuçlar '{c}' case'inden okunacak.")
                    return "case", c, {c}
            except Exception:
                pass
        sys.exit(f"HATA: '{name}' bir Load Pattern ama onu tek başına içeren bir "
                 f"lineer statik Load Case bulunamadı. Bir case tanımlayın.")
    sys.exit(f"HATA: '{name}' modelde Load Case / Combo / Pattern olarak bulunamadı.\n"
             f"Mevcut case'ler: {cases}\nMevcut combo'lar: {combos}")


def combo_cases(Sap, combo, all_cases, seen=None):
    """Bir kombinasyonun (iç içe dahil) bağlı olduğu tüm load case'leri."""
    seen = seen or set()
    out = set()
    r = _ok(Sap.RespCombo.GetCaseList(combo, 0, [], [], []), "RespCombo.GetCaseList")
    n, ctypes_, cnames = r[0], r[1], r[2]
    for i in range(n):
        if ctypes_[i] == 0:                       # LoadCase
            out.add(cnames[i])
        elif cnames[i] not in seen:               # iç içe combo
            seen.add(cnames[i])
            out |= combo_cases(Sap, cnames[i], all_cases, seen)
    return out


def ensure_analysis(Sap, needed, run):
    """Gerekli case'lerin analiz durumunu kontrol eder; gerekirse çalıştırır."""
    r = _ok(Sap.Analyze.GetCaseStatus(0, [], []), "Analyze.GetCaseStatus")
    status = dict(zip(r[1], r[2]))                # 4 = Finished
    missing = [c for c in needed if status.get(c) != 4]
    if not missing:
        print("  Analiz durumu: gerekli tüm case'ler çözülmüş. ✔")
        return
    print(f"  UYARI: Şu case'ler çözülmemiş: {missing}")
    if not run:
        sys.exit("  Analizi ETABS'te çalıştırın ya da scripti --run ile başlatın.")
    for c in missing:
        Sap.Analyze.SetRunCaseFlag(c, True, False)
    print("  Analiz başlatılıyor (model kaydedilmiş olmalı)...")
    _ok(Sap.Analyze.RunAnalysis(), "Analyze.RunAnalysis")
    r = _ok(Sap.Analyze.GetCaseStatus(0, [], []), "Analyze.GetCaseStatus")
    status = dict(zip(r[1], r[2]))
    still = [c for c in needed if status.get(c) != 4]
    if still:
        sys.exit(f"HATA: Analiz sonrası hâlâ çözülmemiş case'ler: {still}")
    print("  Analiz tamamlandı. ✔")


def select_for_output(Sap, kind, name):
    """Show Tables'taki seçimden bağımsız: sadece istenen ismi seçer."""
    _ok(Sap.Results.Setup.DeselectAllCasesAndCombosForOutput(), "DeselectAll")
    if kind == "case":
        _ok(Sap.Results.Setup.SetCaseSelectedForOutput(name, True), "SetCaseSelected")
    else:
        _ok(Sap.Results.Setup.SetComboSelectedForOutput(name, True), "SetComboSelected")
    # Çok adımlı statik / nonlineer sonuçlarda tüm adımları al (mutlak maks. alınacak)
    for fn, val in (("SetOptionMultiStepStatic", 2), ("SetOptionNLStatic", 2),
                    ("SetOptionMultiValuedCombo", 1)):
        try:
            getattr(Sap.Results.Setup, fn)(val)
        except Exception:
            pass


def read_pier_shears(Sap, result_name, story, loc):
    """Seçili kat/konum için her pier'in |V2| mutlak maksimumu (kN)."""
    r = _ok(Sap.Results.PierForce(0, [], [], [], [], [], [], [], [], [], []),
            "Results.PierForce")
    n, st, pier, lc, lo, P, V2 = r[0], r[1], r[2], r[3], r[4], r[5], r[6]
    V = defaultdict(float)
    for i in range(n):
        if st[i] == story and lo[i].lower() == loc.lower() and lc[i] == result_name:
            V[pier[i]] = max(V[pier[i]], abs(V2[i]))
    if not V:
        sys.exit(f"HATA: '{story}' katı, '{loc}' konumu için '{result_name}' sonucu "
                 f"bulunamadı. Kat adını ve pier atamalarını kontrol edin.")
    return dict(V)


def read_geometry(Sap, story):
    """Kat üzerindeki perde/kolon/kiriş geometrisi, pier eksenleri, akslar."""
    pt_cache = {}

    def xy(p):
        if p not in pt_cache:
            r = Sap.PointObj.GetCoordCartesian(p, 0, 0, 0)
            pt_cache[p] = (r[0], r[1])
        return pt_cache[p]

    wall_cache, mat_fck = {}, {}

    def fck_of(mat):
        if mat not in mat_fck:
            try:
                r = Sap.PropMaterial.GetOConcrete_1(mat, 0, False, 0, 0, 0, 0, 0, 0, 0, 0)
                mat_fck[mat] = r[0] * 1000.0            # kN/mm² -> MPa
            except Exception:
                mat_fck[mat] = None
        return mat_fck[mat]

    def wall_prop(prop):
        if prop not in wall_cache:
            r = Sap.PropArea.GetWall(prop, 0, 0, "", 0, 0, "", "")
            if r[-1] != 0:
                wall_cache[prop] = None
            else:
                wall_cache[prop] = (r[3], r[2])          # (kalınlık mm, malzeme)
        return wall_cache[prop]

    # --- Perdeler
    walls = []
    r = _ok(Sap.AreaObj.GetNameListOnStory(story, 0, []), "AreaObj.GetNameListOnStory")
    for a in r[1]:
        if Sap.AreaObj.GetDesignOrientation(a, 0)[0] != 1:   # 1 = Wall
            continue
        prop = Sap.AreaObj.GetProperty(a, "")[0]
        wp = wall_prop(prop)
        if wp is None:
            continue
        t, mat = wp
        pts = Sap.AreaObj.GetPoints(a, 0, [])[1]
        u = []
        for p in pts:
            c = (round(xy(p)[0], 1), round(xy(p)[1], 1))
            if c not in u:
                u.append(c)
        if len(u) < 2:
            continue
        # plandaki iki uç: birbirine en uzak iki nokta
        a_, b_ = max(((p, q) for p in u for q in u), key=lambda pq: math.dist(*pq))
        pier = Sap.AreaObj.GetPier(a, "")[0]
        walls.append(dict(a=a_, b=b_, t=t, pier=pier if pier not in ("", "None") else None,
                          fck=fck_of(mat)))

    # --- Kolon / kiriş
    cols, beams = [], []
    r = _ok(Sap.FrameObj.GetNameListOnStory(story, 0, []), "FrameObj.GetNameListOnStory")
    for f in r[1]:
        ori = Sap.FrameObj.GetDesignOrientation(f, 0)[0]   # 1 kolon, 2 kiriş
        p1, p2 = Sap.FrameObj.GetPoints(f, "", "")[:2]
        if ori == 1:
            sec = Sap.FrameObj.GetSection(f, "", "")[0]
            b = h = 600.0
            try:
                rr = Sap.PropFrame.GetRectangle(sec, "", "", 0, 0, 0, "", "")
                if rr[-1] == 0:
                    h, b = rr[2], rr[3]
            except Exception:
                pass
            cols.append(dict(p=xy(p1), b=b, h=h))
        elif ori == 2:
            beams.append(dict(a=xy(p1), b=xy(p2)))

    # --- Pier lokal eksen açıları
    pier_axis = {}
    r = _ok(Sap.PierLabel.GetNameList(0, []), "PierLabel.GetNameList")
    for p in r[1]:
        try:
            s = Sap.PierLabel.GetSectionProperties(p, 0, [], [], [], [], [], [], [], [], [],
                                                   [], [], [], [], [], [])
            if s[-1] == 0 and story in list(s[1]):
                pier_axis[p] = list(s[2])[list(s[1]).index(story)]
        except Exception:
            pass

    return dict(walls=walls, cols=cols, beams=beams, pier_axis=pier_axis,
                grids=read_grids(Sap))


def read_grids(Sap):
    """Kartezyen aks çizgileri (Database Tables üzerinden). Okunamazsa boş döner."""
    for tname in ("Grid Definitions - Grid Lines", "Grid Lines"):
        try:
            r = Sap.DatabaseTables.GetTableForDisplayArray(tname, [], "", 0, [], 0, [])
            if r[-1] != 0:
                continue
            fields, nrec, data = list(r[2]), r[3], list(r[4])
            nf = len(fields)
            low = [f.lower() for f in fields]
            i_id = next(i for i, f in enumerate(low) if f in ("id", "gridid", "grid id", "label"))
            i_ord = next(i for i, f in enumerate(low) if "ordinate" in f or "coord" in f)
            i_typ = next(i for i, f in enumerate(low) if "linetype" in f or "line type" in f or "axisdir" in f)
            out = []
            for k in range(nrec):
                row = data[k * nf:(k + 1) * nf]
                typ = row[i_typ].upper()
                d = "X" if typ.startswith("X") else "Y" if typ.startswith("Y") else None
                if d:
                    out.append((row[i_id], d, float(row[i_ord])))
            return out
        except Exception:
            continue
    print("  Not: Aks çizgileri okunamadı, aks çizilmeden devam ediliyor.")
    return []


def collect_from_etabs(args):
    etabs, Sap = attach_etabs()
    print(f"ETABS'e bağlanıldı: {Sap.GetModelFilename()}")
    if args.list:
        combos = list(_ok(Sap.RespCombo.GetNameList(0, []), "RespCombo.GetNameList")[1])
        print("\nKombinasyonlar (No  ->  Ad):")
        for i, c in enumerate(combos, 1):
            print(f"  {i:4d}  ->  {c}")
        sys.exit(0)
    units0 = Sap.GetPresentUnits()
    Sap.SetPresentUnits(5)                    # kN_mm_C
    try:
        kind, rname, needed = resolve_name(Sap, args.case)
        print(f"  '{args.case}' -> {kind.upper()} ({rname})")
        ensure_analysis(Sap, needed, args.run)
        select_for_output(Sap, kind, rname)
        V = read_pier_shears(Sap, rname, args.story, args.loc)
        geom = read_geometry(Sap, args.story)
    finally:
        Sap.SetPresentUnits(units0)
    geom["V2"] = V
    geom["result_name"] = rname
    return geom


# ===========================================================================
# 2) HESAP
# ===========================================================================
def _ang(a, b):
    return math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) % 180.0


def _par(a1, a2, tol=ANGLE_TOL):
    d = abs(a1 - a2) % 180.0
    return min(d, 180.0 - d) <= tol


def compute(data, factor=1.0):
    """Her pier için A_ch, V_max, d/c."""
    segs = defaultdict(dict)
    for w in data["walls"]:
        if not w["pier"] or w["pier"] not in data["V2"]:
            continue
        k = tuple(sorted([tuple(map(round, w["a"])), tuple(map(round, w["b"]))]))
        if k[0] != k[1]:
            segs[w["pier"]][k] = (w["t"], w.get("fck"))   # dikey bölünmüş paneller tekilleşir

    res, warn = {}, []
    for p, ss in segs.items():
        axis = data["pier_axis"].get(p)
        if axis is None:   # eksen okunamadıysa: en uzun toplam kol doğrultusu
            acc = defaultdict(float)
            for (a, b), _ in ss.items():
                acc[round(_ang(a, b)) % 180] += math.dist(a, b)
            axis = max(acc, key=acc.get)
        Ach = 0.0
        fcks = []
        cx = cy = tl = 0.0
        for (a, b), (t, fck) in ss.items():
            L = math.dist(a, b)
            cx += L * (a[0] + b[0]) / 2; cy += L * (a[1] + b[1]) / 2; tl += L
            if _par(_ang(a, b), axis):
                Ach += L * t
                if fck: fcks.append(fck)
        if Ach == 0:      # eksene paralel kol yoksa tüm kollar
            Ach = sum(math.dist(a, b) * t for (a, b), (t, _) in ss.items())
            warn.append(p)
        fck = min(fcks) if fcks else 40.0
        Vmax = 0.85 * Ach * math.sqrt(fck) / 1000.0          # kN
        V = data["V2"][p]
        res[p] = dict(V=V, Vd=factor * V, Ach=Ach, fck=fck, Vmax=Vmax,
                      dc=factor * V / Vmax, axis=axis,
                      dir="X" if _par(axis, 0) else "Y" if _par(axis, 90) else f"{axis:.0f}°",
                      cx=cx / tl, cy=cy / tl,
                      segs=[(a, b, t) for (a, b), (t, _) in ss.items()])
    if warn:
        print(f"  Not: {warn} için eksene paralel kol bulunamadı, tüm kollar alındı.")
    miss = set(data["V2"]) - set(res)
    if miss:
        print(f"  Not: Şu pier'ların bu katta perde geometrisi bulunamadı: {sorted(miss)}")
    return res


# ===========================================================================
# 3) ÇİZİM
# ===========================================================================
CMAP = mc.LinearSegmentedColormap.from_list(
    "dc", ["#1a9850", "#91cf60", "#fee08b", "#fc8d59", "#d73027", "#7f0000"])


def _rect(a, b, t):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L = math.hypot(dx, dy); nx, ny = -dy / L * t / 2, dx / L * t / 2
    return [(a[0] + nx, a[1] + ny), (b[0] + nx, b[1] + ny),
            (b[0] - nx, b[1] - ny), (a[0] - nx, a[1] - ny)]


def _fmt(v):
    return f"{v:,.0f}".replace(",", ".")


def _place_labels(ax, fig, items, offsets):
    """Basit çakışma önleyici: her etiketi centroid çevresinde boş bir yere koyar."""
    fig.canvas.draw()
    rend = fig.canvas.get_renderer()
    inv = ax.transData.inverted()
    placed = []
    # büyük perdeler önce
    for t, (x, y), key in sorted(items, key=lambda it: it[0].get_text().count("\n")):
        bb = t.get_window_extent(rend)
        (x0, y0), (x1, y1) = inv.transform([(bb.x0, bb.y0), (bb.x1, bb.y1)])
        w, h = (x1 - x0) * 1.08, (y1 - y0) * 1.15
        if key in offsets:
            cands = [tuple(offsets[key])]
        else:
            cands = [(0, 0)]
            for r in (1.0, 1.6, 2.3, 3.2):
                for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0), (1, 1), (-1, 1), (1, -1), (-1, -1)):
                    cands.append((dx * w * r * 0.75, dy * h * r * 0.75))
        chosen = cands[0]
        for ox, oy in cands:
            box = (x + ox - w / 2, y + oy - h / 2, x + ox + w / 2, y + oy + h / 2)
            if all(box[2] < p[0] or box[0] > p[2] or box[3] < p[1] or box[1] > p[3] for p in placed):
                chosen = (ox, oy); break
        ox, oy = chosen
        placed.append((x + ox - w / 2, y + oy - h / 2, x + ox + w / 2, y + oy + h / 2))
        t.set_position((x + ox, y + oy))
        if ox or oy:
            ax.annotate("", (x, y), (x + ox, y + oy),
                        arrowprops=dict(arrowstyle="-", color="#444", lw=.6), zorder=5)


def plot(data, res, title, subtitle, out_png, factor=1.0, dc_max=1.25, offsets=None):
    offsets = offsets or {}
    norm = mc.Normalize(0, dc_max)
    xs = [c for w in data["walls"] for c in (w["a"][0], w["b"][0])] + [c["p"][0] for c in data["cols"]]
    ys = [c for w in data["walls"] for c in (w["a"][1], w["b"][1])] + [c["p"][1] for c in data["cols"]]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    span = max(x1 - x0, y1 - y0); m = 0.06 * span

    fig = plt.figure(figsize=(20, 11.25), dpi=170)
    ax = fig.add_axes([0.0, 0.01, 0.52, 0.89]); ax.set_aspect("equal"); ax.axis("off")
    ax.set_xlim(x0 - m, x1 + m); ax.set_ylim(y0 - 1.6 * m, y1 + 1.4 * m)

    for lab, d, c in data["grids"]:
        if d == "X" and x0 - m < c < x1 + m:
            ax.plot([c, c], [y0 - 0.6 * m, y1 + 0.4 * m], color="#bbb", lw=.6, ls=(0, (8, 4)), zorder=0)
            ax.text(c, y0 - 0.8 * m, lab, ha="center", va="top", fontsize=8, color="#777")
        elif d == "Y" and y0 - m < c < y1 + m:
            ax.plot([x0 - 0.4 * m, x1 + 0.4 * m], [c, c], color="#bbb", lw=.6, ls=(0, (8, 4)), zorder=0)
            ax.text(x0 - 0.5 * m, c, lab, ha="right", va="center", fontsize=8, color="#777")
    for b in data["beams"]:
        ax.plot([b["a"][0], b["b"][0]], [b["a"][1], b["b"][1]], color="#ddd", lw=.5, zorder=1)
    for c in data["cols"]:
        ax.add_patch(mp.Rectangle((c["p"][0] - c["b"] / 2, c["p"][1] - c["h"] / 2), c["b"], c["h"],
                                  color="#888", zorder=2))
    for w in data["walls"]:
        if not w["pier"] or w["pier"] not in res:
            if w["a"] != w["b"]:
                ax.add_patch(Polygon(_rect(w["a"], w["b"], w["t"]), color="#9a9a9a", zorder=2))

    items = []
    for p, r in res.items():
        col = CMAP(norm(r["dc"]))
        for a, b, t in r["segs"]:
            ax.add_patch(Polygon(_rect(a, b, t), fc=col, ec="k", lw=.6, zorder=3))
        txt = ax.text(r["cx"], r["cy"], f"{p}\nV={_fmt(r['V'])} kN\nd/c={r['dc']:.2f}",
                      ha="center", va="center", fontsize=7.3, zorder=6,
                      bbox=dict(boxstyle="round,pad=0.25", fc="white", ec=col, lw=1.6, alpha=.93))
        items.append((txt, (r["cx"], r["cy"]), p))
    _place_labels(ax, fig, items, offsets)

    # Başlık (sol üst): case / pattern / combo adı
    fig.text(0.015, 0.955, title, fontsize=22, weight="bold")
    fig.text(0.015, 0.922, subtitle, fontsize=12, color="#444")

    # Tablo
    tx = fig.add_axes([0.54, 0.20, 0.44, 0.70]); tx.axis("off")
    rows = sorted(res.items(), key=lambda kv: -kv[1]["dc"])
    hdr = ["Pier", "Yön", "A_ch (m²)", "V (kN)", "V_d (kN)", "V_max (kN)", "d/c"]
    xcol = [0.0, 0.13, 0.22, 0.38, 0.54, 0.72, 0.90]
    n = len(rows); h = 1 / (n + 1.5)
    fs = max(5.5, min(9.5, 260 / max(n, 1)))
    for x, s in zip(xcol, hdr):
        tx.text(x, 1 - h * 0.5, s, weight="bold", fontsize=fs + 1, va="center")
    tx.plot([0, 1], [1 - h, 1 - h], color="k", lw=.8)
    for i, (p, r) in enumerate(rows):
        y = 1 - h * (i + 1.6); dc = r["dc"]
        if i % 2 == 0:
            tx.add_patch(mp.Rectangle((-0.01, y - h / 2), 1.02, h, color="#f3f3f3", zorder=0))
        vals = [p, r["dir"], f"{r['Ach'] / 1e6:.2f}", _fmt(r["V"]), _fmt(r["Vd"]), _fmt(r["Vmax"])]
        for x, s in zip(xcol, vals):
            tx.text(x, y, s, fontsize=fs, va="center")
        tx.add_patch(mp.FancyBboxPatch((0.895, y - h * 0.38), 0.10, h * 0.76,
                                       boxstyle="round,pad=0.003", fc=CMAP(norm(dc)), ec="none"))
        tx.text(0.945, y, f"{dc:.2f}", fontsize=fs, va="center", ha="center", weight="bold",
                color="white" if (dc < 0.25 * dc_max / 1.25 or dc > 0.95 * dc_max / 1.25) else "black")
    tx.set_xlim(-0.01, 1.01); tx.set_ylim(0, 1)

    cax = fig.add_axes([0.56, 0.155, 0.40, 0.018])
    cb = fig.colorbar(plt.cm.ScalarMappable(norm, CMAP), cax=cax, orientation="horizontal")
    cb.set_label("d/c = V_d / V_max", fontsize=10); cb.ax.axvline(1.0, color="k", lw=2)

    fcks = sorted({round(r["fck"]) for r in res.values()})
    vd_line = ("V_d = V  (katsayı uygulanmamıştır)" if abs(factor - 1) < 1e-9
               else f"V_d = {factor:g}·V")
    note = (f"NOT:  {vd_line}\n"
            f"V_max = 0.85·A_ch·√f_ck  (TBDY 7.6.6.3),  f_ck = {', '.join(map(str, fcks))} MPa\n"
            "A_ch = l_w · b_w  (perde gövdesinin brüt kesit alanı)")
    fig.text(0.56, 0.03, note, fontsize=10, va="bottom",
             bbox=dict(boxstyle="round,pad=0.5", fc="#fff8e1", ec="#e0a800"))
    fig.savefig(out_png, dpi=170, facecolor="white")
    plt.close(fig)


def export_table(res, path):
    import pandas as pd
    df = pd.DataFrame([dict(Pier=p, Yon=r["dir"], Ach_m2=r["Ach"] / 1e6, fck_MPa=r["fck"],
                            V_kN=r["V"], Vd_kN=r["Vd"], Vmax_kN=r["Vmax"], dc=r["dc"])
                       for p, r in res.items()]).sort_values("dc", ascending=False)
    df.to_excel(path, index=False)


# ===========================================================================
# 4) ANA AKIŞ
# ===========================================================================
def safe(s):
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in s)


def main():
    ap = argparse.ArgumentParser(description="ETABS pier kesme d/c plan görseli")
    ap.add_argument("--case", help="Load Case / Load Pattern / Combo adı")
    ap.add_argument("--story", default=DEFAULTS["story"])
    ap.add_argument("--loc", default=DEFAULTS["loc"], choices=["Bottom", "Top"])
    ap.add_argument("--factor", type=float, default=DEFAULTS["factor"])
    ap.add_argument("--title", help="Sol üst başlık (varsayılan: case adı)")
    ap.add_argument("--run", action="store_true", help="Analiz yoksa çalıştır")
    ap.add_argument("--list", action="store_true", help="Kombinasyonları numaralarıyla listele")
    ap.add_argument("--offsets", help="Etiket ofsetleri JSON (mm)")
    ap.add_argument("--dcmax", type=float, default=DEFAULTS["dc_max"])
    ap.add_argument("--out", default=DEFAULTS["out_dir"])
    ap.add_argument("--from-json", help=argparse.SUPPRESS)   # test / ETABS'siz çalışma
    args = ap.parse_args()

    if not args.case and not args.list:
        args.case = input("Load Case / Pattern / Combo adı: ").strip()

    if args.from_json:
        data = json.load(open(args.from_json, encoding="utf-8"))
    else:
        data = collect_from_etabs(args)

    rname = data.get("result_name", args.case)
    res = compute(data, args.factor)
    os.makedirs(args.out, exist_ok=True)
    stem = os.path.join(args.out, f"{safe(args.story)}_{safe(rname)}")
    offsets = json.load(open(args.offsets, encoding="utf-8")) if args.offsets else {}
    title = args.title or rname
    subtitle = (f"{args.story} ({args.loc}) perde kesme kuvvetleri ve d/c oranı   |   "
                f"V = |V2| (tüm adımların mutlak maksimumu)")
    plot(data, res, title, subtitle, stem + ".png", args.factor, args.dcmax, offsets)
    export_table(res, stem + ".xlsx")

    top = sorted(res.items(), key=lambda kv: -kv[1]["dc"])[:5]
    print("\nEn kritik 5 pier:")
    for p, r in top:
        print(f"  {p:10s} V={r['V']:10.0f} kN   Vmax={r['Vmax']:10.0f} kN   d/c={r['dc']:.2f}")
    print(f"\nÇıktılar: {stem}.png  |  {stem}.xlsx")


if __name__ == "__main__":
    main()
