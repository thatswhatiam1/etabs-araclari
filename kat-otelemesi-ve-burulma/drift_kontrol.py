# -*- coding: utf-8 -*-
"""
drift_kontrol.py — TBDY 2018 4.9.1.3 etkin göreli kat ötelemesi, nokta yer değiştirmelerinden.

    δi,max = (R / I) · Δi,max
    λ · δi,max / hi  ≤  0.008·κ   (dolgu duvarlar çerçeveye rijit bağlı)
                     ≤  0.016·κ   (dolgu duvarlar esnek bağlı / bağımsız)

Kullanım
  1) ETABS'ta model açık ve analiz yapılmış olsun.
  2) Aşağıdaki KULLANICI GİRDİLERİ bölümünü doldurun.
  3) Komut satırında:  python drift_kontrol.py
  Sonuç: drift_kontrol.xlsx  (Özet, Kat detayı, Nokta detayı, Uyarılar)
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

# =========================== KULLANICI GİRDİLERİ ===========================
# Yükleme durumu VEYA kombinasyon adları (büyük/küçük harf fark etmez).
X_YONU = ["EX+e", "EX-e"]
Y_YONU = ["EY+e", "EY-e"]

R = 7.0          # Taşıyıcı sistem davranış katsayısı
I = 1.0          # Bina önem katsayısı
LAMBDA = 0.5     # λ = Sae(T1) DD-3 / Sae(T1) DD-2   (kendi spektrumunuzdan)
KAPPA = 0.5      # Betonarme: 0.5, çelik: 1.0
DOLGU = "rijit"  # "rijit" -> 0.008κ ,  "esnek" -> 0.016κ

# Yer değiştirmesi R/I ile AZALTILMAMIŞ durumlar için (ör. elastik analiz) özel çarpan:
#   CARPAN_OZEL = {"EX_ELASTIK": 1.0}
CARPAN_OZEL = {}

NOKTA_GRUBU = "All"      # Sadece belirli noktalar için ETABS'ta grup tanımlayıp adını yazın
HARIC_KATLAR = []        # Hesaplanır ama değerlendirmeye alınmaz, ör. ["B3", "B2", "B1"]

CIKTI = "drift_kontrol.xlsx"
# ==========================================================================

import pandas as pd

from etabs_ortak import durumu_hesapla, excel_yaz, model_verisini_hazirla

SINIR = (0.008 if DOLGU.lower().startswith("r") else 0.016) * KAPPA
OZEL = {k.lower(): v for k, v in CARPAN_OZEL.items()}


def main():
    model, katlar, noktalar, uyari = model_verisini_hazirla(NOKTA_GRUBU)
    haric = {k.lower() for k in HARIC_KATLAR}
    detay, nokta = [], []

    for yon, liste in (("X", X_YONU), ("Y", Y_YONU)):
        for isim in liste:
            try:
                s = durumu_hesapla(model, katlar, noktalar, NOKTA_GRUBU, isim, yon, uyari)
            except (ValueError, RuntimeError) as e:
                uyari.ekle("HATA", str(e), isim)
                print(f"  HATA  {isim}: {e}")
                continue
            if s.yanlis_yon:
                print(f"  HATA  {s.isim}: {yon} doğrultusunda öteleme yok, yanlış listeye girilmiş olabilir.")
                continue
            if s.isaretsiz:
                uyari.ekle("UYARI", "Spektral/zarf sonuç: yer değiştirmeler işaretsiz olduğundan ardışık kat "
                                    "farkı gerçek ötelemeyi tam vermez (genelde güvenli tarafta değildir). "
                                    "Mümkünse eşdeğer statik veya ETABS 'Joint Drifts' ile karşılaştırın.", s.isim)
            carpan = OZEL.get(s.isim.lower(), R / I)
            for et, kdf, ndf in s.setler:
                if kdf.empty:
                    continue
                kdf.insert(0, "Durum", s.isim); kdf.insert(1, "Yön", yon); kdf.insert(2, "Sonuç seti", et)
                kdf["Çarpan"] = carpan
                kdf["δmax (mm)"] = kdf["|Δ|max (mm)"] * carpan
                kdf["δ/h"] = kdf["δmax (mm)"] / 1000 / kdf["h (m)"]
                kdf["λ·δ/h"] = LAMBDA * kdf["δ/h"]
                kdf["Sınır"] = SINIR
                kdf["Kullanım oranı"] = kdf["λ·δ/h"] / SINIR
                ndf.insert(0, "Durum", s.isim); ndf.insert(1, "Sonuç seti", et)
                detay.append(kdf); nokta.append(ndf)
            print(f"  {yon}  {s.isim:<25} ({s.tur}, çarpan {carpan:g}) hesaplandı")

    if not detay:
        excel_yaz(CIKTI, {"Uyarılar": uyari.tablo()})
        raise SystemExit("Hiçbir durum hesaplanamadı; ayrıntılar Uyarılar sayfasında.")

    detay = pd.concat(detay, ignore_index=True)
    detay["Sonuç"] = ["KONTROL DIŞI" if k.lower() in haric else ("UYGUN" if o <= 1.0 else "SINIR AŞILDI")
                      for k, o in zip(detay["Kat"], detay["Kullanım oranı"])]

    ozet = detay.sort_values("Kullanım oranı", ascending=False).drop_duplicates(["Yön", "Kat"])
    kat_sira = {k: i for i, k in enumerate(katlar["Kat"])}
    ozet = ozet.sort_values(["Yön", "Kat"], key=lambda c: c.map(kat_sira) if c.name == "Kat" else c,
                            ascending=[True, False])
    ozet = ozet[["Yön", "Kat", "Durum", "Sonuç seti", "h (m)", "|Δ|max (mm)", "Çarpan", "δmax (mm)",
                 "δ/h", "λ·δ/h", "Sınır", "Kullanım oranı", "Sonuç", "|Δ|max noktası", "|Δ|max X",
                 "|Δ|max Y", "Plan değişimi"]]

    dosya = excel_yaz(CIKTI, {"Özet": ozet, "Kat detayı": detay,
                              "Nokta detayı": pd.concat(nokta, ignore_index=True),
                              "Uyarılar": uyari.tablo()})

    print(f"\nSınır: λ·δ/h ≤ {SINIR:.4f}   (R/I = {R / I:g}, λ = {LAMBDA:g}, κ = {KAPPA:g}, dolgu {DOLGU})")
    print(ozet[["Yön", "Kat", "Durum", "δmax (mm)", "λ·δ/h", "Kullanım oranı", "Sonuç"]]
          .to_string(index=False, float_format="%.4f"))
    print(f"\n{uyari.say('HATA')} hata, {uyari.say('UYARI')} uyarı. Rapor: {dosya}")


if __name__ == "__main__":
    main()
