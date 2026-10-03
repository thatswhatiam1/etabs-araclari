"""
Deprem Kayıtları Dönüştürücü
Desteklenen formatlar: AFAD/TADAS (.asc), K-NET (.EW/.NS/.UD), PEER NGA (.AT2), New Zealand (.V2A)
Hedef: zaman (s) ve ivme (mm/s²) sütunlu txt dosyaları
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

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import os
import re
import math
from datetime import datetime

# ─────────────────────────────────────────────────────────────
# FORMAT TESPİT
# ─────────────────────────────────────────────────────────────

def detect_format(filepath):
    """
    Döndürür: (format_kodu, uyari_mesaji)
    format_kodu: 'AFAD' | 'KNET' | 'PEER' | 'NZEALAND' | None
    uyari_mesaji: None veya kullanıcıya gösterilecek uyarı metni
    """
    ext = os.path.splitext(filepath)[1].upper()

    if ext == '.ASC':
        return 'AFAD', None
    if ext in ('.EW', '.NS', '.UD'):
        return 'KNET', None
    if ext == '.AT2':
        return 'PEER', None
    if ext == '.V2A':
        return 'NZEALAND', None

    if ext == '.TXT':
        # .txt dosyası belirsizdir — içeriğe bak
        try:
            with open(filepath, 'r', errors='ignore') as f:
                head = f.read(600)
        except:
            return None, "Dosya okunamadı."

        if 'GNS Science' in head or 'Corrected accelerogram' in head:
            # New Zealand verisi .txt içinde — bu genellikle .V2A ile aynı kayıttır
            return None, (
                "Bu dosya New Zealand (GNS Science) formatında görünüyor, "
                "ancak .txt uzantısıyla kaydedilmiş.\n"
                "New Zealand kayıtları için lütfen .V2A uzantılı dosyaları kullanın.\n"
                "(.txt sürümü genellikle aynı kaydın kopyasıdır — tekrar önlemek için atlanıyor.)"
            )
        if 'PEER' in head or 'ACCELERATION TIME SERIES' in head:
            return None, (
                "Bu dosya PEER NGA formatında görünüyor, ancak .txt uzantısıyla kaydedilmiş.\n"
                "PEER NGA kayıtları için lütfen .AT2 uzantılı dosyaları kullanın."
            )
        if 'EVENT_NAME' in head or 'AFAD' in head:
            return None, (
                "Bu dosya AFAD/TADAS formatında görünüyor, ancak .txt uzantısıyla kaydedilmiş.\n"
                "AFAD kayıtları için lütfen .asc uzantılı dosyaları kullanın."
            )
        return None, (
            f"Tanınmayan .txt dosyası. Desteklenen formatlar:\n"
            "  • AFAD/TADAS → .asc\n"
            "  • K-NET       → .EW / .NS / .UD\n"
            "  • PEER NGA    → .AT2\n"
            "  • New Zealand → .V2A"
        )

    return None, (
        f"Desteklenmeyen uzantı: {ext}\n"
        "Desteklenen uzantılar: .asc  .EW  .NS  .UD  .AT2  .V2A"
    )


# ─────────────────────────────────────────────────────────────
# YÖN TESPİT YARDIMCILARI
# ─────────────────────────────────────────────────────────────

def angle_to_dir(angle_deg):
    """Açıyı (kuzeyden saat yönüyle derece) EW/NS/UD'ye çevirir."""
    a = angle_deg % 180
    if a <= 45 or a >= 135:
        return 'NS'
    return 'EW'


def peer_direction_from_suffix(suffix):
    """
    PEER dosya adındaki son bileşenden yön çıkarır.
    Örnekler: KBU000, KBU090, KBU-UP, TCU065-E, TCU065-N, TCU065-V,
              HECVER, LPCCUP, LPCCN80E, LPCCS10E, 496-EW, 496-NS, 496-UP
    """
    s = suffix.upper()

    # Dikey kontrol
    if any(v in s for v in ['UP', 'VER', '-V', 'VERT']):
        return 'UD'

    # Açıkça belirtilmiş
    if s.endswith('-EW') or s.endswith('-E'):
        return 'EW'
    if s.endswith('-NS') or s.endswith('-N'):
        return 'NS'

    # Azimut (son 3 rakam)
    m = re.search(r'(\d{3})$', s)
    if m:
        return angle_to_dir(int(m.group(1)))

    # N__E / S__E / N__W biçimi (örn: N80E, S10E)
    m = re.search(r'([NS])(\d+)([EW])', s)
    if m:
        ref, deg, side = m.groups()
        deg = int(deg)
        if ref == 'N':
            az = deg if side == 'E' else 360 - deg
        else:
            az = 180 - deg if side == 'E' else 180 + deg
        return angle_to_dir(az)

    return 'H1'  # Belirlenemedi


def nz_direction_from_component(comp_line):
    """
    NZ başlık satırından yön çıkarır.
    Örnek: 'Component N28W  Longitudinal Accelerometer Axis'
           'Component Up  Vertical Accelerometer Axis'
    """
    c = comp_line.upper()
    # Tam kelime eşleşmesi kullan ('LONGITUDINAL' içindeki 'UD' yakalanmasın)
    if re.search(r'\bUP\b', c) or re.search(r'\bVERT(ICAL)?\b', c) or re.search(r'\bUD\b', c):
        return 'UD'

    # N__E / N__W / S__E / S__W
    m = re.search(r'([NS])(\d+)([EW])', c)
    if m:
        ref, deg, side = m.groups()
        deg = int(deg)
        if ref == 'N':
            az = deg if side == 'E' else 360 - deg
        else:
            az = 180 - deg if side == 'E' else 180 + deg
        return angle_to_dir(az)

    if 'LONG' in c or 'N' in c or 'S' in c:
        return 'NS'
    if 'TRANS' in c or 'E' in c or 'W' in c:
        return 'EW'
    return 'H1'


# ─────────────────────────────────────────────────────────────
# AFAD / TADAS  (.asc)
# ─────────────────────────────────────────────────────────────

def parse_afad(filepath):
    """
    Döndürür: (time_list, acc_mm_s2_list, short_name, direction)
    """
    header = {}
    data_lines = []
    in_data = False

    with open(filepath, 'r', errors='ignore') as f:
        for line in f:
            stripped = line.strip()
            if not in_data:
                if ':' in stripped:
                    key, _, val = stripped.partition(':')
                    header[key.strip()] = val.strip()
                # Veri bölümü: satır sayısal ve boş değilse
                if stripped and re.match(r'^-?\d+\.?\d*([Ee][+-]?\d+)?$', stripped):
                    in_data = True
                    data_lines.append(float(stripped))
            else:
                if stripped:
                    try:
                        data_lines.append(float(stripped))
                    except ValueError:
                        pass

    dt = float(header.get('SAMPLING_INTERVAL_S', 0.005))
    ndata = int(header.get('NDATA', len(data_lines)))
    units = header.get('UNITS', 'cm/s^2').lower()
    location = header.get('LOCATION', '')
    event_name = header.get('EVENT_NAME', '')

    # Birim dönüşümü → mm/s²
    if 'cm' in units:
        factor = 10.0
    elif units.startswith('g'):
        factor = 9810.0
    elif 'mm' in units:
        factor = 1.0
    else:
        factor = 10.0  # varsayılan: cm/s²

    acc = [v * factor for v in data_lines[:ndata]]
    time = [i * dt for i in range(len(acc))]

    # Kısa isim: LOCATION'dan ilk 3 harf
    name_src = re.sub(r'[^A-Za-z]', '', location) or re.sub(r'[^A-Za-z]', '', event_name) or 'AFD'
    short_name = name_src[:3].upper()

    # Yön: dosya adından
    fname = os.path.basename(filepath).upper()
    if '_ACC_E' in fname or fname.endswith('_E.ASC'):
        direction = 'EW'
    elif '_ACC_N' in fname or fname.endswith('_N.ASC'):
        direction = 'NS'
    elif '_ACC_U' in fname or fname.endswith('_U.ASC') or '_ACC_Z' in fname:
        direction = 'UD'
    else:
        direction = 'H1'

    return time, acc, short_name, direction


# ─────────────────────────────────────────────────────────────
# K-NET  (.EW / .NS / .UD)
# ─────────────────────────────────────────────────────────────

def parse_knet(filepath):
    header = {}
    raw_values = []

    with open(filepath, 'r', errors='ignore') as f:
        lines = f.readlines()

    # Başlık satırları boş satıra kadar
    data_start = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            data_start = i + 1
            break
        if '.' in line and ':' in line.split('.')[0]:
            key, _, val = line.partition('.')
            pass
        # K-NET başlık formatı: "Anahtar       Değer"
        parts = re.split(r'\s{2,}', stripped, maxsplit=1)
        if len(parts) == 2:
            header[parts[0].strip()] = parts[1].strip()
        elif len(parts) == 1 and stripped:
            # Bazı satırlar farklı formatta
            m = re.match(r'^(.+?)\s{1,}(.+)$', stripped)
            if m:
                header[m.group(1)] = m.group(2)

    # Ham sayısal değerleri oku
    for line in lines[data_start:]:
        for tok in line.split():
            try:
                raw_values.append(int(tok))
            except ValueError:
                pass

    # Scale Factor: "3920(gal)/6182761" veya "19600(gal/100)/26214400" gibi
    scale_str = header.get('Scale Factor', '1/1')
    scale_num, scale_denom = 1.0, 1.0
    m = re.search(r'([\d.]+)\s*(?:\([^)]*\))?\s*/\s*([\d.]+)', scale_str)
    if m:
        scale_num = float(m.group(1))
        scale_denom = float(m.group(2))

    # Örnekleme frekansı
    freq_str = header.get('Sampling Freq(Hz)', '100Hz').replace('Hz', '').strip()
    try:
        fs = float(freq_str)
    except:
        fs = 100.0
    dt = 1.0 / fs

    # gal → mm/s²: ×10
    acc = [v * (scale_num / scale_denom) * 10.0 for v in raw_values]
    time = [i * dt for i in range(len(acc))]

    # İsim: İstasyon kodu ilk 3 harf
    station = header.get('Station Code', 'KNT')
    short_name = re.sub(r'[^A-Za-z0-9]', '', station)[:3].upper()

    # Yön: uzantıdan
    ext = os.path.splitext(filepath)[1].upper().lstrip('.')
    dir_map = {'EW': 'EW', 'NS': 'NS', 'UD': 'UD'}
    direction = dir_map.get(ext, ext)

    return time, acc, short_name, direction


# ─────────────────────────────────────────────────────────────
# PEER NGA  (.AT2)
# ─────────────────────────────────────────────────────────────

