# -*- coding: utf-8 -*-
"""
burulma_kontrol.py — TBDY 2018 burulma düzensizliği (A1b), nokta yer değiştirmelerinden.

    ηbi = (Δi)max / (Δi)ort        (Δi)ort = [(Δi)max + (Δi)min] / 2

Kullanım
  1) ETABS'ta model açık ve analiz yapılmış olsun.
  2) Aşağıdaki KULLANICI GİRDİLERİ bölümünü doldurun.
  3) Komut satırında:  python burulma_kontrol.py
  Sonuç: burulma_kontrol.xlsx  (Özet, Kat detayı, Nokta detayı, Uyarılar)

Not: TBDY'de η, ±%5 ek dışmerkezlikli deprem yüklemesiyle hesaplanır. Girdiğiniz
durumlar bu dışmerkezliği içermelidir (ör. EX+e, EX-e). ETABS'ta tek bir yük
deseninde birden çok dışmerkezlik tanımlıysa her adım ayrı hesaplanır ve en
elverişsizi alınır.
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

NOKTA_GRUBU = "All"      # Sadece belirli noktalar için ETABS'ta grup tanımlayıp adını yazın
HARIC_KATLAR = []        # Hesaplanır ama değerlendirmeye alınmaz, ör. ["B3", "B2", "B1"]

ETA_DUZENSIZLIK = 1.2    # η > 1.2  -> A1b burulma düzensizliği
ETA_UST_SINIR = 2.0      # η > 2.0  -> ayrıca işaretlenir (yönetmelik sınırlarını kontrol edin)

CIKTI = "burulma_kontrol.xlsx"
# ==========================================================================

import math

import pandas as pd

from etabs_ortak import durumu_hesapla, excel_yaz, model_verisini_hazirla


def sinifla(eta, kat, sifir=False):
    if kat.lower() in {k.lower() for k in HARIC_KATLAR}:
        return "KONTROL DIŞI"
    if sifir:
        return "ÖTELEME YOK"
    if eta is None or (isinstance(eta, float) and math.isnan(eta)):
        return "HESAPLANAMADI"
    if eta <= ETA_DUZENSIZLIK:
        return "YOK"
    if eta <= ETA_UST_SINIR:
        return "A1b VAR"
    return "η > 2.0"


def main():
    model, katlar, noktalar, uyari = model_verisini_hazirla(NOKTA_GRUBU)
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
                uyari.ekle("UYARI", "Spektral/zarf sonuç: yer değiştirmeler işaretsiz, farklardan bulunan "
                                    "öteleme yaklaşıktır. Burulma kontrolü ±%5 dışmerkezlikli eşdeğer "
                                    "deprem yüklemesiyle yapılmalıdır.", s.isim)
            for et, kdf, ndf in s.setler:
                if kdf.empty:
                    continue
                kdf.insert(0, "Durum", s.isim); kdf.insert(1, "Yön", yon); kdf.insert(2, "Sonuç seti", et)
                ndf.insert(0, "Durum", s.isim); ndf.insert(1, "Sonuç seti", et)
                detay.append(kdf); nokta.append(ndf)
            print(f"  {yon}  {s.isim:<25} ({s.tur}) hesaplandı")

    if not detay:
        excel_yaz(CIKTI, {"Uyarılar": uyari.tablo()})
        raise SystemExit("Hiçbir durum hesaplanamadı; ayrıntılar Uyarılar sayfasında.")

    detay = pd.concat(detay, ignore_index=True)
    detay["Sonuç"] = [sinifla(e, k, z) for e, k, z in zip(detay["η"], detay["Kat"], detay["Öteleme ~0"])]

    # Özet: her yön ve kat için en büyük η veren durum
    sirali = detay.assign(_e=detay["η"].fillna(-1)).sort_values("_e", ascending=False)
    ozet = sirali.drop_duplicates(["Yön", "Kat"]).drop(columns="_e")
    kat_sira = {k: i for i, k in enumerate(katlar["Kat"])}
    ozet = ozet.sort_values(["Yön", "Kat"], key=lambda c: c.map(kat_sira) if c.name == "Kat" else c,
                            ascending=[True, False])
    ozet = ozet[["Yön", "Kat", "Durum", "Sonuç seti", "h (m)", "Δmax (mm)", "Δmin (mm)", "Δort (mm)",
                 "η", "η (rijit kontrol)", "Sonuç", "Δmax noktası", "Δmin noktası", "Plan değişimi",
                 "Rijit sapma (%)"]]

    dosya = excel_yaz(CIKTI, {"Özet": ozet, "Kat detayı": detay,
                              "Nokta detayı": pd.concat(nokta, ignore_index=True),
                              "Uyarılar": uyari.tablo()})

    print("\nKat bazında en büyük η:")
    print(ozet[["Yön", "Kat", "Durum", "η", "Sonuç"]].to_string(index=False, float_format="%.3f"))
    print(f"\n{uyari.say('HATA')} hata, {uyari.say('UYARI')} uyarı. Rapor: {dosya}")


if __name__ == "__main__":
    main()