def parse_peer(filepath):
    with open(filepath, 'r', errors='ignore') as f:
        lines = f.readlines()

    # Satır 1-3: metin, satır 4: NPTS ve DT
    npts, dt = None, None
    data_start = 4
    for i, line in enumerate(lines):
        m = re.search(r'NPTS\s*=\s*([\d]+).*DT\s*=\s*([\d.Ee+\-]+)', line, re.IGNORECASE)
        if m:
            npts = int(m.group(1))
            dt = float(m.group(2))
            data_start = i + 1
            break

    # Birim satırından kontrol
    units_line = lines[2] if len(lines) > 2 else ''
    if 'CM/S/S' in units_line.upper() or 'CM/S^2' in units_line.upper():
        factor = 10.0
    elif ' G' in units_line.upper() or 'UNITS OF G' in units_line.upper():
        factor = 9810.0
    elif 'MM' in units_line.upper():
        factor = 1.0
    else:
        factor = 9810.0  # varsayılan: g

    values = []
    for line in lines[data_start:]:
        for tok in line.split():
            try:
                values.append(float(tok))
            except ValueError:
                pass

    if npts:
        values = values[:npts]
    acc = [v * factor for v in values]
    if dt is None:
        dt = 0.01
    time = [i * dt for i in range(len(acc))]

    # İsim: RSN####_EARTHQUAKE_STATION.AT2 → EARTHQUAKE'in ilk 3 harfi
    fname = os.path.splitext(os.path.basename(filepath))[0]
    parts = fname.split('_')
    if len(parts) >= 2:
        eq_name = re.sub(r'[^A-Za-z]', '', parts[1])
        short_name = eq_name[:3].upper() or 'PER'
        station_part = parts[2] if len(parts) >= 3 else ''
    else:
        short_name = fname[:3].upper()
        station_part = ''

    direction = peer_direction_from_suffix(station_part)

    return time, acc, short_name, direction


# ─────────────────────────────────────────────────────────────
# NEW ZEALAND  (.txt / .V2A)
# Her dosya 1-3 bileşen içerebilir; her bileşen kendi başlığına sahip.
# Her bileşen: metin başlık (16 satır) + sayısal başlık (10 satır) + ivme + hız + yer değiştirme
# Biz yalnızca ivme bölümünü okuyoruz (başlıktan hemen sonraki npts değer).
# ─────────────────────────────────────────────────────────────

def _parse_nz_sections(lines):
    """
    Dosyayı bileşen bölümlerine ayırır.
    Her bölüm 'Corrected accelerogram' ile başlar.
    Döndürür: list of (start_line_idx, component_header_dict)
    """
    sections = []
    for i, line in enumerate(lines):
        if line.strip().startswith('Corrected accelerogram'):
            sections.append(i)
    return sections


def parse_nzealand(filepath):
    """
    Birden fazla bileşen içerebilen NZ dosyasını okur.
    İlk bileşenin ivme verisini döndürür (tek bileşen olsa da çalışır).
    Tüm bileşenler için parse_nzealand_all() kullanılır.
    """
    records = parse_nzealand_all(filepath)
    if not records:
        raise ValueError(f"NZ dosyasında veri bulunamadı: {filepath}")
    return records[0]   # (time, acc, name, direction)


def parse_nzealand_all(filepath):
    """
    Tüm bileşenleri döndürür: list of (time, acc, short_name, direction)
    """
    with open(filepath, 'r', errors='ignore') as f:
        lines = f.readlines()

    section_starts = _parse_nz_sections(lines)
    if not section_starts:
        raise ValueError(f"NZ formatı tanınamadı: {filepath}")

    results = []
    for sec_idx, sec_start in enumerate(section_starts):
        # Bölüm sonu = bir sonraki bölümün başı veya dosya sonu
        sec_end = section_starts[sec_idx + 1] if sec_idx + 1 < len(section_starts) else len(lines)
        sec_lines = lines[sec_start:sec_end]

        # Başlık ayrıştır
        npts, dt = None, None
        component_line = ''
        site = ''

        for line in sec_lines:
            m = re.search(r'Number of points\s+(\d+)', line)
            if m:
                npts = int(m.group(1))
            m = re.search(r'at\s+([\d.]+)\s+sec intervals', line)
            if m:
                dt = float(m.group(1))
            if re.match(r'^Component\s', line, re.IGNORECASE):
                component_line = line.strip()
            m = re.search(r'^Site\s+(\w+)', line)
            if m:
                site = m.group(1)

        if npts is None or dt is None:
            continue

        # Sayısal başlık: metin başlık biter bitmez (Corrected accelerogram'dan
        # itibaren 16 metin satırı + 10 sayısal başlık satırı = 26 satır)
        # İvme verisi bu 26 satırın hemen ardında başlar
        HDR_LINES = 26
        data_lines = sec_lines[HDR_LINES:]

        # İlk npts değeri = ivme (mm/s²)
        values = []
        for line in data_lines:
            for tok in line.split():
                try:
                    values.append(float(tok))
                except ValueError:
                    pass
            if len(values) >= npts:
                break

        if len(values) < npts:
            # Başlık satır sayısı tahminimiz yanlışsa daha esnek ara
            values = []
            in_data = False
            header_count = 0
            for line in sec_lines:
                stripped = line.strip()
                if not in_data:
                    # Sayısal başlık: metin başlık bitince tamamen sayısal satırlar gelir
                    if header_count >= 16 and stripped and all(
                            re.match(r'^-?[\d.]+$', t) for t in stripped.split()):
                        in_data = True
                    else:
                        header_count += 1
                        continue
                if in_data:
                    for tok in stripped.split():
                        try:
                            values.append(float(tok))
                        except ValueError:
                            pass
                    if len(values) >= npts:
                        break

        acc = values[:npts]
        time = [i * dt for i in range(len(acc))]
        short_name = (site[:3] if site else 'NZL').upper()
        direction = nz_direction_from_component(component_line)
        results.append((time, acc, short_name, direction))

    return results


# ─────────────────────────────────────────────────────────────
# DOSYA YAZICI
# ─────────────────────────────────────────────────────────────

def write_output(time, acc, out_path):
    with open(out_path, 'w') as f:
        f.write("Zaman(s)\tIvme(mm/s2)\n")
        for t, a in zip(time, acc):
            f.write(f"{t:.6f}\t{a:.6f}\n")


def _read_norm_txt(path):
    """Normalize/ölçeklenmiş .txt dosyasından zaman ve ivme listelerini okur."""
    times, accels = [], []
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('Z'):   # başlık satırı
                continue
            parts = line.split('\t')
            if len(parts) >= 2:
                times.append(float(parts[0]))
                accels.append(float(parts[1]))
    return times, accels


def unique_path(folder, name, direction, ext='.txt'):
    """Eski uyumluluk için korundu; yeni kod unique_norm_path kullanır."""
    base = f"{name}_{direction}"
    path = os.path.join(folder, base + ext)
    counter = 2
    while os.path.exists(path):
        path = os.path.join(folder, f"{base}_{counter}{ext}")
        counter += 1
    return path


def unique_norm_path(folder, name, direction, dt, ndata):
    """
    Normalize edilmiş çıktı adı:
      {name}_{direction}_{h1|h2|v}_{dt:.4f}_{ndata}.txt
    Çakışırsa sonuna _{2}, _{3}, ... eklenir.
    """
    label = DIR_LABEL.get(direction.upper(), direction.lower())
    base  = f"{name}_{direction}_{label}_{dt:.4f}_{ndata}"
    path  = os.path.join(folder, base + '.txt')
    ctr   = 2
    while os.path.exists(path):
        path = os.path.join(folder, f"{base}_{ctr}.txt")
        ctr += 1
    return path


# ─────────────────────────────────────────────────────────────
# ANA DÖNÜŞTÜRME FONKSİYONU
# ─────────────────────────────────────────────────────────────

PARSERS = {
    'AFAD':     parse_afad,
    'KNET':     parse_knet,
    'PEER':     parse_peer,
    'NZEALAND': parse_nzealand,
}

FORMAT_NAMES = {
    'AFAD':     'AFAD/TADAS',
    'KNET':     'K-NET (Japonya)',
    'PEER':     'PEER NGA',
    'NZEALAND': 'New Zealand',
}

def convert_files(filepaths, out_folder, log_fn=None):
    os.makedirs(out_folder, exist_ok=True)
    results = []

    for fp in filepaths:
        fmt, warning = detect_format(fp)
        if fmt is None:
            # Uyarı metnini tek satır özetle logla
            short_warn = warning.split('\n')[0] if warning else "Tanımsız format"
            msg = f"[ATLA] {os.path.basename(fp)} — {short_warn}"
            if log_fn: log_fn(msg)
            results.append((fp, False, short_warn))
            continue

        try:
            if fmt == 'NZEALAND':
                records = parse_nzealand_all(fp)
                for time, acc, name, direction in records:
                    dt    = time[1] - time[0] if len(time) > 1 else 0.005
                    ndata = len(acc)
                    out_path = unique_norm_path(out_folder, name, direction, dt, ndata)
                    write_output(time, acc, out_path)
                    msg = f"[OK] {FORMAT_NAMES[fmt]} | {os.path.basename(fp)} → {os.path.basename(out_path)}"
                    if log_fn: log_fn(msg)
                results.append((fp, True, f"{len(records)} bilesen"))
            else:
                time, acc, name, direction = PARSERS[fmt](fp)
                dt    = time[1] - time[0] if len(time) > 1 else 0.005
                ndata = len(acc)
                out_path = unique_norm_path(out_folder, name, direction, dt, ndata)
                write_output(time, acc, out_path)
                msg = f"[OK] {FORMAT_NAMES[fmt]} | {os.path.basename(fp)} → {os.path.basename(out_path)}"
                if log_fn: log_fn(msg)
                results.append((fp, True, out_path))
        except Exception as e:
            msg = f"[HATA] {os.path.basename(fp)}: {e}"
            if log_fn: log_fn(msg)
            results.append((fp, False, str(e)))

    return results


# ─────────────────────────────────────────────────────────────
# SPEKTRUM ANALİZİ
# ─────────────────────────────────────────────────────────────

# Standart periyot dizisi (s) — T_max = 10 s
STANDARD_PERIODS = [
    0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08, 0.09,
    0.10, 0.11, 0.12, 0.13, 0.14, 0.15, 0.16, 0.17, 0.18, 0.19,
    0.20, 0.22, 0.24, 0.25, 0.26, 0.28, 0.30, 0.32, 0.34, 0.35,
    0.36, 0.38, 0.40, 0.42, 0.44, 0.45, 0.46, 0.48,
    0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95,
    1.00, 1.10, 1.20, 1.30, 1.40, 1.50, 1.60, 1.70, 1.80, 1.90,
    2.00, 2.20, 2.40, 2.60, 2.80, 3.00, 3.50, 4.00,
    4.50, 5.00, 5.50, 6.00, 6.50, 7.00, 7.50, 8.00, 8.50, 9.00, 9.50, 10.00,
]

# Yön → H1/H2/V etiket eşlemesi
DIR_LABEL = {
    'EW': 'h1', 'H1': 'h1',
    'NS': 'h2', 'H2': 'h2',
    'UD': 'v',  'V':  'v',
}


def compute_response_spectrum(ag, dt, periods=None, xi=0.05):
    """
    Newmark-β (ortalama ivme, β=0.25, γ=0.5) ile tepki spektrumu.
    ag     : zemin ivmesi (mm/s²), liste
    dt     : zaman adımı (s)
    periods: periyot dizisi (s); None → STANDARD_PERIODS
    xi     : sönüm oranı (varsayılan 0.05 = %5)
    Döndürür: (periods, PSa_list)  — PSa mm/s² cinsinden
    """
    if periods is None:
        periods = STANDARD_PERIODS

    beta  = 0.25
    gamma = 0.5
    n = len(ag)
    psa_out = []

    for T in periods:
        omega = 2.0 * math.pi / T
        k = omega * omega          # rijitlik (m=1)
        c = 2.0 * xi * omega       # sönüm (m=1)

        # Etkin rijitlik
        k_eff = k + gamma / (beta * dt) * c + 1.0 / (beta * dt * dt)

        # Yinelemeli sabitler (Chopra, "Dynamics of Structures" denk. 5.4.8)
        A = 1.0 / (beta * dt) + gamma / beta * c
        B = 1.0 / (2.0 * beta) + dt * (gamma / (2.0 * beta) - 1.0) * c

        # Başlangıç koşulları (sıfır)
        u = 0.0
        v = 0.0
        a = -ag[0]   # denge: a + c·v + k·u = -ag[0]
        max_u = 0.0

        for i in range(n - 1):
            dp = -(ag[i + 1] - ag[i])          # Δyük = -Δag (m=1)
            dp_eff = dp + A * v + B * a

            du = dp_eff / k_eff
            dv = gamma / (beta * dt) * du - gamma / beta * v + dt * (1.0 - gamma / (2.0 * beta)) * a
            da = 1.0 / (beta * dt * dt) * du - 1.0 / (beta * dt) * v - 1.0 / (2.0 * beta) * a

            u += du
            v += dv
            a += da

            abs_u = u if u >= 0 else -u
            if abs_u > max_u:
                max_u = abs_u

        psa_out.append(omega * omega * max_u)   # PSa = ω²·Sd

    return periods, psa_out


def read_normalized_file(filepath):
    """Normalize edilmiş txt dosyasını okur. (zaman, ivme) → (acc list, dt)"""
    acc = []
    times = []
    with open(filepath, 'r', errors='ignore') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('Zaman'):
                continue
            parts = line.split()
            if len(parts) >= 2:
                try:
                    times.append(float(parts[0]))
                    acc.append(float(parts[1]))
                except ValueError:
                    pass
    dt = (times[1] - times[0]) if len(times) > 1 else 0.01
    return acc, dt


def parse_norm_filename(fname):
    """
    Yeni format: 'KOB_EW_h1_0.0100_3200.txt'   → ('KOB', 'EW', '')
    Çakışma   : 'KOB_EW_h1_0.0100_3200_2.txt'  → ('KOB', 'EW', '2')
    Eski format: 'KOB_EW.txt'                   → ('KOB', 'EW', '')
    """
    base  = os.path.splitext(fname)[0]
    parts = base.split('_')
    if len(parts) < 2:
        return base, 'H1', ''

    code      = parts[0]
    direction = parts[1].upper()

    # Çakışma sayacı: son parça saf rakamsa ve en az 5 bölüm varsa
    suffix = ''
    if len(parts) > 2 and parts[-1].isdigit():
        suffix = parts[-1]

    return code, direction, suffix


def write_spectrum(periods, psa, out_path):
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write("T(s)\tSa(mm/s2)\n")
        for T, sa in zip(periods, psa):
            f.write(f"{T:.4f}\t{sa:.4f}\n")


def compute_spectra_for_folder(norm_folder, spec_folder, xi=0.05, log_fn=None):
    """
    norm_folder içindeki normalize edilmiş .txt dosyalarından spektrum hesaplar.
    Bireysel (h1/h2/v etiketli) + GeoMean (yalnızca H) + ortalama spektrumları kaydeder.
    """
    os.makedirs(spec_folder, exist_ok=True)

    all_files = [f for f in os.listdir(norm_folder)
                 if f.lower().endswith('.txt') and '_spektrum' not in f.lower()]
    if not all_files:
        if log_fn: log_fn("[UYARI] Klasörde normalize edilmiş .txt dosyasi bulunamadi.")
        return

    n_per = len(STANDARD_PERIODS)
    # Grup: (code, suffix) → {direction: psa}
    groups = {}
    # Ortalama için tüm yönlerdeki PSA listeleri
    all_h1, all_h2, all_v = [], [], []

    total = len(all_files)
    for idx, fname in enumerate(sorted(all_files)):
        fp = os.path.join(norm_folder, fname)
        code, direction, suffix = parse_norm_filename(fname)
        label = DIR_LABEL.get(direction, direction.lower())   # 'h1' / 'h2' / 'v'
        key = (code, suffix)

        if log_fn: log_fn(f"[{idx+1}/{total}] {fname}  -> {code}_{direction} ({label})")

        try:
            ag, dt = read_normalized_file(fp)
            if len(ag) < 10:
                if log_fn: log_fn(f"  -> Veri cok kisa, atlaniyor.")
                continue

            _, psa = compute_response_spectrum(ag, dt, xi=xi)

            # ── Bireysel spektrum: isim sonuna h1/h2/v ekle ──────
            if suffix:
                out_name = f"{code}_{direction}_{suffix}_{label}_spektrum.txt"
            else:
                out_name = f"{code}_{direction}_{label}_spektrum.txt"
            write_spectrum(STANDARD_PERIODS, psa, os.path.join(spec_folder, out_name))

            # Grup ve ortalama kayıtları
            if key not in groups:
                groups[key] = {}
            groups[key][direction] = psa

            if label == 'h1':
                all_h1.append(psa)
            elif label == 'h2':
                all_h2.append(psa)
            elif label == 'v':
                all_v.append(psa)

        except Exception as e:
            if log_fn: log_fn(f"  -> HATA: {e}")

    # ── GeoMean: yalnızca yatay (H1 × H2) ──────────────────────
    if log_fn: log_fn(f"\nGeoMean hesaplaniyor ({len(groups)} grup, yalnizca H1+H2)...")

    for (code, suffix), dirs in groups.items():
        horizontals = [dirs[d] for d in dirs if DIR_LABEL.get(d, '') in ('h1', 'h2')]
        if len(horizontals) < 1:
            continue

        n_h = len(horizontals)
        geomean = [
            math.exp(sum(math.log(max(h[i], 1e-12)) for h in horizontals) / n_h)
            for i in range(n_per)
        ]
        label_sfx = f"_{suffix}" if suffix else ""
        gm_path = os.path.join(spec_folder, f"{code}{label_sfx}_GeoMean_spektrum.txt")
        write_spectrum(STANDARD_PERIODS, geomean, gm_path)
        dirs_used = '+'.join(d for d in dirs if DIR_LABEL.get(d, '') in ('h1', 'h2'))
        if log_fn: log_fn(f"  GeoMean -> {os.path.basename(gm_path)}  [{dirs_used}]")

    # ── Ortalama spektrumlar ─────────────────────────────────────
    def _avg_spectrum(psa_list, label_str):
        if not psa_list:
            return
        n = len(psa_list)
        avg = [sum(psa_list[j][i] for j in range(n)) / n for i in range(n_per)]
        fname = f"ortalama_{label_str}_spektrum.txt"
        write_spectrum(STANDARD_PERIODS, avg, os.path.join(spec_folder, fname))
        if log_fn: log_fn(f"  Ortalama {label_str} -> {fname}  ({n} kayit)")

    if log_fn: log_fn(f"\nOrtalama spektrumlar hesaplaniyor...")
    _avg_spectrum(all_h1, 'H1')
    _avg_spectrum(all_h2, 'H2')
    _avg_spectrum(all_v,  'V')

    if log_fn: log_fn(f"\nSpektrumlar klasoru: {spec_folder}")


# ─────────────────────────────────────────────────────────────
# TBDY 2018 HEDEF SPEKTRUM
# ─────────────────────────────────────────────────────────────

def tbdy2018_yatay(Sds, Sd1, periods):
    """
    TBDY 2018 Yatay Elastik Tasarım İvme Spektrumu (mm/s²).
    Sds, Sd1 : boyutsuz (g cinsinden katsayı)
    """
    g   = 9810.0
    TA  = 0.2 * Sd1 / Sds
    TS  = Sd1 / Sds
    TL  = 6.0
    sa  = []
    for T in periods:
        if T < TA:
            sa.append((0.4 + 0.6 * T / TA) * Sds * g)
        elif T <= TS:
            sa.append(Sds * g)
        elif T <= TL:
            sa.append(Sd1 / T * g)
        else:
            sa.append(Sd1 * TL / (T * T) * g)
    return sa


def tbdy2018_dusey(Sds, Sd1, periods):
    """
    TBDY 2018 Düşey Elastik Tasarım İvme Spektrumu (mm/s²).
    TBDY 2018 Madde 2.3.4.3:
      Köşe periyotları = yatayın 1/3'ü, tepe ivmesi = 2/3 × Sds.
    """
    g      = 9810.0
    Sds_v  = Sds * 2.0 / 3.0
    TA_v   = 0.2 * Sd1 / (3.0 * Sds)   # TA / 3
    TS_v   = Sd1 / (3.0 * Sds)          # TS / 3
    TL_v   = 2.0                         # TL / 3 = 6/3
    SD1_v  = Sds_v * TS_v               # = 2*Sd1/9
    sa     = []
    for T in periods:
        if T < TA_v:
            sa.append((0.4 + 0.6 * T / TA_v) * Sds_v * g)
        elif T <= TS_v:
            sa.append(Sds_v * g)
        elif T <= TL_v:
            sa.append(SD1_v / T * g)
        else:
            sa.append(SD1_v * TL_v / (T * T) * g)
    return sa


# ─────────────────────────────────────────────────────────────
# NUMPY İVME SPEKTRUM (hızlandırılmış, opsiyonel)
# ─────────────────────────────────────────────────────────────

def _spectrum_np(ag_list, dt, periods, xi=0.05):
    """Numpy ile tüm periyotları aynı anda hesaplar (çok hızlı)."""
    import numpy as np
    ag  = np.asarray(ag_list, dtype=np.float64)
    T   = np.asarray(periods,  dtype=np.float64)
    w   = 2.0 * np.pi / T
    k   = w * w
    c   = 2.0 * xi * w
    b, g_ = 0.25, 0.5
    keff  = k + g_ / (b * dt) * c + 1.0 / (b * dt * dt)
    A     = 1.0 / (b * dt) + g_ / b * c
    B     = 1.0 / (2.0 * b) + dt * (g_ / (2.0 * b) - 1.0) * c
    u = np.zeros_like(T); v = np.zeros_like(T); a = -ag[0] * np.ones_like(T)
    max_u = np.zeros_like(T)
    for i in range(len(ag) - 1):
        dp   = -(ag[i + 1] - ag[i])
        dpef = dp + A * v + B * a
        du   = dpef / keff
        dv   = g_ / (b * dt) * du - g_ / b * v + dt * (1.0 - g_ / (2.0 * b)) * a
        da   = 1.0 / (b * dt * dt) * du - 1.0 / (b * dt) * v - 1.0 / (2.0 * b) * a
        u += du; v += dv; a += da
        np.maximum(max_u, np.abs(u), out=max_u)
    return (w * w * max_u).tolist()


def _spectrum_fast(ag, dt, periods=None, xi=0.05):
    """Numpy varsa hızlandırılmış, yoksa saf-Python versiyonu."""
    if periods is None:
        periods = STANDARD_PERIODS
    try:
        import numpy as np
        return _spectrum_np(ag, dt, periods, xi)
    except ImportError:
        return compute_response_spectrum(ag, dt, periods, xi)[1]


# ─────────────────────────────────────────────────────────────
# FREKANS ALANINDA SPEKTRAL UYUŞUM (İTERATİF)
# ─────────────────────────────────────────────────────────────

def spectral_match(ag, dt, target_sa, T_min=0.1, T_max=4.0,
                   n_iter=3, xi=0.05):
    """
    Frekans alanında iteratif spektral uyuşum (SeismoMatch benzeri).

    ag        : zemin ivmesi (mm/s²) listesi
    dt        : zaman adımı (s)
    target_sa : STANDARD_PERIODS'taki hedef spektrum (mm/s²) listesi
    T_min/max : eşleştirme periyot aralığı (s)
    n_iter    : iterasyon sayısı

    Döndürür: (scaled_ag, sf_mean)
      scaled_ag : uyuşturulmuş ivme dizisi (mm/s²)
      sf_mean   : geometrik ortalama ölçek faktörü (raporlama için)
    """
    import numpy as np

    x       = np.asarray(ag, dtype=np.float64)
    n       = len(x)
    tgt     = np.asarray(target_sa, dtype=np.float64)
    std_T   = np.asarray(STANDARD_PERIODS)
    std_f   = 1.0 / std_T   # frekans (Hz)

    mask    = (std_T >= T_min) & (std_T <= T_max)
    freqs   = np.fft.rfftfreq(n, d=dt)   # FFT frekans ekseni

    for _ in range(n_iter):
        cur  = np.asarray(_spectrum_np(x.tolist(), dt, STANDARD_PERIODS, xi))
        ratio = np.ones(len(std_T))
        ratio[mask] = tgt[mask] / np.maximum(cur[mask], 1e-9)

        # Periyot → frekans: küçük T = büyük f; artan frekans sırası gerekli
        sort_idx    = np.argsort(std_f)
        f_sorted    = std_f[sort_idx]
        r_sorted    = ratio[sort_idx]

        gain = np.interp(freqs, f_sorted, r_sorted,
                         left=float(r_sorted[0]), right=1.0)
        gain[0] = 1.0   # DC bileşeni değiştirilmez

        X = np.fft.rfft(x)
        x = np.fft.irfft(X * gain, n=n)

    # Ortalama ölçek faktörü (geometrik ortalama, eşleştirme aralığında)
    orig  = np.asarray(_spectrum_np(ag, dt, STANDARD_PERIODS, xi))
    sf_log = float(np.mean(np.log(
        tgt[mask] / np.maximum(orig[mask], 1e-9)
    )))
    sf_mean = math.exp(sf_log)

    return x.tolist(), sf_mean


def spectral_match_geomean(ag1, ag2, dt, target_sa, T_min=0.1, T_max=4.0,
                            n_iter=3, xi=0.05):
    """
    İki yatay bileşenin (H1, H2) geometrik ortalamasını hedef spektruma
    frekans alanında iteratif uyuşturur. Her iterasyonda aynı kazanç
    her iki bileşene uygulanır.

    Döndürür: (scaled_ag1, scaled_ag2, sf_mean)
    """
    import numpy as np

    n1, n2 = len(ag1), len(ag2)
    n = max(n1, n2)
    x1 = np.pad(np.asarray(ag1, dtype=np.float64), (0, n - n1))
    x2 = np.pad(np.asarray(ag2, dtype=np.float64), (0, n - n2))

    tgt   = np.asarray(target_sa, dtype=np.float64)
    std_T = np.asarray(STANDARD_PERIODS)
    std_f = 1.0 / std_T
    mask  = (std_T >= T_min) & (std_T <= T_max)
    freqs = np.fft.rfftfreq(n, d=dt)

    for _ in range(n_iter):
        sa1 = np.asarray(_spectrum_np(x1.tolist(), dt, STANDARD_PERIODS, xi))
        sa2 = np.asarray(_spectrum_np(x2.tolist(), dt, STANDARD_PERIODS, xi))
        gm  = np.sqrt(np.maximum(sa1 * sa2, 1e-24))

        ratio = np.ones(len(std_T))
        ratio[mask] = tgt[mask] / np.maximum(gm[mask], 1e-9)

        sort_idx = np.argsort(std_f)
        gain = np.interp(freqs, std_f[sort_idx], ratio[sort_idx],
                         left=float(ratio[sort_idx][0]), right=1.0)
        gain[0] = 1.0

        x1 = np.fft.irfft(np.fft.rfft(x1) * gain, n=n)
        x2 = np.fft.irfft(np.fft.rfft(x2) * gain, n=n)

    # Ortalama ölçek faktörü (orijinal kayıtlara göre)
    sa1_orig = np.asarray(_spectrum_np(ag1, dt, STANDARD_PERIODS, xi))
    sa2_orig = np.asarray(_spectrum_np(ag2, dt, STANDARD_PERIODS, xi))
    gm_orig  = np.sqrt(np.maximum(sa1_orig * sa2_orig, 1e-24))
    sf_log   = float(np.mean(np.log(tgt[mask] / np.maximum(gm_orig[mask], 1e-9))))
    sf_mean  = math.exp(sf_log)

    return x1[:n1].tolist(), x2[:n2].tolist(), sf_mean


# ─────────────────────────────────────────────────────────────
# KLASÖR DÜZEYİNDE ÖLÇEKLEME FONKSİYONU
# ─────────────────────────────────────────────────────────────

def scale_folder(norm_folder, scaled_folder, Sds, Sd1,
                 T_min=0.1, T_max=4.0, n_iter=3, xi=0.05,
                 log_fn=None):
    """
    norm_folder içindeki normalize edilmiş kayıtları TBDY 2018 hedef
    spektrumuna GeoMean tabanlı frekans alanında uyuşturur.
    H1+H2 çiftleri birlikte işlenir (aynı kazanç); dikey (V) bireysel.
    Çıktılar scaled_folder'a, özet scale_faktoru.txt'e kaydedilir.
    """
    try:
        import numpy as np
    except ImportError:
        raise RuntimeError(
            "Bu özellik numpy gerektiriyor.\n"
            "Kurmak için terminalde:  pip install numpy"
        )

    os.makedirs(scaled_folder, exist_ok=True)

    tgt_h = tbdy2018_yatay(Sds, Sd1, STANDARD_PERIODS)
    tgt_v = tbdy2018_dusey(Sds, Sd1, STANDARD_PERIODS)

    all_files = sorted(f for f in os.listdir(norm_folder)
                       if f.lower().endswith('.txt') and '_spektrum' not in f.lower())
    if not all_files:
        if log_fn: log_fn("[UYARI] Klasorde normalize edilmis dosya bulunamadi.")
        return

    # (code, suffix) → {label: (fname, filepath)}
    groups = {}
    for fname in all_files:
        fp = os.path.join(norm_folder, fname)
        code, direction, suffix = parse_norm_filename(fname)
        lbl = DIR_LABEL.get(direction, direction.lower())
        key = (code, suffix)
        if key not in groups:
            groups[key] = {}
        groups[key][lbl] = (fname, fp)

    summary_rows = ["Dosya\tYon\tSF_ortalama\tEslesmePeriyadAraligi"]

    for (code, suffix), lbls in groups.items():

        # ── Dikey: bireysel uyuşum ───────────────────────────
        if 'v' in lbls:
            fname, fp = lbls['v']
            T_max_v = min(T_max, 2.0)
            if log_fn: log_fn(f"[Dusey] {fname}  T=[{T_min:.2f},{T_max_v:.2f}]s")
            try:
                ag, dt = read_normalized_file(fp)
                scaled, sf = spectral_match(ag, dt, tgt_v,
                                            T_min=T_min, T_max=T_max_v,
                                            n_iter=n_iter, xi=xi)
                time_col = [i * dt for i in range(len(scaled))]
                write_output(time_col, scaled, os.path.join(scaled_folder, fname))
                summary_rows.append(f"{fname}\tV\t{sf:.4f}\t{T_min:.2f}-{T_max_v:.2f}")
                if log_fn: log_fn(f"  -> SF = {sf:.4f}   yazildi: {fname}")
            except Exception as e:
                if log_fn: log_fn(f"  -> HATA: {e}")
                summary_rows.append(f"{fname}\tV\tHATA\t-")

        # ── Yatay çift: GeoMean uyuşum ───────────────────────
        h1_entry = lbls.get('h1')
        h2_entry = lbls.get('h2')

        if h1_entry and h2_entry:
            fname1, fp1 = h1_entry
            fname2, fp2 = h2_entry
            if log_fn: log_fn(f"[GeoMean] {fname1} + {fname2}  T=[{T_min:.2f},{T_max:.2f}]s")
            try:
                ag1, dt1 = read_normalized_file(fp1)
                ag2, dt2 = read_normalized_file(fp2)
                sc1, sc2, sf = spectral_match_geomean(ag1, ag2, dt1, tgt_h,
                                                       T_min=T_min, T_max=T_max,
                                                       n_iter=n_iter, xi=xi)
                write_output([i * dt1 for i in range(len(sc1))], sc1,
                             os.path.join(scaled_folder, fname1))
                write_output([i * dt2 for i in range(len(sc2))], sc2,
                             os.path.join(scaled_folder, fname2))
                summary_rows.append(
                    f"{fname1}+{fname2}\tH1+H2(GeoMean)\t{sf:.4f}\t{T_min:.2f}-{T_max:.2f}"
                )
                if log_fn: log_fn(f"  -> SF = {sf:.4f}   yazildi: {fname1}, {fname2}")
            except Exception as e:
                if log_fn: log_fn(f"  -> HATA: {e}")
                summary_rows.append(f"{fname1}+{fname2}\tH1+H2(GeoMean)\tHATA\t-")

        elif h1_entry or h2_entry:
            # Eşsiz yatay bileşen: bireysel uyuşum
            fname, fp = h1_entry or h2_entry
            lbl_name = 'H1' if h1_entry else 'H2'
            if log_fn: log_fn(f"[Yatay-tek] {fname}  T=[{T_min:.2f},{T_max:.2f}]s")
            try:
                ag, dt = read_normalized_file(fp)
                scaled, sf = spectral_match(ag, dt, tgt_h,
                                            T_min=T_min, T_max=T_max,
                                            n_iter=n_iter, xi=xi)
                time_col = [i * dt for i in range(len(scaled))]
                write_output(time_col, scaled, os.path.join(scaled_folder, fname))
                summary_rows.append(f"{fname}\t{lbl_name}\t{sf:.4f}\t{T_min:.2f}-{T_max:.2f}")
                if log_fn: log_fn(f"  -> SF = {sf:.4f}   yazildi: {fname}")
            except Exception as e:
                if log_fn: log_fn(f"  -> HATA: {e}")
                summary_rows.append(f"{fname}\t{lbl_name}\tHATA\t-")

    # Özet dosyası
    summary_path = os.path.join(scaled_folder, "scale_faktoru.txt")
    with open(summary_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(summary_rows) + '\n')
    if log_fn: log_fn(f"\nOzet: {summary_path}")
    if log_fn: log_fn(f"Olceklenmis kayitlar: {scaled_folder}")

    export_scaled_excel(scaled_folder, Sds=Sds, Sd1=Sd1, xi=xi, log_fn=log_fn)


# ─────────────────────────────────────────────────────────────
# ROTD50 / ROTD100
# ─────────────────────────────────────────────────────────────

def compute_rotd(ag1, ag2, dt, periods=None, xi=0.05, n_angles=90):
    """
    0–180° arasında n_angles açıda rotated spektrum hesaplar.
    Döndürür: (rotd50_list, rotd100_list)
    """
    import numpy as np
    if periods is None:
        periods = STANDARD_PERIODS
    angles = np.linspace(0.0, math.pi, n_angles, endpoint=False)
    a1 = np.asarray(ag1, dtype=np.float64)
    a2 = np.asarray(ag2, dtype=np.float64)
    n = max(len(a1), len(a2))
    a1 = np.pad(a1, (0, n - len(a1)))
    a2 = np.pad(a2, (0, n - len(a2)))

    sa_mat = np.empty((n_angles, len(periods)))
    for k, theta in enumerate(angles):
        rotated = a1 * math.cos(float(theta)) + a2 * math.sin(float(theta))
        sa_mat[k] = _spectrum_np(rotated.tolist(), dt, periods, xi)

    rotd50  = np.percentile(sa_mat, 50, axis=0)
    rotd100 = np.max(sa_mat, axis=0)
    return rotd50.tolist(), rotd100.tolist()


# ─────────────────────────────────────────────────────────────
# EXCEL RAPORU
# ─────────────────────────────────────────────────────────────

def export_scaled_excel(scaled_folder, Sds=None, Sd1=None, xi=0.05, log_fn=None):
    """
    scaled_folder içindeki ölçeklenmiş kayıtlardan:
      Sayfa 1 – Kayıt Bilgileri
      Sayfa 2 – H1/H2/V bireysel + GeoMean + TBDY2018 hedef spektrumu + grafik
      Sayfa 3 – RotD50 + TBDY2018 hedef
      Sayfa 4 – RotD100 + TBDY2018 hedef
    Tüm spektrumlar T=0 (PGA) dahil yazılır.
    """
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment
        from openpyxl.utils import get_column_letter
        from openpyxl.chart import ScatterChart, Reference, Series
    except ImportError:
        if log_fn: log_fn("[UYARI] openpyxl bulunamadi. Excel cikti atlandi.\n"
                          "Kurmak icin:  pip install openpyxl")
        return

    try:
        import numpy as np
    except ImportError:
        if log_fn: log_fn("[UYARI] numpy bulunamadi. Excel cikti atlandi.")
        return

    all_files = sorted(
        f for f in os.listdir(scaled_folder)
        if f.lower().endswith('.txt')
        and '_spektrum' not in f.lower()
        and f != 'scale_faktoru.txt'
    )
    if not all_files:
        if log_fn: log_fn("[UYARI] Excel icin scaled dosya bulunamadi.")
        return

    if log_fn: log_fn("\nExcel raporu hazirlaniyor...")

    # Grupla ve Sayfa-1 verilerini topla
    groups    = {}   # (code, suffix) → {lbl: (fname, fp)}
    info_rows = []

    for fname in all_files:
        fp = os.path.join(scaled_folder, fname)
        code, direction, suffix = parse_norm_filename(fname)
        lbl = DIR_LABEL.get(direction, direction.lower())
        key = (code, suffix)
        if key not in groups:
            groups[key] = {}
        groups[key][lbl] = (fname, fp)

        try:
            ag, dt = read_normalized_file(fp)
            pga  = max(abs(v) for v in ag)
            sure = (len(ag) - 1) * dt
            info_rows.append((fname, code, direction,
                               round(dt, 6), round(sure, 3),
                               len(ag), round(pga, 2)))
        except Exception:
            info_rows.append((fname, code, direction, '?', '?', '?', '?'))

    # Periyot listesi: T=0 başa eklendi
    base_periods = STANDARD_PERIODS
    ext_periods  = [0.0] + list(base_periods)
    n_ext = len(ext_periods)

    individual_cols = []   # [(header, psa_ext), ...]
    geomean_cols    = []
    rotd50_cols     = []
    rotd100_cols    = []

    for (code, suffix), lbls in groups.items():
        lbl_sfx  = f"-{suffix}" if suffix else ""
        h1_entry = lbls.get('h1')
        h2_entry = lbls.get('h2')
        v_entry  = lbls.get('v')

        psa_h1_ext = psa_h2_ext = None
        ag1_arr = ag2_arr = None
        dt1_val = None

        if h1_entry:
            _, fp = h1_entry
            ag, dt = read_normalized_file(fp)
            pga = max(abs(v) for v in ag)
            psa = _spectrum_fast(ag, dt, base_periods, xi)
            psa_h1_ext = [pga] + psa
            individual_cols.append((f"{code}{lbl_sfx}-H1", psa_h1_ext))
            ag1_arr, dt1_val = np.asarray(ag, dtype=np.float64), dt

        if h2_entry:
            _, fp = h2_entry
            ag, dt = read_normalized_file(fp)
            pga = max(abs(v) for v in ag)
            psa = _spectrum_fast(ag, dt, base_periods, xi)
            psa_h2_ext = [pga] + psa
            individual_cols.append((f"{code}{lbl_sfx}-H2", psa_h2_ext))
            ag2_arr = np.asarray(ag, dtype=np.float64)

        if v_entry:
            _, fp = v_entry
            ag, dt = read_normalized_file(fp)
            pga = max(abs(v) for v in ag)
            psa = _spectrum_fast(ag, dt, base_periods, xi)
            individual_cols.append((f"{code}{lbl_sfx}-V", [pga] + psa))

        if psa_h1_ext and psa_h2_ext:
            gm = [math.exp((math.log(max(psa_h1_ext[i], 1e-12)) +
                            math.log(max(psa_h2_ext[i], 1e-12))) / 2.0)
                  for i in range(n_ext)]
            geomean_cols.append((f"{code}{lbl_sfx}-GeoMean", gm))

            # RotD50 / RotD100 (T=0 dahil)
            try:
                n_pad = max(len(ag1_arr), len(ag2_arr))
                a1 = np.pad(ag1_arr, (0, n_pad - len(ag1_arr)))
                a2 = np.pad(ag2_arr, (0, n_pad - len(ag2_arr)))
                angles = np.linspace(0.0, math.pi, 36, endpoint=False)

                # T=0: PGA of rotated component
                pga_rot = np.array([
                    float(np.max(np.abs(a1 * math.cos(float(th)) +
                                        a2 * math.sin(float(th)))))
                    for th in angles
                ])
                rd50_zero  = float(np.percentile(pga_rot, 50))
                rd100_zero = float(np.max(pga_rot))

                # T > 0
                sa_mat = np.empty((36, len(base_periods)))
                for k, theta in enumerate(angles):
                    rot = a1 * math.cos(float(theta)) + a2 * math.sin(float(theta))
                    sa_mat[k] = _spectrum_np(rot.tolist(), dt1_val, base_periods, xi)

                rd50  = [rd50_zero]  + np.percentile(sa_mat, 50, axis=0).tolist()
                rd100 = [rd100_zero] + np.max(sa_mat, axis=0).tolist()
                rotd50_cols.append((f"{code}{lbl_sfx}", rd50))
                rotd100_cols.append((f"{code}{lbl_sfx}", rd100))
                if log_fn: log_fn(f"  RotD hesaplandi: {code}{lbl_sfx}")
            except Exception as e:
                if log_fn: log_fn(f"  RotD HATA ({code}): {e}")

    # TBDY 2018 hedef spektrumu (T=0 dahil)
    tbdy_col = None
    if Sds is not None and Sd1 is not None:
        tbdy_vals = tbdy2018_yatay(Sds, Sd1, ext_periods)
        tbdy_col  = ("TBDY2018-Hedef", tbdy_vals)

    # ── Excel oluştur ────────────────────────────────────────
    wb = openpyxl.Workbook()

    hdr_font  = Font(bold=True, color="FFFFFF")
    hdr_fill  = PatternFill("solid", fgColor="1F4E79")
    tbdy_fill = PatternFill("solid", fgColor="C00000")
    center    = Alignment(horizontal='center')

    def _hdr(ws, row, col, val, fill=None):
        c = ws.cell(row=row, column=col, value=val)
        c.font      = hdr_font
        c.fill      = fill or hdr_fill
        c.alignment = center

    def _write_spec_sheet(ws, cols_data):
        _hdr(ws, 1, 1, "T(s)")
        for ci, (hdr, _) in enumerate(cols_data, start=2):
            fill = tbdy_fill if "TBDY" in hdr else hdr_fill
            _hdr(ws, 1, ci, hdr, fill=fill)
        for ri, T in enumerate(ext_periods, start=2):
            ws.cell(row=ri, column=1, value=round(T, 4))
            for ci, (_, psa) in enumerate(cols_data, start=2):
                ws.cell(row=ri, column=ci, value=round(psa[ri - 2], 4))
        ws.column_dimensions['A'].width = 10
        for ci in range(2, len(cols_data) + 2):
            ws.column_dimensions[get_column_letter(ci)].width = 16

    def _add_chart(ws, all_cols, title):
        """
        Bireysel H1/H2/V sütunları hariç tüm serileri grafiğe ekler.
        TBDY serisi kırmızı kalın çizgi, diğerleri normal.
        """
        chart = ScatterChart()
        chart.title  = title
        chart.style  = 10
        chart.x_axis.title = "T (s)"
        chart.y_axis.title = "Sa (mm/s²)"
        chart.width  = 22
        chart.height = 14
        n_rows = len(ext_periods) + 1
        x_ref  = Reference(ws, min_col=1, min_row=2, max_row=n_rows)

        for ci, (hdr, _) in enumerate(all_cols, start=2):
            # Bireysel bileşenleri (H1, H2, V) grafige ekleme
            if any(hdr.endswith(s) for s in ('-H1', '-H2', '-V')):
                continue
            is_tbdy = "TBDY" in hdr
            y_ref = Reference(ws, min_col=ci, min_row=1, max_row=n_rows)
            ser   = Series(y_ref, x_ref, title_from_data=True)
            ser.smooth = True
            if is_tbdy:
                ser.graphicalProperties.line.solidFill = "C00000"
                ser.graphicalProperties.line.width     = 25000
                ser.smooth = False
            chart.series.append(ser)

        ws.add_chart(chart, f"A{n_rows + 3}")

    # Sayfa 1 – Kayıt Bilgileri
    ws1 = wb.active
    ws1.title = "Kayit Bilgileri"
    hdrs1 = ["Dosya", "Kod", "Yon", "dt(s)", "Sure(s)", "Nokta Sayisi", "PGA(mm/s2)"]
    for ci, h in enumerate(hdrs1, 1):
        _hdr(ws1, 1, ci, h)
    for ri, row in enumerate(info_rows, 2):
        for ci, val in enumerate(row, 1):
            ws1.cell(row=ri, column=ci, value=val)
    for ci in range(1, len(hdrs1) + 1):
        ws1.column_dimensions[get_column_letter(ci)].width = 20

    # Sayfa 2 – GeoMean Spektrumları + TBDY hedef + grafik
    ws2 = wb.create_sheet("GeoMean Spektrumlari")
    spec2_cols = individual_cols + geomean_cols + ([tbdy_col] if tbdy_col else [])
    _write_spec_sheet(ws2, spec2_cols)
    _add_chart(ws2, spec2_cols, "GeoMean vs TBDY 2018")

    # Sayfa 3 – RotD50 + TBDY hedef
    ws3 = wb.create_sheet("RotD50")
    spec3_cols = rotd50_cols + ([tbdy_col] if tbdy_col else [])
    _write_spec_sheet(ws3, spec3_cols)
    _add_chart(ws3, spec3_cols, "RotD50 vs TBDY 2018")

    # Sayfa 4 – RotD100 + TBDY hedef
    ws4 = wb.create_sheet("RotD100")
    spec4_cols = rotd100_cols + ([tbdy_col] if tbdy_col else [])
    _write_spec_sheet(ws4, spec4_cols)
    _add_chart(ws4, spec4_cols, "RotD100 vs TBDY 2018")

    date_str   = datetime.now().strftime('%Y%m%d_%H%M')
    excel_path = os.path.join(scaled_folder, f"spektrum_raporu_{date_str}.xlsx")
    wb.save(excel_path)
    if log_fn: log_fn(f"Excel raporu kaydedildi: {excel_path}")


# ─────────────────────────────────────────────────────────────
# TKINTER ARAYÜZÜ
# ─────────────────────────────────────────────────────────────

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Deprem Kayıtları Dönüştürücü  │  mm/s²")
        self.resizable(True, True)
        self.configure(bg='#1a1a2e')
        self.geometry("860x640")
        self._file_paths = []   # file_list görsel indeksiyle eşleşen gerçek yollar
        self._build()

    # ── UI ────────────────────────────────────────────────────
    def _build(self):
        PAD = dict(padx=10, pady=6)
        BG = '#1a1a2e'
        FG = '#e0e0ff'
        ACCENT = '#4ecca3'
        BTN = dict(bg='#0f3460', fg=FG, activebackground=ACCENT,
                   activeforeground='#0d0d0d', relief='flat', cursor='hand2',
                   font=('Courier New', 10, 'bold'), padx=10, pady=4)
        FRAME = dict(bg=BG)

        # ── Üst bölüm ──────────────────────────────────────
        top = tk.Frame(self, **FRAME)
        top.pack(fill='x', **PAD)

        tk.Label(top, text="DEPREM KAYITLARI DÖNÜŞTÜRÜCÜ",
                 bg=BG, fg=ACCENT, font=('Courier New', 14, 'bold')).pack(side='left')

        # Log alanını ÖNCE oluştur (dosya eklerken uyarı yazılabilir)
        lsec = tk.LabelFrame(self, text=" İşlem Günlüğü ", bg=BG, fg=ACCENT,
                             font=('Courier New', 9), labelanchor='nw')

        self.status_var = tk.StringVar(value="Hazir.")
        status_lbl = tk.Label(self, textvariable=self.status_var, bg=BG, fg='#888',
                              font=('Courier New', 9))

        # ── Dosya seçimi ───────────────────────────────────
        fsec = tk.LabelFrame(self, text=" Kaynak Dosyalar ", bg=BG, fg=ACCENT,
                             font=('Courier New', 9), labelanchor='nw')
        fsec.pack(fill='both', expand=True, **PAD)

        btn_row = tk.Frame(fsec, **FRAME)
        btn_row.pack(fill='x', padx=6, pady=(6, 2))
        tk.Button(btn_row, text="+ Dosya Ekle", command=self._add_files, **BTN).pack(side='left', padx=(0, 6))
        tk.Button(btn_row, text="+ Klasör Ekle", command=self._add_folder, **BTN).pack(side='left', padx=(0, 6))
        tk.Button(btn_row, text="Listeyi Temizle", command=self._clear_list,
                  bg='#3d0f0f', fg=FG, activebackground='#e94560',
                  activeforeground='#fff', relief='flat', cursor='hand2',
                  font=('Courier New', 10, 'bold'), padx=10, pady=4).pack(side='left')

        list_frame = tk.Frame(fsec, **FRAME)
        list_frame.pack(fill='both', expand=True, padx=6, pady=4)

        sb = tk.Scrollbar(list_frame)
        sb.pack(side='right', fill='y')
        self.file_list = tk.Listbox(list_frame, yscrollcommand=sb.set,
                                    bg='#0a0a1a', fg=FG, selectbackground=ACCENT,
                                    selectforeground='#0d0d0d', font=('Courier New', 9),
                                    activestyle='none', height=8)
        self.file_list.pack(fill='both', expand=True)
        sb.config(command=self.file_list.yview)

        # ── Çıktı klasörü ──────────────────────────────────
        osec = tk.LabelFrame(self, text=" Çıktı Klasörü ", bg=BG, fg=ACCENT,
                             font=('Courier New', 9), labelanchor='nw')
        osec.pack(fill='x', **PAD)

        orow = tk.Frame(osec, **FRAME)
        orow.pack(fill='x', padx=6, pady=6)

        self.out_var = tk.StringVar(value=self._default_out())
        tk.Entry(orow, textvariable=self.out_var, bg='#0a0a1a', fg=FG,
                 insertbackground=FG, font=('Courier New', 9), relief='flat').pack(
                 side='left', fill='x', expand=True, ipady=4)
        tk.Button(orow, text="Gözat", command=self._browse_out, **BTN).pack(side='left', padx=(6, 0))

        # ── Buton satırı: Dönüştür + Spektrum ─────────────
        btn_action = tk.Frame(self, bg=BG)
        btn_action.pack(fill='x', padx=10, pady=(0, 6))

        tk.Button(btn_action, text="▶  DÖNÜŞTÜR", command=self._run,
                  bg=ACCENT, fg='#0d0d0d', activebackground='#38b892',
                  activeforeground='#0d0d0d', relief='flat', cursor='hand2',
                  font=('Courier New', 12, 'bold'), pady=8).pack(side='left', fill='x', expand=True, padx=(0, 6))

        tk.Button(btn_action, text="◈  SPEKTRUM HESAPLA", command=self._run_spectra,
                  bg='#2d1b69', fg='#c77dff', activebackground='#c77dff',
                  activeforeground='#0d0d0d', relief='flat', cursor='hand2',
                  font=('Courier New', 12, 'bold'), pady=8).pack(side='left', fill='x', expand=True)

        # ── Sönüm oranı seçici ──────────────────────────
        xi_row = tk.Frame(self, bg=BG)
        xi_row.pack(fill='x', padx=10, pady=(0, 4))
        tk.Label(xi_row, text="Sönüm oranı (ξ):", bg=BG, fg='#888',
                 font=('Courier New', 9)).pack(side='left')
        self.xi_var = tk.StringVar(value='0.05')
        xi_opts = ['0.02', '0.05', '0.10', '0.20']
        xi_menu = ttk.Combobox(xi_row, textvariable=self.xi_var, values=xi_opts,
                               width=6, font=('Courier New', 9), state='readonly')
        xi_menu.pack(side='left', padx=(6, 0))
        tk.Label(xi_row, text="  (GeoMean: H1 × H2)", bg=BG, fg='#555',
                 font=('Courier New', 9)).pack(side='left', padx=(12, 0))

        # ── Ölçekleme butonu ──────────────────────────────
        tk.Button(self, text="⚖  TBDY 2018 ÖLÇEKLEMESİ",
                  command=self._open_scaling,
                  bg='#1a0a3d', fg='#e2b96f', activebackground='#e2b96f',
                  activeforeground='#0d0d0d', relief='flat', cursor='hand2',
                  font=('Courier New', 11, 'bold'), pady=6).pack(
                  fill='x', padx=10, pady=(0, 6))

        # ── Log (daha önce oluşturuldu, şimdi yerleştirildi) ──
        lsec.pack(fill='both', expand=True, **PAD)
        self.log = scrolledtext.ScrolledText(lsec, bg='#040410', fg='#aaffcc',
                                             font=('Courier New', 9), height=7,
                                             state='disabled', relief='flat')
        self.log.tag_config('warn', foreground='#ffaa33')
        self.log.pack(fill='both', expand=True, padx=6, pady=6)

        status_lbl.pack(anchor='w', padx=12, pady=(0, 6))

    # ── Yardımcılar ───────────────────────────────────────────
    def _default_out(self):
        base = r"G:\Drive'ım\claudecodeworks\DEPREMKAYITLARI"
        date_str = datetime.now().strftime('%Y%m%d')
        return os.path.join(base, f"normalize edilmiş-{date_str}")

    def _log(self, msg):
        if not hasattr(self, 'log'):
            return  # log widget henüz oluşturulmadı
        self.log.configure(state='normal')
        tag = 'warn' if msg.startswith('[UYARI]') or msg.startswith('[ATLA]') or msg.startswith('[HATA]') else None
        if tag:
            self.log.insert('end', msg + '\n', tag)
        else:
            self.log.insert('end', msg + '\n')
        self.log.see('end')
        self.log.configure(state='disabled')
        self.update_idletasks()

    # Dosya listesindeki her satır için format etiketini renklendir
    FORMAT_COLORS = {
        'AFAD':     '#ffe066',   # sarı
        'KNET':     '#80d8ff',   # mavi
        'PEER':     '#4ecca3',   # yeşil
        'NZEALAND': '#c77dff',   # mor
        None:       '#e94560',   # kırmızı — bilinmiyor/hata
    }
    FORMAT_LABELS = {
        'AFAD':     'AFAD',
        'KNET':     'KNET',
        'PEER':     'PEER',
        'NZEALAND': 'NZ  ',
        None:       'HATA',
    }

    def _add_to_list(self, filepath):
        """Dosyayı listeye ekle; format rengini uygula ve uyarı varsa göster."""
        existing = self.file_list.get(0, 'end')
        if filepath in existing:
            return False

        fmt, warning = detect_format(filepath)
        label = self.FORMAT_LABELS.get(fmt, '????')
        color = self.FORMAT_COLORS.get(fmt, '#e94560')
        display = f"[{label}]  {os.path.basename(filepath)}"

        idx = self.file_list.size()
        self.file_list.insert('end', display)
        self.file_list.itemconfig(idx, fg=color)

        # Tam yolu sakla (display metninden değil gerçek yoldan işlem yapacağız)
        self._file_paths.append(filepath)

        if warning:
            # Uyarıyı log alanına yaz (messagebox yerine — akışı kesmez)
            self._log(f"[UYARI] {os.path.basename(filepath)}:\n         {warning.split(chr(10))[0]}")

        return True

    def _add_files(self):
        files = filedialog.askopenfilenames(
            title="Deprem kayıt dosyalarını seç",
            filetypes=[
                ("Tüm desteklenen", "*.asc *.EW *.NS *.UD *.AT2 *.V2A"),
                ("AFAD/TADAS", "*.asc"),
                ("K-NET", "*.EW *.NS *.UD"),
                ("PEER NGA", "*.AT2"),
                ("New Zealand", "*.V2A"),
                ("Tüm dosyalar", "*.*"),
            ]
        )
        added = sum(1 for f in files if self._add_to_list(f))
        self.status_var.set(f"{self.file_list.size()} dosya listede.  ({added} yeni eklendi)")

    def _add_folder(self):
        folder = filedialog.askdirectory(title="Klasör seç (tüm uyumlu dosyalar eklenir)")
        if not folder:
            return
        # .txt dahil edilmiyor — belirsiz format; yalnızca kesin uzantılar
        EXTS = {'.asc', '.ew', '.ns', '.ud', '.at2', '.v2a'}
        added = 0
        for root, _, files in os.walk(folder):
            for fname in files:
                if os.path.splitext(fname)[1].lower() in EXTS:
                    if self._add_to_list(os.path.join(root, fname)):
                        added += 1
        self.status_var.set(f"{added} dosya eklendi. Toplam: {self.file_list.size()}")

    def _clear_list(self):
        self.file_list.delete(0, 'end')
        self._file_paths.clear()
        self.status_var.set("Liste temizlendi.")

    def _browse_out(self):
        folder = filedialog.askdirectory(title="Çıktı klasörü seç")
        if folder:
            self.out_var.set(folder)

    def _open_scaling(self):
        ScalingWindow(self, default_norm=self.out_var.get())

    def _run(self):
        files = list(self._file_paths)
        if not files:
            messagebox.showwarning("Uyarı", "Lütfen en az bir dosya ekleyin.")
            return

        out_folder = self.out_var.get().strip()
        if not out_folder:
            messagebox.showwarning("Uyarı", "Çıktı klasörü belirtilmedi.")
            return

        self.log.configure(state='normal')
        self.log.delete('1.0', 'end')
        self.log.configure(state='disabled')

        self._log(f"Başlıyor: {len(files)} dosya → {out_folder}\n")
        results = convert_files(files, out_folder, log_fn=self._log)

        ok = sum(1 for _, s, _ in results if s)
        fail = len(results) - ok
        self._log(f"\n{'─'*60}")
        self._log(f"Tamamlandı: {ok} başarılı, {fail} hatalı.")
        self._log(f"Çıktı klasörü: {out_folder}")
        self.status_var.set(f"✔ {ok} dosya dönüştürüldü  •  {fail} hata")

        if ok > 0:
            ans = messagebox.askyesnocancel(
                "Tamamlandı",
                f"{ok} dosya dönüştürüldü.\n\nSpektrum hesaplamak ister misiniz?\n"
                "(Evet = Spektrum hesapla ve klasörü aç  |  Hayır = Sadece klasörü aç  |  İptal = Kapat)"
            )
            if ans is True:
                self._run_spectra(norm_folder=out_folder)
            elif ans is False:
                os.startfile(out_folder)

    def _run_spectra(self, norm_folder=None):
        """Normalize edilmiş klasörden spektrum hesapla."""
        if norm_folder is None:
            # Kullanıcıdan klasör seç
            norm_folder = filedialog.askdirectory(
                title="Normalize edilmiş .txt dosyalarının bulunduğu klasörü seçin",
                initialdir=os.path.dirname(self.out_var.get())
            )
            if not norm_folder:
                return

        # Spektrum çıktı klasörü
        date_str = datetime.now().strftime('%Y%m%d')
        base_dir = os.path.dirname(norm_folder)
        spec_folder = os.path.join(base_dir, f"spektrumlar-{date_str}")

        # Sönüm oranı
        try:
            xi = float(self.xi_var.get())
        except:
            xi = 0.05

        self.log.configure(state='normal')
        self.log.delete('1.0', 'end')
        self.log.configure(state='disabled')

        self._log(f"Spektrum hesaplanıyor...\n"
                  f"Kaynak : {norm_folder}\n"
                  f"Çıktı  : {spec_folder}\n"
                  f"Sönüm  : xi = {xi:.2f}  |  {len(STANDARD_PERIODS)} periyot\n"
                  f"{'─'*60}")

        self.status_var.set("Spektrum hesaplanıyor...")
        self.update_idletasks()

        try:
            compute_spectra_for_folder(norm_folder, spec_folder, xi=xi, log_fn=self._log)
            self.status_var.set(f"Spektrumlar hazır: {spec_folder}")
            if messagebox.askyesno("Spektrum Tamamlandı",
                                   f"Spektrumlar hesaplandı.\nKlasörü açmak ister misiniz?\n{spec_folder}"):
                os.startfile(spec_folder)
        except Exception as e:
            self._log(f"[HATA] Spektrum hesabı başarısız: {e}")
            self.status_var.set("Spektrum hatası!")


# ─────────────────────────────────────────────────────────────
# ÖLÇEKLEME PENCERESİ
# ─────────────────────────────────────────────────────────────

class ScalingWindow(tk.Toplevel):
    """TBDY 2018 hedef spektrumuna frekans alanında spektral uyuşum penceresi."""

    def __init__(self, parent, default_norm=''):
        super().__init__(parent)
        self.title("TBDY 2018 Spektral Ölçekleme")
        self.geometry("720x680")
        self.configure(bg='#12102a')
        self.resizable(True, True)
        self._build(default_norm)

    def _build(self, default_norm):
        BG = '#12102a'; FG = '#e0e0ff'; ACC = '#e2b96f'
        BTN = dict(bg='#2a1a5e', fg=FG, activebackground=ACC,
                   activeforeground='#000', relief='flat', cursor='hand2',
                   font=('Courier New', 10, 'bold'), padx=10, pady=4)
        FRAME = dict(bg=BG)
        PAD   = dict(padx=10, pady=5)

        tk.Label(self, text="TBDY 2018 — SPEKTRAL UYUŞUM ÖLÇEKLEMESİ",
                 bg=BG, fg=ACC, font=('Courier New', 13, 'bold')).pack(**PAD)

        # ── TBDY parametreleri ────────────────────────────
        psec = tk.LabelFrame(self, text=" TBDY 2018 Spektrum Parametreleri ",
                             bg=BG, fg=ACC, font=('Courier New', 9))
        psec.pack(fill='x', **PAD)

        grid = tk.Frame(psec, bg=BG)
        grid.pack(fill='x', padx=8, pady=6)

        labels  = ["Ss  (kısa periyot ivme katsayısı):",
                   "S1  (1 sn ivme katsayısı):",
                   "Sds (kısa periyot tasarım kat.):",
                   "Sd1 (1 sn tasarım kat.):"]
        defaults = ['1.0', '0.4', '0.8', '0.3']
        self._param_vars = []
        for row, (lbl, dflt) in enumerate(zip(labels, defaults)):
            tk.Label(grid, text=lbl, bg=BG, fg='#aaa',
                     font=('Courier New', 9), anchor='w', width=40).grid(
                     row=row, column=0, sticky='w', pady=2)
            v = tk.StringVar(value=dflt)
            tk.Entry(grid, textvariable=v, bg='#0a0a1a', fg=FG,
                     insertbackground=FG, font=('Courier New', 10),
                     relief='flat', width=8).grid(row=row, column=1, padx=(6,0), pady=2)
            self._param_vars.append(v)
        # [0]=Ss [1]=S1 [2]=Sds [3]=Sd1

        # Hesaplanan köşe periyotları
        self._corner_var = tk.StringVar(value="TA=?  TS=?  TL=6.0 s")
        tk.Label(psec, textvariable=self._corner_var, bg=BG, fg='#e2b96f',
                 font=('Courier New', 9)).pack(pady=(0, 4))

        for v in self._param_vars:
            v.trace_add('write', lambda *_: self._update_corners())
        self._update_corners()

        # ── Eşleştirme ayarları ───────────────────────────
        msec = tk.LabelFrame(self, text=" Eşleştirme Ayarları ",
                             bg=BG, fg=ACC, font=('Courier New', 9))
        msec.pack(fill='x', **PAD)

        mrow = tk.Frame(msec, bg=BG); mrow.pack(fill='x', padx=8, pady=6)

        def _lbl(text): return tk.Label(mrow, text=text, bg=BG, fg='#aaa',
                                        font=('Courier New', 9))
        def _ent(var, w=7):
            return tk.Entry(mrow, textvariable=var, bg='#0a0a1a', fg=FG,
                            insertbackground=FG, font=('Courier New', 10),
                            relief='flat', width=w)

        self.tmin_var  = tk.StringVar(value='0.10')
        self.tmax_var  = tk.StringVar(value='4.00')
        self.niter_var = tk.StringVar(value='3')
        self.xi2_var   = tk.StringVar(value='0.05')

        _lbl("T_min (s):").grid(row=0, column=0, sticky='w')
        _ent(self.tmin_var).grid(row=0, column=1, padx=(4,16))
        _lbl("T_max (s):").grid(row=0, column=2, sticky='w')
        _ent(self.tmax_var).grid(row=0, column=3, padx=(4,16))
        _lbl("İterasyon:").grid(row=0, column=4, sticky='w')
        _ent(self.niter_var, 4).grid(row=0, column=5, padx=(4,16))
        _lbl("ξ:").grid(row=0, column=6, sticky='w')
        _ent(self.xi2_var, 5).grid(row=0, column=7, padx=(4,0))

        # ── Klasör seçimi ─────────────────────────────────
        fsec = tk.LabelFrame(self, text=" Klasörler ", bg=BG, fg=ACC,
                             font=('Courier New', 9))
        fsec.pack(fill='x', **PAD)

        self.norm_var   = tk.StringVar(value=default_norm)
        self.scaled_var = tk.StringVar(value='')
        self._update_scaled_default()
        self.norm_var.trace_add('write', lambda *_: self._update_scaled_default())

        for row_i, (label, var, cmd) in enumerate([
            ("Normalize klasörü:", self.norm_var,   self._browse_norm),
            ("Ölçeklenmiş çıktı:", self.scaled_var, self._browse_scaled),
        ]):
            fr = tk.Frame(fsec, bg=BG); fr.pack(fill='x', padx=8, pady=3)
            tk.Label(fr, text=label, bg=BG, fg='#aaa',
                     font=('Courier New', 9), width=22, anchor='w').pack(side='left')
            tk.Entry(fr, textvariable=var, bg='#0a0a1a', fg=FG,
                     insertbackground=FG, font=('Courier New', 9),
                     relief='flat').pack(side='left', fill='x', expand=True, ipady=3)
            tk.Button(fr, text="Gözat", command=cmd, **BTN).pack(side='left', padx=(4,0))

        # ── Çalıştır butonu ───────────────────────────────
        tk.Button(self, text="▶  ÖLÇEKLEMEYİ BAŞLAT", command=self._run,
                  bg=ACC, fg='#000', activebackground='#f0c830',
                  activeforeground='#000', relief='flat', cursor='hand2',
                  font=('Courier New', 12, 'bold'), pady=8).pack(
                  fill='x', padx=10, pady=(4, 2))

        tk.Button(self, text="🏗  ETABS'A AKTAR  (Functions)",
                  command=self._run_etabs,
                  bg='#1a3a2a', fg='#4ecca3',
                  activebackground='#2aaa6a', activeforeground='#000',
                  relief='flat', cursor='hand2',
                  font=('Courier New', 11, 'bold'), pady=6).pack(
                  fill='x', padx=10, pady=(2, 4))

        # ── Log ───────────────────────────────────────────
        lsec = tk.LabelFrame(self, text=" İşlem Günlüğü ", bg=BG, fg=ACC,
                             font=('Courier New', 9))
        lsec.pack(fill='both', expand=True, **PAD)
        self.log = scrolledtext.ScrolledText(
            lsec, bg='#050510', fg='#ffe066',
            font=('Courier New', 9), state='disabled', relief='flat')
        self.log.tag_config('ok',   foreground='#4ecca3')
        self.log.tag_config('warn', foreground='#ff6b6b')
        self.log.pack(fill='both', expand=True, padx=6, pady=6)

        self.status_var = tk.StringVar(value="Hazır.")
        tk.Label(self, textvariable=self.status_var, bg=BG, fg='#888',
                 font=('Courier New', 9)).pack(anchor='w', padx=12, pady=(0,6))

    # ── Yardımcılar ───────────────────────────────────────
    def _update_corners(self):
        try:
            Sds = float(self._param_vars[2].get())
            Sd1 = float(self._param_vars[3].get())
            TA  = 0.2 * Sd1 / Sds
            TS  = Sd1 / Sds
            self._corner_var.set(
                f"TA = {TA:.3f} s    TS = {TS:.3f} s    TL = 6.000 s"
                f"  |  Dusette: TA_v={TA/3:.3f}s  TS_v={TS/3:.3f}s  TL_v=2.000s"
            )
        except:
            self._corner_var.set("Geçersiz giriş")

    def _update_scaled_default(self):
        norm = self.norm_var.get().strip()
        if norm:
            base = os.path.dirname(norm)
            date_str = datetime.now().strftime('%Y%m%d')
            self.scaled_var.set(os.path.join(base, f"scaled-{date_str}"))

    def _browse_norm(self):
        d = filedialog.askdirectory(title="Normalize edilmiş klasörü seç")
        if d: self.norm_var.set(d)

    def _browse_scaled(self):
        d = filedialog.askdirectory(title="Ölçeklenmiş çıktı klasörü seç")
        if d: self.scaled_var.set(d)

    def _log(self, msg):
        """Thread-safe log: tkinter çağrısını ana thread'e yönlendirir."""
        def _do():
            self.log.configure(state='normal')
            tag = 'ok' if msg.startswith('[OK]') or msg.startswith('->') else \
                  'warn' if 'HATA' in msg or 'UYARI' in msg else None
            if tag:
                self.log.insert('end', msg + '\n', tag)
            else:
                self.log.insert('end', msg + '\n')
            self.log.see('end')
            self.log.configure(state='disabled')
        self.after(0, _do)

    def _run(self):
        import threading
        try:
            Sds  = float(self._param_vars[2].get())
            Sd1  = float(self._param_vars[3].get())
            T_min  = float(self.tmin_var.get())
            T_max  = float(self.tmax_var.get())
            n_iter = int(self.niter_var.get())
            xi     = float(self.xi2_var.get())
        except ValueError:
            messagebox.showerror("Hata", "Lütfen tüm sayısal değerleri doğru girin.")
            return

        norm_folder   = self.norm_var.get().strip()
        scaled_folder = self.scaled_var.get().strip()

        if not norm_folder or not os.path.isdir(norm_folder):
            messagebox.showerror("Hata", "Geçerli bir normalize klasörü seçin.")
            return
        if not scaled_folder:
            messagebox.showerror("Hata", "Çıktı klasörü belirtilmedi.")
            return

        self.log.configure(state='normal')
        self.log.delete('1.0', 'end')
        self.log.configure(state='disabled')

        self._log(f"TBDY 2018 Ölçekleme Basliyor")
        self._log(f"  Sds={Sds}  Sd1={Sd1}  ξ={xi}")
        self._log(f"  Eslesme T=[{T_min},{T_max}]s   {n_iter} iterasyon")
        self._log(f"  Kaynak : {norm_folder}")
        self._log(f"  Cikti  : {scaled_folder}\n{'─'*55}")
        self.after(0, lambda: self.status_var.set("Ölçekleniyor..."))

        def _worker():
            try:
                scale_folder(norm_folder, scaled_folder, Sds, Sd1,
                             T_min=T_min, T_max=T_max, n_iter=n_iter, xi=xi,
                             log_fn=self._log)
                self.after(0, lambda: self.status_var.set(f"Tamamlandi: {scaled_folder}"))
                self.after(0, _on_done)
            except Exception as e:
                self.after(0, lambda err=e: self._log(f"[HATA] {err}"))
                self.after(0, lambda: self.status_var.set("Hata!"))
                self.after(0, lambda err=e: messagebox.showerror("Hata", str(err)))

        def _on_done():
            if messagebox.askyesno("Tamamlandı",
                                   "Ölçekleme tamamlandı.\nKlasörü açmak ister misiniz?"):
                os.startfile(scaled_folder)

        threading.Thread(target=_worker, daemon=True).start()

    # ── ETABS Aktarımı ────────────────────────────────────
    def _run_etabs(self):
        import tempfile, shutil

        # ── 1. ETABS model dosyasını seç ──────────────────────────
        model_path = filedialog.askopenfilename(
            title="ETABS Modelini Sec (.edb)",
            filetypes=[("ETABS Model", "*.edb"), ("Tum dosyalar", "*.*")]
        )
        if not model_path:
            return

        # ── 2. Aktarılacak ölçekli .txt dosyalarını seç ───────────
        init_dir = self.scaled_var.get().strip()
        if not init_dir or not os.path.isdir(init_dir):
            init_dir = os.path.dirname(model_path)
        files = filedialog.askopenfilenames(
            title="Aktarilacak Olcekli Dosyalari Sec (.txt)",
            initialdir=init_dir,
            filetypes=[("Text dosyalari", "*.txt"), ("Tum dosyalar", "*.*")]
        )
        if not files:
            return

        self.log.configure(state='normal')
        self.log.delete('1.0', 'end')
        self.log.configure(state='disabled')
        self._log(f"{'─'*55}")
        self._log(f"ETABS Fonksiyon Aktarimi")
        self._log(f"  Model    : {os.path.basename(model_path)}")
        self._log(f"  Dosyalar : {len(files)} adet")
        self._log(f"{'─'*55}")
        self.status_var.set("ETABS'a aktariliyor...")
        self.update_idletasks()

        # ── 3. comtypes + TLB ile bağlan ──────────────────────────
        try:
            import comtypes.client as cc
        except ImportError:
            messagebox.showerror("Eksik Paket", "pip install comtypes")
            return

        _TLB = (r"C:\Program Files\Computers and Structures"
                r"\ETABS 22\NativeAPI\x64\ETABSv1.tlb")
        _EXE = (r"C:\Program Files\Computers and Structures"
                r"\ETABS 22\ETABS.exe")
        try:
            cc.GetModule(_TLB)
            self._log("  TLB yuklendi.")
        except Exception as ex:
            self._log(f"  TLB uyarisi: {ex}")

        SapModel = None
        # Önce çalışan ETABS varsa bağlan
        try:
            etabs    = cc.GetActiveObject('CSI.ETABS.API.ETABSObject')
            SapModel = etabs.SapModel
            self._log("  Calisan ETABS bulundu.")
        except Exception as ex:
            self._log(f"  Calisan ETABS yok ({ex}), yeni oturum aciliyor...")

        # Yoksa yeni oturum aç (dynamic=True → IDispatch, CreateObjectAPI erişilebilir)
        if SapModel is None:
            try:
                helper   = cc.CreateObject('ETABSv1.Helper', dynamic=True)
                etabs    = helper.CreateObjectAPI(_EXE)
                SapModel = etabs.SapModel
                self._log("  Yeni ETABS oturumu acildi.")
            except Exception as ex:
                self._log(f"[HATA] ETABS baslatilamadi: {ex}")
                messagebox.showerror(
                    "ETABS Hatasi",
                    f"ETABS başlatılamadı veya bağlanılamadı.\n\n"
                    f"ETABS'ı manuel açıp modeli yükleyin,\nsonra tekrar deneyin.\n\n{ex}"
                )
                self.status_var.set("Hata!")
                return

        # ── 4. Model dosyasını aç ──────────────────────────────────
        self._log(f"  Model aciliyor...")
        try:
            ret = SapModel.File.OpenFile(model_path)
            if ret != 0:
                raise RuntimeError(f"OpenFile ret={ret}")
            self._log(f"[OK] Model acildi.")
        except Exception as ex:
            self._log(f"[HATA] Model acilamadi: {ex}")
            messagebox.showerror("Model Hatasi", str(ex))
            self.status_var.set("Hata!")
            return

        # Model kilidini aç
        try:
            if SapModel.GetModelIsLocked():
                SapModel.SetModelIsLocked(False)
                self._log("  Model kilidi acildi.")
        except Exception:
            pass

        # ── 5. Txt dosyalarını oku ─────────────────────────────────
        ok_n  = 0
        err_n = 0
        new_rows = []   # (func_name, t, a) satırları

        for fpath in files:
            fname     = os.path.basename(fpath)
            func_name = os.path.splitext(fname)[0]
            try:
                times, accels = _read_norm_txt(fpath)
                for t, a in zip(times, accels):
                    new_rows.append((func_name, t, a))
                self._log(f"  Okundu: {func_name}  ({len(times)} nokta)")
                ok_n += 1
            except Exception as ex:
                self._log(f"[HATA] {fname}: {ex}")
                err_n += 1
            self.update_idletasks()

        if ok_n == 0:
            self._log("[HATA] Hicbir dosya okunamadi.")
            self.status_var.set("Hata!")
            return

        # ── 6. DatabaseTables — CSV dosyası üzerinden yaz ─────────
        TABLE_KEY = "Functions - Time History - User Defined"
        tmp_dir   = tempfile.mkdtemp(prefix='etabs_th_')
        try:
            existing_csv = os.path.join(tmp_dir, 'existing.csv')
            new_csv      = os.path.join(tmp_dir, 'new_funcs.csv')

            # 6a. Mevcut tablo içeriğini dosyaya al
            table_ver = 0
            try:
                g = SapModel.DatabaseTables.GetTableForEditingCSVFile(
                    TABLE_KEY, '', 0, existing_csv, ','
                )
                g_ret     = g[0] if isinstance(g, (list, tuple)) else g
                table_ver = g[2] if isinstance(g, (list, tuple)) and len(g) > 2 else 0
                self._log(f"  Mevcut tablo alindi (ret={g_ret}, ver={table_ver}).")
            except Exception as ex:
                self._log(f"  Mevcut tablo alinamadi (devam): {ex}")

            # 6b. Mevcut CSV'yi oku (yoksa sadece başlık)
            try:
                with open(existing_csv, 'r', encoding='utf-8') as f:
                    ex_lines = f.read().splitlines()
            except Exception:
                ex_lines = ['Name,Time,Value,GUID']

            header    = ex_lines[0] if ex_lines else 'Name,Time,Value,GUID'
            ex_data   = ex_lines[1:] if len(ex_lines) > 1 else []

            # 6c. Yeni satırları ekle (GUID sütunu boş)
            add_lines = [f'{fn},{t:.6f},{a:.6f},' for fn, t, a in new_rows]
            merged    = '\n'.join([header] + ex_data + add_lines)

            with open(new_csv, 'w', encoding='utf-8', newline='') as f:
                f.write(merged)

            # 6d. Dosyayı tabloya yaz
            s = SapModel.DatabaseTables.SetTableForEditingCSVFile(
                TABLE_KEY, table_ver, new_csv, ','
            )
            s_ret = s[0] if isinstance(s, (list, tuple)) else s
            self._log(f"  SetTableForEditingCSVFile ret={s_ret}")

            # 6e. Uygula
            a = SapModel.DatabaseTables.ApplyEditedTables(True)
            a_ret = a[0] if isinstance(a, (list, tuple)) else a
            if a_ret == 0:
                self._log(f"[OK] ApplyEditedTables tamamlandi.")
            else:
                self._log(f"[UYARI] ApplyEditedTables ret={a_ret}  detay={a}")

        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

        # ── 7. Kaydet ─────────────────────────────────────────────
        try:
            SapModel.File.Save("")
            self._log(f"[OK] Model kaydedildi.")
        except Exception as ex:
            self._log(f"[UYARI] Kayit basarisiz: {ex}  — Manuel kaydedin.")

        self._log(f"{'─'*55}")
        self._log(f"Tamamlandi: {ok_n} OK  |  {err_n} hata")
        self.status_var.set(f"Tamamlandi: {ok_n}/{len(files)}")

        if err_n == 0:
            messagebox.showinfo(
                "Tamamlandi",
                f"{ok_n} fonksiyon ETABS'a aktarildi.\n\n"
                "Not: Ivme mm/s2 birimindedir.\n"
                "Yukleyici tanimlarinda olcek katsayisini kontrol edin."
            )
        else:
            messagebox.showwarning(
                "Kismi Tamamlandi",
                f"{ok_n} fonksiyon OK, {err_n} hatali.\n"
                "Detaylar icin log alanini inceleyin."
            )


# ─────────────────────────────────────────────────────────────
# GİRİŞ NOKTASI
# ─────────────────────────────────────────────────────────────

if __name__ == '__main__':
    app = App()
    app.mainloop()
