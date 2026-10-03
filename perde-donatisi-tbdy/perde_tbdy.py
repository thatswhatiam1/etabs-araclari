# -*- coding: utf-8 -*-
"""
perde_tbdy.py
=============
ETABS'teki pier'lerin kuvvet ve geometrisini API ile okur, TBDY 2018 Bölüm 7.6'ya göre perde
uç (başlık) bölgelerini, boyuna ve gövde donatısını tasarlar; plan donatı çizimini, metrajı
ve açılımları üretir.

Perde tipleri
  * Dikdörtgen perde      : lif modeli ile P–M3, uçta küme yerleşimi
  * Çok kollu (U/H/L/T)   : ETABS alan elemanlarından kesit, 2B lif ağı ile P–M2–M3 (PMM),
                            serbest uç ve kol birleşimi uç bölgeleri
  * Bodrum (toprak) perdesi (--bodrum): shell M11/M22/M12 (Wood–Armer) ile metre şerit tasarımı
  * Kiriş bağlantı bölgeleri: perdeye bağlanan kirişlerin altında, genişlik = kiriş + her yanda
                            bw (≥ 300 mm), uç bölge kurallarıyla donatı + etriye
  * Kat geçişleri         : üst katın donatıları alt planında düz / kırım (≤1/6) / filiz ekimi

Çıktılar – klasör adı ETABS model dosyasının adıdır (--cikti ile değiştirilebilir)
  <MODEL ADI>/
    Perdeler/
      Doneler/            KAT_<kat>.dxf (başlık/gövde yazılı kat planı), perde_rapor.txt,
                          baslik_cakismalari.txt, SectionDesigner/ (ETABS için kesitler)
      Filiz_Cakismalari/  <PIER>_filiz.dxf (kat geçişleri: düz / krank / filiz / biten donatı ve
                          çakışmalar), filiz_rapor.txt, filiz_ozet.csv, filiz_cubuk_listesi.csv
    Bodrum_Perdeleri/
      Doneler/            KAT_<kat>.dxf, bodrum_rapor.txt
      Filiz_Cakismalari/  bodrum_filiz_rapor.txt, bodrum_filiz_ozet.csv
    hata_log.txt, etabs_yaz_log.txt

Kullanım
  python perde_tbdy.py                          -> açık ETABS'te seçili nesnelerin pier'leri
  python perde_tbdy.py --piers P1 P2            -> belirtilen pier'ler
  python perde_tbdy.py --tum                    -> modeldeki tüm pier'ler
  python perde_tbdy.py --piers P1 --bodrum BP1  -> BP1 bodrum perdesi olarak
  python perde_tbdy.py --demo                   -> ETABS olmadan örnek veriyle
  python perde_tbdy.py --tum --etabs_yaz e      -> tasarım bitince donatıyı ETABS'e Section
                                                   Designer pier kesiti olarak yazar ve pier'lere
                                                   "Reinforcement to be Checked" atar (duvar
                                                   kesitleri değişmez). Varsayılan: sonunda sorar.

Gereksinim:  pip install comtypes numpy matplotlib ezdxf
             (ETABS açık ve analiz yapılmış olmalı; comtypes sadece Windows'ta)
Birimler: içeride N, mm, MPa. ETABS'ten kN-m-C ile okunur.

ÖNEMLİ: Bu kod bir ön tasarım / detaylandırma yardımcısıdır. Sonuçlar proje mühendisince
TBDY 2018 ve TS 500 metnine göre kontrol edilmelidir (özellikle "VARSAYIM" notları).
Çok kollu kesitlerde M2/M3 işaret kabulü bir pier'de ETABS PMM ile doğrulanmalıdır
(AYAR: M2_isaret, M3_isaret).
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
import csv
import math
import os
import sys
import time
import traceback
from dataclasses import dataclass, field

import numpy as np

# =====================================================================================
# 1) AYARLAR
# =====================================================================================
AYAR = dict(
    fck=30.0,             # MPa  (C30)
    fyk=420.0,            # MPa  (B420C) boyuna
    fywk=420.0,           # MPa  enine
    gamma_c=1.5,
    gamma_s=1.15,
    paspayi=25.0,         # mm, net beton örtüsü (etriye / yatay donatı dış yüzüne)
    # KESME TASARIM KUVVETİ
    #   kesme_yontemi="1.2D"  : Ve = kesme_katsayi · D · Vd   (varsayılan: Ve = 1.2·D·Vd)
    #                           Vd: 1.2D'li kombinasyonlardan (kesme_komb_filtre) kat kesmesi.
    #                           D: dayanım fazlalığı katsayısı – KULLANICI GİRER (TBDY Tablo 4.1)
    #   kesme_yontemi="TBDY"  : Ve = βv·(Mp/Md)·Vd (TBDY 7.6.6.3, lif analizinden Mp ≈ 1.4·Mr)
    kesme_yontemi="1.2D",
    kesme_katsayi=1.2,
    # Seçilen kesme kombinasyonlarında deprem etkisi ZATEN 1.2·D ile büyütülmüş mü?
    #   True  -> Ve = Vd (kod tekrar büyütmez)      False -> Ve = 1.2·D·Vd
    #   None  -> ETABS'ten kombinasyon katsayıları okunarak önerilir ve kullanıcıya sorulur
    kesme_komb_buyutulmus=None,
    deprem_durum_anahtar=["DEP", "EQ", "EX", "EY", "EZ", "SPEC", "RS", "DEPREM", "QUAKE"],
    D=2.5,                # dayanım fazlalığı katsayısı (kullanıcı girer)
    R=6.0,
    beta_v=1.5,           # yalnız kesme_yontemi="TBDY"
    Mp_Mr_orani=1.4,      # yalnız kesme_yontemi="TBDY": Mp ≈ 1.4 Mr
    ve_ust_sinir_RD=True,
    # Kesit koşulları (TBDY 7.6.1): normalde bw ≥ max(250, h/16); 7.6.1.3 koşulları
    # sağlanıyorsa bw ≥ max(200, h/20)
    ozel_kosul_7613=False,
    # KOMBİNASYON SEÇİMİ (program başında sorulur; Enter = önerilen/son seçim)
    #   kombinasyonlar       : P–M (eğilme + eksenel) için ad listesi; None -> sorulur
    #   kesme_kombinasyonlari: kesme (Vd) için ad listesi; None -> sorulur
    #   Öneri: envelope (zarf) kombinasyonları – P–M için adında "1.2D" geçmeyen zarflar,
    #   kesme için adında "1.2D" geçen zarf (ör. ENV-DEP-1.2D).
    #   Envelope satırlarında (max/min) P, M2, M3 köşe birleşimleri talep olarak alınır.
    kombinasyonlar=None,
    kesme_kombinasyonlari=None,
    kombinasyon_sor=True,    # False: sormadan öneri/son seçim kullanılır
    kombinasyon_filtre="",   # (eski) ör. "EQ" -> adında EQ geçen kombinasyonlar
    zarf_kose=True,          # envelope max/min satırlarından köşe (P,M2,M3) birleşimleri
    # Donatı seçenekleri
    uc_caplari=[14, 16, 18, 20, 22, 25, 28, 32],
    govde_caplari=[10, 12, 14, 16],
    etriye_capi_kritik=10,
    etriye_capi_ust=8,
    s_govde_max=250.0,       # TBDY: gövde donatı aralığı ≤ 250 mm
    s_uc_bar_max=200.0,      # uç bölgede kümeden sonraki (kuyruk) donatı aralığı üst sınırı
    uc_sira_max=10,          # uç YÜZDE (perde ucunda) en çok donatı sayısı (sıra sayısı
                             # net aralık sınırlarından hesaplanır; bu yalnız üst sınır)
    uc_sira_min=3,           # kalınlık yetiyorsa uçtaki kümede en az bu kadar sıra (uca yoğunlaşma)
    uc_kume_min_aralik=75.0, # uç kümede donatı eksen aralığı alt sınırı (mm) – bindirme
                             # bölgesinde çubuklar yan yana geleceği için pratik alt sınır
    agrega_dmax=22.0,        # mm, TS 500 net aralık ≥ max(Ø, 25, 4/3·Dmax)
    uc_kume_kolon_max=4,     # kümede en çok kolon sayısı (perde boyunca)
    kuyruk_araliklari=[200.0, 150.0, 125.0, 100.0],  # küme sonrası uç bölge donatı aralığı adayları
    rho_kume_max=None,       # (kullanılmıyor) – sıkışıklık bindirmeli net aralık kuralıyla kontrol edilir
    bindirme_araligi=False,  # True: en küçük aralık bindirmede yan yana gelen çubuk dikkate
                             # alınarak (eksen ≥ Ø1 + Ø2 + net_min); False: net ≥ uc_net_min
    uc_net_min=45.0,         # mm, uç bölgede iki boyuna donatı arasındaki en küçük NET aralık
                             # (TS 500: ≥ max(Ø, 25, 4/3·Dmax) ile birlikte; bindirmede de sağlanır)
    uc_net_max=120.0,        # mm, uç bölge YÜZLERİ boyunca komşu iki boyuna donatı arasındaki en
                             # büyük NET aralık (kolon gibi)
    uc_net_max_kalinlik=250.0,  # mm, kalınlık doğrultusunda (iki yüz arası) en büyük net aralık;
                             # aşılırsa ara sıra eklenir (300'lük perdede 2 sıra yeterli)
    # ÇOK KOLLU (U/H/L/T) perdeler:
    #   "basit": minimum başlık bölgesi + minimum donatı çizilir; tüm kesitin PMM'inden her uç
    #            bölgede GEREKLİ donatı (cm²) ve kol eksenel yük / moment talepleri yazılır
    #   "optimum": PMM'e göre donatı düzeni aranır (uç bölge uzatılarak)
    cok_kollu_yontem="basit",
    # Enine donatı kolları: False -> yalnız TBDY'nin gerektirdiği kadar çiroz (a ≤ 25Ø ve
    # kritik bölgede Ash), True -> uç bölgedeki her boyuna donatı kolonuna çiroz
    ciroz_her_kolon=False,
    kademe_kat=3,            # TBDY 7.6.5.1: kritik bölge üstünde geçişin yapıldığı kat sayısı
    talep_dilim=60,          # çok sayıda kombinasyonda talepler N dilimlerinde zarflanır
    etriye_max_uzunluk=700.0,  # mm; daha uzun uç bölgelerde iç içe (bindirmeli) etriyeler
    etriye_caplari=[8, 10, 12, 14],
    yatay_uc_detay="firkete",  # serbest uçta gövde yatay donatısı: "firkete" (U, ≥1.5ℓb) / "kanca"
    firkete_bindirme=1.5,      # U-firkete bacak boyu = katsayı × ℓb (Şekil 7.11)
    cubuk_max_boy=12000.0,     # mm, piyasa çubuk boyu (aşılırsa bindirmeli ek)
    # Kat geçişleri (filiz / kırım)
    birlesim_yuksekligi=500.0,  # mm, kırımın yapıldığı döşeme/kiriş yüksekliği (eğim ≤ 1/6)
    kirim_egim_max=1 / 6,       # TS 500: birleşim bölgesinde boyuna donatının düşeye göre eğimi
    duz_tolerans=10.0,          # mm, bu kadar kaçıklık "düz devam" sayılır
    alfa1_bindirme=1.8,         # TS 500 Denk. 9.2: l0 = α1·lb (tüm donatı aynı kesitte eklenirse)
    filiz_gomulme_katsayi=1.0,  # filiz ekiminde alt perdeye gömülme = katsayı × lb
    govde_ciroz_adim=2,      # gövde çirozu: planda her n. düşey donatıda (şaşırtmalı: komşu
                             # çiroz seviyeleri yarım adım kaydırılır)
    ciroz_yogunluk_kritik=10.0,  # adet/m², kritik perde yüksekliği boyunca (özel deprem çirozu)
    ciroz_yogunluk_normal=4.0,   # adet/m², kritik perde yüksekliği dışında
    rho_uc_max=0.03,         # TBDY 7.6.5.1: uç bölge boyuna donatı oranı ≤ 0.03 (bindirmede 0.06)
    Lu_yuvarlama=50.0,       # uç bölge uzunluğu yukarı yuvarlama (mm)
    s_yuvarlama=10.0,        # etriye aralığı aşağı yuvarlama
    cikti_klasoru=None,        # None: ETABS model dosyasının adı (yoksa "perde_cikti")
    kat_plani=True,            # (ayrıntılı mod) her kat için bütün pier'lerin donatısı tek planda
    cikti_modu="sade",         # "sade": kat DXF'leri (yazılı) + Section Designer DXF + tek rapor
                               # "ayrintili": eski çıktılar (donatı çizimleri, metraj, boy kesit...)
    uc_caplari_sade=[16, 18, 20, 22, 25, 28, 32],   # başlıkta tek çap – seçilebilir çaplar
    uc_n_max=80,               # başlıkta en çok çubuk sayısı (aday üretimi)
    temel_yuksekligi=None,     # mm; verilirse en alt grupta temelde kenetlenme (lb) kontrol edilir
    # SİSTEM TÜRÜ (TBDY 7.6.6.3 / 7.6.7.1 / 7.10)
    #   perde_sistemi: "bosluksuz" -> Ve = 1.2·D·Vd, Ve ≤ 0.85·Ach·√fck
    #                  "bag_kirisli" -> Ve = 1.4·D·Vd, Ve ≤ 0.65·Ach·√fck
    #   bag_kirisli_pierler: yalnız bu pier'ler bağ kirişli sayılır (liste boşsa perde_sistemi
    #                  bütün pier'lere uygulanır)
    #   suneklik: "yuksek" (TBDY 7.6) / "sinirli" (TBDY 7.10: Ve = Vd, tasarım momenti diyagramı
    #             uygulanmaz; uç bölge kuralları güvenli tarafta 7.6 gibi bırakılır)
    perde_sistemi="bosluksuz",
    bag_kirisli_pierler=[],
    kesme_katsayi_bag=1.4,
    suneklik="yuksek",
    # TASARIM MOMENTİ DİYAGRAMI (TBDY 7.6.6.1): Hw/lw > 2 perdelerde kritik yükseklik boyunca
    # taban momenti sabit; üstünde taban–tepe momentlerini birleştiren doğruya paralel diyagram
    tasarim_momenti_diyagrami=True,
    # KRİTİK YÜKSEKLİĞİN BAŞLANGICI (TBDY 7.6.2.2)
    #   kritik_baslangic_kat: rijit bodrumlu binada ZEMİN KATIN adı (Hw ve Hcr bu katın
    #       tabanından ölçülür). None: perdenin tabanı (temel üstü).
    #   bodrumda_kritik: "tum" -> başlangıcın altındaki bütün bodrum katları kritik bölge gibi
    #       donatılır; "ilk" -> yalnız ilk bodrum katı (yönetmeliğin alt sınırı)
    #   Perdenin plandaki uzunluğu %20'den fazla küçülürse kritik yükseklik o kattan yeniden başlar.
    kritik_baslangic_kat=None,
    bodrumda_kritik="tum",
    kesit_kuculme_orani=0.20,
    # KESME SÜRTÜNMESİ (TBDY 7.6.7.2, TS 500): temel bağlantısı ve kat derzleri
    #   Vr = [fctd·Ac (pürüzlendirilmiş yüzeyde)] + μ·As·fyd  ≤ min(0.2·fck, 3.3 + 0.08·fck)·Ac
    #   μ: 1.4 monolitik, 1.0 pürüzlendirilmiş, 0.6 pürüzlendirilmemiş (TS 500)
    derz_mu=1.0,
    derz_puruzlu=True,
    derz_eksenel=False,       # True: en küçük basınç kuvveti sürtünmeye eklenir (μ·N)
    # MALZEME: True -> beton sınıfı kat kat ETABS'teki pier malzemesinden okunur (fck);
    #          False (ya da --fck verilirse) -> AYAR["fck"] bütün perdelerde
    malzeme_etabs=True,
    # ETABS'E YAZMA (tasarım bitince): seçilen donatı her pier/grup için General Pier Section
    # (Section Designer) olarak tanımlanır ve pier'lere "Reinforcement to be Checked" atanır.
    #   "sor": program sonunda sorar   True: sormadan yazar   False: yazmaz
    # Duvar kesitleri / alan atamaları / pier etiketleri DEĞİŞMEZ. Kesit adları sd_onek ile başlar.
    etabs_yaz="sor",
    sd_onek="PT_",
    # Lif (fiber) modeli
    lif_boyutu=25.0,         # mm, beton ağ (mesh) boyutu
    eps_c0=0.002,
    eps_cu=0.003,
    pm_nokta=400,            # P–M eğrisi için tarafsız eksen tarama sayısı
    # Kesme tevzisi kombinasyonları: adında bu ifade geçenler; bulunamazsa ölü yük
    # durumunun katsayısı 1.2 olan kombinasyonlar (ETABS kombinasyon tanımından) seçilir
    kesme_komb_filtre="1.2D",
    olu_yuk_durumlari=["Dead", "DEAD", "D", "G", "SDead", "SD", "SUPERDEAD"],
    kesme_olu_katsayi=1.2,
    # Çok kollu perdeler (P–M2–M3): moment işaret kabulü. ETABS PMM ile bir kez
    # karşılaştırıp gerekiyorsa -1 yapın.
    M3_isaret=1,
    M2_isaret=1,
    # Kiriş bağlantı bölgeleri: genişlik = kiriş genişliği + her yanda bw (en az 300 mm);
    # boyuna donatı ve etriye kuralları uç bölgeyle aynı
    kiris_bolgesi=True,
    kiris_genislik_varsayilan=300.0,   # kiriş kesiti okunamazsa (mm)
)

ES = 200000.0
EPS_CU = 0.003


def sayi(v):
    return f"{v:.0f}" if abs(v) >= 100 else f"{v:.3g}"


def alan(d):
    return math.pi * d * d / 4.0


def malzeme(a=AYAR):
    fck = a["fck"]
    fcd = fck / a["gamma_c"]
    fyd = a["fyk"] / a["gamma_s"]
    fywd = a["fywk"] / a["gamma_s"]
    fctd = 0.35 * math.sqrt(fck) / a["gamma_c"]
    k1 = 0.85 if fck <= 25 else max(0.70, 0.85 - 0.006 * (fck - 25))
    return dict(fck=fck, fcd=fcd, fyd=fyd, fywd=fywd, fctd=fctd, k1=k1)


# =====================================================================================
# 2) VERİ YAPILARI
# =====================================================================================
@dataclass
class KatVerisi:
    kat: str
    z_alt: float      # mm
    z_ust: float      # mm
    lw: float         # mm
    bw: float         # mm
    # (kombinasyon, konum, N[kN, basınç +], M3[kNm], V2[kN], M2[kNm], V3[kN])
    kuvvetler: list = field(default_factory=list)
    cg_alt: tuple = (0.0, 0.0)   # mm, global X-Y (kat altında pier ağırlık merkezi)
    cg_ust: tuple = (0.0, 0.0)   # mm, global X-Y (kat üstünde)
    aci: float = 0.0             # derece, global X'ten pier yerel 2 eksenine (perde boyu)
    kollar: list = None          # çok kollu kesit: [((u1,v1),(u2,v2),t)] yerel, mm
    orijin: tuple = (0.0, 0.0)   # çok kollu kesit yerel orijininin global X-Y'si (mm)
    _segs: list = None           # ETABS alan elemanı izleri (global), okuma sırasında
    kademe: float = 0.0          # kritik bölge üstü kademeli geçiş katsayısı (1: kritik, 0: normal)
    kiris: list = None           # bağlanan kirişler: [dict(u, v, b, ad)] pier yerel ekseninde
    bodrum: dict = None          # bodrum perdesi shell kuvvet zarfı (kNm/m, kN/m) + t, L
    malzeme_adi: str = None      # ETABS'teki pier beton malzemesi
    fck: float = None            # MPa – ETABS malzemesinden (yoksa AYAR["fck"])
    kritik: bool = False
    tasarim: list = None         # tasarım momenti diyagramı uygulanmış kuvvet satırları
    # kuvvet satırı: (komb, konum, N, M3, V2, M2, V3, T, kimlik[, M3_ham, M2_ham])


@dataclass
class Pier:
    ad: str
    katlar: list      # alttan üste KatVerisi
    kesme_kombs: list = field(default_factory=list)   # 1.2D'li kombinasyonlar
    bodrum: bool = False                               # bodrum (toprak) perdesi
    pm_kombs: list = field(default_factory=list)       # P–M talebi kombinasyonları (boş: hepsi)

    @property
    def tip(self):
        return "cok" if any(k.kollar and len(k.kollar) > 1 for k in self.katlar) else "dikdortgen"


# =====================================================================================
# 3) ETABS'TEN OKUMA
# =====================================================================================
def etabs_baglan():
    import comtypes.client
    try:
        helper = comtypes.client.CreateObject("ETABSv1.Helper")
        helper = helper.QueryInterface(comtypes.gen.ETABSv1.cHelper)
        etabs = helper.GetObject("CSI.ETABS.API.ETABSObject")
    except Exception:
        etabs = comtypes.client.GetActiveObject("CSI.ETABS.API.ETABSObject")
    if etabs is None:
        raise RuntimeError("Açık bir ETABS oturumu bulunamadı.")
    return etabs.SapModel


def api(fn, *args):
    """ETABS API çağrısı. Sürümler arasında çıktı dizisi sayısı farklı olabildiği için
    'takes exactly N arguments (M given)' hatasında sondaki boş dizi argümanlarını
    kısaltıp/uzatarak yeniden dener."""
    import re
    args = list(args)
    for _ in range(4):
        try:
            return fn(*args)
        except TypeError as e:
            mt = re.search(r"exactly (\d+) arguments? \((\d+) given\)", str(e))
            if not mt:
                raise
            fark = int(mt.group(1)) - int(mt.group(2))
            if fark < 0 and all(isinstance(a, list) for a in args[fark:]):
                args = args[:fark]
            elif fark > 0:
                args = args + [[] for _ in range(fark)]
            else:
                raise
    return fn(*args)


def _duvar_kalinligi(sm, prop, t_cache):
    if prop not in t_cache:
        try:
            w = api(sm.PropArea.GetWall, prop, 0, 0, "", 0.0, 0, "", "")
            t_cache[prop] = float(w[3]) * 1000.0
        except Exception:
            t_cache[prop] = None
    return t_cache[prop]


def etabs_pier_geometri_hizli(sm, pierler, kat_ust):
    """AreaObj.GetAllAreas ile tek çağrıda bütün alan elemanlarının köşe koordinatları;
    yalnız düşey panellerde pier etiketi sorulur. kat_ust: [(kat adı, üst kot [m, mutlak])]"""
    r = api(sm.AreaObj.GetAllAreas, 0, [], [], 0, [], [], [], [], [])
    n, adlar, yon, npt, delim = r[0], list(r[1]), list(r[2]), r[3], list(r[4])
    X, Y, Z = list(r[6]), list(r[7]), list(r[8])
    if n == 0 or len(adlar) != n or len(delim) != n:
        raise RuntimeError("GetAllAreas beklenmeyen sonuç")
    # PointDelimiter: her alanın son noktasının indisi (dahil) – değilse (hariç) kabul et
    dahil = (delim[-1] == npt - 1)
    out, t_cache, adlar_k = {}, {}, {}
    pset = set(pierler)
    bas = 0
    for i, ad in enumerate(adlar):
        son = delim[i] + (1 if dahil else 0)
        idx = range(bas, son)
        bas = son
        try:
            if int(yon[i]) != 1:                  # 1 = Wall
                continue
        except Exception:
            pass
        xy = []
        for j in idx:
            q = (round(X[j] * 1000.0), round(Y[j] * 1000.0))
            if q not in xy:
                xy.append(q)
        if len(xy) != 2:
            continue
        try:
            p = api(sm.AreaObj.GetPier, ad, "")[0]
        except Exception:
            continue
        if p not in pset:
            continue
        z_ust = max(Z[j] for j in idx)
        kat = min(kat_ust, key=lambda kz: abs(kz[1] - z_ust))[0]
        try:
            prop = api(sm.AreaObj.GetProperty, ad, "")[0]
        except Exception:
            continue
        t = _duvar_kalinligi(sm, prop, t_cache)
        if not t:
            continue
        out.setdefault((p, kat), []).append((xy[0], xy[1], t))
        adlar_k.setdefault((p, kat), []).append(ad)
    return out, adlar_k


def etabs_pier_geometri(sm, pierler, kat_ust=None):
    """Pier'lere ait düşey alan elemanlarının plan izleri:
    {(pier, kat): [((X1, Y1), (X2, Y2), t)]}  (global, mm)"""
    t0 = time.time()
    if kat_ust:
        try:
            r = etabs_pier_geometri_hizli(sm, pierler, kat_ust)
            print(f"  Pier geometrisi okundu (toplu, {time.time() - t0:.1f} s)", flush=True)
            return r
        except Exception as e:
            print(f"  Toplu alan okuma başarısız ({e}); eleman eleman okunuyor – büyük modelde "
                  f"uzun sürebilir...", flush=True)
    out, t_cache, adlar_k = {}, {}, {}
    pset = set(pierler)
    try:
        adlar = api(sm.AreaObj.GetNameList, 0, [])[1]
    except Exception as e:
        print("Uyarı: alan elemanları okunamadı:", e)
        return out, adlar_k
    for ad in adlar:
        try:
            p = api(sm.AreaObj.GetPier, ad, "")[0]
            if p not in pset:
                continue
            kat = api(sm.AreaObj.GetLabelFromName, ad, "", "")[1]
            pts = api(sm.AreaObj.GetPoints, ad, 0, [])[1]
            xy = []
            for pn in pts:
                x, y, z = api(sm.PointObj.GetCoordCartesian, pn, 0.0, 0.0, 0.0)[:3]
                q = (round(x * 1000.0), round(y * 1000.0))
                if q not in xy:
                    xy.append(q)
            if len(xy) != 2:
                continue                      # düşey perde paneli değil
            prop = api(sm.AreaObj.GetProperty, ad, "")[0]
            t = _duvar_kalinligi(sm, prop, t_cache)
            if not t:
                continue
            out.setdefault((p, kat), []).append((xy[0], xy[1], t))
            adlar_k.setdefault((p, kat), []).append(ad)
        except Exception:
            continue
    return out, adlar_k


def _shell_zarf(r, kombs, nesneler=None):
    """AreaForceShell sonucundan Wood–Armer zarfı; nesneler verilirse yalnız o alan nesneleri."""
    z = dict(M11p=0.0, M11n=0.0, M22p=0.0, M22n=0.0, F11t=0.0, F22t=0.0, V13=0.0, V23=0.0)
    n = r[0]
    for i in range(n):
        if r[4][i] not in kombs:
            continue
        if nesneler is not None and r[1][i] not in nesneler:
            continue
        F11, F22 = r[7][i], r[8][i]
        M11, M22, M12 = r[14][i], r[15][i], r[16][i]
        z["M11p"] = max(z["M11p"], M11 + abs(M12))
        z["M11n"] = max(z["M11n"], -(M11 - abs(M12)))
        z["M22p"] = max(z["M22p"], M22 + abs(M12))
        z["M22n"] = max(z["M22n"], -(M22 - abs(M12)))
        z["F11t"] = max(z["F11t"], F11)
        z["F22t"] = max(z["F22t"], F22)
        z["V13"] = max(z["V13"], abs(r[20][i]))
        z["V23"] = max(z["V23"], abs(r[21][i]))
    return z


def etabs_bodrum_toplu(sm, gruplar, kombs):
    """Bodrum perdelerinin shell kuvvetleri TEK çağrıda: ilgili alan nesneleri seçilir ve
    AreaForceShell 'SelectionElm' ile okunur (her alan için ayrı çağrı çok yavaştır).
    gruplar: {(pier, kat): [alan adları]} -> {(pier, kat): zarf}. Kullanıcının seçimi geri
    yüklenir. Başarısız olursa None döner (alan alan okumaya düşülür)."""
    t0 = time.time()
    tum = sorted({a for v in gruplar.values() for a in v})
    if not tum:
        return {}
    eski = None
    try:
        eski = api(sm.SelectObj.GetSelected, 0, [], [])
    except Exception:
        pass
    try:
        sm.SelectObj.ClearSelection()
        for a in tum:
            sm.AreaObj.SetSelected(a, True, 0)
        r = api(sm.Results.AreaForceShell, "", 3, 0, [], [], [], [], [], [], [], [], [], [],
                [], [], [], [], [], [], [], [], [], [], [], [])
        if not r or r[0] <= 0:
            return None
        out = {k: _shell_zarf(r, kombs, set(v)) for k, v in gruplar.items()}
        print(f"  Bodrum shell kuvvetleri okundu (toplu, {len(tum)} alan, {r[0]} satır, "
              f"{time.time() - t0:.1f} s)", flush=True)
        return out
    except Exception as e:
        print(f"  Uyarı: toplu shell okuması yapılamadı ({e}); alan alan okunuyor...", flush=True)
        return None
    finally:
        try:
            sm.SelectObj.ClearSelection()
            if eski and eski[0]:
                for tip, ad in zip(eski[1], eski[2]):
                    f = {1: sm.PointObj, 2: sm.FrameObj, 5: sm.AreaObj}.get(int(tip))
                    if f is not None:
                        f.SetSelected(ad, True, 0)
        except Exception:
            pass


def etabs_bodrum_kuvvet(sm, alan_adlari, kombs):
    """Alan elemanlarının shell kuvvetlerinden kat zarfı (Wood–Armer) – alan alan okuma."""
    z = dict(M11p=0.0, M11n=0.0, M22p=0.0, M22n=0.0, F11t=0.0, F22t=0.0, V13=0.0, V23=0.0)
    for ad in alan_adlari:
        try:
            r = api(sm.Results.AreaForceShell, ad, 0, 0, [], [], [], [], [], [], [], [], [], [],
                    [], [], [], [], [], [], [], [], [], [], [], [])
        except Exception as e:
            print(f"Uyarı: {ad} shell kuvvetleri okunamadı: {e}")
            continue
        n = r[0]
        for i in range(n):
            if r[4][i] not in kombs:
                continue
            F11, F22 = r[7][i], r[8][i]
            M11, M22, M12 = r[14][i], r[15][i], r[16][i]
            z["M11p"] = max(z["M11p"], M11 + abs(M12))
            z["M11n"] = max(z["M11n"], -(M11 - abs(M12)))
            z["M22p"] = max(z["M22p"], M22 + abs(M12))
            z["M22n"] = max(z["M22n"], -(M22 - abs(M12)))
            z["F11t"] = max(z["F11t"], F11)
            z["F22t"] = max(z["F22t"], F22)
            z["V13"] = max(z["V13"], abs(r[20][i]))
            z["V23"] = max(z["V23"], abs(r[21][i]))
    return z


def _kiris_genisligi(sm, prop, w_cache):
    if prop not in w_cache:
        try:
            rr = api(sm.PropFrame.GetRectangle, prop, "", "", 0.0, 0.0, 0, "", "")
            w_cache[prop] = float(rr[3]) * 1000.0
        except Exception:
            w_cache[prop] = AYAR["kiris_genislik_varsayilan"]
    return w_cache[prop]


def etabs_kirisler(sm, taban, kutu=None):
    """Yatay çubuk elemanların uç noktaları: [dict(X, Y, z, b, ad)] (mm, z tabandan).
    kutu: (Xmin, Xmax, Ymin, Ymax) mm – yalnız pier'lerin çevresindeki uçlar alınır."""
    out, w_cache = [], {}
    t0 = time.time()

    def kutuda(x, y):
        return kutu is None or (kutu[0] <= x <= kutu[1] and kutu[2] <= y <= kutu[3])
    try:
        r = sm.FrameObj.GetAllFrames(0, [], [], [], [], [], [], [], [], [], [], [], [], [], [],
                                     [], [], [], [], [], "Global")
        n, adlar, props = r[0], r[1], r[2]
        x1, y1, z1, x2, y2, z2 = r[6], r[7], r[8], r[9], r[10], r[11]
        if n and len(adlar) == n and len(z2) == n:
            for i in range(n):
                if abs(z1[i] - z2[i]) > 0.02:
                    continue
                for X, Y, Z in ((x1[i], y1[i], z1[i]), (x2[i], y2[i], z2[i])):
                    X, Y = X * 1000.0, Y * 1000.0
                    if kutuda(X, Y):
                        out.append(dict(X=X, Y=Y, z=(Z - taban) * 1000.0,
                                        b=_kiris_genisligi(sm, props[i], w_cache), ad=adlar[i]))
            print(f"  Kirişler okundu (toplu, {len(out)} kiriş ucu, {time.time() - t0:.1f} s)",
                  flush=True)
            return out
    except Exception as e:
        print(f"  Toplu çubuk okuma başarısız ({e}); eleman eleman okunuyor...", flush=True)
    try:
        adlar = api(sm.FrameObj.GetNameList, 0, [])[1]
    except Exception as e:
        print("Uyarı: çubuk elemanlar okunamadı:", e)
        return out
    for ad in adlar:
        try:
            r = api(sm.FrameObj.GetPoints, ad, "", "")
            c1 = api(sm.PointObj.GetCoordCartesian, r[0], 0.0, 0.0, 0.0)[:3]
            c2 = api(sm.PointObj.GetCoordCartesian, r[1], 0.0, 0.0, 0.0)[:3]
            if abs(c1[2] - c2[2]) > 0.02:
                continue                           # yatay değil (kolon, çapraz)
            prop = api(sm.FrameObj.GetSection, ad, "", "")[0]
            for c in (c1, c2):
                if kutuda(c[0] * 1000.0, c[1] * 1000.0):
                    out.append(dict(X=c[0] * 1000.0, Y=c[1] * 1000.0, z=(c[2] - taban) * 1000.0,
                                    b=_kiris_genisligi(sm, prop, w_cache), ad=ad))
        except Exception:
            continue
    return out


def _kirisleri_ata(kv, kirisler, cok):
    """Kat üst kotuna bağlanan ve perde izine düşen kiriş uçlarını pier yerel ekseninde
    kv.kiris'e yazar."""
    a = math.radians(kv.aci)
    X0, Y0 = kv.orijin if cok else kv.cg_ust
    kollar = kollari_kur(kv.kollar) if cok else None
    sec = []
    for q in kirisler:
        if abs(q["z"] - kv.z_ust) > 60:
            continue
        dx, dy = q["X"] - X0, q["Y"] - Y0
        u, v = dx * math.cos(a) + dy * math.sin(a), -dx * math.sin(a) + dy * math.cos(a)
        if cok:
            ic = any(k.icinde(u, v, 60.0) for k in kollar)
        else:
            ic = abs(u) <= kv.lw / 2 + 60 and abs(v) <= kv.bw / 2 + 60
        if ic:
            sec.append(dict(u=u, v=v, b=q["b"], ad=q["ad"]))
    kv.kiris = sec


_SECIM_DOSYASI = "perde_kombinasyon_secimi.json"


def _secim_yolu():
    try:
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), _SECIM_DOSYASI)
    except Exception:
        return _SECIM_DOSYASI


def _son_secim():
    try:
        import json
        with open(_secim_yolu(), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _secim_kaydet(pm, kesme):
    try:
        import json
        with open(_secim_yolu(), "w", encoding="utf-8") as f:
            json.dump(dict(pm=pm, kesme=kesme, buyutulmus=AYAR.get("kesme_komb_buyutulmus")), f,
                  ensure_ascii=False, indent=1)
    except Exception:
        pass


def _komb_coz(metin, adlar):
    """'3,5,7-9' (sıra no), '*' (hepsi) ya da ad parçası ('ENV', 'ENV-DEP-1.2D') -> ad listesi.
    Tam ad eşleşmesi varsa yalnız o alınır; yoksa adında geçenler."""
    secim = []
    for tok in [t.strip() for t in metin.replace(";", ",").split(",") if t.strip()]:
        if tok == "*":
            secim += adlar
        elif tok.replace("-", "").isdigit() and "-" in tok and tok[0] != "-":
            a, b = tok.split("-", 1)
            secim += [adlar[i - 1] for i in range(int(a), int(b) + 1) if 1 <= i <= len(adlar)]
        elif tok.isdigit():
            i = int(tok)
            if 1 <= i <= len(adlar):
                secim.append(adlar[i - 1])
        else:
            tam = [a for a in adlar if a.upper() == tok.upper()]
            secim += tam or [a for a in adlar if tok.upper() in a.upper()]
    return list(dict.fromkeys(secim))


def _deprem_katsayisi(sm, komb, derinlik=0, _onbellek=None):
    """Kombinasyon içindeki deprem yük durumlarının en büyük (iç içe çarpılmış) katsayısı.
    Envelope/iç içe kombinasyonlar özyinelemeli açılır. Okunamazsa None."""
    _onbellek = {} if _onbellek is None else _onbellek
    if komb in _onbellek:
        return _onbellek[komb]
    if derinlik > 6:
        return None
    try:
        r = api(sm.RespCombo.GetCaseList, komb, 0, [], [], [])
    except Exception:
        return None
    n, tipler, adlar, sfs = r[0], r[1], r[2], r[3]
    anah = [a.upper() for a in AYAR["deprem_durum_anahtar"]]
    en = None
    for i in range(n):
        ad, sf, tip = adlar[i], float(sfs[i]), int(tipler[i])
        if tip == 1:                              # iç kombinasyon
            k_ = _deprem_katsayisi(sm, ad, derinlik + 1, _onbellek)
            v = None if k_ is None else abs(sf) * k_
        else:
            v = abs(sf) if any(a in ad.upper() for a in anah) else None
        if v is not None:
            en = v if en is None else max(en, v)
    _onbellek[komb] = en
    return en


def kesme_buyutme_sor(sm, kesme_kombs):
    """Seçilen kesme kombinasyonları 1.2·D ile zaten büyütülmüş mü? ETABS katsayılarından
    öneri yapar, kullanıcıya sorar. AYAR['kesme_komb_buyutulmus'] ayarlanır."""
    A = AYAR
    hedef = A["kesme_katsayi"] * A["D"]
    ks = {k: _deprem_katsayisi(sm, k) for k in kesme_kombs}
    bilinen = [v for v in ks.values() if v]
    for k, v in ks.items():
        print(f"  {k}: deprem yükü katsayısı = {v:.3f}" if v else
              f"  {k}: deprem yükü katsayısı okunamadı")
    if bilinen:
        kmax = max(bilinen)
        oneri = kmax >= 0.5 * (1.0 + hedef)     # 1 ile 1.2·D arasının üst yarısı -> büyütülmüş
        print(f"  Tespit: en büyük deprem katsayısı {kmax:.3f}; 1.2·D = {A['kesme_katsayi']:g}×"
              f"{A['D']:g} = {hedef:.2f} -> kombinasyon "
              f"{'ZATEN BÜYÜTÜLMÜŞ görünüyor' if oneri else 'büyütülmemiş görünüyor'}.")
    else:
        f12 = A["kesme_komb_filtre"].replace(" ", "").upper()
        oneri = any(f12 in k.replace(" ", "").upper() for k in kesme_kombs)
        print(f"  Katsayı okunamadı; adından tahmin: {'büyütülmüş' if oneri else 'büyütülmemiş'}.")
    if A.get("kesme_komb_buyutulmus") is not None:
        secim = bool(A["kesme_komb_buyutulmus"])
    elif A["kombinasyon_sor"]:
        while True:
            try:
                c = input(f"\nKesme kombinasyonlarında deprem etkisi zaten 1.2·D ile büyütülmüş mü?\n"
                          f"  E = evet (Ve = Vd, kod tekrar büyütmez)   H = hayır (Ve = 1.2·D·Vd = "
                          f"{hedef:.2f}·Vd)\n  önerilen: {'E' if oneri else 'H'}\n  > ")
            except (EOFError, OSError):
                c = ""
            c = c.strip().upper()
            if not c:
                secim = oneri
                break
            if c[0] in "EY":
                secim = True
                break
            if c[0] in "HN":
                secim = False
                break
            print("  E ya da H girin.")
    else:
        secim = oneri
    A["kesme_komb_buyutulmus"] = secim
    print("  -> " + ("Ve = Vd (tekrar büyütülmüyor)" if secim else
                     f"Ve = 1.2·D·Vd = {hedef:.2f}·Vd"))
    return secim


def kombinasyon_sec(sm):
    """ETABS kombinasyon listesini gösterir; P–M ve kesme kombinasyonlarını sorar.
    Döndürür: (pm_kombs, kesme_kombs, zarf_kumesi)."""
    A = AYAR
    r = api(sm.RespCombo.GetNameList, 0, [])
    adlar = [k for k in r[1] if A["kombinasyon_filtre"] in k]
    zarf = set()
    for k in adlar:
        try:
            t = sm.RespCombo.GetTypeCombo(k, 0)
            t = t[0] if isinstance(t, (list, tuple)) else t
            if int(t) == 1:
                zarf.add(k)
        except Exception:
            if "ENV" in k.upper() or "ZARF" in k.upper():
                zarf.add(k)
    f12 = A["kesme_komb_filtre"].replace(" ", "").upper()
    has12 = lambda k: bool(f12) and f12 in k.replace(" ", "").upper()
    # öneriler
    pm_oneri = [k for k in adlar if k in zarf and not has12(k)] or list(adlar)
    ks_oneri = [k for k in adlar if k in zarf and has12(k)] or [k for k in adlar if has12(k)]
    son = _son_secim()
    if son.get("pm") and all(k in adlar for k in son["pm"]):
        pm_oneri = son["pm"]
    if son.get("kesme") and all(k in adlar for k in son["kesme"]):
        ks_oneri = son["kesme"]
    def ayardan(liste):
        if not liste:
            return None
        out = []
        for t in liste:
            out += _komb_coz(t[1:], adlar) if t.startswith("§") else ([t] if t in adlar else [])
        return list(dict.fromkeys(out)) or None
    pm = ayardan(A["kombinasyonlar"])
    ks = ayardan(A["kesme_kombinasyonlari"])
    if (pm is None or ks is None) and A["kombinasyon_sor"]:
        print("\nModeldeki yük kombinasyonları ([ZARF] = envelope):")
        gen = max([len(a) for a in adlar] + [10])
        sutun = max(1, 110 // (gen + 12))
        satir = []
        for i, a in enumerate(adlar, 1):
            satir.append(f"{i:>4}. {a}{' [ZARF]' if a in zarf else ''}".ljust(gen + 13))
            if len(satir) == sutun:
                print("".join(satir))
                satir = []
        if satir:
            print("".join(satir))
        print("Seçim: sıra no ('3,5,7-9'), ad ya da ad parçası ('ENV'), '*' = hepsi, "
              "Enter = önerilen.")

        def sor(baslik, oneri):
            while True:
                try:
                    cevap = input(f"\n{baslik}\n  önerilen: {', '.join(oneri) or '(yok)'}\n  > ")
                except (EOFError, OSError):
                    cevap = ""
                cevap = cevap.strip()
                if not cevap:
                    if oneri:
                        return list(oneri)
                    print("  Önerilen yok – seçim yapın.")
                    continue
                sec = _komb_coz(cevap, adlar)
                if sec:
                    return sec
                print("  Eşleşen kombinasyon yok, tekrar deneyin.")
        if pm is None:
            pm = sor("EĞİLME + EKSENEL (P–M / PMM) için kombinasyon(lar):", pm_oneri)
        if ks is None:
            ks = sor("KESME (Vd) için kombinasyon(lar):", ks_oneri)
    pm = pm or pm_oneri
    ks = ks or ks_oneri
    print("P–M kombinasyonları  :", ", ".join(pm))
    print("Kesme kombinasyonları:", ", ".join(ks) or "(yok)")
    if ks and AYAR["kesme_yontemi"].upper() == "1.2D":
        if son.get("kesme") == ks and son.get("buyutulmus") is not None and \
                AYAR.get("kesme_komb_buyutulmus") is None and not AYAR["kombinasyon_sor"]:
            AYAR["kesme_komb_buyutulmus"] = son["buyutulmus"]
        kesme_buyutme_sor(sm, ks)
    _secim_kaydet(pm, ks)
    return pm, ks, zarf


def zarf_kose_birlesimleri(kuvvetler, zarf):
    """Envelope kombinasyonlarında aynı kat/konumdaki max ve min satırlarından (P, M3, M2)
    köşe birleşimleri: 2×2×2 = 8 talep; kesmeler |max|. Diğer satırlar aynen kalır."""
    if not zarf or not AYAR["zarf_kose"]:
        return kuvvetler
    gr, out = {}, []
    for f in kuvvetler:
        if f[0] in zarf:
            gr.setdefault((f[0], f[1]), []).append(f)
        else:
            out.append(f)
    for (c, konum), fs in gr.items():
        if len(fs) < 2:
            out += fs
            continue
        Ns = (max(f[2] for f in fs), min(f[2] for f in fs))
        M3s = (max(f[3] for f in fs), min(f[3] for f in fs))
        M2s = (max(f[5] for f in fs), min(f[5] for f in fs))
        V2 = max(abs(f[4]) for f in fs)
        V3 = max(abs(f[6]) for f in fs)
        T_ = max((abs(f[7]) for f in fs if len(f) > 7), default=0.0)
        for i_, N_ in enumerate(Ns):
            for j_, M3_ in enumerate(M3s):
                for k_, M2_ in enumerate(M2s):
                    out.append((c, konum, N_, M3_, V2, M2_, V3, T_,
                                f"{c} [zarf: N {'max' if i_ == 0 else 'min'}, M3 "
                                f"{'max' if j_ == 0 else 'min'}, M2 {'max' if k_ == 0 else 'min'}]"))
    return out


def satir_kimlikle(kuvvetler):
    """Her kuvvet satırına T (yoksa 0) ve kimlik ekler. Kimlik, aynı kombinasyonun farklı
    katlardaki satırlarını eşlemek içindir (çok değerli kombinasyonda: sıra numarası)."""
    say, out = {}, []
    for f in kuvvetler:
        f = tuple(f)
        if len(f) >= 9:
            out.append(f)
            continue
        T_ = f[7] if len(f) > 7 else 0.0
        f = f + (0.0,) * (7 - len(f))          # eski biçim: (komb, konum, N, M3, V2[, M2, V3])
        i = say.get((f[0], f[1]), 0)
        say[(f[0], f[1])] = i + 1
        out.append(f[:7] + (T_, f[0] if i == 0 else f"{f[0]} #{i + 1}"))
    return out


MODEL_ADI = None      # ETABS model dosyasının adı (çıktı klasörü bu adla açılır)


def etabs_oku(pier_listesi=None, tum=False, bodrum_listesi=None):
    global MODEL_ADI
    sm = etabs_baglan()
    try:
        fn = sm.GetModelFilename(False)
        fn = fn[0] if isinstance(fn, (list, tuple)) else fn
        MODEL_ADI = os.path.splitext(os.path.basename(str(fn)))[0] or None
    except Exception:
        MODEL_ADI = None
    eski_birim = sm.GetPresentUnits()
    sm.SetPresentUnits(6)  # kN_m_C
    try:
        # --- pier listesi
        if tum:
            r = api(sm.PierLabel.GetNameList, 0, [])
            pierler = list(r[1])
        elif pier_listesi:
            pierler = list(pier_listesi)
        else:
            r = api(sm.SelectObj.GetSelected, 0, [], [])
            n, tipler, adlar = r[0], r[1], r[2]
            pierler = []
            for t, ad in zip(tipler, adlar):
                p = None
                if t == 5:
                    p = sm.AreaObj.GetPier(ad, "")[0]
                elif t == 2:
                    p = sm.FrameObj.GetPier(ad, "")[0]
                if p and p.lower() != "none" and p not in pierler:
                    pierler.append(p)
            if not pierler:
                raise RuntimeError("Seçili nesnelerde pier atanmış eleman yok. "
                                   "ETABS'te perdeleri seçin ya da --piers kullanın.")
        for b_ in bodrum_listesi or []:
            if b_ not in pierler:
                pierler.append(b_)
        print("Pier'ler:", ", ".join(pierler))

        # --- kat kotları
        r = api(sm.Story.GetStories_2, 0.0, 0, [], [], [], [], [], [], [], [])
        taban = r[0]
        kat_adlari, kat_kot, kat_h = list(r[2]), list(r[3]), list(r[4])
        kat_bilgi = {k: ((z - h - taban) * 1000.0, (z - taban) * 1000.0)
                     for k, z, h in zip(kat_adlari, kat_kot, kat_h)}

        # --- kombinasyonlar: kullanıcıya sorulur (P–M ve kesme ayrı)
        pm_kombs, kesme_kombs, zarf = kombinasyon_sec(sm)
        kombs = list(dict.fromkeys(pm_kombs + kesme_kombs))
        sm.Results.Setup.DeselectAllCasesAndCombosForOutput()
        for k in kombs:
            sm.Results.Setup.SetComboSelectedForOutput(k, True)
        print(f"{len(kombs)} kombinasyon çıktı için seçildi.")
        if not kesme_kombs:
            olu = {x.upper() for x in AYAR["olu_yuk_durumlari"]}
            for k in kombs:
                try:
                    rr = api(sm.RespCombo.GetCaseList, k, 0, [], [], [])
                    for ad, sf in zip(rr[2], rr[3]):
                        if ad.upper() in olu and abs(sf - AYAR["kesme_olu_katsayi"]) < 1e-3:
                            kesme_kombs.append(k)
                            break
                except Exception:
                    pass
            print("Kesme kombinasyonları (ölü yük katsayısından):", ", ".join(kesme_kombs)
                  or "(bulunamadı)")

        # --- pier kuvvetleri (tek çağrı, tüm pier'ler)
        print("Pier kuvvetleri okunuyor...", flush=True)
        t0 = time.time()
        r = api(sm.Results.PierForce, 0, [], [], [], [], [], [], [], [], [], [])
        nres = r[0]
        print(f"  {nres} satır okundu ({time.time() - t0:.1f} s)", flush=True)
        if nres == 0:
            raise RuntimeError("Pier kuvveti sonucu yok. Analiz çalıştırıldı mı?")
        s_kat, s_pier, s_komb, s_konum = r[1], r[2], r[3], r[4]
        P, V2, V3, M2, M3 = r[5], r[6], r[7], r[9], r[10]
        Tb = r[8]
        fck_cache = {}

        def fck_oku(mat):
            """ETABS beton malzemesinin karakteristik dayanımı (MPa); okunamazsa None."""
            if mat not in fck_cache:
                v = None
                try:
                    q = api(sm.PropMaterial.GetOConcrete_1, mat, 0.0, False, 0.0, 0, 0, 0.0, 0.0,
                            0.0, 0.0, 0.0)
                    v = float(q[0]) / 1000.0            # kN/m² -> MPa
                    if not (10.0 <= v <= 120.0):
                        v = None
                except Exception:
                    v = None
                fck_cache[mat] = v
            return fck_cache[mat]

        print("Pier geometrisi okunuyor...", flush=True)
        kat_ust = [(k, z) for k, z in zip(kat_adlari, kat_kot)]
        geo, alan_adlari = etabs_pier_geometri(sm, pierler, kat_ust)
        kir = []
        if AYAR["kiris_bolgesi"]:
            print("Kirişler okunuyor...", flush=True)
            pts = [q for v in geo.values() for a_, b_, t_ in v for q in (a_, b_)]
            kutu = None
            if pts:
                kutu = (min(p_[0] for p_ in pts) - 1000, max(p_[0] for p_ in pts) + 1000,
                        min(p_[1] for p_ in pts) - 1000, max(p_[1] for p_ in pts) + 1000)
            kir = etabs_kirisler(sm, taban, kutu)
        sonuc = []
        bodrum_zarf = None
        if bodrum_listesi:
            gr_ = {k: v for k, v in alan_adlari.items() if k[0] in bodrum_listesi}
            print(f"Bodrum perdesi shell kuvvetleri okunuyor ({sum(len(v) for v in gr_.values())} "
                  f"alan)...", flush=True)
            bodrum_zarf = etabs_bodrum_toplu(sm, gr_, set(kombs))
        for pad in pierler:
            g = api(sm.PierLabel.GetSectionProperties, pad, 0, [], [], [], [], [], [], [], [],
                                                  [], [], [], [], [], [], [])
            nk, kat_list = g[0], list(g[1])
            wbot, tbot = list(g[5]), list(g[6])
            katlar = {}
            for i, k in enumerate(kat_list):
                za, zu = kat_bilgi[k]
                katlar[k] = KatVerisi(k, za, zu, wbot[i] * 1000.0, tbot[i] * 1000.0,
                                      cg_alt=(g[10][i] * 1000.0, g[11][i] * 1000.0),
                                      cg_ust=(g[13][i] * 1000.0, g[14][i] * 1000.0),
                                      aci=float(g[2][i]))
                katlar[k]._segs = geo.get((pad, k))
                try:
                    katlar[k].malzeme_adi = str(g[9][i])
                    katlar[k].fck = fck_oku(str(g[9][i]))
                except Exception:
                    pass
            for i in range(nres):
                if s_pier[i] != pad or s_kat[i] not in katlar:
                    continue
                katlar[s_kat[i]].kuvvetler.append(
                    (s_komb[i], s_konum[i], -P[i], M3[i], V2[i], M2[i], V3[i], Tb[i]))  # N basınç +
            kl = sorted(katlar.values(), key=lambda k: k.z_alt)
            for kv in kl:
                kv.kuvvetler = satir_kimlikle(zarf_kose_birlesimleri(kv.kuvvetler, zarf))
            if pad in (bodrum_listesi or []):
                for kv in kl:
                    if bodrum_zarf is not None and (pad, kv.kat) in bodrum_zarf:
                        kv.bodrum = dict(bodrum_zarf[(pad, kv.kat)])
                    else:
                        kv.bodrum = etabs_bodrum_kuvvet(sm, alan_adlari.get((pad, kv.kat), []),
                                                        set(kombs))
                    kv.bodrum.update(t=kv.bw, L=kv.lw)
                    if kv._segs:          # plan izi (U/H/L bodrum perdeleri kat planında)
                        kv.bodrum["izler"] = [((float(a[0]), float(a[1])), (float(b[0]), float(b[1])),
                                               float(t)) for a, b, t in segleri_birlestir(kv._segs)]
                sonuc.append(Pier(pad, kl, kesme_kombs, bodrum=True, pm_kombs=pm_kombs))
                continue
            cok = any(kv._segs and len(segleri_birlestir(kv._segs)) > 1 for kv in kl)
            if cok:
                # çok kollu pier: bütün katların kesiti alan elemanlarından (tek kollu katlar da)
                for kv in kl:
                    a = math.radians(kv.aci)
                    if kv._segs:
                        cg, _ = kesit_agirlik_merkezi(kv._segs)
                        loc = []
                        for p1, p2, t in kv._segs:
                            q = []
                            for X, Y in (p1, p2):
                                dx, dy = X - cg[0], Y - cg[1]
                                q.append((dx * math.cos(a) + dy * math.sin(a),
                                          -dx * math.sin(a) + dy * math.cos(a)))
                            loc.append((q[0], q[1], t))
                        kv.kollar, kv.orijin = loc, (float(cg[0]), float(cg[1]))
                    else:     # alan elemanı okunamadı: pier genişlik/kalınlığıyla dikdörtgen
                        kv.kollar = [((-kv.lw / 2, 0.0), (kv.lw / 2, 0.0), kv.bw)]
                        kv.orijin = kv.cg_ust
            for kv in kl:
                _kirisleri_ata(kv, kir, cok)
            sonuc.append(Pier(pad, kl, kesme_kombs, pm_kombs=pm_kombs))
        return sonuc
    finally:
        sm.SetPresentUnits(eski_birim)


# =====================================================================================
# 4) ÖRNEK VERİ (--demo)
# =====================================================================================
def ornek_veri():
    """ETABS olmadan denemek için iki pier. Aynı yapıda elle veri de girilebilir."""
    pierler = []

    def uret(ad, h_kat, n_kat, geo, V0, N_kat, kaçık=lambda i: (0.0, 0.0), kirisler=()):
        katlar = []
        H = h_kat * n_kat
        for i in range(n_kat):
            za, zu = i * h_kat, (i + 1) * h_kat
            lw, bw = geo(i)
            kv = KatVerisi(f"KAT{i + 1}", za, zu, lw, bw, cg_alt=kaçık(i), cg_ust=kaçık(i),
                           kiris=[dict(q) for q in kirisler])
            for konum, z in (("Bottom", za), ("Top", zu)):
                # yaklaşık üçgen yük altında konsol: V ve M dağılımı
                xi = z / H
                V = V0 * (1 - xi ** 2)
                M = V0 * H / 1000 * (2 / 3 - xi + xi ** 3 / 3) * 1.0
                Ng = N_kat * (n_kat - i - (0 if konum == "Bottom" else 1))
                Ng = max(Ng, N_kat * 0.3)
                kv.kuvvetler += [
                    ("1.4D+1.6L", konum, 1.4 * Ng, 0.05 * M, 0.05 * V),
                    ("1.2D+1.0L+1.0EX", konum, 1.1 * Ng, M, V),
                    ("1.2D+1.0L-1.0EX", konum, 1.1 * Ng, -M, -V),
                    ("0.9D+1.0EX", konum, 0.9 * Ng * 0.8, M, 0.9 * V),
                    ("0.9D-1.0EX", konum, 0.9 * Ng * 0.8, -M, -0.9 * V),
                ]
            katlar.append(kv)
        return Pier(ad, katlar, ["1.2D+1.0L+1.0EX", "1.2D+1.0L-1.0EX"])

    # P1: perdenin ortasına dik bir kiriş (K101, b=300) her katta bağlanıyor
    pierler.append(uret("P1", 3000.0, 10, lambda i: (4000.0, 300.0), 480.0, 520.0,
                        kirisler=[dict(u=300.0, v=0.0, b=300.0, ad="K101")]))
    # P2: KAT4'te kalınlık 300 -> 250, dış yüz hizalı (ağırlık merkezi 25 mm kayar)
    pierler.append(uret("P2", 3000.0, 6,
                        lambda i: (2500.0, 300.0 if i < 3 else 250.0), 230.0, 260.0,
                        lambda i: (0.0, 0.0 if i < 3 else -25.0)))
    # P3: KAT3'te boy 3000 -> 2400 (bir uç hizalı) ve kalınlık 300 -> 250 (bir yüz hizalı)
    pierler.append(uret("P3", 3000.0, 5,
                        lambda i: (3000.0, 300.0) if i < 2 else (2400.0, 250.0), 260.0, 300.0,
                        lambda i: (0.0, 0.0) if i < 2 else (-300.0, -25.0)))

    def uret_cok(ad, segs_f, h_kat, n_kat, V2_0, V3_0, N_kat, kirisler=()):
        """Çok kollu örnek: segs_f(i) -> merkez çizgileri (ağırlık merkezinden bağımsız)."""
        katlar = []
        H = h_kat * n_kat
        for i in range(n_kat):
            segs = segs_f(i)
            cg, Ac = kesit_agirlik_merkezi(segs)
            loc = [((a[0] - cg[0], a[1] - cg[1]), (b[0] - cg[0], b[1] - cg[1]), t) for a, b, t in segs]
            za, zu = i * h_kat, (i + 1) * h_kat
            kv = KatVerisi(f"KAT{i + 1}", za, zu, 0.0, 0.0, kollar=loc,
                           orijin=(float(cg[0]), float(cg[1])),
                           kiris=[dict(u=q["u"] - cg[0], v=q["v"] - cg[1], b=q["b"], ad=q["ad"])
                                  for q in kirisler])
            for konum, z in (("Bottom", za), ("Top", zu)):
                xi = z / H
                f_v = 1 - xi ** 2
                f_m = H / 1000 * (2 / 3 - xi + xi ** 3 / 3)
                Ng = max(N_kat * (n_kat - i - (0 if konum == "Bottom" else 1)), N_kat * 0.3)
                V2, M3 = V2_0 * f_v, V2_0 * f_m
                V3, M2 = V3_0 * f_v, V3_0 * f_m
                kv.kuvvetler += [
                    ("1.4D+1.6L", konum, 1.4 * Ng, 0.05 * M3, 0.05 * V2, 0.05 * M2, 0.05 * V3),
                    ("1.2D+1.0L+1.0EX", konum, 1.1 * Ng, M3, V2, 0.3 * M2, 0.3 * V3),
                    ("1.2D+1.0L-1.0EX", konum, 1.1 * Ng, -M3, -V2, -0.3 * M2, -0.3 * V3),
                    ("1.2D+1.0L+1.0EY", konum, 1.1 * Ng, 0.3 * M3, 0.3 * V2, M2, V3),
                    ("1.2D+1.0L-1.0EY", konum, 1.1 * Ng, -0.3 * M3, -0.3 * V2, -M2, -V3),
                    ("0.9D+1.0EX", konum, 0.72 * Ng, M3, V2, 0.3 * M2, 0.3 * V3),
                    ("0.9D-1.0EY", konum, 0.72 * Ng, -0.3 * M3, -0.3 * V2, -M2, -V3),
                ]
            katlar.append(kv)
        return Pier(ad, katlar, ["1.2D+1.0L+1.0EX", "1.2D+1.0L-1.0EX", "1.2D+1.0L+1.0EY",
                                 "1.2D+1.0L-1.0EY"])

    # U çekirdek: gövde 4000, kanatlar 2000, t = 300
    U = lambda i: [((0, 0), (4000, 0), 300.0), ((0, 0), (0, -2000), 300.0),
                   ((4000, 0), (4000, -2000), 300.0)]
    pierler.append(uret_cok("PU", U, 3000.0, 8, 900.0, 450.0, 900.0,
                            kirisler=[dict(u=2000.0, v=0.0, b=300.0, ad="K201")]))
    # H perde: gövde 5000 (t=300), başlıklar 3000 (t=300); KAT4'ten itibaren başlıklar 2000
    Hf = lambda i: [((0, 0), (5000, 0), 300.0),
                    ((0, -1500 if i < 3 else -1000), (0, 1500 if i < 3 else 1000), 300.0),
                    ((5000, -1500 if i < 3 else -1000), (5000, 1500 if i < 3 else 1000), 300.0)]
    # BP1: bodrum perdesi, 2 bodrum katı, 8000×300; toprak itkisinden shell momentleri
    bp = []
    for i, (M22p, M22n, M11p, M11n, V23) in enumerate(((28.0, 46.0, 9.0, 7.0, 55.0),
                                                       (18.0, 30.0, 6.0, 5.0, 38.0))):
        kv = KatVerisi(f"B{2 - i}", i * 3500.0, (i + 1) * 3500.0, 8000.0, 300.0)
        kv.kuvvetler = [(c_, "Bottom", 1200.0, 900.0, 180.0, 0.0, 0.0) for c_ in
                        ("1.2D+1.0L+1.0EX", "1.2D+1.0L-1.0EX")]
        kv.bodrum = dict(M11p=M11p, M11n=M11n, M22p=M22p, M22n=M22n, F11t=0.0, F22t=0.0,
                         V13=12.0, V23=V23, t=300.0, L=8000.0)
        bp.append(kv)
    pierler.append(Pier("BP1", bp, ["1.2D+1.0L+1.0EX", "1.2D+1.0L-1.0EX"], bodrum=True))
    pierler.append(uret_cok("PH", Hf, 3000.0, 6, 1100.0, 500.0, 1100.0,
                            kirisler=[dict(u=2500.0, v=0.0, b=300.0, ad="K301"),
                                      dict(u=0.0, v=-700.0, b=250.0, ad="K302")]))
    return pierler


# =====================================================================================
# 5) LİF (FIBER) KESİT MODELİ – P–M3 etkileşimi
# =====================================================================================
#   * Beton kesit lw × bw, "lif_boyutu" aralıklı bir ağ (mesh) ile liflere bölünür.
#   * Donatılar kendi koordinatlarında ayrık lif olarak eklenir (yerini aldığı beton düşülür).
#   * Beton: TS 500 parabol-dikdörtgen, 0.85·fcd, εc0 = 0.002, εcu = 0.003, çekme yok.
#     (Sargılı/sargısız ayrımı yapılmaz.)
#   * Çelik: elasto-plastik, Es = 200 GPa, ±fyd.
#   * Düzlemler düzlem kalır; yalnızca perde düzlemi içindeki eğilme (M3) ele alınır.
#     Bu durumda şekil değiştirme kalınlık boyunca sabittir; aynı x'teki lifler aynı
#     gerilmeyi taşır, hesap bu yüzden şeritler üzerinden yapılır (sonuç 2B ağ ile aynıdır).
#   * Tarafsız eksen derinliği c taranarak P–M3 eğrisi (her iki yön) üretilir;
#     her talep için Nd'deki Mr eğriden okunur.

def _daire_topla(ax, bars, fc, ec, zorder=4):
    """Çok sayıda donatı dairesini tek koleksiyonla çizer (tek tek add_patch yavaştır)."""
    from matplotlib.collections import PatchCollection
    import matplotlib.patches as mp
    if not bars:
        return
    pc = PatchCollection([mp.Circle((x, y), d / 2) for x, y, d in bars], facecolor=fc,
                         edgecolor=ec, linewidth=0.6, zorder=zorder)
    ax.add_collection(pc)


def sigma_beton(eps, m):
    e0, ecu = AYAR["eps_c0"], AYAR["eps_cu"]
    fc = 0.85 * m["fcd"]
    e = np.clip(eps, 0.0, ecu)
    return np.where(e < e0, fc * (2 * e / e0 - (e / e0) ** 2), fc)


def sigma_celik(eps, m):
    return np.clip(ES * eps, -m["fyd"], m["fyd"])


class LifKesit:
    def __init__(self, lw, bw, uc, govde, m):
        h = AYAR["lif_boyutu"]
        self.lw, self.bw, self.m = lw, bw, m
        self.nx = max(1, math.ceil(lw / h))
        self.ny = max(1, math.ceil(bw / h))
        dx = lw / self.nx
        self.xc = (np.arange(self.nx) + 0.5) * dx          # lif merkezleri (x)
        self.Ac = np.full(self.nx, dx * bw)                  # aynı x'teki liflerin toplamı
        bars = uc + govde
        self.bx = np.array([b[0] for b in bars])
        self.bA = np.array([alan(b[2]) for b in bars])
        self.n_lif = self.nx * self.ny
        self._egri = None

    def _NM(self, c, yon):
        """c: tarafsız eksen derinliği dizisi. yon=+1: basınç sol kenarda, -1: sağ kenarda."""
        c = np.atleast_1d(c)[:, None]
        ecu = AYAR["eps_cu"]
        xc = self.xc if yon > 0 else self.lw - self.xc
        bx = self.bx if yon > 0 else self.lw - self.bx
        eps_c = ecu * (c - xc[None, :]) / c
        eps_s = ecu * (c - bx[None, :]) / c
        sc = sigma_beton(eps_c, self.m)
        ss = sigma_celik(eps_s, self.m) - sigma_beton(eps_s, self.m)  # yer değiştiren beton
        Fc = sc * self.Ac[None, :]
        Fs = ss * self.bA[None, :]
        kol_c = self.lw / 2 - self.xc   # moment kolu gerçek koordinata göre
        kol_s = self.lw / 2 - self.bx
        N = Fc.sum(1) + Fs.sum(1)
        M = (Fc * kol_c[None, :]).sum(1) + (Fs * kol_s[None, :]).sum(1)
        return N, M

    def egri(self):
        """P–M3 eğrisi: {+1: (N, M), -1: (N, M)}, N artan sırada (N, Nmm)."""
        if self._egri is None:
            n = AYAR["pm_nokta"]
            c = np.geomspace(1e-4 * self.lw, 1e3 * self.lw, n)
            out = {}
            for yon in (1, -1):
                N, M = self._NM(c, yon)
                N = np.maximum.accumulate(N)
                out[yon] = (N, M)
            self._egri = out
            self._c = c
        return self._egri

    def durum(self, Nd, Md):
        """Nd'de taşıma gücüne ulaşıldığı andaki şekil değiştirme / gerilme dağılımı.
        Döndürür: (eps_beton[x], sig_beton[x], eps_celik[bar], sig_celik[bar]) veya None"""
        yon = 1 if Md >= 0 else -1
        N, _ = self.egri()[yon]
        if Nd < N[0] or Nd > N[-1]:
            return None
        c = float(np.exp(np.interp(Nd, N, np.log(self._c))))
        ecu = AYAR["eps_cu"]
        xc = self.xc if yon > 0 else self.lw - self.xc
        bx = self.bx if yon > 0 else self.lw - self.bx
        ec = ecu * (c - xc) / c
        es = ecu * (c - bx) / c
        return ec, sigma_beton(ec, self.m), es, sigma_celik(es, self.m)

    def Mr(self, Nd, Md_isaret=1.0):
        """Nd (N, basınç +) altında, Md işaretine göre ilgili daldan |Mr|. Aşım -> nan"""
        Nd = np.atleast_1d(np.asarray(Nd, float))
        isr = np.broadcast_to(np.atleast_1d(np.sign(Md_isaret)), Nd.shape)
        out = np.full(Nd.shape, np.nan)
        for yon in (1, -1):
            N, M = self.egri()[yon]
            sec = (isr >= 0) if yon > 0 else (isr < 0)
            ic = sec & (Nd >= N[0]) & (Nd <= N[-1])
            out[ic] = np.abs(np.interp(Nd[ic], N, M))
        return out

    def talep_orani(self, talepler):
        """talepler: [(N, M)] -> her talep için Md/Mr (kapasite dışı -> inf)"""
        T = np.array(talepler, float)
        Mr = self.Mr(T[:, 0], T[:, 1])
        with np.errstate(divide="ignore", invalid="ignore"):
            dc = np.where(np.isnan(Mr), np.inf, np.abs(T[:, 1]) / Mr)
        return dc


# =====================================================================================
# 6) PERDE TASARIMI
# =====================================================================================
def yerlesim(lw, bw, Lu, de, ny, ncol, dw, sw_hedef, det, dh, govde_ref=None, s_kuy=None,
             dt=None, kolonlar=None):
    """Boyuna donatı koordinatları. x: perde boyunca (0..lw), y: kalınlık (0..bw).

    Uç bölge düzeni (her uç için, sol uç tanımlanır, sağ uç aynalanır):
      * KÜME   : perdenin en ucunda ny sıra × ncol kolon, çap de, en küçük aralıkla – eğilmede
                 kolu en büyük olan bölge; eğilme donatısının asıl kısmı buradadır.
      * KUYRUK : kümeden uç bölge sınırına kadar iki yüzde, çap dt (≤ de), aralık ≤ s_kuy.
      * SINIR  : uç bölgenin gövdeye bakan son kolonu ny sıra, çap dt.
    kolonlar verilirse (alt kattan gelen kolon konumları) küme ilk ncol kolona, kuyruk
    kalanlara yerleştirilir – üst kat çubukları alt kat çubuklarının üstüne oturur.
    Uygun değilse None döner."""
    A = AYAR
    c = A["paspayi"]
    dt = dt or de
    ic = c + max(det, dh)                      # etriye ve yatay donatının (aynı tabaka) içi
    # ny   : perdenin UÇ YÜZÜNDEKİ (en uç kolon) donatı sayısı – eğilmede en etkili yer; çubuklar
    #        önce bu yüze dizilir (net aralık ≥ uc_net_min).
    # ny_ic: kalınlık kuralının gerektirdiği sıra sayısı (iki yüz arası net ≤ uc_net_max_kalinlik);
    #        ara sıra(lar) yalnız bu kural gerektiriyorsa vardır ve sınır kolonunda da bulunur.
    nys_ = ny_aralik(bw, de, det, dh)
    if ny < 2 or not nys_:
        return None
    ny_ic = nys_[0]
    if ny < ny_ic or (ny_ic > 2 and (ny - 1) % (ny_ic - 1)):
        return None
    ys = list(np.linspace(ic + de / 2, bw - ic - de / 2, ny))
    k_ = (ny - 1) // (ny_ic - 1)
    ys_ic = ys[::k_] if ny_ic > 2 else [ys[0], ys[-1]]
    ys_t = [ic + dt / 2] + ys_ic[1:-1] + [bw - ic - dt / 2]
    x0 = c + max(det, dh * (2 if A["yatay_uc_detay"] == "firkete" else 1)) + de / 2
    s_k = kume_araligi(de)
    for ys_, d_ in ((ys, de), (ys_t, dt)):
        s_y = min(np.diff(ys_))
        s_yM = max(np.diff(ys_))
        if s_y < eksen_min(d_, d_) - 1e-6 or s_yM > d_ + A["uc_net_max_kalinlik"] + 1e-6:
            return None
    if kolonlar:
        if ncol > len(kolonlar):
            return None
        xk, xt = list(kolonlar[:ncol]), list(kolonlar[ncol:])
        x1 = (xt or xk)[-1]
        if Lu < x1 + det + (dt if xt else de) / 2 - 1e-6:
            return None
    else:
        x1 = Lu - det - dt / 2
        xk = [x0 + i * s_k for i in range(ncol)]
        if xk[-1] > Lu - det - de / 2 + 1e-6:
            return None
        kalan = x1 - xk[-1]
        xt = []
        if kalan > 1e-6:
            if kalan < eksen_min(de, dt) - 1e-6:
                return None
            s_e = min(s_kuy or A["s_uc_bar_max"], A["s_uc_bar_max"], eksen_max(de, dt),
                      eksen_max(dt, dt))
            n = math.ceil(kalan / s_e - 1e-9)
            if kalan / n < eksen_min(de, dt) - 1e-6:
                return None
            xt = [xk[-1] + (i + 1) * kalan / n for i in range(n)]
        else:
            x1 = xk[-1]
    sol = [(xk[0], y, de) for y in ys]                  # uç yüz: dolu
    for j, x in enumerate(xk[1:], 1):                   # kümenin diğer kolonları: iki yüz
        sira = ys_ic if (j == len(xk) - 1 and not xt) else (ys[0], ys[-1])
        sol += [(x, y, de) for y in sira]
    for i, x in enumerate(xt):
        sira = ys_t if i == len(xt) - 1 else (ys_t[0], ys_t[-1])
        sol += [(x, y, dt) for y in sira]
    # geometrik denetim: yüzler (alt/üst) ve tam sıralı kolonlar boyunca komşu çubuklar
    kol_idx = {}
    for i, (x, y, d) in enumerate(sol):
        kol_idx.setdefault(round(x, 3), []).append(i)
    xs_sira = sorted(kol_idx)
    zin = [[min(kol_idx[x], key=lambda i: sol[i][1]) for x in xs_sira],
           [max(kol_idx[x], key=lambda i: sol[i][1]) for x in xs_sira]]
    zin_k = [sorted(v, key=lambda i: sol[i][1]) for v in kol_idx.values() if len(v) > 2
             or v is kol_idx[xs_sira[0]] or v is kol_idx[xs_sira[-1]]]
    den = aralik_denetimi(sol, zin, zin_k)
    if not den["ok"]:
        return None
    uc = []
    for x, y, d in sol:
        uc += [(x, y, d), (lw - x, y, d)]
    kolon = xk + xt
    ciroz_x = [x for x in kolon if abs(x - kolon[0]) > 1 and abs(x - kolon[-1]) > 1]
    x_son = kolon[-1]
    ciroz_y = [(y, kolon[0], x_son) for y in ys_ic[1:-1]] if (ncol > 1 or xt) else []
    # gövde düşey
    y_g = c + dh + dw / 2
    xa, xb = x1, lw - x1
    if govde_ref:
        s_min = max(dw + max(25.0, dw), A["uc_kume_min_aralik"])
        ref = sorted({round(x, 1) for x in govde_ref if xa + s_min <= x <= xb - s_min})
        pts = [xa] + ref + [xb]
        xs_g, s_gercek = [], 0.0
        for a_, b_ in zip(pts[:-1], pts[1:]):
            n_ = max(1, math.ceil((b_ - a_) / sw_hedef - 1e-9))
            s_gercek = max(s_gercek, (b_ - a_) / n_)
            xs_g += [a_ + k * (b_ - a_) / n_ for k in range(1, n_)]
            if b_ != xb:
                xs_g.append(b_)
    else:
        n_ara = max(1, math.ceil((xb - xa) / sw_hedef - 1e-9))
        s_gercek = (xb - xa) / n_ara
        xs_g = [xa + i * s_gercek for i in range(1, n_ara)]
    govde = []
    for xx in xs_g:
        govde += [(xx, y_g, dw), (xx, bw - y_g, dw)]
    Lk = xk[-1] + s_k / 2
    n_kume = sum(1 for x, y, d in sol if x <= xk[-1] + 1e-6)
    rho_kume = n_kume * alan(de) / (Lk * bw)
    As_kume = n_kume * alan(de)
    As_top = sum(alan(d) for _, _, d in sol)
    duzen = dict(s_kuy=s_kuy, ny=ny, ny_ic=ny_ic, ncol=ncol, xk=xk, xt=xt, ys=ys, ys_ic=ys_ic,
                 ciroz_x=ciroz_x, n_kume=n_kume,
                 ciroz_y=ciroz_y, n_uc=len(sol), n_kuyruk=len(sol) - n_kume, s_k=s_k,
                 rho_kume=rho_kume, de=de, dt=dt, As=As_top, As_kume=As_kume,
                 yogunlasma=As_kume / As_top, kolonlar=kolon, aralik=den)
    return uc, govde, s_gercek, duzen


def govde_sec(bw, rho, s_max):
    """İki yüzde donatı: 2*A/s >= rho*bw. En küçük alanlı uygun (çap, aralık, ρ)."""
    en_iyi = None
    for d in AYAR["govde_caplari"]:
        s = min(s_max, 2 * alan(d) / (rho * bw))
        s = math.floor(s / 25.0) * 25.0
        if s < 100:
            continue
        oran = 2 * alan(d) / s / bw
        if en_iyi is None or oran < en_iyi[2] - 1e-9:
            en_iyi = (d, s, oran)
    return en_iyi


def uc_bolge_uzunlugu(lw, bw, kritik, kademe=0.0):
    """TBDY 7.6.2.3; kritik bölge üstündeki geçiş katlarında kritik ve normal değer arasında
    doğrusal (TBDY 7.6.5.1: geometri geçişi üç kat boyunca kademeli)."""
    Lk = max(0.20 * lw, 2 * bw)
    Ln = max(0.10 * lw, bw)
    Lu = Lk if kritik else Ln + kademe * (Lk - Ln)
    r = AYAR["Lu_yuvarlama"]
    return math.ceil(Lu / r - 1e-9) * r


def rho_uc_min(kritik, kademe=0.0):
    return 0.002 if kritik else 0.001 + 0.001 * kademe


def talep_azalt_pm(talepler, nbin=None):
    """P–M3 talepleri: N aralığı nbin dilime bölünür, her dilimde en büyük +M ve −M ile
    en büyük/küçük N tutulur. Yüzlerce kombinasyonda hesap süresini kısaltır."""
    nbin = nbin or AYAR["talep_dilim"]
    if len(talepler) <= 2 * nbin:
        return list(talepler)
    T = np.asarray(talepler, float)
    Nmin, Nmax = T[:, 0].min(), T[:, 0].max()
    w = max((Nmax - Nmin) / nbin, 1.0)
    sec = {int(np.argmin(T[:, 0])), int(np.argmax(T[:, 0]))}
    idx = np.minimum(((T[:, 0] - Nmin) / w).astype(int), nbin - 1)
    for b in np.unique(idx):
        ii = np.where(idx == b)[0]
        sec.add(int(ii[np.argmax(T[ii, 1])]))
        sec.add(int(ii[np.argmin(T[ii, 1])]))
    return [tuple(T[i]) for i in sorted(sec)]


def talep_azalt_pmm(talepler, nbin=None, nacı=36):
    """P–M2–M3 talepleri: N dilimi × moment doğrultusu (nacı) hücresinde en büyük |M|."""
    nbin = nbin or AYAR["talep_dilim"]
    if len(talepler) <= nbin * 4:
        return list(talepler)
    T = np.asarray(talepler, float)
    Nmin, Nmax = T[:, 0].min(), T[:, 0].max()
    w = max((Nmax - Nmin) / nbin, 1.0)
    ib = np.minimum(((T[:, 0] - Nmin) / w).astype(int), nbin - 1)
    ia = ((np.arctan2(T[:, 2], T[:, 1]) + math.pi) / (2 * math.pi) * nacı).astype(int) % nacı
    Mm = np.hypot(T[:, 1], T[:, 2])
    sec = {int(np.argmin(T[:, 0])), int(np.argmax(T[:, 0]))}
    anahtar = ib * nacı + ia
    for a_ in np.unique(anahtar):
        ii = np.where(anahtar == a_)[0]
        sec.add(int(ii[np.argmax(Mm[ii])]))
    return [tuple(T[i]) for i in sorted(sec)]


def kritik_ve_kademe(katlar, z0, Hcr):
    """Her kata kritik (bool) ve kademe (0..1) atar. Kritik bölgenin üstündeki ilk
    kademe_kat kat için kademe = (n+1-j)/(n+1): 3 katta 0.75, 0.50, 0.25."""
    n = AYAR["kademe_kat"]
    j = 0
    for k in katlar:
        k.kritik = (k.z_alt - z0) < Hcr - 1e-6
        if k.kritik:
            k.kademe = 1.0
        else:
            j += 1
            k.kademe = max(0.0, (n + 1 - j) / (n + 1)) if n > 0 else 0.0


def bolge_adi(g, kisa=False):
    if g["kritik"]:
        return "Kritik" if kisa else "KRİTİK PERDE BÖLGESİ"
    if g.get("kademe", 0) > 0:
        return (f"Geçiş %{g['kademe'] * 100:.0f}" if kisa else
                f"KRİTİK BÖLGE ÜSTÜ – KADEMELİ GEÇİŞ (%{g['kademe'] * 100:.0f})")
    return "Kritik üstü" if kisa else "KRİTİK BÖLGE ÜSTÜ"


def etriye_bolumle(ss, h0, h1, de, det):
    """Uç bölge etriyesini gerekiyorsa iç içe (bir kolon bindirmeli) kapalı etriyelere böler.
    ss: kolon konumları (artan), h0/h1: etriyenin dış sınırları.
    Döndürür: hooplar [(a, b)] (dış sınırlar), kenar kolon indisleri (etriye kısa kolları)."""
    n_seg = max(1, math.ceil((h1 - h0) / AYAR["etriye_max_uzunluk"] - 1e-9))
    n_seg = min(n_seg, max(1, len(ss) - 1))
    if n_seg == 1 or len(ss) < 3:
        return [(h0, h1)], {0, len(ss) - 1}
    # bölme kolonları: eşit etriye boylarına en yakın kolonlar
    idx = []
    for k in range(1, n_seg):
        hedef = h0 + k * (h1 - h0) / n_seg
        adaylar = [i for i in range(1, len(ss) - 1) if i not in idx]
        if adaylar:
            idx.append(min(adaylar, key=lambda i: abs(ss[i] - hedef)))
    idx = sorted(set(idx))
    kenar = {0, len(ss) - 1} | set(idx)
    e = de / 2 + det
    hooplar = []
    bas = h0
    for i in idx:
        hooplar.append((bas, ss[i] + e))
        bas = ss[i] - e
    hooplar.append((bas, h1))
    return hooplar, kenar


def ciroz_konumlari(kol, sabit, a_max, n_gerek):
    """Boyuna donatı kolonları (kol) arasından çiroz konacak olanları seçer: sabit kollar
    (etriye kısa kolları) arasında hiçbir aralık a_max'ı aşmayacak ve toplam kol sayısı en az
    n_gerek olacak şekilde EN AZ çiroz. Döndürür: çiroz konumları."""
    if AYAR.get("ciroz_her_kolon"):
        return [x for x in kol if all(abs(x - q) > 1 for q in sabit)]
    aday = [x for x in kol if all(abs(x - q) > 1 for q in sabit)]
    sec = []
    kollar_ = sorted(sabit)
    # (1) a ≤ a_max: her büyük aralığa, sol kolun a_max içindeki en uzak adayı eklenir
    degisti = True
    while degisti:
        degisti = False
        tum = sorted(kollar_ + sec)
        for a_, b_ in zip(tum[:-1], tum[1:]):
            if b_ - a_ > a_max + 1e-6:
                ic_ = [x for x in aday if a_ + 1 < x < b_ - 1 and x not in sec]
                if not ic_:
                    continue
                uygun = [x for x in ic_ if x - a_ <= a_max + 1e-6]
                sec.append(max(uygun) if uygun else min(ic_))
                degisti = True
                break
    # (2) sayı: en büyük aralığın ortasına en yakın aday
    while len(kollar_) + len(sec) < n_gerek:
        tum = sorted(kollar_ + sec)
        bos_ = [x for x in aday if x not in sec]
        if not bos_:
            break
        a_, b_ = max(zip(tum[:-1], tum[1:]), key=lambda t: t[1] - t[0])
        sec.append(min(bos_, key=lambda x: abs(x - (a_ + b_) / 2)))
    return sorted(sec)


def etriye_duzeni_dik(duzen, Lu, bw, de, det, n_gerek_y=0):
    c = AYAR["paspayi"]
    kol = sorted(duzen["xk"] + duzen["xt"])
    hooplar, kenar = etriye_bolumle(kol, c, Lu, de, det)
    sabit = sorted({kol[i] for i in kenar})
    bacak = sorted([ha + det / 2 for ha, hb in hooplar] + [hb - det / 2 for ha, hb in hooplar])
    ciroz_x = ciroz_konumlari(kol, sabit, 25 * det, n_gerek_y - 2 * len(hooplar) + len(sabit))
    ciroz_y = duzen["ciroz_y"]
    tum = sorted(set(round(x, 1) for x in sabit + ciroz_x))
    a_boy = max(np.diff(tum)) if len(tum) > 1 else 0.0
    ys = duzen.get("ys_ic", duzen["ys"])
    a_kal = max(np.diff(ys)) if ciroz_y else (bw - 2 * c - det)
    return dict(hooplar=hooplar, ciroz_x=ciroz_x, ciroz_y=ciroz_y, a_boy=float(a_boy),
                a_kal=float(a_kal), n_y=2 * len(hooplar) + len(ciroz_x), n_x=2 + len(ciroz_y),
                n_kolon=len(kol))


def etriye_sec(bw, Lu, duzen, de, kritik, m):
    """TBDY 7.6.5.2: Ø ≥ 8; a ≤ 25·Ø; kritik bölgede Ash ≥ 2/3·Denk.(7.1b), 50 ≤ s ≤
    min(150, 6Ø, bw/3); dışında s ≤ min(bw, 200)."""
    A = AYAR
    c = A["paspayi"]
    d0 = A["etriye_capi_kritik"] if kritik else A["etriye_capi_ust"]
    adaylar = [d for d in A["etriye_caplari"] if d >= max(8, d0)]
    s_max = min(150.0, 6 * de, bw / 3.0) if kritik else min(bw, 200.0)
    son = None
    gecerli = []
    for d in adaylar:
        r_ = _etriye_sec_d(bw, Lu, duzen, de, kritik, m, d, s_max)
        if r_ is None:
            lay = etriye_duzeni_dik(duzen, Lu, bw, de, d)
            son = (d, lay, max(lay["a_boy"], lay["a_kal"]))
            continue
        gecerli.append(r_)
    if gecerli:
        # en az enine donatı (kol alanı / aralık); eşitlikte küçük çap
        sec_ = min(gecerli, key=lambda r: ((r["n_y"] * bw + r["n_x"] * Lu) * alan(r["d"]) / r["s"],
                                            r["d"]))
        sec_["alternatifler"] = [(r["d"], r["s"], r["n_y"], r["n_x"]) for r in gecerli]
        return sec_
    d, lay, a_max = son if son else (adaylar[-1], etriye_duzeni_dik(duzen, Lu, bw, de, adaylar[-1]), 0)
    return dict(d=d, s=50.0, **lay, a_max=a_max,
                not_=f"UYARI: enine donatı koşulu sağlanamadı (a={a_max:.0f} mm) – çiroz ekleyin")


def _etriye_sec_d(bw, Lu, duzen, de, kritik, m, d, s_max):
    """Tek çap için: a ≤ 25Ø ve (kritikte) Ash koşulunu sağlayan en büyük s ve en az çiroz."""
    A = AYAR
    c = A["paspayi"]
    if True:
        lay = etriye_duzeni_dik(duzen, Lu, bw, de, d)
        a_max = max(lay["a_boy"], lay["a_kal"])
        if a_max > 25 * d + 1e-6:
            return None
        s = math.floor(s_max / A["s_yuvarlama"]) * A["s_yuvarlama"]
        if not kritik:
            lay["n_y_gerek"] = lay["n_y"]
            return dict(d=d, s=s, **lay, a_max=a_max, n_y_a=lay["n_y"],
                        not_=f"kritik dışı: s ≤ min(bw, 200); a={a_max:.0f} ≤ 25Ø={25 * d:.0f}; "
                             f"kol sayısı a koşulundan: kalınlık doğr. {lay['n_y']}")
        while s >= 50:
            bk_x = Lu - c
            bk_y = bw - 2 * c
            gerek_x = (2 / 3) * 0.075 * s * bk_x * m["fck"] / A["fywk"]
            gerek_y = (2 / 3) * 0.075 * s * bk_y * m["fck"] / A["fywk"]
            n_gy = math.ceil(gerek_x / alan(d) - 1e-9)
            lay = etriye_duzeni_dik(duzen, Lu, bw, de, d, n_gy)
            a_max = max(lay["a_boy"], lay["a_kal"])
            lay["n_y_gerek"] = n_gy
            if lay["n_y"] * alan(d) >= gerek_x and lay["n_x"] * alan(d) >= gerek_y \
                    and a_max <= 25 * d + 1e-6:
                n_a = etriye_duzeni_dik(duzen, Lu, bw, de, d)["n_y"]
                return dict(d=d, s=s, **lay, a_max=a_max, n_y_a=n_a, not_=(
                    f"kritik: s ≤ min(150, 6Ø, bw/3)={s_max:.0f}; a={a_max:.0f} ≤ 25Ø={25 * d:.0f}; "
                    f"Ash ≥ 2/3·Denk.(7.1b) [{lay['n_y']}×{alan(d):.0f}={lay['n_y'] * alan(d):.0f} ≥ "
                    f"{gerek_x:.0f} mm², {lay['n_x']}×{alan(d):.0f}={lay['n_x'] * alan(d):.0f} ≥ "
                    f"{gerek_y:.0f} mm²]; kalınlık doğr. gerekli kol: Ash için {n_gy}, "
                    f"a ≤ 25Ø için {n_a} -> {lay['n_y']} kol"))
            s -= A["s_yuvarlama"]
    return None


def ciroz_adimlari(sw, sh, kritik):
    """Özel deprem çirozu yoğunluğu (kritik ≥ 10/m², dışında ≥ 4/m²) sağlanacak şekilde
    planda her n_p. düşey, düşeyde her n_v. yatay donatı seviyesi. En az çirozlu düzen seçilir;
    şaşırtma için n_p = 2 tercih edilir."""
    gerek = AYAR["ciroz_yogunluk_kritik"] if kritik else AYAR["ciroz_yogunluk_normal"]
    en = None
    for n_p in (2, 1, 3, 4):
        for n_v in (1, 2, 3, 4):
            yog = 1e6 / (n_p * sw * n_v * sh)
            if yog + 1e-9 < gerek:
                continue
            anahtar = (-(n_p * n_v), 0 if n_p == 2 else 1)
            if en is None or anahtar < en[0]:
                en = (anahtar, n_p, n_v, yog)
    if en is None:
        return 1, 1, 1e6 / (sw * sh), gerek
    return en[1], en[2], en[3], gerek


def govde_ciroz(govde, dh, sh, sw, kritik=True):
    """Şaşırtmalı gövde çirozu. Planda her n_p. düşey donatıda (seviye A), bir sonraki çiroz
    seviyesinde yarım adım kaydırılmış (seviye B). Düşeyde her n_v. yatay donatı seviyesinde."""
    n_p, n_v, yog, gerek = ciroz_adimlari(sw, sh, kritik)
    xs = sorted({round(x, 3) for x, y, d in govde})
    xA = xs[0::n_p]
    xB = xs[n_p // 2::n_p] if n_p > 1 else xs
    return dict(d=dh, xA=xA, xB=xB, s_duz=n_v * sh, adet_m2=yog, adim=n_p, adim_v=n_v,
                gerek=gerek)


def kesme_carpani_12D():
    """Ve / Vd: kombinasyon zaten büyütülmüşse 1, değilse kesme_katsayi · D (1.2·D)."""
    a = _aktif()
    if a["sinirli"] or AYAR.get("kesme_komb_buyutulmus"):
        return 1.0
    return a["katsayi"] * float(AYAR["D"])


def kesme_notu():
    a = _aktif()
    tur = ("bağ kirişli (boşluklu) perde" if a["bag"] else "boşluksuz perde") + \
          f" – Ve ≤ {a['vmax']:g}·Ach·√fck"
    if a["sinirli"]:
        return f"Kesme: Ve = Vd – süneklik düzeyi sınırlı perde (TBDY 7.10); {tur}."
    if AYAR.get("kesme_komb_buyutulmus"):
        return ("Kesme: Ve = Vd – seçilen kesme kombinasyonlarında deprem etkisi zaten "
                f"{a['katsayi']:g}·D ile büyütülmüş kabul edildi; kod TEKRAR BÜYÜTMEDİ; {tur}.")
    return (f"Kesme: Ve = {a['katsayi']:g}·D·Vd = {a['katsayi']:g}×{AYAR['D']:g} = "
            f"{kesme_carpani_12D():.2f}·Vd ({tur}); Vd seçilen kesme kombinasyonlarından "
            f"(büyütülmemiş kabul edildi; βv·Mp/Md uygulanmadı). D = {AYAR['D']:g} (ayarlardan).")


def _kesme_notu_eski():
    if AYAR.get("kesme_komb_buyutulmus"):
        return ("Kesme: Ve = Vd – seçilen kesme kombinasyonlarında deprem etkisi zaten "
                f"{AYAR['kesme_katsayi']:g}·D ile büyütülmüş; kod TEKRAR BÜYÜTMEDİ "
                "(βv·Mp/Md de uygulanmadı).")
    return (f"Kesme: Ve = {AYAR['kesme_katsayi']:g}·D·Vd = {AYAR['kesme_katsayi']:g}×{AYAR['D']:g} = "
            f"{kesme_carpani_12D():.2f}·Vd; Vd seçilen kesme kombinasyonlarından (kombinasyonlar "
            f"büyütülmemiş kabul edildi; βv·Mp/Md uygulanmadı). D = {AYAR['D']:g} (ayarlardan).")


def kesme_tevzi(lw, bw, kat_kesme, Ve_carpan, m):
    """Her kat için 1.2D kombinasyonlarından Vd -> Ve -> yatay gövde donatısı."""
    Ach = bw * lw
    Vmax = _aktif()["vmax"] * Ach * math.sqrt(m["fck"])
    satirlar = []
    for kat, Vd in kat_kesme:
        Ve = Vd * Ve_carpan
        rho = max(0.0025, (Ve / Ach - 0.65 * m["fctd"]) / m["fywd"])
        sec = govde_sec(bw, rho, AYAR["s_govde_max"])
        if sec is None:
            d = AYAR["govde_caplari"][-1]
            sec = (d, 100.0, 2 * alan(d) / 100 / bw)
        Vr = Ach * (0.65 * m["fctd"] + sec[2] * m["fywd"])
        satirlar.append(dict(kat=kat, Vd=Vd, Ve=Ve, rho=rho, d=sec[0], s=sec[1], Vr=Vr,
                             ok=(Ve <= Vr * 1.0001) and (Ve <= Vmax), Vmax=Vmax))
    return satirlar


def kiris_bolgeleri_dik(lw, bw, Lu, duzen, de, etr, govde, kirisler, dw):
    """Dikdörtgen perdede kirişlerin bağlandığı yerlerde uç bölge benzeri etriyeli bölge.
    kirisler: [dict(u, v, b, ad)] – u perde ekseni boyunca, perde ortasından (mm)."""
    A = AYAR
    det = etr["d"]
    s_k = duzen["s_k"]
    s_hedef = max(eksen_min(de, de), min(duzen.get("s_kuy") or A["s_uc_bar_max"],
                                         A["s_uc_bar_max"], eksen_max(de, de)))
    araliklar, notlar = [], []
    for q in kirisler or []:
        x = q["u"] + lw / 2
        L = max(q["b"] + 2 * bw, 300.0)
        x0, x1 = max(x - L / 2, Lu), min(x + L / 2, lw - Lu)
        if x1 - x0 < 0.5 * L:
            notlar.append(f"Kiriş {q['ad']} uç bölgesine bağlanıyor (ayrı bölge gerekmedi).")
            continue
        araliklar.append([x0, x1, [q]])
    araliklar.sort()
    birlesik = []
    for a in araliklar:
        if birlesik and a[0] <= birlesik[-1][1]:
            birlesik[-1][1] = max(birlesik[-1][1], a[1])
            birlesik[-1][2] += a[2]
        else:
            birlesik.append(a)
    bolgeler, bars = [], []
    s_gmin = max(dw + max(25.0, dw), 50.0)
    for x0, x1, qs in birlesik:
        a, b = x0 + det + de / 2, x1 - det - de / 2
        n = max(2, math.ceil((b - a) / s_hedef - 1e-9) + 1)
        xs = list(np.linspace(a, b, n))
        ys = duzen.get("ys_ic", duzen["ys"])
        for x in xs:
            for y in ys:
                bars.append((x, y, de))
        hp, kenar = etriye_bolumle(xs, x0, x1, de, det)
        bolgeler.append(dict(x0=x0, x1=x1, xs=xs, ys=ys, kirisler=[q["ad"] for q in qs],
                             hooplar=hp, ciroz_x=[x for i, x in enumerate(xs) if i not in kenar],
                             b=max(q["b"] for q in qs),
                             aciklama=", ".join(f"{q['ad']} (b={q['b']:.0f})" for q in qs)
                             + f": bölge {x1 - x0:.0f} mm, {len(ys)}×{len(xs)}Ø{de}"))
    kalan = [g for g in govde
             if not any(z["x0"] - s_gmin / 2 <= g[0] <= z["x1"] + s_gmin / 2 for z in bolgeler)]
    return bolgeler, bars, kalan, notlar


def grup_tasarla(ad, lw, bw, kritik, talepler, kat_kesme, Ve_carpan, h_kat_max, m,
                 govde_ref=None, kirisler=None, kademe=0.0, kis=None):
    """talepler: [(N[N], M[Nmm])], kat_kesme: [(kat, Vd[N])]"""
    A = AYAR
    uyarilar, kontroller = [], []

    bw_min = max(200.0, h_kat_max / 20) if A["ozel_kosul_7613"] else max(250.0, h_kat_max / 16)
    kontroller.append(("lw/bw ≥ 6 (TBDY 7.6.1.1)", lw / bw, 6.0, lw / bw >= 6.0))
    kontroller.append((f"bw ≥ {bw_min:.0f} mm (TBDY 7.6.1.2/3)", bw, bw_min, bw >= bw_min - 1e-6))
    Nmax = max(n for n, _ in talepler)
    N_sinir = 0.35 * bw * lw * m["fck"]
    kontroller.append(("Nd,max ≤ 0.35·Ac·fck [kN]", Nmax / 1e3, N_sinir / 1e3, Nmax <= N_sinir))

    Lu = uc_bolge_uzunlugu(lw, bw, kritik, kademe)
    rho_min = rho_uc_min(kritik, kademe)
    As_uc_min = max(rho_min * bw * lw, 4 * alan(14))

    # gövde düşey: yalnız minimum (eğilme ihtiyacı uç bölgeye konur)
    dw, sw, _ = govde_sec(bw, 0.0025, A["s_govde_max"])

    # kesme tevzisi (kat kat) – grup çizimi için en elverişsiz kat esas
    ks = kesme_tevzi(lw, bw, kat_kesme, Ve_carpan, m)
    kr = max(ks, key=lambda r: (r["rho"], -r["s"]))
    dh, sh = kr["d"], kr["s"]
    kontroller.append(("Ve ≤ 0.85·Ach·√fck [kN] (TBDY 7.6.7)", kr["Ve"] / 1e3, kr["Vmax"] / 1e3,
                       kr["Ve"] <= kr["Vmax"]))
    kontroller.append(("Ve ≤ Vr [kN] (en elverişsiz kat)", kr["Ve"] / 1e3, kr["Vr"] / 1e3,
                       kr["Ve"] <= kr["Vr"] * 1.0001))
    if not all(r["ok"] for r in ks):
        uyarilar.append("Bazı katlarda kesme kontrolü sağlanmıyor – kat tablosuna bakın.")

    det_tahmin = A["etriye_capi_kritik"] if kritik else A["etriye_capi_ust"]
    kis = dict(kis or {})
    Lu = max(Lu, kis.get("Lu_min", 0.0))

    def dt_secenek(de):
        """Kuyruk/sınır çapı adayları: 14 ≤ dt ≤ de."""
        s_ = {d for d in A["uc_caplari"] if 14 <= d <= de and d in (14, 16, 20, de)}
        return sorted(d for d in s_ if d >= kis.get("dt_min", 0))

    def uygun(y, Lu_):
        du = y[3]
        if du["As"] < max(As_uc_min, kis.get("As_min", 0.0)) - 1e-6:
            return False
        return du["As"] <= A["rho_uc_max"] * Lu_ * bw

    def sirala(ad_):
        # önce toplam alan (%5 bant), sonra en uçta yoğunlaşma (küme payı), sonra az çubuk
        return sorted(ad_, key=lambda t: (math.floor(math.log(t[0]) / math.log(1.05)),
                                          -round(t[7], 2), t[1], -t[5]))

    def adaylar_uret(Lu_):
        """(As, n_bar, de, ny, ncol, s_kuy, dt, yoğunlaşma)"""
        ad_ = set()
        for de in A["uc_caplari"]:
            if de < kis.get("de_min", 0):
                continue
            nys = ny_aralik(bw, de, det_tahmin, dh)
            if not nys:
                continue
            ny_alt = max(min(A["uc_sira_min"], nys[-1]), kis.get("ny_min", 0))
            for ny in [n_ for n_ in nys if n_ >= ny_alt]:
                for dt in dt_secenek(de):
                    s_ler = sorted({min(s_, eksen_max(de, dt), eksen_max(dt, dt))
                                    for s_ in A["kuyruk_araliklari"]}, reverse=True)
                    for s_kuy in [s_ for s_ in s_ler if s_ >= eksen_min(de, dt) - 1e-6]:
                        for ncol in range(max(1, kis.get("ncol_min", 1)),
                                          A["uc_kume_kolon_max"] + 1):
                            y = yerlesim(lw, bw, Lu_, de, ny, ncol, dw, sw, det_tahmin, dh,
                                         govde_ref, s_kuy, dt)
                            if y is None:
                                continue
                            if not uygun(y, Lu_):
                                continue
                            du = y[3]
                            ad_.add((round(du["As"], 1), du["n_uc"], de, ny, ncol, s_kuy, dt,
                                     du["yogunlasma"]))
        return sirala(ad_)

    def dene(Lu_, aday, kolonlar=None):
        As_uc, nbar, de, ny, ncol, s_kuy, dt, _ = aday
        y = yerlesim(lw, bw, Lu_, de, ny, ncol, dw, sw, det_tahmin, dh, govde_ref, s_kuy, dt,
                     kolonlar)
        if y is None:
            return None
        uc, gov, s_g, duzen = y
        lk = LifKesit(lw, bw, uc, gov, m)
        dc = lk.talep_orani(talepler)
        return (duzen["As"], duzen["n_uc"], de, uc, gov, s_g, duzen, lk, float(dc.max()), Lu_)

    Lu_min = Lu
    secim = None
    # (1) alt kattan gelen kolonlara oturtma: üst kat çubukları alt kat çubuklarının üstünde
    ref = kis.get("kolon_ref")
    if ref:
        kol = ref["kolonlar"]
        for k_ in range(1, len(kol) + 1):
            son_d = ref["dt"] if k_ > ref["ncol"] else ref["de"]
            Lu_k = math.ceil((kol[k_ - 1] + det_tahmin + son_d / 2) / 10.0) * 10.0
            if Lu_k < Lu_min - 1e-6:
                continue
            aday_k = []
            for de in [d for d in A["uc_caplari"] if 14 <= d <= ref["de"]]:
                for dt in [d for d in dt_secenek(de) if d <= ref["dt"]]:
                    for ny in range(2, ref["ny"] + 1):
                        for ncol in range(1, min(ref["ncol"], k_) + 1):
                            y = yerlesim(lw, bw, Lu_k, de, ny, ncol, dw, sw, det_tahmin, dh,
                                         govde_ref, ref.get("s_kuy"), dt, kol[:k_])
                            if y is None or not uygun(y, Lu_k):
                                continue
                            du = y[3]
                            aday_k.append((round(du["As"], 1), du["n_uc"], de, ny, ncol,
                                           ref.get("s_kuy"), dt, du["yogunlasma"]))
            for aday in sirala(set(aday_k)):
                r = dene(Lu_k, aday, kol[:k_])
                if r and r[8] <= 1.0:
                    secim = r
                    break
            if secim:
                break
        if secim:
            uyarilar.append("Bilgi: uç bölge çubukları alt grubun çubuk konumlarına oturtuldu "
                            "(düz devam).")
    # (2) serbest tasarım: Lu minimumdan başlar, yetmezse 50 mm adımlarla büyür
    if secim is None:
        while secim is None and Lu <= 0.40 * lw + 1e-6:
            for aday in adaylar_uret(Lu):
                r = dene(Lu, aday)
                if r and r[8] <= 1.0:
                    secim = r
                    break
            if secim is None:
                Lu += A["Lu_yuvarlama"]
        if secim is not None and Lu > Lu_min:
            uyarilar.append(f"Bilgi: lif analizine göre Lu, {Lu_min:.0f} mm'den {Lu:.0f} mm'ye "
                            f"büyütüldü.")
    if secim is None:
        Lu = Lu_min
        ad_ = adaylar_uret(Lu)
        uyarilar.append("Eğilme+eksenel kuvvet uç bölge donatısıyla KARŞILANAMADI "
                        "(ρ ≤ 0.03, Lu ≤ 0.4lw). lw ve/veya bw artırılmalı.")
        if not ad_ and A["bindirme_araligi"]:
            A["bindirme_araligi"] = False
            try:
                ad_ = adaylar_uret(Lu)
                secim = dene(Lu, ad_[-1]) if ad_ else None
            finally:
                A["bindirme_araligi"] = True
            uyarilar.append("UYARI: kalınlık bindirmeli net aralığa yetmiyor – ekler kademeli "
                            "(şaşırtmalı) yapılmalı ya da bw artırılmalı.")
        else:
            secim = dene(Lu, ad_[-1]) if ad_ else None
        if secim is None:
            raise RuntimeError("Uç bölge için uygun donatı düzeni bulunamadı (kesit çok küçük).")

    As_uc, nbar, de, uc, gov, s_g, duzen, lk, oran, Lu = secim
    etr = etriye_sec(bw, Lu, duzen, min(de, duzen["dt"]), kritik, m)
    if etr["d"] != det_tahmin and not ref:
        y = yerlesim(lw, bw, Lu, de, duzen["ny"], duzen["ncol"], dw, sw, etr["d"], dh,
                     govde_ref, duzen["s_kuy"], duzen["dt"])
        if y is not None:
            uc, gov, s_g, duzen = y
            lk = LifKesit(lw, bw, uc, gov, m)
            oran = float(lk.talep_orani(talepler).max())
            etr = etriye_sec(bw, Lu, duzen, min(de, duzen["dt"]), kritik, m)
        else:
            uyarilar.append(f"UYARI: etriye Ø{etr['d']} ile düzen yeniden kurulamadı (aralık "
                            f"sınırı); çubuk konumları Ø{det_tahmin} etriyeye göredir.")

    kb, kb_bars, kb_not = [], [], []
    if AYAR["kiris_bolgesi"] and kirisler:
        kb, kb_bars, gov, kb_not = kiris_bolgeleri_dik(lw, bw, Lu, duzen, duzen["dt"], etr, gov,
                                                       kirisler, dw)
        if kb:
            lk = LifKesit(lw, bw, uc, gov + kb_bars, m)
            oran = float(lk.talep_orani(talepler).max())
    uyarilar += kb_not
    kontroller.append((f"Uç bölge As ≥ max({rho_min:.4f}·bw·lw, 4Ø14) [mm²]",
                       As_uc, As_uc_min, As_uc >= As_uc_min))
    kontroller.append(("Uç bölge ρ ≤ 0.03 (TBDY 7.6.5.1)", As_uc / (Lu * bw), A["rho_uc_max"],
                       As_uc / (Lu * bw) <= A["rho_uc_max"] + 1e-9))
    ar = duzen.get("aralik") or {}
    if ar:
        kontroller.append((f"Uç bölge en küçük net aralık, bindirmede [mm] ≥ "
                           f"max({A['uc_net_min']:.0f}, Ø, 4/3·Dmax)", ar["net_min_b"],
                           net_min_etkin(de), ar["ok_min"]))
        kontroller.append(("Uç bölge komşu donatı en büyük net aralık [mm]", ar["net_max"],
                           A["uc_net_max"], ar["ok_max"]))
    kontroller.append(("Md/Mr lif analizi (en elverişsiz)", oran, 1.0, oran <= 1.0))

    bw_oneri = max(bw_min, lw / 6 if lw / bw < 6 else 0,
                   kr["Ve"] / (0.85 * lw * math.sqrt(m["fck"])),
                   Nmax / (0.35 * lw * m["fck"]))

    out = dict(pier=ad, kritik=kritik, kademe=kademe, lw=lw, bw=bw, Lu=Lu, de=de, dt=duzen["dt"],
               nbar_uc=nbar, kis=kis,
                As_uc=As_uc, rho_uc_min=rho_min, uc=uc, govde=gov, duzen=duzen,
                dw=dw, sw=sw, sw_gercek=s_g, dh=dh, sh=sh, rho_sh_gerek=kr["rho"],
                gciroz=govde_ciroz(gov, dh, sh, s_g, kritik),
                etr=etr, Ve=kr["Ve"], Vd=kr["Vd"], Vr=kr["Vr"], kesme=ks, Md_Mr=oran,
                lif=lk, talepler=talepler, kontroller=kontroller,
                uyarilar=uyarilar, bw_oneri=bw_oneri, Nmax=Nmax,
                kiris_bolgeleri=kb, kiris_bars=kb_bars)
    out["yatay"] = dict(zip(("cubuklar", "firketeler"), yatay_cubuklar_dik(out, m)))
    return out


def pier_tasarla(p, m):
    z0 = p.katlar[0].z_alt
    Hw = p.katlar[-1].z_ust - z0
    lw0 = p.katlar[0].lw
    Hcr = min(max(lw0, Hw / 6.0), 2 * lw0)
    notlar = [f"Hw = {Hw / 1000:.2f} m, lw(taban) = {lw0 / 1000:.2f} m, Hw/lw = {Hw / lw0:.2f}",
              f"Hcr = min(max(lw, Hw/6), 2lw) = {Hcr / 1000:.2f} m (TBDY Denk. 7.15)",
              f"Lif modeli: lif boyutu {AYAR['lif_boyutu']:.0f} mm, beton TS 500 parabol-"
              f"dikdörtgen (εc0={AYAR['eps_c0']}, εcu={AYAR['eps_cu']}), çelik elasto-plastik fyd; "
              f"yalnız P–M3."]

    kritik_ve_kademe(p.katlar, z0, Hcr)
    notlar.append(f"Kademeli geçiş (TBDY 7.6.5.1): kritik bölge üstündeki {AYAR['kademe_kat']} "
                  f"katta Lu ve ρmin kritik değerden normal değere doğrusal azaltıldı.")

    # kesme kombinasyonları (1.2D)
    kk = set(p.kesme_kombs or [])
    if not kk:
        kk = {f[0] for k in p.katlar for f in k.kuvvetler}
        notlar.append("UYARI: 1.2D'li kombinasyon bulunamadı; kesmede tüm kombinasyonlar kullanıldı.")
    else:
        notlar.append("Kesme tevzisi kombinasyonları: " + ", ".join(sorted(kk)))

    pm_k = set(p.pm_kombs or []) or {f[0] for k in p.katlar for f in k.kuvvetler}
    notlar.append("P–M talep kombinasyonları: " + ", ".join(sorted(pm_k)[:12])
                  + (f" … (+{len(pm_k) - 12})" if len(pm_k) > 12 else ""))

    def kat_Vd(k):
        v = [abs(f[4]) for f in k.kuvvetler if f[0] in kk]
        return max(v) * 1e3 if v else 0.0

    # Ve büyütme katsayısı – taban kesiti, 1.2D kombinasyonlarından Md
    taban = p.katlar[0]
    tb = [f for f in taban.kuvvetler if f[0] in kk and f[1].lower().startswith("bot")] \
        or [f for f in taban.kuvvetler if f[0] in kk] or taban.kuvvetler
    Md_t = max(tb, key=lambda f: abs(f[3]))
    if AYAR["kesme_yontemi"].upper() == "1.2D":
        Ve_carpan = kesme_carpani_12D()
        notlar.append(kesme_notu())
    elif Hw / lw0 > 2.0:
        Ve_carpan = None
        notlar.append("Hw/lw > 2: Ve = βv·(Mp/Md)·Vd (TBDY 7.6.6.3), Mp ≈ 1.4·Mr (lif analizi)")
    else:
        Ve_carpan = 1.0
        notlar.append("VARSAYIM: Hw/lw ≤ 2 -> Ve = Vd alındı; yönetmelik maddesini kontrol edin.")

    gruplar = []
    for k in p.katlar:
        anahtar = (k.kritik, round(k.kademe, 3), round(k.lw), round(k.bw),
                   tuple(sorted((round(q["u"]), round(q["b"])) for q in (k.kiris or []))))
        if gruplar and gruplar[-1]["anahtar"] == anahtar:
            gruplar[-1]["katlar"].append(k)
        else:
            gruplar.append(dict(anahtar=anahtar, katlar=[k]))

    def kayma(kL, kU):
        dX = kU.cg_alt[0] - kL.cg_ust[0]
        dY = kU.cg_alt[1] - kL.cg_ust[1]
        a = math.radians(kL.aci)
        return dX * math.cos(a) + dY * math.sin(a), -dX * math.sin(a) + dY * math.cos(a)

    sonuc_gruplar = []
    for gi, g in enumerate(gruplar):
        kr, kd, lw, bw, _ = g["anahtar"]
        kirisler = list({(round(q["u"]), round(q["b"])): q for k in g["katlar"]
                         for q in (k.kiris or [])}.values())
        ref = None
        if sonuc_gruplar:   # alt grubun gövde donatı konumları bu grubun koordinatında
            gL = sonuc_gruplar[-1]
            boy_, _ = kayma(gL["_katlar"][-1], g["katlar"][0])
            ref = [x - (gL["lw"] / 2 + boy_) + lw / 2 for x, y, d in gL["govde"]]
        talepler = talep_azalt_pm([(f[2] * 1e3, f[3] * 1e6) for k in g["katlar"]
                                   for f in k.kuvvetler if f[0] in pm_k])
        kat_kesme = [(k.kat, kat_Vd(k)) for k in g["katlar"]]
        print(f"    grup {gi + 1}/{len(gruplar)} ({g['katlar'][0].kat}–{g['katlar'][-1].kat}, "
              f"{len(talepler)} talep noktası) ...", flush=True)
        h_max = max(k.z_ust - k.z_alt for k in g["katlar"])
        if Ve_carpan is None:
            on = grup_tasarla(p.ad, lw, bw, kr, talepler, kat_kesme, 1.0, h_max, m, ref,
                              kirisler, kd)
            Mr_t = float(on["lif"].Mr(Md_t[2] * 1e3, Md_t[3])[0])
            Mr_t = 0.0 if math.isnan(Mr_t) else Mr_t
            Md_abs = abs(Md_t[3]) * 1e6
            oran = AYAR["Mp_Mr_orani"] * Mr_t / Md_abs if Md_abs > 0 else 1.0
            Ve_carpan = AYAR["beta_v"] * max(oran, 1.0)
            txt = (f"Taban ({Md_t[0]}): Md={Md_abs / 1e6:.0f} kNm, Mr={Mr_t / 1e6:.0f} kNm, "
                   f"Mp/Md={oran:.2f} -> βv·Mp/Md={Ve_carpan:.2f}")
            if AYAR["ve_ust_sinir_RD"] and Ve_carpan > AYAR["R"] / AYAR["D"]:
                Ve_carpan = AYAR["R"] / AYAR["D"]
                txt += f" -> (R/D)={Ve_carpan:.2f} ile sınırlandı (VARSAYIM)"
            notlar.append(txt)
        s = grup_tasarla(p.ad, lw, bw, kr, talepler, kat_kesme, Ve_carpan, h_max, m, ref,
                         kirisler, kd)
        s["katlar"] = [k.kat for k in g["katlar"]]
        s["z"] = (g["katlar"][0].z_alt, g["katlar"][-1].z_ust)
        s["etiket"] = f"G{gi + 1}"
        s["_katlar"] = g["katlar"]
        s["_param"] = dict(lw=lw, bw=bw, kr=kr, talepler=talepler, kat_kesme=kat_kesme,
                           h_max=h_max, kirisler=kirisler, kd=kd)
        sonuc_gruplar.append(s)

    # ---- katlar arası tutarlılık -------------------------------------------------------
    def yeniden(i, kis, ref_gov):
        g0 = sonuc_gruplar[i]
        P_ = g0["_param"]
        s_ = grup_tasarla(p.ad, P_["lw"], P_["bw"], P_["kr"], P_["talepler"], P_["kat_kesme"],
                          Ve_carpan, P_["h_max"], m, ref_gov, P_["kirisler"], P_["kd"], kis)
        for k_ in ("katlar", "z", "etiket", "_katlar", "_param"):
            s_[k_] = g0[k_]
        sonuc_gruplar[i] = s_
        return s_

    def gov_ref(i):
        if i == 0:
            return None
        gL = sonuc_gruplar[i - 1]
        gU = sonuc_gruplar[i]
        boy_, _ = kayma(gL["_katlar"][-1], gU["_katlar"][0])
        return [x - (gL["lw"] / 2 + boy_) + gU["lw"] / 2 for x, y, d in gL["govde"]]

    def ayni_kesit(gL, gU):
        boy_, kal_ = kayma(gL["_katlar"][-1], gU["_katlar"][0])
        return abs(gL["lw"] - gU["lw"]) < 1 and abs(boy_) < 1

    # (a) yukarıdan aşağı: alt grup, üst gruptan zayıf olamaz (Lu, As, çap, sıra, kolon)
    for i in range(len(sonuc_gruplar) - 2, -1, -1):
        gL, gU = sonuc_gruplar[i], sonuc_gruplar[i + 1]
        if abs(gL["lw"] - gU["lw"]) > 1:
            continue
        ayni_t = abs(gL["bw"] - gU["bw"]) < 1
        dL, dU = gL["duzen"], gU["duzen"]
        zayif = (gL["Lu"] < gU["Lu"] - 1 or gL["As_uc"] < gU["As_uc"] - 1 or gL["de"] < gU["de"]
                 or (ayni_t and (dL["ny"] < dU["ny"] or dL["ncol"] < dU["ncol"]
                                 or gL["dt"] < gU["dt"])))
        if not zayif:
            continue
        kis = dict(Lu_min=gU["Lu"], As_min=gU["As_uc"], de_min=gU["de"])
        if ayni_t:
            kis.update(ny_min=dU["ny"], ncol_min=dU["ncol"], dt_min=gU["dt"])
        print(f"    {gL['etiket']} üstteki {gU['etiket']}'den zayıf çıktı -> yeniden tasarlanıyor",
              flush=True)
        eski = f"Lu={gL['Lu']:.0f}, As={gL['As_uc']:.0f}"
        yeni = yeniden(i, kis, gov_ref(i))
        if abs(yeni["As_uc"] - gL["As_uc"]) > 0.01 * gL["As_uc"] or abs(yeni["Lu"] - gL["Lu"]) > 1:
          yeni["uyarilar"].append(f"Bilgi: üstteki {gU['etiket']} daha güçlü çıktığı için bu grup en "
                                f"az onun kadar donatıldı ({eski} -> Lu={yeni['Lu']:.0f}, "
                                f"As={yeni['As_uc']:.0f}).")
    # (b) aşağıdan yukarı: üst grubun uç bölge çubukları alt grubun çubuklarının üstüne oturur
    for i in range(1, len(sonuc_gruplar)):
        gL, gU = sonuc_gruplar[i - 1], sonuc_gruplar[i]
        if not ayni_kesit(gL, gU):        # kalınlık değişse de kolonlar aynı x'te kalır
            continue
        dL = gL["duzen"]
        kis = dict(gU.get("kis") or {})
        kis["kolon_ref"] = dict(kolonlar=dL["kolonlar"], de=gL["de"], dt=gL["dt"], ny=dL["ny"],
                                ncol=dL["ncol"], s_kuy=dL.get("s_kuy"))
        try:
            yeni = yeniden(i, kis, gov_ref(i))
        except Exception:
            continue
        if yeni["Lu"] > gL["Lu"] + 1:          # olmamalı; güvenlik
            yeni["uyarilar"].append("UYARI: üst grup uç bölgesi alt gruptan uzun.")
    # geçişlerin sınıflandırılması için gövde referansı güncel
    notlar.append("Katlar arası tutarlılık: alt grup üst gruptan zayıf olamaz (Lu, As, çap, sıra "
                  "ve kolon sayısı); üst grubun uç bölge çubukları alt grubun çubuk konumlarına "
                  "oturtulur (mümkünse düz devam).")

    # ---- kat geçişleri: her grubun üstündeki gruba donatı aktarımı (düz / kırım / filiz)
    for gL, gU in zip(sonuc_gruplar[:-1], sonuc_gruplar[1:]):
        kL, kU = gL["_katlar"][-1], gU["_katlar"][0]
        boy, kal = kayma(kL, kU)
        gL["gecis"] = gecis_analizi(gL, gU, boy, kal, m)
        gL["gecis"]["ust_etiket"] = gU["etiket"]
        gL["gecis"]["kat"] = f"{kL.kat} → {kU.kat}"
    if len(sonuc_gruplar) > 1:
        notlar.append("Kat geçişlerinde üst grubun donatıları alt grubun planında gösterildi "
                      "(düz devam / kırım ≤ 1/6 / filiz ekimi). TBDY 7.6.5.1: uç bölge geometri ve "
                      "donatı geçişi üç kat boyunca kademeli yapılmalıdır.")
    return dict(pier=p.ad, Hw=Hw, Hcr=Hcr, notlar=notlar, gruplar=sonuc_gruplar,
                Ve_carpan=Ve_carpan)


def kenetlenme(d, m):
    """TS 500 Denk. 9.1 (nervürlü): lb = 0.12·fyd/fctd·Ø ≥ 20Ø.
    Bindirme (Denk. 9.2): l0 = α1·lb ≥ max(lb, 300)."""
    lb = max(0.12 * m["fyd"] / m["fctd"] * d, 20 * d)
    l0 = max(AYAR["alfa1_bindirme"] * lb, lb, 300.0)
    return lb, l0


def gecis_analizi(gL, gU, boy, kal, m):
    """Üst grubun (gU) boyuna donatılarını alt grubun (gL) koordinatlarına taşır ve her birini
    sınıflandırır:
      DÜZ   : alttaki bir donatının üstünde (kaçıklık ≤ duz_tolerans) – düz devam
      KIRIM : alttaki donatı birleşim bölgesinde kırılarak üst konuma getirilebilir
              (kaçıklık ≤ birlesim_yuksekligi × 1/6)
      FİLİZ : karşılayan alt donatı yok -> alt perdeye filiz ekimi
    Alttaki eşleşmeyen donatılar üstte devam etmez (döşemede kenetlenerek biter).
    boy, kal: üst kesit ağırlık merkezinin alt kesite göre kayması (mm, perde yerel ekseninde)."""
    A = AYAR
    lwL, bwL, lwU, bwU = gL["lw"], gL["bw"], gU["lw"], gU["bw"]

    def tasi(x, y):
        return lwL / 2 + boy + (x - lwU / 2), bwL / 2 + kal + (y - bwU / 2)

    alt = [(x, y, d) for x, y, d in gL["uc"] + gL["govde"] + gL.get("kiris_bars", [])]
    ust = [tasi(x, y) + (d,) for x, y, d in gU["uc"] + gU["govde"] + gU.get("kiris_bars", [])]
    e_max = A["birlesim_yuksekligi"] * A["kirim_egim_max"]
    ciftler = []
    for j, (xu, yu, du) in enumerate(ust):
        for i, (xl, yl, dl) in enumerate(alt):
            e = math.hypot(xu - xl, yu - yl)
            if e <= e_max + 1e-6:
                ciftler.append((e, i, j))
    ciftler.sort()
    alt_bos, ust_bos = set(range(len(alt))), set(range(len(ust)))
    duz, kirim = [], []
    for e, i, j in ciftler:
        if i in alt_bos and j in ust_bos:
            alt_bos.discard(i)
            ust_bos.discard(j)
            xl, yl, dl = alt[i]
            xu, yu, du = ust[j]
            (duz if e <= A["duz_tolerans"] else kirim).append(
                dict(xl=xl, yl=yl, dl=dl, xu=xu, yu=yu, du=du, e=e))
    filiz, disarida = [], 0
    c = A["paspayi"]
    for j in sorted(ust_bos):
        xu, yu, du = ust[j]
        ic = (c <= xu <= lwL - c) and (c <= yu <= bwL - c)
        disarida += (not ic)
        lb, l0 = kenetlenme(du, m)
        filiz.append(dict(xu=xu, yu=yu, du=du, gomulme=A["filiz_gomulme_katsayi"] * lb,
                          bindirme=l0, alt_kesitte=ic))
    biten = [dict(x=alt[i][0], y=alt[i][1], d=alt[i][2]) for i in sorted(alt_bos)]
    # bindirme boyları (çaplara göre)
    caplar = sorted({b["du"] for b in duz + kirim} | {f["du"] for f in filiz}
                    | {b["dl"] for b in duz + kirim})
    boylar = {d: kenetlenme(d, m) for d in caplar}
    ust_kontur = [tasi(0, 0), tasi(lwU, 0), tasi(lwU, bwU), tasi(0, bwU)]
    uyari = []
    if disarida:
        uyari.append(f"{disarida} filiz alt kesitin dışına düşüyor – gömülemez, detay gerekli!")
    return dict(duz=duz, kirim=kirim, filiz=filiz, biten=biten, e_max=e_max, boylar=boylar,
                ust_kontur=ust_kontur, kayma=(boy, kal), uyari=uyari,
                ozet=(f"üst {gU['etiket']}: {len(duz)} düz, {len(kirim)} kırım "
                      f"(e ≤ {e_max:.0f} mm, max {max([k['e'] for k in kirim], default=0):.0f}), "
                      f"{len(filiz)} filiz ekimi, alttan {len(biten)} donatı biter"))


# =====================================================================================
# 7) ÇİZİM – PNG (matplotlib)
# =====================================================================================
def uc_txt(g):
    """Uç bölge donatısı: küme çapı ve kuyruk/sınır çapı ayrı."""
    du = g.get("duzen")
    if du is None or du.get("dt", g["de"]) == g["de"]:
        return f"{g['nbar_uc']}Ø{g['de']}"
    n_k = du.get("n_kume", du["ny"] * du["ncol"])
    return f"{n_k}Ø{g['de']} + {g['nbar_uc'] - n_k}Ø{du['dt']}"


def _metin_donati(g):
    return dict(
        uc=f"{_poz_str(g, 'uc')}{uc_txt(g)} (her uçta)",
        uc_d=(f"uç yüzde {g['duzen']['ny']}, küme {g['duzen']['ncol']} kolon "
              f"({g['duzen'].get('n_kume', 0)} çubuk)"
              + (f" + {g['duzen']['n_kuyruk']} kuyruk (sınırda {g['duzen'].get('ny_ic', 2)})"
                 if g['duzen']['xt'] else "")),
        etr=(f"{_poz_str(g, 'etr')}Etriye Ø{g['etr']['d']}/{g['etr']['s']:.0f} + {_poz_str(g, 'ciroz')}"
             f"çiroz Ø{g['etr']['d']}/{g['etr']['s']:.0f}"
             f" + kol: kalınlık doğr. {g['etr']['n_y']}, boy doğr. {g['etr']['n_x']}"),
        gd=f"{_poz_str(g, 'gd')}Gövde düşey Ø{g['dw']}/{g['sw_gercek']:.0f} (2 yüz)",
        gy=(f"{_poz_str(g, 'gy')}Gövde yatay Ø{g['dh']}/{g['sh']:.0f} (2 yüz = 2 kol)"
            + (" + uçta U-firkete" if AYAR["yatay_uc_detay"] == "firkete" else "")),
        gc=(f"{_poz_str(g, 'gc')}Özel deprem çirozu Ø{g['gciroz']['d']} şaşırtmalı: planda her {g['gciroz']['adim']}. düşey, "
            f"düşeyde /{g['gciroz']['s_duz']:.0f} ({g['gciroz']['adet_m2']:.1f} adet/m² ≥ "
            f"{g['gciroz']['gerek']:.0f})"),
    )


GECIS_RENK = dict(duz="#1e8449", kirim="#e67e22", filiz="#c2185b", biten="#7f8c8d",
                  kontur="#8e44ad")


def gecis_ciz(ax, gc, detay=False):
    """Üst grubun donatılarını alt grubun planına işler."""
    import matplotlib.patches as mp
    R = GECIS_RENK
    k = gc["ust_kontur"] + [gc["ust_kontur"][0]]
    ax.plot([p[0] for p in k], [p[1] for p in k], color=R["kontur"], ls=(0, (6, 3)),
            lw=1.3 if detay else 0.9, zorder=5)
    r_ek = 9 if detay else 14
    for b in gc["duz"]:
        ax.add_patch(mp.Circle((b["xu"], b["yu"]), b["du"] / 2 + r_ek, fc="none", ec=R["duz"],
                               lw=1.2, zorder=6))
    for b in gc["kirim"]:
        ax.annotate("", (b["xu"], b["yu"]), (b["xl"], b["yl"]),
                    arrowprops=dict(arrowstyle="-|>", color=R["kirim"], lw=1.2,
                                    mutation_scale=8 if detay else 5), zorder=6)
        ax.add_patch(mp.Circle((b["xu"], b["yu"]), b["du"] / 2, fc="white", ec=R["kirim"],
                               lw=1.4, zorder=6))
    for f in gc["filiz"]:
        h = f["du"] / 2 + (8 if detay else 12)
        ax.add_patch(mp.Rectangle((f["xu"] - h, f["yu"] - h), 2 * h, 2 * h, fc="none",
                                  ec=R["filiz"], lw=1.5, zorder=6))
        ax.plot([f["xu"]], [f["yu"]], marker="o", ms=3 if detay else 2, color=R["filiz"], zorder=7)
    for b in gc["biten"]:
        h = b["d"] / 2 + 4
        ax.plot([b["x"] - h, b["x"] + h], [b["y"] - h, b["y"] + h], color=R["biten"], lw=1.2,
                zorder=6)
        ax.plot([b["x"] - h, b["x"] + h], [b["y"] + h, b["y"] - h], color=R["biten"], lw=1.2,
                zorder=6)


def gecis_lejant(ax, gc, x, y):
    R = GECIS_RENK
    satir = [(R["kontur"], f"- - üst kesit ({gc['ust_etiket']}, {gc['kat']})"),
             (R["duz"], f"○ düz devam: {len(gc['duz'])}"),
             (R["kirim"], f"→ kırım (e ≤ {gc['e_max']:.0f} mm, eğim ≤ 1/6): {len(gc['kirim'])}"),
             (R["filiz"], f"□ filiz ekimi: {len(gc['filiz'])}"),
             (R["biten"], f"× üstte devam etmeyen: {len(gc['biten'])}")]
    for i, (renk, t) in enumerate(satir):
        sx, sy = (x, y) if i == 0 else (x + (0.0 if i < 3 else 0.5), y - 0.33 * (1 + (i - 1) % 2))
        ax.text(sx, sy, t, color=renk, fontsize=8.5, transform=ax.transAxes, va="top",
                weight="bold" if i == 0 else None)


def plan_ciz(ax, g, detay=False):
    import matplotlib.patches as mp
    lw, bw, Lu = g["lw"], g["bw"], g["Lu"]
    c = AYAR["paspayi"]
    det = g["etr"]["d"]
    t = _metin_donati(g)
    fs = 9 if detay else 7.5

    ax.add_patch(mp.Rectangle((0, 0), lw, bw, fc="#eeeeee", ec="black", lw=1.4, zorder=1))
    for x0 in (0, lw - Lu):
        ax.add_patch(mp.Rectangle((x0, 0), Lu, bw, fc="#fde8d6", ec="none", zorder=1.5,
                                  hatch="///" if not detay else None, alpha=0.8))
    for xb in (Lu, lw - Lu):
        ax.plot([xb, xb], [0, bw], ls="--", color="#c0392b", lw=1, zorder=2)

    # yatay gövde donatısı (+ uçta U-firkete)
    for cb in g["yatay"]["cubuklar"]:
        ax.plot([p_[0] for p_ in cb["pts"]], [p_[1] for p_ in cb["pts"]], color="#2e86c1",
                lw=1.0 if detay else 0.7, zorder=2)
    for fk in g["yatay"]["firketeler"]:
        ax.plot([p_[0] for p_ in fk["pts"]], [p_[1] for p_ in fk["pts"]], color="#117a65",
                lw=1.3 if detay else 0.8, zorder=2.5)
    # etriyeler (uzun uç bölgelerde iç içe, bir kolon bindirmeli)
    for i_h, (ha, hb) in enumerate(g["etr"]["hooplar"]):
        for a_, b_ in ((ha, hb), (lw - hb, lw - ha)):
            off = det * 0.6 * (i_h % 2)          # bindirmeli etriyeler ayırt edilsin
            ax.add_patch(mp.FancyBboxPatch((a_ + det / 2, c + det / 2 + off), b_ - a_ - det,
                                           bw - 2 * c - det - 2 * off,
                                           boxstyle=f"round,pad=0,rounding_size={det * 2}",
                                           fc="none", ec="#c0392b", lw=1.6 if detay else 1.0,
                                           zorder=3))
    # çirozlar: kalınlık boyunca ve iç sıralar için boy boyunca
    for x in g["etr"]["ciroz_x"]:
        for xx in (x, lw - x):
            ax.plot([xx, xx], [c + det / 2, bw - c - det / 2], color="#c0392b",
                    lw=1.2 if detay else 0.8, zorder=3)
    for y, xa, xb in g["etr"]["ciroz_y"]:
        for a_, b_ in ((c + det / 2, xb), (lw - xb, lw - c - det / 2)):
            ax.plot([a_, b_], [y, y], color="#c0392b", lw=1.2 if detay else 0.8, zorder=3)
    # kiriş bağlantı bölgeleri
    for z in g.get("kiris_bolgeleri", []):
        ax.add_patch(mp.Rectangle((z["x0"], 0), z["x1"] - z["x0"], bw, fc="#d6eaf8", ec="#2471a3",
                                  ls="--", lw=0.8, zorder=1.6))
        for i_h, (ha, hb) in enumerate(z["hooplar"]):
            off = det * 0.6 * (i_h % 2)
            ax.add_patch(mp.Rectangle((ha + det / 2, c + det / 2 + off), hb - ha - det,
                                      bw - 2 * c - det - 2 * off, fc="none", ec="#c0392b",
                                      lw=1.4 if detay else 0.9, zorder=3))
        for x in z["ciroz_x"]:
            ax.plot([x, x], [c + det / 2, bw - c - det / 2], color="#c0392b", lw=0.8, zorder=3)
        for y in z["ys"][1:-1]:
            ax.plot([z["x0"] + det / 2, z["x1"] - det / 2], [y, y], color="#c0392b", lw=0.8,
                    zorder=3)
        for x in z["xs"]:
            for y in z["ys"]:
                ax.add_patch(mp.Circle((x, y), g["de"] / 2, fc="black", zorder=4))
        if not detay:
            ax.text((z["x0"] + z["x1"]) / 2, bw + 60,
                    "KİRİŞ " + ", ".join(z["kirisler"]) + f"\n{len(z['ys'])}×{len(z['xs'])}Ø{g['de']}",
                    ha="center", va="bottom", fontsize=fs, color="#2471a3", weight="bold")
    # gövde çirozları (şaşırtmalı): seviye A düz, seviye B kesikli
    gc = g["gciroz"]
    for xs_, ls_ in ((gc["xA"], "-"), (gc["xB"], (0, (3, 2)))):
        for x in xs_:
            ax.plot([x, x], [c + g["dh"] / 2, bw - c - g["dh"] / 2], color="#7d3c98", ls=ls_,
                    lw=1.1 if detay else 0.6, zorder=3)
    # donatılar
    _daire_topla(ax, [b[:3] for b in g["uc"]], "black", "black")
    _daire_topla(ax, g["govde"], "#2e86c1", "#1b4f72")
    if "gecis" in g:
        gecis_ciz(ax, g["gecis"], detay)

    def olcu(x1, x2, y, yazi, dikey=False):
        if dikey:
            ax.annotate("", (y, x1), (y, x2), arrowprops=dict(arrowstyle="<->", lw=0.7))
            ax.text(y - 20, (x1 + x2) / 2, yazi, rotation=90, ha="right", va="center",
                    fontsize=fs)
        else:
            ax.annotate("", (x1, y), (x2, y), arrowprops=dict(arrowstyle="<->", lw=0.7))
            ax.text((x1 + x2) / 2, y - 25, yazi, ha="center", va="top", fontsize=fs)

    if not detay:
        olcu(0, Lu, -120, f"Lu={Lu:.0f}")
        olcu(lw - Lu, lw, -120, f"Lu={Lu:.0f}")
        olcu(Lu, lw - Lu, -120, f"{lw - 2 * Lu:.0f}")
        olcu(0, lw, -330, f"lw = {lw:.0f}")
        olcu(0, bw, -80, f"bw={bw:.0f}", dikey=True)
        ax.text(0, bw + 60, "UÇ BÖLGE\n" + t["uc"] + "\n" + t["uc_d"], ha="left",
                va="bottom", fontsize=fs, color="#922b21", weight="bold")
        ax.text(lw, bw + 60, "UÇ BÖLGE\n(simetrik)", ha="right",
                va="bottom", fontsize=fs, color="#922b21", weight="bold")
        ax.text(lw / 2, -450, t["gd"] + "   |   " + t["gy"], ha="center", va="top",
                fontsize=fs, color="#1b4f72")
        ax.text(lw / 2, -570, t["gc"] + "  (düz: seviye A, kesikli: bir sonraki seviye B)",
                ha="center", va="top", fontsize=fs, color="#7d3c98")
        ax.set_xlim(-250, lw + 150)
        ax.set_ylim(-710, bw + 440)
    else:
        xmax = min(lw / 2, Lu + max(700, 3 * g["sw_gercek"]))
        olcu(0, Lu, -90, f"Lu = {Lu:.0f}")
        olcu(0, bw, -60, f"bw = {bw:.0f}", dikey=True)
        x_ilk = g["uc"][0][0]
        ax.annotate(t["uc"] + " – " + t["uc_d"], (x_ilk, bw - g["uc"][0][1]), (x_ilk + 40, bw + 170), fontsize=fs,
                    color="black", arrowprops=dict(arrowstyle="-", lw=0.6))
        ax.annotate(t["etr"].replace(" + kol", "\nkol"), (Lu - c - det, bw - c),
                    (Lu * 0.45, bw + 60), fontsize=fs,
                    color="#922b21", arrowprops=dict(arrowstyle="-", lw=0.6, color="#922b21"))
        if g["govde"]:
            gx, gy_, _ = g["govde"][0]
            ax.annotate(t["gd"], (gx, gy_), (Lu + 60, -150), fontsize=fs, color="#1b4f72",
                        arrowprops=dict(arrowstyle="-", lw=0.6, color="#1b4f72"))
        ax.annotate(t["gy"], (xmax - 100, bw - c - g["dh"] / 2), (xmax * 0.62, bw + 250),
                    fontsize=fs, color="#1b4f72",
                    arrowprops=dict(arrowstyle="-", lw=0.6, color="#1b4f72"))
        ax.text(Lu + 15, bw / 2, "uç bölge\nsınırı", fontsize=fs - 1.5, color="#c0392b",
                va="center")
        if gc["xA"] and gc["xA"][0] < xmax:
            ax.annotate(t["gc"].replace(" şaşırtmalı: ", " şaşırtmalı\n"), (gc["xA"][0], bw / 2),
                        (Lu + 60, -290), fontsize=fs - 0.5, color="#7d3c98",
                        arrowprops=dict(arrowstyle="-", lw=0.6, color="#7d3c98"))
        ax.set_xlim(-200, xmax)
        ax.set_ylim(-380, bw + 330)
    ax.set_aspect("equal")
    ax.axis("off")


def png_ciz(ps, yol):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.gridspec import GridSpec, GridSpecFromSubplotSpec

    gr = ps["gruplar"]
    n = len(gr)
    fig = plt.figure(figsize=(16.5, 9.6 * n))
    gs_d = GridSpec(n, 1, figure=fig, hspace=0.10)
    for i, g in enumerate(gr):
        gs = GridSpecFromSubplotSpec(3, 2, subplot_spec=gs_d[i], height_ratios=[1.0, 1.75, 0.30],
                                     width_ratios=[1.25, 1], hspace=0.18, wspace=0.05)
        bolge = bolge_adi(g)
        ax = fig.add_subplot(gs[0, :])
        plan_ciz(ax, g)
        ax.set_title(f"{ps['pier']} – {g['etiket']}: {bolge}   |   Katlar: {g['katlar'][0]}–"
                     f"{g['katlar'][-1]}  (z = {g['z'][0] / 1000:.2f} – {g['z'][1] / 1000:.2f} m)"
                     f"   |   Ölçek: mm", fontsize=11, weight="bold", loc="left")
        axd = fig.add_subplot(gs[1, 0])
        plan_ciz(axd, g, detay=True)
        axd.set_title("Sol uç bölge detayı (sağ uç simetrik)"
                      + ("  +  üst kata geçiş işaretleri" if "gecis" in g else ""),
                      fontsize=9, loc="left")
        axl = fig.add_subplot(gs[2, 0])
        axl.axis("off")
        if "gecis" in g:
            gecis_lejant(axl, g["gecis"], 0.02, 0.98)
        axt = fig.add_subplot(gs[1:, 1])
        axt.axis("off")
        satir = [f"lw = {g['lw']:.0f} mm   bw = {g['bw']:.0f} mm   Lu = {g['Lu']:.0f} mm",
                 f"Hw = {ps['Hw'] / 1000:.2f} m   Hcr = {ps['Hcr'] / 1000:.2f} m",
                 f"Uç bölge: {uc_txt(g)}  As={g['As_uc']:.0f} mm² "
                 f"(ρ/bw·lw={g['As_uc'] / g['bw'] / g['lw']:.4f})",
                 f"Etriye: Ø{g['etr']['d']}/{g['etr']['s']:.0f}  kol: kalınlık {g['etr']['n_y']}, "
                 f"boy {g['etr']['n_x']}",
                 f"Gövde çirozu: Ø{g['gciroz']['d']} şaşırtmalı, her {g['gciroz']['adim']}. düşey, "
                 f"düşeyde /{g['gciroz']['s_duz']:.0f} ({g['gciroz']['adet_m2']:.1f}/m² ≥ "
                 f"{g['gciroz']['gerek']:.0f})",
                 f"Gövde düşey Ø{g['dw']}/{g['sw_gercek']:.0f}, yatay Ø{g['dh']}/{g['sh']:.0f}",
                 *[f"Kiriş bölgesi: {z['aciklama']}" for z in g.get("kiris_bolgeleri", [])],
                 f"Lif ağı: {g['lif'].nx}×{g['lif'].ny} = {g['lif'].n_lif} beton lifi + "
                 f"{len(g['uc']) + len(g['govde'])} donatı lifi",
                 "", "KESME TEVZİSİ (1.2D kombinasyonları):"]
        for r in g["kesme"]:
            satir.append(f"  {r['kat']:<10} Vd={r['Vd'] / 1e3:6.0f}  Ve={r['Ve'] / 1e3:6.0f}  "
                         f"-> yatay Ø{r['d']}/{r['s']:.0f} (2 kol)  Vr={r['Vr'] / 1e3:.0f} kN "
                         f"{'✓' if r['ok'] else '✗'}")
        if "gecis" in g:
            gc = g["gecis"]
            satir += ["", f"ÜST KATA GEÇİŞ ({gc['kat']}, üst {gc['ust_etiket']}):",
                      f"  {len(gc['duz'])} düz, {len(gc['kirim'])} kırım, {len(gc['filiz'])} filiz, "
                      f"{len(gc['biten'])} biten"]
            for d_, (lb, l0) in gc["boylar"].items():
                satir.append(f"  Ø{d_}: lb={lb:.0f}  bindirme l0={l0:.0f} mm")
            if gc["filiz"]:
                f0 = gc["filiz"][0]
                satir.append(f"  Filiz: alta gömülme ≥ {f0['gomulme']:.0f}, üste ≥ {f0['bindirme']:.0f} mm")
            for u in gc["uyari"]:
                satir.append("  ⚠ " + u)
        satir += ["", "KONTROLLER:"]
        for ad, v, s, ok in g["kontroller"]:
            satir.append(f"{'✓' if ok else '✗'} {ad}: {sayi(v)} / {sayi(s)}")
        for u in g["uyarilar"]:
            satir.append("⚠ " + u)
        axt.text(0.0, 1.0, "\n".join(satir), va="top", ha="left", fontsize=8.0,
                 family="DejaVu Sans Mono")
    fig.savefig(yol, dpi=150, bbox_inches="tight")
    plt.close(fig)


def pm_ciz(ps, yol):
    """Her grup: sol – P–M3 eğrisi + talepler; sağ – en elverişsiz talepte, taşıma gücü
    anındaki lif gerilmeleri (beton lifleri ve donatı lifleri), çekme ve basınç uçları."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mp
    from matplotlib.gridspec import GridSpec, GridSpecFromSubplotSpec
    from matplotlib import cm, colors

    gr = ps["gruplar"]
    fig = plt.figure(figsize=(16, 6.0 * len(gr)))
    gs = GridSpec(len(gr), 2, figure=fig, width_ratios=[1.0, 1.15], hspace=0.35, wspace=0.12)
    norm_c = colors.Normalize(0, 0.85 * malzeme()["fcd"])
    fyd = malzeme()["fyd"]
    norm_s = colors.TwoSlopeNorm(vmin=-fyd, vcenter=0, vmax=fyd)
    cmap_c, cmap_s = cm.Greys, cm.coolwarm_r   # donatı: kırmızı çekme, mavi basınç

    for i, g in enumerate(gr):
        lk = g["lif"]
        ax = fig.add_subplot(gs[i, 0])
        for yon in (1, -1):
            N, M = lk.egri()[yon]
            ax.plot(M / 1e6, N / 1e3, color="#1f4e79", lw=1.8,
                    label="Kapasite (lif analizi)" if yon == 1 else None)
        T = np.array(g["talepler"])
        dc = lk.talep_orani(g["talepler"])
        ax.scatter(T[:, 1] / 1e6, T[:, 0] / 1e3, s=14, c="#e67e22", edgecolor="none",
                   alpha=0.8, label="Talepler (Nd, Md3)", zorder=3)
        j = int(np.argmax(dc))
        ax.scatter([T[j, 1] / 1e6], [T[j, 0] / 1e3], s=70, facecolor="none", edgecolor="#c0392b",
                   lw=1.8, zorder=4, label=f"En elverişsiz: Md/Mr = {dc[j]:.2f}")
        ax.axhline(0, color="#999", lw=0.6)
        ax.axvline(0, color="#999", lw=0.6)
        ax.set_xlabel("M3 [kNm]")
        ax.set_ylabel("N [kN]  (basınç +)")
        ax.grid(alpha=0.25)
        ax.legend(fontsize=8, loc="lower right")
        bolge = bolge_adi(g, True)
        ax.set_title(f"{ps['pier']} – {g['etiket']} ({bolge}, {g['katlar'][0]}–{g['katlar'][-1]}): "
                     f"P–M3, uç {uc_txt(g)}", fontsize=10, loc="left")

        # --- lif gerilmeleri (en elverişsiz talep, Mr anında)
        sub = GridSpecFromSubplotSpec(2, 1, subplot_spec=gs[i, 1], hspace=0.45)
        du = lk.durum(T[j, 0], T[j, 1])
        hx = g["lw"] / lk.nx
        hy = g["bw"] / lk.ny
        L = min(g["lw"] / 2, g["Lu"] + 350)
        bars = g["uc"] + g["govde"]
        for k, (x_a, x_b, ad) in enumerate(((0, L, "sol uç"), (g["lw"] - L, g["lw"], "sağ uç"))):
            a2 = fig.add_subplot(sub[k])
            if du is not None:
                ec, sc, es, ss = du
                ix = np.where((lk.xc >= x_a) & (lk.xc <= x_b))[0]
                for q in ix:
                    for r in range(lk.ny):
                        a2.add_patch(mp.Rectangle((q * hx, r * hy), hx, hy, lw=0.25, ec="#9a9a9a",
                                                  fc=cmap_c(norm_c(sc[q])) if sc[q] > 0 else "white"))
                for (x, y, d), sg in zip(bars, ss):
                    if x_a <= x <= x_b:
                        a2.add_patch(mp.Circle((x, y), d / 2 * 1.3, fc=cmap_s(norm_s(sg)),
                                               ec="black", lw=0.5, zorder=3))
                # tarafsız eksen
                eks = np.where(np.diff(np.sign(ec)) != 0)[0]
                for e in eks:
                    xn = lk.xc[e] + hx / 2
                    if x_a <= xn <= x_b:
                        a2.axvline(xn, color="#27ae60", lw=1.5, ls="-.")
            a2.axvline(g["Lu"] if k == 0 else g["lw"] - g["Lu"], color="#c0392b", ls="--", lw=1)
            a2.add_patch(mp.Rectangle((x_a, 0), x_b - x_a, g["bw"], fc="none", ec="black", lw=1.2))
            a2.set_xlim(x_a - 20, x_b + 20)
            a2.set_ylim(-20, g["bw"] + 20)
            a2.set_aspect("equal")
            a2.axis("off")
            a2.set_title(f"{ad}  |  lif ağı {lk.nx}×{lk.ny} ({hx:.0f}×{hy:.0f} mm)", fontsize=8.5,
                         loc="left")
        a_ = fig.axes[-2]
        a_.text(0, 1.28, f"En elverişsiz talep (Nd={T[j, 0] / 1e3:.0f} kN, Md={T[j, 1] / 1e6:.0f} kNm) "
                         f"için taşıma gücü anında lif gerilmeleri — beton: gri (basınç), donatı: "
                         f"kırmızı çekme / mavi basınç, yeşil: tarafsız eksen",
                transform=a_.transAxes, fontsize=8.5)
    fig.savefig(yol, dpi=140, bbox_inches="tight")
    plt.close(fig)


# =====================================================================================
# 8) ÇİZİM – DXF (ezdxf)
# =====================================================================================
KATMANLAR = {"BETON": 7, "UC_BOLGE": 1, "DONATI_UC": 6, "DONATI_GOVDE": 5, "YATAY_GOVDE": 4,
             "ETRIYE": 1, "CIROZ": 1, "GOVDE_CIROZ_A": 6, "GOVDE_CIROZ_B": 6, "OLCU": 3,
             "YAZI": 2, "UST_KESIT": 200, "FILIZ_DUZ": 3, "FILIZ_KIRIM": 30, "FILIZ_EKIM": 221,
             "BITEN_DONATI": 8, "KIRIS_BOLGESI": 5, "FIRKETE": 94, "KOL_NO": 140,
             "KOL_TABLO": 2, "GEREKLI_DONATI": 1, "KOL_TALEP": 4}


def _dxf_kol_no(msp, pts, katman="KOL_NO", h=38.0):
    """Etriye/çiroz kollarını numaralar: pts = [(x, y, hizalama)]."""
    from ezdxf.enums import TextEntityAlignment as TA
    hz = {"alt": TA.BOTTOM_CENTER, "ust": TA.TOP_CENTER, "sag": TA.MIDDLE_RIGHT,
          "sol": TA.MIDDLE_LEFT, "orta": TA.MIDDLE_CENTER}
    # birbirine çok yakın kollar (iç içe etriyelerin bindirdiği kolon) tek etikette: "4-5"
    gruplar = []
    for i, (x, y, al) in enumerate(pts, 1):
        if gruplar and math.hypot(x - gruplar[-1][-1][1], y - gruplar[-1][-1][2]) < 1.6 * h:
            gruplar[-1].append((i, x, y, al))
        else:
            gruplar.append([(i, x, y, al)])
    for gr in gruplar:
        etiket = str(gr[0][0]) if len(gr) == 1 else f"{gr[0][0]}-{gr[-1][0]}"
        x = sum(q[1] for q in gr) / len(gr)
        y = sum(q[2] for q in gr) / len(gr)
        msp.add_text(etiket, height=h, dxfattribs={"layer": katman, "style": "TR"}
                     ).set_placement((x, y), align=hz[gr[0][3]])


def _dxf_tablo(msp, x0, y0, satirlar, katman="KOL_TABLO", h=55.0, genislik=None):
    """Basit çerçeveli tablo: satirlar = [[hücre, ...], ...] (ilk satır başlık). Sol-üst köşe
    (x0, y0). Sütun genişlikleri karakter sayısından."""
    ncol = max(len(r) for r in satirlar)
    gen = genislik or [max(len(str(r[j])) if j < len(r) else 0 for r in satirlar) * h * 0.72 + 1.6 * h
                       for j in range(ncol)]
    yuk = h * 1.9
    xs = [x0]
    for w in gen:
        xs.append(xs[-1] + w)
    for i, r in enumerate(satirlar):
        y = y0 - i * yuk
        for j, hucre in enumerate(r):
            msp.add_text(str(hucre), height=h, dxfattribs={"layer": katman, "style": "TR"}
                         ).set_placement((xs[j] + 0.6 * h, y - 1.35 * h))
    y_son = y0 - len(satirlar) * yuk
    for i in range(len(satirlar) + 1):
        msp.add_line((x0, y0 - i * yuk), (xs[-1], y0 - i * yuk), dxfattribs={"layer": katman})
    for xx in xs:
        msp.add_line((xx, y0), (xx, y_son), dxfattribs={"layer": katman})
    return xs[-1], y_son


def kol_konumlari_dik(g):
    """Dikdörtgen perdede (sol uç + kiriş bölgeleri) etriye/çiroz kollarının plan konumları.
    y_kol: kalınlık doğrultusundaki kolların x'leri (etriye düşey kenarları + çirozlar),
    x_kol: boy doğrultusundaki kolların y'leri (etriye yatay kenarları + çirozlar)."""
    c, det, bw = AYAR["paspayi"], g["etr"]["d"], g["bw"]
    e = g["etr"]
    y_kol = sorted([ha + det / 2 for ha, hb in e["hooplar"]] + [hb - det / 2 for ha, hb in e["hooplar"]]
                   + list(e["ciroz_x"]))
    x_kol = sorted([c + det / 2, bw - c - det / 2] + [y for y, *_ in e["ciroz_y"]])
    kb = []
    for z in g.get("kiris_bolgeleri", []):
        yk = sorted([ha + det / 2 for ha, hb in z["hooplar"]] + [hb - det / 2 for ha, hb in z["hooplar"]]
                    + list(z["ciroz_x"]))
        xk = sorted([c + det / 2, bw - c - det / 2] + list(z["ys"][1:-1]))
        kb.append(dict(z=z, y_kol=yk, x_kol=xk))
    return dict(y_kol=y_kol, x_kol=x_kol, kb=kb)



def _dxf_yeni():
    import ezdxf
    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = 4  # mm
    for ad, renk in KATMANLAR.items():
        doc.layers.add(ad, color=renk)
    doc.layers.get("UC_BOLGE").dxf.linetype = "DASHED"
    doc.layers.get("GOVDE_CIROZ_B").dxf.linetype = "DASHED"
    doc.layers.get("UST_KESIT").dxf.linetype = "DASHED"
    doc.layers.get("KIRIS_BOLGESI").dxf.linetype = "DASHED"
    doc.styles.add("TR", font="arial.ttf")
    return doc


def dxf_grup(msp, g, ox, oy, baslik):
    lw, bw, Lu = g["lw"], g["bw"], g["Lu"]
    c = AYAR["paspayi"]
    det = g["etr"]["d"]
    t = _metin_donati(g)
    P = lambda x, y: (ox + x, oy + y)
    msp.add_lwpolyline([P(0, 0), P(lw, 0), P(lw, bw), P(0, bw)], close=True,
                       dxfattribs={"layer": "BETON"})
    for xb in (Lu, lw - Lu):
        msp.add_line(P(xb, 0), P(xb, bw), dxfattribs={"layer": "UC_BOLGE"})
    for cb in g["yatay"]["cubuklar"]:
        msp.add_lwpolyline([P(*p_) for p_ in cb["pts"]], dxfattribs={"layer": "YATAY_GOVDE"})
    for fk in g["yatay"]["firketeler"]:
        msp.add_lwpolyline([P(*p_) for p_ in fk["pts"]], dxfattribs={"layer": "FIRKETE"})
    for i_h, (ha, hb) in enumerate(g["etr"]["hooplar"]):
        off = det * (i_h % 2)
        for a_, b_ in ((ha, hb), (lw - hb, lw - ha)):
            a, b = a_ + det / 2, b_ - det / 2
            msp.add_lwpolyline([P(a, c + det / 2 + off), P(b, c + det / 2 + off),
                                P(b, bw - c - det / 2 - off), P(a, bw - c - det / 2 - off)],
                               close=True, dxfattribs={"layer": "ETRIYE", "const_width": det})
    for x in g["etr"]["ciroz_x"]:
        for xx in (x, lw - x):
            msp.add_lwpolyline([P(xx, c + det / 2), P(xx, bw - c - det / 2)],
                               dxfattribs={"layer": "CIROZ", "const_width": det})
    for y, xa, xb in g["etr"]["ciroz_y"]:
        for a_, b_ in ((c + det / 2, xb), (lw - xb, lw - c - det / 2)):
            msp.add_lwpolyline([P(a_, y), P(b_, y)],
                               dxfattribs={"layer": "CIROZ", "const_width": det})
    gc = g["gciroz"]
    for katman, xs_ in (("GOVDE_CIROZ_A", gc["xA"]), ("GOVDE_CIROZ_B", gc["xB"])):
        for x in xs_:
            msp.add_lwpolyline([P(x, c + g["dh"] / 2), P(x, bw - c - g["dh"] / 2)],
                               dxfattribs={"layer": katman, "const_width": gc["d"]})
    for katman, liste in (("DONATI_UC", g["uc"]), ("DONATI_GOVDE", g["govde"])):
        for x, y, d in liste:
            msp.add_circle(P(x, y), d / 2, dxfattribs={"layer": katman})
            h = msp.add_hatch(dxfattribs={"layer": katman})
            h.paths.add_edge_path().add_arc(P(x, y), d / 2, 0, 360)
    # kiriş bağlantı bölgeleri
    for z in g.get("kiris_bolgeleri", []):
        for xb in (z["x0"], z["x1"]):
            msp.add_line(P(xb, 0), P(xb, bw), dxfattribs={"layer": "KIRIS_BOLGESI"})
        a, b = z["x0"] + det / 2, z["x1"] - det / 2
        for i_h, (ha, hb) in enumerate(z["hooplar"]):
            off = det * (i_h % 2)
            msp.add_lwpolyline([P(ha + det / 2, c + det / 2 + off), P(hb - det / 2, c + det / 2 + off),
                                P(hb - det / 2, bw - c - det / 2 - off),
                                P(ha + det / 2, bw - c - det / 2 - off)], close=True,
                               dxfattribs={"layer": "ETRIYE", "const_width": det})
        for x in z["ciroz_x"]:
            msp.add_lwpolyline([P(x, c + det / 2), P(x, bw - c - det / 2)],
                               dxfattribs={"layer": "CIROZ", "const_width": det})
        for y in z["ys"][1:-1]:
            msp.add_lwpolyline([P(a, y), P(b, y)], dxfattribs={"layer": "CIROZ", "const_width": det})
        for x in z["xs"]:
            for y in z["ys"]:
                msp.add_circle(P(x, y), g["de"] / 2, dxfattribs={"layer": "DONATI_UC"})
                h = msp.add_hatch(dxfattribs={"layer": "DONATI_UC"})
                h.paths.add_edge_path().add_arc(P(x, y), g["de"] / 2, 0, 360)
        msp.add_mtext("KİRİŞ " + ", ".join(z["kirisler"]) + f"\\P{len(z['ys'])}x{len(z['xs'])}%%c{g['de']}"
                      + f"\\PEtriye %%c{det}/{g['etr']['s']:.0f}",
                      dxfattribs=dict(layer="YAZI", style="TR", char_height=60)
                      ).set_location(P((z["x0"] + z["x1"]) / 2, bw + 120), attachment_point=8)

    # üst kata geçiş
    if "gecis" in g:
        gc = g["gecis"]
        msp.add_lwpolyline([P(*q) for q in gc["ust_kontur"]], close=True,
                           dxfattribs={"layer": "UST_KESIT"})
        for b in gc["duz"]:
            msp.add_circle(P(b["xu"], b["yu"]), b["du"] / 2 + 12, dxfattribs={"layer": "FILIZ_DUZ"})
        for b in gc["kirim"]:
            msp.add_line(P(b["xl"], b["yl"]), P(b["xu"], b["yu"]), dxfattribs={"layer": "FILIZ_KIRIM"})
            msp.add_circle(P(b["xu"], b["yu"]), b["du"] / 2, dxfattribs={"layer": "FILIZ_KIRIM"})
        for f in gc["filiz"]:
            h = f["du"] / 2 + 12
            msp.add_lwpolyline([P(f["xu"] - h, f["yu"] - h), P(f["xu"] + h, f["yu"] - h),
                                P(f["xu"] + h, f["yu"] + h), P(f["xu"] - h, f["yu"] + h)],
                               close=True, dxfattribs={"layer": "FILIZ_EKIM"})
            msp.add_circle(P(f["xu"], f["yu"]), f["du"] / 2, dxfattribs={"layer": "FILIZ_EKIM"})
        for b in gc["biten"]:
            h = b["d"] / 2 + 5
            msp.add_line(P(b["x"] - h, b["y"] - h), P(b["x"] + h, b["y"] + h),
                         dxfattribs={"layer": "BITEN_DONATI"})
            msp.add_line(P(b["x"] - h, b["y"] + h), P(b["x"] + h, b["y"] - h),
                         dxfattribs={"layer": "BITEN_DONATI"})
        yazi = [f"ÜST KATA GEÇİŞ {gc['kat']} (üst {gc['ust_etiket']}): " + gc["ozet"]]
        yazi += [f"%%c{d_}: lb={lb:.0f}, bindirme l0={l0:.0f} mm" for d_, (lb, l0) in gc["boylar"].items()]
        yazi += ["Katmanlar: FILIZ_DUZ (düz devam), FILIZ_KIRIM (kırım <= 1/6), "
                 "FILIZ_EKIM (filiz ekimi), BITEN_DONATI, UST_KESIT"] + gc["uyari"]
        msp.add_mtext("\\P".join(yazi), dxfattribs=dict(layer="YAZI", style="TR", char_height=60)
                      ).set_location(P(0, -1050))

    # ölçüler
    ds = dict(dimstyle="EZDXF", override={"dimtxt": 60, "dimasz": 40, "dimexe": 20,
                                          "dimexo": 20, "dimdec": 0, "dimlfac": 1.0,
                                          "dimtad": 1, "dimgap": 15}, dxfattribs={"layer": "OLCU"})
    for x1, x2, y in ((0, Lu, -200), (Lu, lw - Lu, -200), (lw - Lu, lw, -200), (0, lw, -400)):
        msp.add_linear_dim(base=P(x1, y), p1=P(x1, 0), p2=P(x2, 0), **ds).render()
    msp.add_linear_dim(base=P(-200, 0), p1=P(0, 0), p2=P(0, bw), angle=90, **ds).render()
    # yazılar
    yz = dict(layer="YAZI", style="TR", char_height=70)
    msp.add_mtext(baslik, dxfattribs=dict(yz, char_height=110)).set_location(P(0, bw + 1350))
    msp.add_mtext("UÇ BÖLGE\\P" + t["uc"] + "\\P" + t["uc_d"] + "\\P"
                  + t["etr"].replace(" + ", "\\P+ "),
                  dxfattribs=yz).set_location(P(0.0, bw + 120), attachment_point=7)
    msp.add_mtext("UÇ BÖLGE\\P(simetrik)", dxfattribs=yz).set_location(P(lw, bw + 120),
                                                                        attachment_point=9)
    msp.add_mtext(t["gd"] + "\\P" + t["gy"] + "\\P" + t["gc"]
                  + "\\P(GOVDE_CIROZ_A: bu seviye, GOVDE_CIROZ_B: sonraki seviye)",
                  dxfattribs=yz).set_location(P(0, -560), attachment_point=1)
    _dxf_kol_dik(msp, g, P)


def _dxf_kol_dik(msp, g, P):
    """Etriye kol numaraları (KOL_NO) ve kol/çiroz tablosu (KOL_TABLO) – dikdörtgen perde."""
    lw, bw, Lu = g["lw"], g["bw"], g["Lu"]
    e, gc, det = g["etr"], g["gciroz"], g["etr"]["d"]
    kk = kol_konumlari_dik(g)
    # uç bölge: kalınlık doğrultusundaki kollar üst yüzün üstünde, boy doğrultusundakiler solda
    for xs_ in (kk["y_kol"], [lw - x for x in kk["y_kol"]]):      # her uçta perde ucundan içe
        _dxf_kol_no(msp, [(*P(x, bw + 30), "alt") for x in xs_])
    _dxf_kol_no(msp, [(*P(-45, y), "sag") for y in kk["x_kol"]])
    _dxf_kol_no(msp, [(*P(lw + 45, y), "sol") for y in kk["x_kol"]])
    for q in kk["kb"]:
        _dxf_kol_no(msp, [(*P(x, bw + 30), "alt") for x in q["y_kol"]])
    # gövde çirozu: A/B harfleri alt yüzün altında
    for harf, xs_ in (("A", gc["xA"]), ("B", gc["xB"])):
        for x in xs_:
            msp.add_text(harf, height=38, dxfattribs={"layer": "KOL_NO", "style": "TR"}
                         ).set_placement(P(x, -30), align=_hz("ust"))
    # tablo
    n_h = len(e["hooplar"])
    sat = [["BÖLGE", "ENİNE DONATI", "KOL (kalınlık doğr.)", "KOL (boy doğr.)",
            "KAPALI ETRİYE / seviye", "ÇİROZ / seviye", "DÜŞEY ARALIK"]]
    sat.append([f"Uç bölge (her uç, Lu={Lu:.0f})", f"Ø{det} etriye+çiroz", f"{e['n_y']}",
                f"{e['n_x']}", f"{n_h} ({'iç içe' if n_h > 1 else 'tek'})",
                f"{len(e['ciroz_x'])} (kalınlık) + {len(e['ciroz_y'])} (boy)", f"/{e['s']:.0f}"])
    for q in kk["kb"]:
        z = q["z"]
        sat.append([f"Kiriş bölgesi {', '.join(z['kirisler'])}", f"Ø{det} etriye+çiroz",
                    f"{len(q['y_kol'])}", f"{len(q['x_kol'])}", f"{len(z['hooplar'])}",
                    f"{len(z['ciroz_x'])} (kalınlık) + {len(z['ys']) - 2} (boy)", f"/{e['s']:.0f}"])
    sat.append(["Gövde", f"Ø{g['dh']} yatay (2 yüz)", "–", "2 (yatay donatı)", "–",
                f"Ø{gc['d']}: A seviyesi {len(gc['xA'])}, B seviyesi {len(gc['xB'])} adet",
                f"yatay /{g['sh']:.0f}, çiroz /{gc['s_duz']:.0f}"])
    sat.append(["Gövde çirozu", f"planda her {gc['adim']}. düşey", "", "", "",
                f"{gc['adet_m2']:.1f} adet/m² ≥ {gc['gerek']:.0f}", ""])
    x0, y0 = P(lw + 400, bw + 300)
    msp.add_text("ETRİYE KOL / ÇİROZ TABLOSU (KOL_NO katmanı: plandaki kol numaraları)", height=65,
                 dxfattribs={"layer": "KOL_TABLO", "style": "TR"}).set_placement((x0, y0 + 40))
    _dxf_tablo(msp, x0, y0, sat)


def _hz(ad):
    from ezdxf.enums import TextEntityAlignment as TA
    return {"alt": TA.BOTTOM_CENTER, "ust": TA.TOP_CENTER, "sag": TA.MIDDLE_RIGHT,
            "sol": TA.MIDDLE_LEFT, "orta": TA.MIDDLE_CENTER}[ad]


def _dis_yuz(k, kollar, s_):
    """k kolunun s_ noktasında kesitin dışına bakan yüzü (+1 / −1): ağırlık merkezinden uzak
    olan yüz."""
    pts = np.array([q for kk in kollar for q in kk.koseler()], float)
    cg = pts.mean(axis=0)
    a = np.asarray(k.nokta(s_, k.t / 2), float)
    b = np.asarray(k.nokta(s_, -k.t / 2), float)
    return 1 if np.hypot(*(a - cg)) >= np.hypot(*(b - cg)) else -1


def dxf_ciz(ps, yol):
    doc = _dxf_yeni()
    msp = doc.modelspace()
    oy = 0.0
    for g in ps["gruplar"]:
        bolge = bolge_adi(g)
        dxf_grup(msp, g, 0.0, oy, f"{ps['pier']} - {g['etiket']} {bolge}  "
                 f"({g['katlar'][0]}-{g['katlar'][-1]})")
        oy -= g["bw"] + (4300 if "gecis" in g else 3300)
    doc.saveas(yol)


def dxf_section_designer(g, yol):
    """Sade DXF: dış hat (SHAPE) + donatı daireleri (REBAR), orijin = ağırlık merkezi, mm."""
    import ezdxf
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 4
    doc.layers.add("SHAPE", color=7)
    doc.layers.add("REBAR", color=1)
    msp = doc.modelspace()
    lw, bw = g["lw"], g["bw"]
    cx, cy = lw / 2, bw / 2
    msp.add_lwpolyline([(-cx, -cy), (cx, -cy), (cx, cy), (-cx, cy)], close=True,
                       dxfattribs={"layer": "SHAPE"})
    for x, y, d in g["uc"] + g["govde"] + g.get("kiris_bars", []):
        msp.add_circle((x - cx, y - cy), d / 2, dxfattribs={"layer": "REBAR"})
    doc.saveas(yol)


# =====================================================================================
# 9) RAPOR
# =====================================================================================
def rapor_yaz(ps, yol):
    L = [f"PERDE {ps['pier']} – TBDY 2018 Bölüm 7.6 tasarım raporu", "=" * 70]
    L += ps["notlar"] + [""]
    for g in ps["gruplar"]:
        L.append(f"[{g['etiket']}] {bolge_adi(g)}  katlar: "
                 f"{', '.join(g['katlar'])}")
        L.append(f"  lw={g['lw']:.0f} bw={g['bw']:.0f} Lu={g['Lu']:.0f} mm")
        L.append(f"  Uç bölge: {uc_txt(g)} ({_metin_donati(g)['uc_d']}, "
                 f"As={g['As_uc']:.0f} mm²), "
                 f"Etriye/çiroz Ø{g['etr']['d']}/{g['etr']['s']:.0f}  – {g['etr']['not_']}")
        for z in g.get("kiris_bolgeleri", []):
            L.append(f"  Kiriş bağlantı bölgesi: {z['aciklama']}, x=[{z['x0']:.0f}, {z['x1']:.0f}], "
                     f"etriye Ø{g['etr']['d']}/{g['etr']['s']:.0f} (uç bölge kuralları)")
        L.append(f"  Küme: eksen aralığı {g['duzen']['s_k']:.0f} mm (bindirmede yan yana çubuk "
                 f"dahil net aralık), küme payı %{g['duzen']['yogunlasma'] * 100:.0f}; uç bölge "
                 f"ρ = {g['As_uc'] / (g['Lu'] * g['bw']):.4f} ≤ {AYAR['rho_uc_max']}")
        L.append(f"  Etriye kol sayısı: kalınlık doğrultusunda {g['etr']['n_y']} (TBDY 7.6.5.2: Ash için "
                 f"{g['etr'].get('n_y_gerek', '-')}, kollar arası a ≤ 25Ø için "
                 f"{g['etr'].get('n_y_a', '-')} kol gerekli), boy doğrultusunda {g['etr']['n_x']}; "
                 f"uç bölgede {g['etr'].get('n_kolon', '-')} boyuna donatı kolonu var, çiroz yalnız "
                 f"gerekli kolonlarda")
        if g["etr"].get("alternatifler") and len(g["etr"]["alternatifler"]) > 1:
            L.append("  Enine donatı seçenekleri (hepsi TBDY'yi sağlar): " + "; ".join(
                f"Ø{d_}/{s_:.0f} -> kalınlık doğr. {ny_} kol, boy doğr. {nx_} kol"
                for d_, s_, ny_, nx_ in g["etr"]["alternatifler"]))
        L.append("  " + _metin_donati(g)["gc"])
        L.append(f"  Gövde: düşey Ø{g['dw']}/{g['sw_gercek']:.0f}, yatay Ø{g['dh']}/{g['sh']:.0f} (2 kol) "
                 f"(ρsh gerekli={g['rho_sh_gerek']:.4f})")
        L.append(f"  Lif ağı: {g['lif'].nx}×{g['lif'].ny} beton lifi, "
                 f"{len(g['uc']) + len(g['govde'])} donatı lifi; en elverişsiz Md/Mr = {g['Md_Mr']:.3f}")
        L.append("  Kesme tevzisi (1.2D):  Kat | Vd | Ve | ρsh gerek | yatay donatı (2 kol) | Vr [kN]")
        for r in g["kesme"]:
            L.append(f"     {r['kat']:<10} {r['Vd'] / 1e3:7.0f} {r['Ve'] / 1e3:7.0f}  {r['rho']:.4f}"
                     f"   Ø{r['d']}/{r['s']:.0f}   {r['Vr'] / 1e3:7.0f}  {'OK' if r['ok'] else 'YOK'}")
        if "gecis" in g:
            gc = g["gecis"]
            L.append(f"  Üst kata geçiş ({gc['kat']}): {gc['ozet']}")
            L.append(f"     Üst kesit kayması: boy {gc['kayma'][0]:.0f} mm, kalınlık {gc['kayma'][1]:.0f} mm")
            for d_, (lb, l0) in gc["boylar"].items():
                L.append(f"     Ø{d_}: lb = {lb:.0f} mm, bindirme l0 = {l0:.0f} mm")
            for f in gc["filiz"]:
                L.append(f"     Filiz Ø{f['du']} @ x={f['xu']:.0f}, y={f['yu']:.0f}: "
                         f"gömülme ≥ {f['gomulme']:.0f}, bindirme ≥ {f['bindirme']:.0f} mm"
                         + ("" if f["alt_kesitte"] else "  (ALT KESİT DIŞINDA!)"))
            for u in gc["uyari"]:
                L.append("     UYARI: " + u)
        for ad, v, s, ok in g["kontroller"]:
            L.append(f"   {'OK ' if ok else 'YOK'}  {ad}: {sayi(v)}  (sınır {sayi(s)})")
        for u in g["uyarilar"]:
            L.append("   UYARI: " + u)
        if not all(k[3] for k in g["kontroller"]):
            L.append(f"   Öneri: bw ≥ {math.ceil(g['bw_oneri'] / 10) * 10:.0f} mm")
        L.append("")
    L += ["Notlar:",
          "- Uç bölge: kritikte Lu ≥ max(0.20lw, 2bw), üstte Lu ≥ max(0.10lw, bw) (TBDY 7.6.2.3).",
          "- Uç bölge boyuna donatı: ≥0.002·bw·lw (kritik), ≥0.001·bw·lw (üst), ≥4Ø14; oran ≤ 0.03",
          "  (bindirmede 0.06) (7.6.5.1).",
          "- Geometri/donatı geçişi kritik bölge üstündeki 3 katta kademeli: Lu ve ρmin %75/%50/%25.",
          "- Uç bölge enine donatısı (7.6.5.2): Ø ≥ 8, etriye kolları/çirozlar arası a ≤ 25Ø; kritik",
          "  bölgede Ash ≥ 2/3·Denk.(7.1b), 50 ≤ s ≤ min(150, 6Ø, bw/3); dışında s ≤ min(bw, 200).",
          f"  Uç bölge {AYAR['etriye_max_uzunluk']:.0f} mm'den uzunsa iç içe (bir kolon bindirmeli) etriye.",
          "- Kritik bölge uç bölge etriyeleri temel içinde max(300, bw) boyunca devam eder (metrajda).",
          "- Özel deprem çirozu: kritik yükseklikte ≥ 10 adet/m², dışında ≥ 4 adet/m², şaşırtmalı;",
          "  çiroz çapı = yatay donatı çapı.",
          "- Uç bölge etriyesi ve gövde yatay donatısı aynı dış tabakada (farklı seviyelerde); uç",
          "  boyuna donatı ikisinin de içinde. Serbest uçta yatay donatı U-firkete ile ≥1.5ℓb",
          "  bindirilir; köşe/T birleşiminde karşı kolun dış yüzüne kadar uzanıp ℓb kancayla biter.",
          f"- {AYAR['cubuk_max_boy'] / 1000:.0f} m'yi aşan yatay çubuklar l0 = α1·ℓb bindirmeli eklenir.",
          "- Bodrumlu binalarda Hcr zemin kat altındaki ilk bodrum boyunca da uzatılır (elle kontrol).",
          "- Mp ≈ 1.4·Mr yaklaşımı ve Ve ≤ (R/D)·Vd sınırı VARSAYIMdır; ayarlardan değiştirilebilir."]
    with open(yol, "w", encoding="utf-8") as f:
        f.write("\n".join(L))


# =====================================================================================
# 9b) ÇOK KOLLU PERDELER (U, H, L, T, kutu) – genel kesit, 2B lif ağı, P–M2–M3
# =====================================================================================
#   * Kesit, ETABS'teki pier alan elemanlarının plan izlerinden (merkez çizgisi + kalınlık)
#     kurulur. Aynı doğrultudaki bitişik alan elemanları tek kola birleştirilir.
#   * Kollar birleşim noktalarında üst üste binmeyecek şekilde uzatılır/kısaltılır
#     (köşede birincil kol uzatılır, diğerleri onun yüzünde başlar; T birleşiminde biten
#     kol geçen kolun yüzünde başlar). Böylece lif ağı ve Section Designer DXF'i çift sayım
#     yapmaz.
#   * Uç bölgeler: serbest uçlarda TBDY 7.6.2.3 (kolun kendi boyuna göre); birleşimlerde
#     TBDY 7.6.2.4 – birleşen her kolun gövdesine max(t, 300 mm) uzatılır.
#   * Lif ağı iki doğrultuda kurulur; tarafsız eksen açısı θ ve derinliği c taranarak
#     P–M2–M3 yüzeyi elde edilir. Talep (Nd, M2d, M3d) için Nd düzleminde kapasite konturu
#     çıkarılır, talep doğrultusundaki kapasiteye oranlanır (Md/Mr).

class Kol:
    """P: başlangıç yüzü orta noktası (u, v), e: birim doğrultu, n: birim normal.
    Nokta = P + s·e + w·n,   s ∈ [0, L],  w ∈ [-t/2, t/2]"""

    def __init__(self, P, e, L, t, ad):
        self.P = np.asarray(P, float)
        self.e = np.asarray(e, float) / np.linalg.norm(e)
        self.n = np.array([-self.e[1], self.e[0]])
        self.L, self.t, self.ad = float(L), float(t), ad
        self.uclar = {0: dict(tip="serbest"), 1: dict(tip="serbest")}   # 0: s=0, 1: s=L
        self.dugumler = []   # (s, tip, eş kol, eş kalınlık) – bu kol üzerindeki birleşimler

    def nokta(self, s, w):
        return self.P + s * self.e + w * self.n

    def koseler(self):
        return [tuple(self.nokta(s, w)) for s, w in ((0, -self.t / 2), (self.L, -self.t / 2),
                                                     (self.L, self.t / 2), (0, self.t / 2))]

    def yerel(self, u, v):
        d = np.stack([np.asarray(u) - self.P[0], np.asarray(v) - self.P[1]], -1)
        return d @ self.e, d @ self.n

    def icinde(self, u, v, pay=0.0):
        s, w = self.yerel(u, v)
        return (s >= -pay) & (s <= self.L + pay) & (np.abs(w) <= self.t / 2 + pay)

    @property
    def aci(self):
        return math.degrees(math.atan2(self.e[1], self.e[0]))


def _nokta_seg(p, a, b):
    ab = b - a
    t = float(np.clip(np.dot(p - a, ab) / np.dot(ab, ab), 0, 1))
    return float(np.linalg.norm(p - (a + t * ab))), t


def segleri_birlestir(segler, tol=5.0):
    """Aynı doğrultuda, uç uca gelen ve aynı kalınlıktaki merkez çizgilerini birleştirir."""
    segler = [(np.asarray(a, float), np.asarray(b, float), t) for a, b, t in segler]
    degisti = True
    while degisti:
        degisti = False
        for i in range(len(segler)):
            for j in range(i + 1, len(segler)):
                a1, b1, t1 = segler[i]
                a2, b2, t2 = segler[j]
                if abs(t1 - t2) > 1:
                    continue
                d1 = (b1 - a1) / np.linalg.norm(b1 - a1)
                d2 = (b2 - a2) / np.linalg.norm(b2 - a2)
                if abs(abs(np.dot(d1, d2)) - 1) > 1e-3:
                    continue
                if _nokta_seg(a2, a1 - 1e6 * d1, a1 + 1e6 * d1)[0] > tol:
                    continue
                s = [np.dot(p - a1, d1) for p in (a1, b1, a2, b2)]
                if min(s[2:]) > max(s[:2]) + tol or max(s[2:]) < min(s[:2]) - tol:
                    continue
                segler[i] = (a1 + min(s) * d1, a1 + max(s) * d1, t1)
                segler.pop(j)
                degisti = True
                break
            if degisti:
                break
    return segler


def kollari_kur(segler):
    """segler: [((u1,v1),(u2,v2),t)] -> [Kol]. Birleşimlere göre uzatma/kısaltma yapar."""
    segler = segleri_birlestir(segler)
    n = len(segler)
    ext = [[0.0, 0.0] for _ in range(n)]          # + uzat, - kısalt
    uc_bilgi = [[dict(tip="serbest"), dict(tip="serbest")] for _ in range(n)]
    ic_dugum = [[] for _ in range(n)]              # geçen kol üzerindeki T birleşimleri
    islenen = set()
    for i in range(n):
        for k in (0, 1):
            if (i, k) in islenen:
                continue
            p = segler[i][k]
            ti = segler[i][2]
            grup = [(i, k)]
            for j in range(n):
                if j == i:
                    continue
                for l in (0, 1):
                    if np.linalg.norm(segler[j][l] - p) < 0.6 * max(ti, segler[j][2]) + 1:
                        grup.append((j, l))
            if len(grup) > 1:            # köşe (uç-uç)
                grup.sort()
                (pi, pk) = grup[0]
                t_max_diger = max(segler[j][2] for j, l in grup[1:])
                ext[pi][pk] = t_max_diger / 2
                uc_bilgi[pi][pk] = dict(tip="kose", birincil=True,
                                        es=[j for j, l in grup[1:]], t_es=t_max_diger)
                for j, l in grup[1:]:
                    ext[j][l] = -segler[pi][2] / 2
                    uc_bilgi[j][l] = dict(tip="kose", birincil=False, es=[pi],
                                          t_es=segler[pi][2])
                islenen.update(grup)
                continue
            for j in range(n):           # T (uç-gövde)
                if j == i:
                    continue
                a, b, tj = segler[j]
                d, tpar = _nokta_seg(p, a, b)
                if d < tj / 2 + 1 and 0.0 < tpar < 1.0:
                    ext[i][k] = -tj / 2
                    uc_bilgi[i][k] = dict(tip="T", es=[j], t_es=tj)
                    ic_dugum[j].append((tpar * np.linalg.norm(b - a), i, ti))
                    break
            islenen.add((i, k))
    kollar = []
    for i, (a, b, t) in enumerate(segler):
        e = (b - a) / np.linalg.norm(b - a)
        P = a - e * ext[i][0]
        L = np.linalg.norm(b - a) + ext[i][0] + ext[i][1]
        kol = Kol(P, e, L, t, f"K{i + 1}")
        kol.uclar = {0: uc_bilgi[i][0], 1: uc_bilgi[i][1]}
        kol.merkez_L = float(np.linalg.norm(b - a))
        kol.ic_dugum = [(s + ext[i][0], j, tj) for s, j, tj in ic_dugum[i]]
        kollar.append(kol)
    return kollar


def kesit_agirlik_merkezi(segler):
    kollar = kollari_kur(segler)
    A = sum(k.L * k.t for k in kollar)
    c = sum((k.P + k.e * k.L / 2) * k.L * k.t for k in kollar) / A
    return c, A


# ---------------------------------------------------------------- uç bölgeler
def cok_bolgeler(kollar, kritik, Lu_ek=0.0, kademe=0.0):
    """Uç bölge parçaları: dict(kol, s0, s1, bolge, tip, yuz0, yuz1).
    yuz0/yuz1: parçanın o ucunda beton yüzü (True) mı, gövde içinde sınır (False) mı."""
    r = AYAR["Lu_yuvarlama"]
    parca = []
    bid = 0
    for ki, k in enumerate(kollar):
        for uc in (0, 1):
            ub = k.uclar[uc]
            hoop = 0.0
            if ub["tip"] == "serbest":
                Lu = uc_bolge_uzunlugu(k.merkez_L, k.t, kritik, kademe) + Lu_ek
                uzun, tip = min(Lu, 0.45 * k.L), "serbest uç"
            elif ub["tip"] == "kose" and ub.get("birincil"):
                # birincil kol köşe karesini de içerir (dış köşeye kadar uzatılmıştır)
                uzun, tip = ub["t_es"] + max(k.t, 300.0), "köşe birleşimi"
            else:
                # köşede ikincil kol / T'de biten kol: etriyesi birleşimin içinden karşı kolun
                # dış yüzüne kadar geçer (iki etriye köşede birbirinin içinden geçer)
                uzun, tip = max(k.t, 300.0), "birleşim"
                hoop = ub["t_es"]
            uzun = min(uzun, 0.5 * k.L)
            s0, s1 = (0.0, uzun) if uc == 0 else (k.L - uzun, k.L)
            parca.append(dict(kol=ki, s0=s0, s1=s1, bolge=bid, tip=tip,
                              yuz0=(uc == 0), yuz1=(uc == 1), uc_taraf=uc,
                              hoop0=hoop if uc == 0 else 0.0, hoop1=hoop if uc == 1 else 0.0))
            bid += 1
        # kısa kol (kanat / saplama): iki uç bölgesi arasında gövde kalmıyorsa kolun tamamı tek
        # uç bölgesidir (TBDY 7.6.2.4 – kısa kanat birleşim bölgesine katılır)
        p0, p1 = parca[-2], parca[-1]
        if p1["s0"] - p0["s1"] < max(k.t, 300.0):
            p0["s1"], p1["s0"] = k.L, 0.0
            p0["tip"] = p1["tip"] = "kısa kol (tamamı uç bölgesi)"
            # birleşim tarafındaki uç sanal: o sıranın çubukları karşı kolun bölgesindedir
            for uc_ in (0, 1):
                ub = k.uclar[uc_]
                if ub["tip"] == "T" or (ub["tip"] == "kose" and not ub.get("birincil")):
                    p0[f"sanal{uc_}"] = p1[f"sanal{uc_}"] = True
        for s_d, j, t_es in getattr(k, "ic_dugum", []):   # geçen kolda T birleşimi
            y = t_es / 2 + max(k.t, 300.0)
            s0, s1 = max(0.0, s_d - y), min(k.L, s_d + y)
            parca.append(dict(kol=ki, s0=s0, s1=s1, bolge=bid, tip="T birleşimi",
                              yuz0=(s0 == 0), yuz1=(s1 == k.L), uc_taraf=None,
                              hoop0=0.0, hoop1=0.0))
            bid += 1
    # aynı koldaki çakışan parçaları birleştir
    son = []
    for ki in range(len(kollar)):
        ps = sorted([p for p in parca if p["kol"] == ki], key=lambda p: p["s0"])
        for p in ps:
            if son and son[-1]["kol"] == ki and p["s0"] <= son[-1]["s1"] + 1:
                q = son[-1]
                _parca_ekle(q, p)
                q.setdefault("bolgeler", {q["bolge"]}).add(p["bolge"])
            else:
                son.append(dict(p, bolgeler={p["bolge"]}))
    # köşe ve T birleşimlerinde farklı kollardaki parçalar aynı bölgeye aittir
    for i, k in enumerate(kollar):
        for uc in (0, 1):
            ub = k.uclar[uc]
            if ub["tip"] in ("kose", "T"):
                s_uc = 0.0 if uc == 0 else k.L
                pi = [p for p in son if p["kol"] == i and p["s0"] - 1 <= s_uc <= p["s1"] + 1]
                for j in ub["es"]:
                    pj = [p for p in son if p["kol"] == j]
                    if pi and pj:
                        # eş koldaki, bu uca en yakın parça
                        uc_n = k.nokta(s_uc, 0.0)
                        pj.sort(key=lambda p: min(np.linalg.norm(kollar[j].nokta(p["s0"], 0) - uc_n),
                                                  np.linalg.norm(kollar[j].nokta(p["s1"], 0) - uc_n)))
                        birlik = pi[0]["bolgeler"] | pj[0]["bolgeler"]
                        for p in son:
                            if p["bolgeler"] & birlik:
                                p["bolgeler"] = p["bolgeler"] | birlik
    ids = {}
    for p in son:
        anahtar = min(p["bolgeler"])
        p["bolge"] = ids.setdefault(anahtar, len(ids) + 1)
        del p["bolgeler"]
    # TBDY 7.6.2.4: birleşim uç bölgesinin alanı, birleşen kolların dikdörtgen perde uç bölgesi
    # alanından az olamaz -> gerekirse en uzun koldaki parça gövdeye doğru uzatılır
    for bid in {p["bolge"] for p in son}:
        ps = [p for p in son if p["bolge"] == bid]
        if len(ps) < 2:
            continue
        A_var = sum((p["s1"] - p["s0"]) * kollar[p["kol"]].t for p in ps)
        A_ist = max(uc_bolge_uzunlugu(kollar[p["kol"]].merkez_L, kollar[p["kol"]].t, kritik, kademe)
                    * kollar[p["kol"]].t for p in ps)
        if A_var < A_ist - 1:
            p = max(ps, key=lambda q: kollar[q["kol"]].merkez_L)
            k = kollar[p["kol"]]
            ek = math.ceil((A_ist - A_var) / k.t / r - 1e-9) * r
            if p["yuz0"] and not p["yuz1"]:
                p["s1"] = min(k.L / 2, p["s1"] + ek)
            elif p["yuz1"] and not p["yuz0"]:
                p["s0"] = max(k.L / 2, p["s0"] - ek)
            else:
                p["s0"], p["s1"] = max(0.0, p["s0"] - ek / 2), min(k.L, p["s1"] + ek / 2)
            p["tip"] += " (alan koşulu ile uzatıldı)"
    return son


# ---------------------------------------------------------------- donatı yerleşimi
def net_min_etkin(d1, d2=None):
    """En küçük net aralık: max(uc_net_min, Ø, 25, 4/3·Dmax) (TS 500 + kullanıcı sınırı)."""
    A = AYAR
    d2 = d1 if d2 is None else d2
    return max(A["uc_net_min"], 25.0, d1, d2, 4 / 3 * A["agrega_dmax"])


def eksen_min(d1, d2=None):
    """İki boyuna donatının en küçük EKSEN aralığı. Bindirme bölgesinde her çubuğun yanında ek
    çubuğu bulunduğundan çift çubuk genişliği (Ø1+Ø2) üzerinden net aralık aranır."""
    d2 = d1 if d2 is None else d2
    gen = (d1 + d2) if AYAR["bindirme_araligi"] else (d1 + d2) / 2
    return gen + net_min_etkin(d1, d2)


def eksen_max(d1, d2=None):
    """İki komşu boyuna donatının en büyük EKSEN aralığı: (Ø1+Ø2)/2 + uc_net_max."""
    d2 = d1 if d2 is None else d2
    return (d1 + d2) / 2 + AYAR["uc_net_max"]


def kume_araligi(de):
    """Uç kümede eksen aralığı (= en küçük, bindirmeli)."""
    return eksen_min(de, de)


def ny_aralik(t, d, det, dh=0.0):
    """Kalınlık doğrultusunda net aralık sınırlarını [net_min(bindirmeli), net_max] sağlayan sıra
    sayıları (artan). Kalın perdede sıra sayısı artar; hiçbiri sağlamıyorsa boş liste."""
    A = AYAR
    span = t - 2 * (A["paspayi"] + max(det, dh, 8.0)) - d
    out = []
    for ny in range(2, A["uc_sira_max"] + 1):
        s_ = span / (ny - 1)
        if eksen_min(d, d) - 1e-6 <= s_ <= d + A["uc_net_max_kalinlik"] + 1e-6:
            out.append(ny)
    return out


def sira_siniri(t, de, det):
    """(eski arayüz) kalınlığa sığan en çok sıra sayısı."""
    r = ny_aralik(t, de, det)
    return r[-1] if r else 2


def aralik_denetimi(cubuklar, zincirler, kalinlik_zincirleri=()):
    """Uç bölge boyuna donatılarının geometrik denetimi.
    cubuklar: [(x, y, Ø)]; zincirler: çevre boyunca komşu çubuk indis dizileri (yüzler, uç ve
    sınır kolonları). Döndürür: net_min (düz), net_min_b (bindirmede, çift çubuk), net_max
    (zincirlerde komşu), ok, ihlaller."""
    A = AYAR
    n = len(cubuklar)
    ihl = []
    nm = nmb = 0.0
    ok_min = True
    if n > 1:
        C = np.asarray([b[:3] for b in cubuklar], float)
        x, y, d = C[:, 0], C[:, 1], C[:, 2]
        iu, ju = np.triu_indices(n, 1)
        L = np.hypot(x[iu] - x[ju], y[iu] - y[ju])
        dd = d[iu] + d[ju]
        net = L - dd / 2
        netb = L - dd if A["bindirme_araligi"] else net
        sinir = np.maximum.reduce([np.full(L.shape, max(A["uc_net_min"], 25.0,
                                                          4 / 3 * A["agrega_dmax"])),
                                   d[iu], d[ju]])
        nm, nmb = float(net.min()), float(netb.min())
        kotu = np.where(netb < sinir - 0.5)[0]
        ok_min = kotu.size == 0
        for q in kotu[:5]:
            ihl.append(f"({x[iu[q]]:.0f},{y[iu[q]]:.0f})–({x[ju[q]]:.0f},{y[ju[q]]:.0f}) "
                       f"net {net[q]:.0f} mm")
    nx, ok_max = 0.0, True
    for z, sinir_ in [(z, A["uc_net_max"]) for z in zincirler] + \
            [(z, A["uc_net_max_kalinlik"]) for z in kalinlik_zincirleri]:
        for a, b in zip(z[:-1], z[1:]):
            xa, ya, da = cubuklar[a][:3]
            xb, yb, db = cubuklar[b][:3]
            net_ = math.hypot(xa - xb, ya - yb) - (da + db) / 2
            if sinir_ == A["uc_net_max"]:
                nx = max(nx, net_)
            if net_ > sinir_ + 0.5:
                ok_max = False
                if len(ihl) < 5:
                    ihl.append(f"({xa:.0f},{ya:.0f})–({xb:.0f},{yb:.0f}) net {net_:.0f} > "
                               f"{A['uc_net_max']:.0f} mm")
    return dict(net_min=nm, net_min_b=nmb, net_max=nx, ok_min=ok_min, ok_max=ok_max,
                ok=ok_min and ok_max, ihlaller=ihl)


def _parca_kolonlari(a, b, uc_taraf, ncol, s_k, s_kuy):
    """Parça boyunca kolon konumları ve tam sıralı (ny) kolonlar.
    uc_taraf 0/1/"iki": o uçta ncol kolonluk küme (aralık s_k), gerisi aralık ≤ s_kuy ile iki
    yüzde; parçanın öbür sınırındaki kolon da tam sıralıdır. None: iki sınır kolonu tam, arası
    iki yüzde."""
    if b - a < s_k - 1e-6:
        # çok kısa parça (kısa kanat / saplama): tek kolon
        m_ = [(a + b) / 2]
        return m_, {0}, (m_ if uc_taraf is not None else [])
    L = b - a
    if uc_taraf is None:
        n = max(2, math.ceil(L / s_kuy - 1e-9) + 1)
        while n > 2 and L / (n - 1) < s_k - 1e-6:
            n -= 1
        ss = list(np.linspace(a, b, n)) if L > 1e-6 else [a]
        return ss, {0, len(ss) - 1}, []
    iki = uc_taraf == "iki"
    for nc in range(ncol, 0, -1):
        span = (nc - 1) * s_k
        if (2 * span if iki else span) > L + 1e-6:
            continue
        if uc_taraf == 1:
            kume = [b - i * s_k for i in range(nc)][::-1]
            bas, son_ = a, kume[0]
        else:
            kume = [a + i * s_k for i in range(nc)]
            bas, son_ = kume[-1], (b - span if iki else b)
        kalan = son_ - bas
        if kalan > 1e-6 and kalan < s_k - 1e-6:
            continue
        ara = []
        if kalan > 1e-6:
            n = math.ceil(kalan / s_kuy - 1e-9)
            ara = [bas + (i + 1) * kalan / n for i in range(n)]
            if uc_taraf == 1:
                ara = [bas] + ara[:-1]          # a sınırından başlar, kümeye kadar
        if uc_taraf == 1:
            ss = ara + kume
            tam = set(range(len(ara), len(ss))) | ({0} if ara else set())
            kume_ss = kume
        elif iki:
            kume2 = [b - i * s_k for i in range(nc)][::-1]
            ss = kume + ara[:-1] + kume2 if ara else kume + kume2
            tam = set(range(nc)) | set(range(len(ss) - nc, len(ss)))
            kume_ss = kume + kume2
        else:
            ss = kume + ara
            tam = set(range(nc)) | {len(ss) - 1}
            kume_ss = kume
        return ss, tam, kume_ss
    # küme sığmıyor: yalnız iki sınır kolonu (tam sıralı)
    return [a, b], {0, 1}, ([a, b] if uc_taraf is not None else [])


_YERLESIM_ONBELLEK = {}


def _birlesim_kapsanir(kollar, parcalar, pi, uc_):
    """pi parçasının uc_ ucundaki birleşim bölgesi başka bir kolun parçasıyla örtülü mü?"""
    p = parcalar[pi]
    k = kollar[p["kol"]]
    s_ = -60.0 if uc_ == 0 else k.L + 60.0
    u, v = k.nokta(s_, 0.0)
    for j, q in enumerate(parcalar):
        if j == pi or q["kol"] == p["kol"]:
            continue
        kk = kollar[q["kol"]]
        sq, wq = kk.yerel(u, v)
        if q["s0"] - 1.0 <= float(sq) <= q["s1"] + 1.0 and abs(float(wq)) <= kk.t / 2 + 1.0:
            return True
    return False


def _parca_imza(kollar, parcalar):
    ki = tuple((round(float(k.P[0]), 1), round(float(k.P[1]), 1), round(float(k.e[0]), 5),
                round(float(k.e[1]), 5), round(float(k.L), 1), round(float(k.t), 1)) for k in kollar)
    pi = tuple((p["kol"], round(p["s0"], 1), round(p["s1"], 1), bool(p["yuz0"]), bool(p["yuz1"]),
                str(p.get("uc_taraf")), p["tip"], round(p.get("hoop0", 0.0), 1),
                round(p.get("hoop1", 0.0), 1)) for p in parcalar)
    return ki, pi


def cok_yerlesim(kollar, parcalar, de, ny, ncol, s_kuy, dw, sw, det, dh, dt=None):
    """Önbellekli: aynı kesit/parça/parametre için yerleşim bir kez kurulur."""
    import copy
    A = AYAR
    anahtar = (_parca_imza(kollar, parcalar), de, ny, ncol, round(s_kuy, 3), dw, round(sw, 3),
               det, dh, dt, A["uc_net_min"], A["uc_net_max"], A["bindirme_araligi"],
               A["paspayi"], A["uc_sira_max"])
    if anahtar in _YERLESIM_ONBELLEK:
        r = _YERLESIM_ONBELLEK[anahtar]
    else:
        if len(_YERLESIM_ONBELLEK) > 20000:
            _YERLESIM_ONBELLEK.clear()
        r = _cok_yerlesim(kollar, parcalar, de, ny, ncol, s_kuy, dw, sw, det, dh, dt)
        _YERLESIM_ONBELLEK[anahtar] = r
    if r is None:
        return None
    uc, govde, s_g, detay, s_min = r
    return list(uc), list(govde), s_g, copy.deepcopy(detay), s_min


def _cok_yerlesim(kollar, parcalar, de, ny, ncol, s_kuy, dw, sw, det, dh, dt=None):
    """Uç bölgelerde: en uçta (serbest uç / dış köşe) ny sıra × ncol kolonluk küme (çap de),
    gerisi iki yüzde aralık ≤ s_kuy, parçanın öbür sınırı tam sıralı (çap dt ≤ de).
    Kümesi olmayan birleşim bölgelerinde çap de, kiriş bölgelerinde dt. Uygun değilse None."""
    dt = dt or de
    A = AYAR
    c = A["paspayi"]
    s_min = kume_araligi(de)
    s_kuy = min(s_kuy, eksen_max(de, dt), eksen_max(dt, dt))
    if s_kuy < eksen_min(de, dt) - 1e-6:
        return None
    uc = []          # (u, v, d, parça no)
    detay = []
    zincir_p = []    # parça başına yüz zincirleri (uc listesindeki indisler)
    zincir_k = []    # kalınlık doğrultusundaki (tam sıralı kolon) zincirler
    for pi, p in enumerate(parcalar):
        k = kollar[p["kol"]]
        e_yuz = c + max(det, dh) + de / 2
        w0 = k.t / 2 - e_yuz
        e_uc = c + max(det, dh * (2 if A["yatay_uc_detay"] == "firkete" else 1)) + de / 2
        nys = ny_aralik(k.t, de, det, dh)
        if not nys:
            return None
        ny_ic = nys[0]
        uyum = [n_ for n_ in nys if ny_ic <= 2 or (n_ - 1) % (ny_ic - 1) == 0]
        ny_p = max([n_ for n_ in uyum if n_ <= ny], default=uyum[0])
        ws_uc = list(np.linspace(-w0, w0, ny_p))          # serbest uçtaki uç yüz: dolu
        ws = ws_uc[::(ny_p - 1) // (ny_ic - 1)] if ny_ic > 2 else [ws_uc[0], ws_uc[-1]]
        # birleşim yüzünde biten kol: ilk (son) kolon karşı kolun yüze yakın sırasıdır; o sıranın
        # çubukları karşı kolun parçasında zaten vardır -> sanal kolon, çubuk konmaz
        bos = set()
        bas_b = (p.get("sanal0") or not p["yuz0"]) and p["s0"] < 1.0 and \
        _birlesim_kapsanir(kollar, parcalar, pi, 0)
        son_b = (p.get("sanal1") or not p["yuz1"]) and p["s1"] > k.L - 1.0 and \
        _birlesim_kapsanir(kollar, parcalar, pi, 1)
        a = p["s0"] - e_yuz if bas_b else p["s0"] + (e_uc if p["yuz0"] else det + de / 2)
        b = p["s1"] + e_yuz if son_b else p["s1"] - (e_uc if p["yuz1"] else det + de / 2)
        r = _parca_kolonlari(a, b, p.get("uc_taraf"), ncol, s_min, s_kuy)
        if r is None:
            return None
        ss, tam, kume_ss = r
        if bas_b:
            bos.add(0)
        if son_b:
            bos.add(len(ss) - 1)
        kume_set = {round(x_, 3) for x_ in kume_ss}
        kume_i = {i for i, s_ in enumerate(ss) if round(s_, 3) in kume_set}
        uc_kol = set()
        if kume_i:
            if p.get("uc_taraf") in (0, "iki"):
                uc_kol.add(min(kume_i))
            if p.get("uc_taraf") in (1, "iki"):
                uc_kol.add(max(kume_i))
        sinir = {i for i in tam if i not in kume_i} if kume_i else set(tam)
        yuz_a, yuz_b = [], []
        for i, s_ in enumerate(ss):
            if kume_ss:
                d_ = de if round(s_, 3) in kume_set else dt
            else:
                d_ = dt if "kiriş" in p["tip"] else de
            if i in bos:
                continue
            kol_i = []
            Px, Py, ex, ey, nx_, ny_ = k.P[0], k.P[1], k.e[0], k.e[1], k.n[0], k.n[1]
            sira_ = ws_uc if i in uc_kol else (ws if i in sinir else (ws[0], ws[-1]))
            for w_ in sira_:
                kol_i.append(len(uc))
                uc.append((float(Px + s_ * ex + w_ * nx_), float(Py + s_ * ey + w_ * ny_), d_, pi))
            yuz_a.append(kol_i[0])
            yuz_b.append(kol_i[-1])
            if i in uc_kol or i in sinir:
                zincir_k.append(kol_i)
        zincir_p += [yuz_a, yuz_b]
        # etriye: birleşimde karşı kolun dış yüzüne kadar uzanır
        h0 = p["s0"] - p.get("hoop0", 0.0) + (c if p["yuz0"] else 0.0)
        h1 = p["s1"] + p.get("hoop1", 0.0) - (c if p["yuz1"] else 0.0)
        # küme yerel donatı oranı
        if kume_ss:
            Lk = (max(kume_ss) - min(kume_ss)) + s_min
            As_k = len(kume_ss) * ny * alan(de)
            if p.get("uc_taraf") == "iki" and len(kume_ss) >= 2:
                n_ = len(kume_ss) // 2
                rho_k = n_ * ny_p * alan(de) / (n_ * s_min * k.t)
            else:
                rho_k = len(kume_ss) * ny_p * alan(de) / (Lk * k.t)
        else:
            rho_k = len(ss) * 2 * alan(de) / (max(b - a, s_min) * k.t)
        n_kume = sum(len(ws_uc) if i in uc_kol else 2 for i in kume_i)
        detay.append(dict(ss=ss, ws=ws, ws_uc=ws_uc, tam=sinir | uc_kol, uc_kol=uc_kol, kume=kume_ss,
                          hoop=(h0, h1), rho_kume=rho_k, bos=bos, n_kume=n_kume))
    # yakın çubukları ayıkla (birleşimlerde iki parça üst üste gelebilir): bindirmeli en küçük
    # eksen aralığından yakın çubuklar tek çubukta birleşir (büyük çap kalır)
    temiz, esle = [], {}
    net0 = max(AYAR["uc_net_min"], 25.0, 4 / 3 * AYAR["agrega_dmax"])
    lap = AYAR["bindirme_araligi"]
    yakin = [[] for _ in uc]
    if len(uc) > 1:
        C = np.asarray([b_[:3] for b_ in uc], float)
        iu, ju = np.triu_indices(len(uc), 1)
        L = np.hypot(C[iu, 0] - C[ju, 0], C[iu, 1] - C[ju, 1])
        dd = C[iu, 2] + C[ju, 2]
        esik = (dd if lap else dd / 2) + np.maximum(net0, np.maximum(C[iu, 2], C[ju, 2]))
        for a_, b2 in zip(iu[L < esik - 1e-6].tolist(), ju[L < esik - 1e-6].tolist()):
            yakin[b2].append(a_)            # a_ < b2
    konum = {}                               # tutulan çubuğun (uc indisi) temiz'deki yeri
    for i, b_ in enumerate(uc):
        j = next((konum[a_] for a_ in sorted(yakin[i]) if a_ in konum), None)
        if j is None:
            konum[i] = esle[i] = len(temiz)
            temiz.append(b_)
        else:
            esle[i] = j
            if b_[2] > temiz[j][2]:
                temiz[j] = (temiz[j][0], temiz[j][1], b_[2], temiz[j][3])
    uc = temiz
    def _esle(zl):
        out_ = []
        for z in zl:
            z2 = []
            for i in z:
                if not z2 or z2[-1] != esle[i]:
                    z2.append(esle[i])
            if len(z2) > 1:
                out_.append(z2)
        return out_
    den = aralik_denetimi(uc, _esle(zincir_p), _esle(zincir_k))
    if not den["ok"]:
        return None
    # gövde düşey
    govde = []
    s_gmin = max(dw + max(25.0, dw), 50.0)
    s_gercek = 0.0
    UCX = np.array([b_[0] for b_ in uc], float)
    UCY = np.array([b_[1] for b_ in uc], float)
    for ki, k in enumerate(kollar):
        kapali = sorted([(p["s0"], p["s1"]) for p in parcalar if p["kol"] == ki])
        araliklar, bas = [], 0.0
        for s0, s1 in kapali:
            if s0 > bas:
                araliklar.append((bas, s0))
            bas = max(bas, s1)
        if bas < k.L:
            araliklar.append((bas, k.L))
        w_g = k.t / 2 - c - dh - dw / 2
        S_, W_ = [], []
        for s0, s1 in araliklar:
            a = s0 + (s_gmin if s0 > 0 else c + dh + dw / 2)
            b = s1 - (s_gmin if s1 < k.L else c + dh + dw / 2)
            if b < a:
                continue
            # kol ortasına göre sabit ızgara: bölge boyları değişse de gövde çubukları katlar
            # arasında aynı konumda kalır (düz devam)
            ss_g = _izgara(a - 75.0, b + 75.0, k.L / 2, sw)
            if not ss_g:
                ss_g = [(a + b) / 2]
            s_gercek = max(s_gercek, float(sw))
            for s_ in ss_g:
                S_ += [s_, s_]
                W_ += [-w_g, w_g]
        if not S_:
            continue
        S_, W_ = np.array(S_), np.array(W_)
        U_ = k.P[0] + S_ * k.e[0] + W_ * k.n[0]
        V_ = k.P[1] + S_ * k.e[1] + W_ * k.n[1]
        iyi = np.ones(U_.size, bool)
        for jj, kk in enumerate(kollar):
            if jj != ki:
                iyi &= ~kk.icinde(U_, V_)
        if UCX.size:
            dmin = np.min(np.hypot(U_[:, None] - UCX[None, :], V_[:, None] - UCY[None, :]), axis=1)
            iyi &= dmin >= s_gmin * 0.9
        govde += [(float(u), float(v), dw) for u, v in zip(U_[iyi], V_[iyi])]
    if detay:
        detay[0]["aralik"] = den
    return uc, govde, s_gercek or sw, detay, s_min


# ---------------------------------------------------------------- 2B lif kesiti
_BETON_ONBELLEK = {}


class LifKesit2B:
    """Çok kollu kesit için lif modeli ve P–M2–M3 etkileşim yüzeyi.
    Beton liflerinin katkısı yalnız kesit geometrisine bağlıdır; aynı kesitte denenen bütün
    donatı düzenleri için bir kez hesaplanıp önbellekte tutulur (her adayda yalnız çelik)."""

    def __init__(self, kollar, uc, govde, m, h=None, na=None, nc=None, bA=None):
        self.m = m
        h = h or AYAR["lif_boyutu"]
        self._kollar, self._h = kollar, h
        fu, fv, fA = [], [], []
        kose_u, kose_v = [], []
        for k in kollar:
            nx, nw = max(1, math.ceil(k.L / h)), max(1, math.ceil(k.t / h))
            s = (np.arange(nx) + 0.5) * k.L / nx
            w = (np.arange(nw) + 0.5) * k.t / nw - k.t / 2
            S, W = np.meshgrid(s, w, indexing="ij")
            U = k.P[0] + S * k.e[0] + W * k.n[0]
            V = k.P[1] + S * k.e[1] + W * k.n[1]
            fu.append(U.ravel())
            fv.append(V.ravel())
            fA.append(np.full(U.size, k.L / nx * k.t / nw))
            for q in k.koseler():
                kose_u.append(q[0])
                kose_v.append(q[1])
        self.fu, self.fv, self.fA = np.concatenate(fu), np.concatenate(fv), np.concatenate(fA)
        bars = list(uc) + list(govde)
        self.bu = np.array([b[0] for b in bars], float)
        self.bv = np.array([b[1] for b in bars], float)
        self.bA = np.array([alan(b[2]) for b in bars], float) if bA is None else \
            np.asarray(bA, float)
        self.ku, self.kv = np.array(kose_u), np.array(kose_v)
        self.na = na or 72
        self.nc = nc or 160
        self.n_lif = self.fu.size
        self._yuzey = None
        self._poly_ = None

    @property
    def _poly(self):
        """Lif çokgenleri (yalnız çizim için; gerektiğinde üretilir)."""
        if self._poly_ is None:
            h = self._h
            out = []
            for k in self._kollar:
                nx, nw = max(1, math.ceil(k.L / h)), max(1, math.ceil(k.t / h))
                hs, hw = k.L / nx / 2, k.t / nw / 2
                s = (np.arange(nx) + 0.5) * k.L / nx
                w = (np.arange(nw) + 0.5) * k.t / nw - k.t / 2
                S, W = [a.ravel() for a in np.meshgrid(s, w, indexing="ij")]
                kose = []
                for a, b in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
                    ss_, ww_ = S + a * hs, W + b * hw
                    kose.append(np.stack([k.P[0] + ss_ * k.e[0] + ww_ * k.n[0],
                                          k.P[1] + ss_ * k.e[1] + ww_ * k.n[1]], -1))
                out.append(np.stack(kose, 1))            # (n, 4, 2)
            self._poly_ = np.concatenate(out, 0)
        return self._poly_

    def _beton(self):
        """(ths, c, smax, Nc, Muc, Mvc) – kesit geometrisine bağlı, önbellekli."""
        m, A = self.m, AYAR
        anahtar = (tuple((round(float(k.P[0]), 1), round(float(k.P[1]), 1),
                          round(float(k.e[0]), 6), round(float(k.e[1]), 6),
                          round(float(k.L), 1), round(float(k.t), 1)) for k in self._kollar),
                   self._h, self.na, self.nc, round(m["fcd"], 4), A["eps_c0"], A["eps_cu"])
        r = _BETON_ONBELLEK.get(anahtar)
        if r is not None:
            return r
        if len(_BETON_ONBELLEK) > 40:
            _BETON_ONBELLEK.clear()
        ecu = A["eps_cu"]
        D = max(np.ptp(self.ku), np.ptp(self.kv))
        c = np.geomspace(1e-4 * D, 1e3 * D, self.nc)
        ths = np.linspace(0, 2 * math.pi, self.na, endpoint=False)
        smax = np.empty(self.na)
        Nc = np.empty((self.na, self.nc))
        Muc, Mvc = np.empty_like(Nc), np.empty_like(Nc)
        cc = c[:, None]
        for i, th in enumerate(ths):
            ct, st = math.cos(th), math.sin(th)
            smax[i] = np.max(self.ku * ct + self.kv * st)
            dc = smax[i] - (self.fu * ct + self.fv * st)
            Fc = sigma_beton(ecu * (cc - dc[None, :]) / cc, m) * self.fA[None, :]
            Nc[i] = Fc.sum(1)
            Muc[i] = Fc @ self.fu
            Mvc[i] = Fc @ self.fv
        r = (ths, c, smax, Nc, Muc, Mvc)
        _BETON_ONBELLEK[anahtar] = r
        return r

    def _NM(self, th, c):
        ecu = AYAR["eps_cu"]
        ct, st = math.cos(th), math.sin(th)
        smax = np.max(self.ku * ct + self.kv * st)
        dc = smax - (self.fu * ct + self.fv * st)
        ds = smax - (self.bu * ct + self.bv * st)
        c = c[:, None]
        ec = ecu * (c - dc[None, :]) / c
        es = ecu * (c - ds[None, :]) / c
        Fc = sigma_beton(ec, self.m) * self.fA[None, :]
        Fs = (sigma_celik(es, self.m) - sigma_beton(es, self.m)) * self.bA[None, :]
        N = Fc.sum(1) + Fs.sum(1)
        Mu = (Fc * self.fu).sum(1) + (Fs * self.bu).sum(1)      # Σ F·u
        Mv = (Fc * self.fv).sum(1) + (Fs * self.bv).sum(1)      # Σ F·v
        M3 = -AYAR["M3_isaret"] * Mu
        M2 = AYAR["M2_isaret"] * Mv
        return N, M2, M3

    def yuzey(self):
        if self._yuzey is None:
            ths, c, smax, Nc, Muc, Mvc = self._beton()
            ecu = AYAR["eps_cu"]
            cc = c[:, None]
            Ns, M2s, M3s = [], [], []
            for i, th in enumerate(ths):
                ct, st = math.cos(th), math.sin(th)
                ds = smax[i] - (self.bu * ct + self.bv * st)
                es = ecu * (cc - ds[None, :]) / cc
                Fs = (sigma_celik(es, self.m) - sigma_beton(es, self.m)) * self.bA[None, :]
                N = Nc[i] + Fs.sum(1)
                Mu = Muc[i] + Fs @ self.bu
                Mv = Mvc[i] + Fs @ self.bv
                Ns.append(np.maximum.accumulate(N))
                M2s.append(AYAR["M2_isaret"] * Mv)
                M3s.append(-AYAR["M3_isaret"] * Mu)
            self._yuzey = (ths, c, np.array(Ns), np.array(M2s), np.array(M3s))
        return self._yuzey

    def N_sinir(self):
        _, _, N, _, _ = self.yuzey()
        return N[:, 0].max(), N[:, -1].min()

    def kontur(self, Nd):
        ths, c, N, M2, M3 = self.yuzey()
        x, y = [], []
        for i in range(len(ths)):
            if N[i, 0] <= Nd <= N[i, -1]:
                x.append(np.interp(Nd, N[i], M2[i]))
                y.append(np.interp(Nd, N[i], M3[i]))
        return np.array(x), np.array(y)

    def kapasite(self, Nd, M2d, M3d):
        """Talep doğrultusundaki moment kapasitesi |Mr| (Nd düzleminde). Aşım -> nan"""
        x, y = self.kontur(Nd)
        if x.size < 3:
            return float("nan")
        phi = np.arctan2(y, x)
        r = np.hypot(x, y)
        o = np.argsort(phi)
        phi, r = phi[o], r[o]
        phi = np.concatenate([phi - 2 * math.pi, phi, phi + 2 * math.pi])
        r = np.tile(r, 3)
        return float(np.interp(math.atan2(M3d, M2d), phi, r))

    def talep_orani(self, talepler):
        """talepler: [(N, M2, M3)] -> Md/Mr dizisi (tüm talepler birlikte, vektörel)."""
        T = np.asarray(talepler, float).reshape(-1, 3)
        if T.size == 0:
            return np.zeros(0)
        ths, c, N, M2, M3 = self.yuzey()
        Nmin, Nmax = N[:, 0].max(), N[:, -1].min()
        Nd, M2d, M3d = T[:, 0], T[:, 1], T[:, 2]
        Md = np.hypot(M2d, M3d)
        na = len(ths)
        X = np.empty((na, len(Nd)))
        Y = np.empty_like(X)
        V = np.empty(X.shape, bool)
        for i in range(na):
            X[i] = np.interp(Nd, N[i], M2[i])
            Y[i] = np.interp(Nd, N[i], M3[i])
            V[i] = (N[i, 0] <= Nd) & (Nd <= N[i, -1])
        PHI = np.arctan2(Y, X)
        R = np.hypot(X, Y)
        hedef = np.arctan2(M3d, M2d)
        out = np.empty(len(Nd))
        for j in range(len(Nd)):
            if Nd[j] > Nmax or Nd[j] < Nmin:
                out[j] = np.inf
                continue
            if Md[j] < 1e-6 * max(1.0, abs(Nmax)):
                out[j] = Nd[j] / Nmax if Nd[j] > 0 else Nd[j] / Nmin
                continue
            v = V[:, j]
            if v.sum() < 3:
                out[j] = np.inf
                continue
            ph, r = PHI[v, j], R[v, j]
            o = np.argsort(ph)
            ph, r = ph[o], r[o]
            ph3 = np.concatenate([ph - 2 * math.pi, ph, ph + 2 * math.pi])
            Mr = float(np.interp(hedef[j], ph3, np.tile(r, 3)))
            out[j] = np.inf if not Mr or math.isnan(Mr) else Md[j] / Mr
        return out

    def durum(self, Nd, M2d, M3d):
        """Talep doğrultusuna en yakın θ'da, Nd'deki şekil değiştirme alanı."""
        ths, c, N, M2, M3 = self.yuzey()
        hedef = math.atan2(M3d, M2d)
        en_iyi, j = 1e9, None
        for i in range(len(ths)):
            if N[i, 0] <= Nd <= N[i, -1]:
                x, y = np.interp(Nd, N[i], M2[i]), np.interp(Nd, N[i], M3[i])
                fark = abs((math.atan2(y, x) - hedef + math.pi) % (2 * math.pi) - math.pi)
                if fark < en_iyi:
                    en_iyi, j = fark, i
        if j is None:
            return None
        th = ths[j]
        cc = float(np.exp(np.interp(Nd, N[j], np.log(c))))
        ct, st = math.cos(th), math.sin(th)
        smax = np.max(self.ku * ct + self.kv * st)
        ecu = AYAR["eps_cu"]
        ec = ecu * (cc - (smax - (self.fu * ct + self.fv * st))) / cc
        es = ecu * (cc - (smax - (self.bu * ct + self.bv * st))) / cc
        return ec, sigma_beton(ec, self.m), es, sigma_celik(es, self.m), th, cc

    def meridyen(self, phi, n=60):
        """P – M eğrisi: M2–M3 düzleminde phi doğrultusunda (M kapasitesi, N)."""
        Nmin, Nmax = self.N_sinir()
        Ns = np.linspace(Nmin * 0.999, Nmax * 0.999, n)
        M0 = 1e12                       # birim doğrultuda büyük bir moment: Mr = M0 / oran
        r = self.talep_orani([(Nd, M0 * math.cos(phi), M0 * math.sin(phi)) for Nd in Ns])
        with np.errstate(divide="ignore"):
            Ms = np.where(np.isfinite(r) & (r > 0), M0 / r, np.nan)
        return Ns, Ms


# ---------------------------------------------------------------- tasarım
def cok_etriye(kollar, parcalar, detay, de, kritik, m):
    """Her uç bölge parçası: etriye(ler) + çirozlar. TBDY 7.6.5.2: Ø ≥ 8, a ≤ 25Ø, kritik bölgede
    Ash ≥ 2/3·Denk.(7.1b) ve 50 ≤ s ≤ min(150, 6Ø, bw/3); dışında s ≤ min(bw, 200).
    detay'a hooplar / ciroz_s / a_max yazılır; en elverişsiz parça esas alınır."""
    A = AYAR
    c = A["paspayi"]
    tmin = min(kollar[p["kol"]].t for p in parcalar)
    d0 = A["etriye_capi_kritik"] if kritik else A["etriye_capi_ust"]
    adaylar = [d for d in A["etriye_caplari"] if d >= max(8, d0)]
    s_max = min(150.0, 6 * de, tmin / 3.0) if kritik else min(tmin, 200.0)

    def duzen(d, s=None):
        a_max = 0.0
        for p, x in zip(parcalar, detay):
            k = kollar[p["kol"]]
            hp, kenar = etriye_bolumle(x["ss"], x["hoop"][0], x["hoop"][1], de, d)
            x["hooplar"] = hp
            kol_ = [s_ for i, s_ in enumerate(x["ss"]) if i not in x.get("bos", set())]
            sabit = [x["ss"][i] for i in kenar]
            n_g = 0
            if s is not None and kritik:
                bk_x = x["hoop"][1] - x["hoop"][0]
                n_g = math.ceil((2 / 3) * 0.075 * s * bk_x * m["fck"] / A["fywk"] / alan(d) - 1e-9)
            x["ciroz_s"] = ciroz_konumlari(kol_, sabit, 25 * d, n_g - 2 * len(hp) + len(sabit))
            bacak = sorted(set(round(v, 1) for v in sabit + list(x["ciroz_s"])))
            a_b = max(np.diff(bacak)) if len(bacak) > 1 else 0.0
            a_k = max(np.diff(x["ws"])) if len(x["ws"]) > 2 else (k.t - 2 * c - d)
            x["a_max"] = float(max(a_b, a_k))
            x["n_y"] = 2 * len(hp) + len(x["ciroz_s"])
            x["n_y_gerek"] = n_g
            x["n_x"] = len(x["ws"])
            a_max = max(a_max, x["a_max"])
        return a_max

    for d in adaylar:
        a_max = duzen(d)
        if a_max > 25 * d + 1e-6:
            continue
        s = math.floor(s_max / A["s_yuvarlama"]) * A["s_yuvarlama"]
        ozet = dict(n_y=max(x["n_y"] for x in detay), n_x=max(x["n_x"] for x in detay), a_max=a_max)
        if not kritik:
            return dict(d=d, s=s, **ozet,
                        not_=f"kritik dışı: s ≤ min(bw, 200); a={a_max:.0f} ≤ 25Ø={25 * d:.0f}")
        while s >= 50:
            ok = True
            a_max = duzen(d, s)
            if a_max > 25 * d + 1e-6:
                s -= A["s_yuvarlama"]
                continue
            ozet = dict(n_y=max(x["n_y"] for x in detay), n_x=max(x["n_x"] for x in detay),
                        a_max=a_max)
            for p, x in zip(parcalar, detay):
                k = kollar[p["kol"]]
                bk_x = x["hoop"][1] - x["hoop"][0]
                bk_y = k.t - 2 * c
                if x["n_y"] * alan(d) < (2 / 3) * 0.075 * s * bk_x * m["fck"] / A["fywk"] or \
                        x["n_x"] * alan(d) < (2 / 3) * 0.075 * s * bk_y * m["fck"] / A["fywk"]:
                    ok = False
                    break
            if ok:
                return dict(d=d, s=s, **ozet,
                            not_=f"kritik: s ≤ min(150, 6Ø, bw/3)={s_max:.0f}; a={a_max:.0f} ≤ "
                                 f"25Ø={25 * d:.0f}; her parçada Ash ≥ 2/3·Denk.(7.1b) sağlandı")
            s -= A["s_yuvarlama"]
    a_max = duzen(adaylar[-1])
    return dict(d=adaylar[-1], s=50.0, n_y=0, n_x=0, a_max=a_max,
                not_=f"UYARI: enine donatı koşulu sağlanamadı (a={a_max:.0f} mm)")


def cok_kesme(kollar, kat_kesme, Ve_carpan, m):
    """kat_kesme: [(kat, V2d, V3d)] N. Kollar doğrultularına göre V2 veya V3'ü taşır."""
    satir = []
    for yon, idx in (("V2", 1), ("V3", 2)):
        kl = [k for k in kollar if (abs(k.e[0]) if yon == "V2" else abs(k.e[1])) >= 0.5]
        if not kl:
            continue
        Ach = sum(k.L * k.t for k in kl)
        bw = min(k.t for k in kl)
        Vmax = _aktif()["vmax"] * Ach * math.sqrt(m["fck"])
        for r in kat_kesme:
            Vd = r[idx]
            Ve = Vd * Ve_carpan
            rho = max(0.0025, (Ve / Ach - 0.65 * m["fctd"]) / m["fywd"])
            sec = govde_sec(bw, rho, AYAR["s_govde_max"])
            if sec is None:
                d = AYAR["govde_caplari"][-1]
                sec = (d, 100.0, 2 * alan(d) / 100 / bw)
            Vr = Ach * (0.65 * m["fctd"] + sec[2] * m["fywd"])
            satir.append(dict(yon=yon, kat=r[0], Vd=Vd, Ve=Ve, rho=rho, d=sec[0], s=sec[1],
                              Vr=Vr, Vmax=Vmax, ok=(Ve <= Vr * 1.0001) and (Ve <= Vmax),
                              kollar=[k.ad for k in kl]))
    return satir


def bolge_gerekli_donati(kollar, parcalar, uc, gov, m, talepler):
    """Tüm kesitin P–M2–M3 lif analizinden her uç bölge (B1, B2, ...) için gerekli boyuna donatı.
    Çubuk konumları sabit tutulup her bölgenin donatı alanı bir katsayıyla ölçeklenir:
      1) bütün bölgeler için ortak en küçük katsayı (ikiye bölme),
      2) bölgeler sırayla, diğerleri sabitken, kendi en küçük katsayısına indirilir.
    Sonuç: bölge başına PMM gereği As (mm²) – minimum donatıdan küçük çıkabilir."""
    bid = [parcalar[b[3]]["bolge"] for b in uc]
    bolgeler = sorted(set(bid))
    A0 = {z: sum(alan(b[2]) for b, zb in zip(uc, bid) if zb == z) for z in bolgeler}
    a_uc = np.array([alan(b[2]) for b in uc], float)
    a_gv = np.array([alan(b[2]) for b in gov], float)
    zi = np.array([bolgeler.index(z) for z in bid])

    def oran(f, ince=False):
        bA = np.concatenate([a_uc * np.asarray(f)[zi], a_gv])
        lk = LifKesit2B(kollar, uc, gov, m, bA=bA, **({} if ince else dict(na=36, nc=110)))
        r = lk.talep_orani(talepler)
        return float(np.max(r)) if len(r) else 0.0

    nz = len(bolgeler)
    bir = [1.0] * nz
    r1 = oran(bir)
    # (1) ortak katsayı
    if r1 <= 1.0:
        lo, hi = 0.0, 1.0
    else:
        lo, hi = 1.0, 2.0
        while oran([hi] * nz) > 1.0 and hi < 16:
            lo, hi = hi, hi * 2
    yeterli = oran([hi] * nz) <= 1.0
    if yeterli:
        for _ in range(8):
            md = (lo + hi) / 2
            if oran([md] * nz) <= 1.0:
                hi = md
            else:
                lo = md
    f = [hi] * nz
    # (2) bölge bölge indir
    if yeterli:
        for j in sorted(range(nz), key=lambda j: -A0[bolgeler[j]]):
            lo_, hi_ = 0.0, f[j]
            for _ in range(6):
                md = (lo_ + hi_) / 2
                f2 = list(f)
                f2[j] = md
                if oran(f2) <= 1.0:
                    hi_ = md
                else:
                    lo_ = md
            f[j] = hi_
        # ince ağla doğrulama (gerekirse hepsi %3 adımlarla artırılır)
        for _ in range(10):
            if oran(f, ince=True) <= 1.0:
                break
            f = [x * 1.03 + 0.02 for x in f]
    out = []
    for j, z in enumerate(bolgeler):
        pis = [i for i, p in enumerate(parcalar) if p["bolge"] == z]
        As_p = f[j] * A0[z]
        As_g = max(A0[z], As_p)
        oneri = []
        for d in (16, 20, 25, 28, 32):
            oneri.append(f"{math.ceil(As_g / alan(d) - 1e-9)}Ø{d}")
        out.append(dict(bolge=z, parcalar=pis, tip=" + ".join(sorted({parcalar[i]["tip"] for i in pis})),
                        kollar=sorted({kollar[parcalar[i]["kol"]].ad for i in pis}),
                        As_min=A0[z], As_pmm=As_p if yeterli else float("inf"), As_gerek=As_g,
                        n_min=int(sum(1 for zb in bid if zb == z)),
                        oneri=oneri, yeterli=yeterli))
    return out


def kol_talepleri(kollar, talepler):
    """Kesit kuvvetlerinin (N, M2, M3) doğrusal-elastik gerilme dağılımıyla kollara paylaşımı:
    her kol için eksenel kuvvet N_i ve kendi düzlemindeki moment M_i (kol ağırlık merkezine göre).
    Döndürür: kol başına en büyük basınç/çekme ve en büyük |M_i| (eşlik eden N_i ile)."""
    A = AYAR
    lk = LifKesit2B(kollar, [], [], None, h=50.0)
    u, v, a = lk.fu, lk.fv, lk.fA
    K = np.array([[a.sum(), (a * u).sum(), (a * v).sum()],
                  [(a * u).sum(), (a * u * u).sum(), (a * u * v).sum()],
                  [(a * v).sum(), (a * u * v).sum(), (a * v * v).sum()]])
    T = np.asarray(talepler, float).reshape(-1, 3)
    if not len(T):
        return []
    N, M2, M3 = T[:, 0], T[:, 1], T[:, 2]
    Mu = -M3 / A["M3_isaret"]          # Σ F·u
    Mv = M2 / A["M2_isaret"]           # Σ F·v
    kat = np.linalg.solve(K, np.vstack([N, Mu, Mv]))      # σ = k0 + k1·u + k2·v  (3 × n)
    out = []
    for k in kollar:
        mask = k.icinde(u, v, 1.0)
        if not mask.any():
            continue
        uu, vv, aa = u[mask], v[mask], a[mask]
        sig = kat[0][None, :] + kat[1][None, :] * uu[:, None] + kat[2][None, :] * vv[:, None]
        Ni = (sig * aa[:, None]).sum(0)
        s_, _ = k.yerel(uu, vv)
        sc = (aa * s_).sum() / aa.sum()
        Mi = (sig * (aa * (s_ - sc))[:, None]).sum(0)
        j = int(np.argmax(np.abs(Mi)))
        out.append(dict(kol=k.ad, L=k.L, t=k.t, N_bas=float(Ni.max()), N_cek=float(Ni.min()),
                        M_max=float(abs(Mi[j])), N_M=float(Ni[j])))
    return out


def cok_grup_tasarla(ad, kollar, kritik, talepler, kat_kesme, Ve_carpan, h_kat_max, m,
                     kiris_parcalari=None, kademe=0.0, kis=None):
    A = AYAR
    uyarilar, kontroller = [], []
    tmin = min(k.t for k in kollar)
    bw_min = max(200.0, h_kat_max / 20) if A["ozel_kosul_7613"] else max(250.0, h_kat_max / 16)
    kontroller.append((f"bw ≥ {bw_min:.0f} mm (en ince kol)", tmin, bw_min, tmin >= bw_min - 1e-6))
    for k in kollar:
        if k.merkez_L / k.t < 6:
            uyarilar.append(f"Kol {k.ad}: ℓ/b = {k.merkez_L / k.t:.1f} < 6 (kol tek başına perde "
                            f"koşulunu sağlamıyor; kesit bütün olarak hesaplandı).")
    Ac = sum(k.L * k.t for k in kollar)
    Nmax = max(t[0] for t in talepler)
    kontroller.append(("Nd,max ≤ 0.35·Ac·fck [kN]", Nmax / 1e3, 0.35 * Ac * m["fck"] / 1e3,
                       Nmax <= 0.35 * Ac * m["fck"]))
    rho_min = rho_uc_min(kritik, kademe)
    dw, sw, _ = govde_sec(tmin, 0.0025, A["s_govde_max"])
    ks = cok_kesme(kollar, kat_kesme, Ve_carpan, m)
    kr = max(ks, key=lambda r: (r["rho"], -r["s"]))
    dh, sh = kr["d"], kr["s"]
    for yon in ("V2", "V3"):
        r_ = [r for r in ks if r["yon"] == yon]
        if r_:
            g_ = max(r_, key=lambda r: r["Ve"])
            kontroller.append((f"Ve ≤ 0.85·Ach·√fck [kN] ({yon})", g_["Ve"] / 1e3, g_["Vmax"] / 1e3,
                               g_["Ve"] <= g_["Vmax"]))
            kontroller.append((f"Ve ≤ Vr [kN] ({yon}, en elverişsiz kat)", g_["Ve"] / 1e3,
                               g_["Vr"] / 1e3, g_["Ve"] <= g_["Vr"] * 1.0001))
    det = A["etriye_capi_kritik"] if kritik else A["etriye_capi_ust"]
    kis = dict(kis or {})
    ek_parca = [dict(q) for q in kis.get("parca_ek", [])]
    basit = str(A.get("cok_kollu_yontem", "basit")).lower() == "basit"

    def uzay(guclu_once=False, dt_kucuk_once=False):
        """Aday parametreleri (de, ny, ncol, s_, dt). guclu_once: en çok donatılıdan başlar."""
        des = [d for d in A["uc_caplari"] if d >= kis.get("de_min", 0)]
        for de in (sorted(des, reverse=True) if guclu_once else des):
            nys_k = [ny_aralik(k.t, de, det, dh) for k in kollar]
            if not all(nys_k):
                continue
            ny_ust = max(n_[-1] for n_ in nys_k)
            ny_lo = max(min(n_[0] for n_ in nys_k), kis.get("ny_min", 0)) if basit else \
                max(min(A["uc_sira_min"], ny_ust), kis.get("ny_min", 0))
            nyl = list(range(ny_lo, ny_ust + 1))
            dts = sorted(d for d in A["uc_caplari"] if 14 <= d <= de and d in (14, 16, 20, de)
                         and d >= kis.get("dt_min", 0))
            ncl = list(range(max(1, kis.get("ncol_min", 1)), A["uc_kume_kolon_max"] + 1))
            if guclu_once:
                nyl, ncl = nyl[::-1], ncl[::-1]
                dts = dts if dt_kucuk_once else dts[::-1]
            for ny in nyl:
                for dt in dts:
                    s_ler = sorted({min(s_, eksen_max(de, dt), eksen_max(dt, dt))
                                    for s_ in A["kuyruk_araliklari"]}, reverse=True)
                    s_ler = [x_ for x_ in s_ler if x_ >= eksen_min(de, dt) - 1e-6]
                    if guclu_once and not dt_kucuk_once:
                        s_ler = s_ler[::-1]
                    for ncol in ncl:
                        for s_ in s_ler:
                            yield de, ny, ncol, s_, dt

    def degerlendir(parcalar, de, ny, ncol, s_, dt):
        y = cok_yerlesim(kollar, parcalar, de, ny, ncol, s_, dw, sw, det, dh, dt)
        if y is None:
            return None
        uc, gov, s_g, detay, s_min = y
        # bölge bazında alt sınır ve yerel oran
        for bid in {p["bolge"] for p in parcalar}:
            pi = [i for i, p in enumerate(parcalar) if p["bolge"] == bid]
            As_b = sum(alan(b[2]) for b in uc if b[3] in pi)
            alan_b = sum((parcalar[i]["s1"] - parcalar[i]["s0"]) *
                         kollar[parcalar[i]["kol"]].t for i in pi)
            if all(parcalar[i]["tip"].startswith("kiriş") for i in pi):
                req = 4 * alan(14)
            else:
                req = max(rho_min * max(kollar[parcalar[i]["kol"]].t *
                                        kollar[parcalar[i]["kol"]].merkez_L
                                        for i in pi), 4 * alan(14))
            if As_b < req - 1e-6 or As_b / alan_b > A["rho_uc_max"]:
                return None
        As = sum(alan(b[2]) for b in uc)
        if As < kis.get("As_min", 0.0) - 1e-6:
            return None
        yog = sum(alan(de) * x.get("n_kume", 0) for x in detay) / As
        return (round(As, 1), len(uc), de, ny, ncol, s_, dt, round(yog, 2))

    def adaylar(parcalar):
        out = {t_ for t_ in (degerlendir(parcalar, *q) for q in uzay()) if t_}
        # alanı %5 içinde olanlarda: en uçta yoğunlaşan (küme payı büyük), sonra az çubuk
        return sorted(out, key=lambda t: (math.floor(math.log(t[0]) / math.log(1.05)),
                                          -t[7], t[1]))

    def guclu_adaylar(parcalar):
        """Hızlı ön kontrol için iki temsilci: en çok donatılı ve uca en çok yoğunlaşmış."""
        out = []
        for dk in (False, True):
            for q in uzay(guclu_once=True, dt_kucuk_once=dk):
                t_ = degerlendir(parcalar, *q)
                if t_:
                    out.append(t_)
                    break
        return list(dict.fromkeys(out))

    gerekli, kol_tal = None, None
    if basit:
        # minimum başlık bölgesi + minimum donatı; PMM'den bölge bölge gerekli donatı
        parcalar = _parca_birlestir(cok_bolgeler(kollar, kritik, 0.0, kademe)
                                    + list(kiris_parcalari or []) + [dict(q) for q in ek_parca])
        ad_ = adaylar(parcalar)
        print(f"      minimum başlık bölgesi: {len(ad_)} aday düzen, en az donatılı seçiliyor", flush=True)
        if not ad_:
            raise RuntimeError("Uç bölgeler için uygun donatı düzeni bulunamadı.")
        t_ = min(ad_, key=lambda t: (t[0], t[1], -t[7]))
        y_ = cok_yerlesim(kollar, parcalar, t_[2], t_[3], t_[4], t_[5], dw, sw, det, dh, t_[6])
        lk_ = LifKesit2B(kollar, y_[0], y_[1], m)
        secim = (parcalar, t_[2], t_[3], t_[4], t_[5], y_[0], y_[1], y_[2], y_[3], lk_,
                 float(np.max(lk_.talep_orani(talepler))), t_[6])
        Lu_ek = 0.0
        print("      PMM: bölge bölge gerekli donatı hesaplanıyor...", flush=True)
        gerekli = bolge_gerekli_donati(kollar, parcalar, y_[0], y_[1], m, talepler)
        kol_tal = kol_talepleri(kollar, talepler)
    else:
        # ---- arama: (1) uç bölge uzatması Lu_ek için en güçlü adayla üstel + ikiye bölme araması,
        #      (2) bulunan Lu_ek'te adaylar %5 alan bantlarında: en düşük geçen bant ikiye bölmeyle,
        #      o bantta tercih sırasına göre ilk geçen aday. Her değerlendirme önbellekli.
        adim = A["Lu_yuvarlama"]
        n_max = int(0.25 * max(k.L for k in kollar) // adim)
        parcs, tumler, oranlar = {}, {}, {}

        def parca(i):
            if i not in parcs:
                parc = cok_bolgeler(kollar, kritik, i * adim, kademe) + list(kiris_parcalari or [])
                parcs[i] = _parca_birlestir(parc + [dict(q) for q in ek_parca])
            return parcs[i]

        def durum(i):
            if i not in tumler:
                tumler[i] = adaylar(parca(i))
                print(f"      Lu ek = {i * adim:.0f} mm: {len(tumler[i])} aday düzen", flush=True)
            return parca(i), tumler[i]

        def oran(i, t_):
            anahtar = (i, t_[2:7])
            if anahtar not in oranlar:
                parc = parca(i)
                y_ = cok_yerlesim(kollar, parc, t_[2], t_[3], t_[4], t_[5], dw, sw, det, dh, t_[6])
                lk_ = LifKesit2B(kollar, y_[0], y_[1], m)
                oranlar[anahtar] = (float(np.max(lk_.talep_orani(talepler))), y_, lk_)
            return oranlar[anahtar][0]

        def temsilciler(liste):
            """Bandın en çok donatılı ve uca en çok yoğunlaşmış adayları."""
            a_ = max(liste, key=lambda t: t[0])
            b_ = max(liste, key=lambda t: (t[7], t[0]))
            return [a_] if a_ == b_ else [a_, b_]

        def gecer(i):
            g_ = guclu_adaylar(parca(i))
            ok = bool(g_) and any(oran(i, t_) <= 1.0 for t_ in g_)
            print(f"      Lu ek = {i * adim:.0f} mm: en güçlü düzen {'yeterli' if ok else 'yetmiyor'}",
                  flush=True)
            return ok

        def sec(i):
            ad_ = durum(i)[1]
            if not ad_:
                return None
            bant = lambda t: math.floor(math.log(t[0]) / math.log(1.05))
            bantlar = []
            for t_ in ad_:                      # ad_ zaten (bant, -yoğunlaşma, n) sıralı
                if bantlar and bant(bantlar[-1][0]) == bant(t_):
                    bantlar[-1].append(t_)
                else:
                    bantlar.append([t_])
            lo, hi = 0, len(bantlar) - 1
            if not any(oran(i, t_) <= 1.0 for t_ in temsilciler(bantlar[hi])):
                aday_sira = ad_                   # tekdüze değil: sırayla hepsi
            else:
                while lo < hi:
                    md = (lo + hi) // 2
                    if any(oran(i, t_) <= 1.0 for t_ in temsilciler(bantlar[md])):
                        hi = md
                    else:
                        lo = md + 1
                aday_sira = [t_ for b_ in bantlar[max(0, lo - 1):] for t_ in b_]
            for t_ in aday_sira:
                if oran(i, t_) <= 1.0:
                    _, y_, lk_ = oranlar[(i, t_[2:7])]
                    uc, gov, s_g, detay, s_min = y_
                    return (durum(i)[0], t_[2], t_[3], t_[4], t_[5], uc, gov, s_g, detay, lk_,
                            oranlar[(i, t_[2:7])][0], t_[6])
            return None

        secim, i_bul = None, None
        if gecer(0):
            i_bul = 0
        else:
            onceki, j = 0, 1
            while j <= n_max and not gecer(j):
                onceki, j = j, j * 2
            if j > n_max and n_max > onceki and gecer(n_max):
                j = n_max
            if j <= n_max:
                lo, hi = onceki + 1, j
                while lo < hi:
                    md = (lo + hi) // 2
                    if gecer(md):
                        hi = md
                    else:
                        lo = md + 1
                i_bul = lo
        if i_bul is not None:
            i_ = i_bul
            while secim is None and i_ <= n_max:
                secim = sec(i_)
                if secim is None:
                    i_ += 1
            Lu_ek = i_ * adim
        else:
            Lu_ek = n_max * adim
    if secim is None:
        parcalar = _parca_birlestir(cok_bolgeler(kollar, kritik, 0.0, kademe)
                                    + list(kiris_parcalari or []) + [dict(q) for q in ek_parca])
        ad_ = adaylar(parcalar)
        if not ad_:
            raise RuntimeError("Uç bölgeler için uygun donatı düzeni bulunamadı.")
        As, nb, de, ny, ncol, s_, dt, _y = max(ad_, key=lambda t: t[0])   # en güçlü düzen
        uc, gov, s_g, detay, s_min = cok_yerlesim(kollar, parcalar, de, ny, ncol, s_, dw, sw,
                                                  det, dh, dt)
        ince = LifKesit2B(kollar, uc, gov, m)
        secim = (parcalar, de, ny, ncol, s_, uc, gov, s_g, detay, ince,
                 float(np.max(ince.talep_orani(talepler))), dt)
        uyarilar.append("P–M2–M3 talebi uç bölge donatısıyla KARŞILANAMADI – kesit büyütülmeli.")
    elif Lu_ek > 0:
        uyarilar.append(f"Bilgi: serbest uç bölgeleri lif analizine göre {Lu_ek:.0f} mm uzatıldı.")
    parcalar, de, ny, ncol, s_, uc, gov, s_g, detay, lk, oran, dt = secim
    etr = cok_etriye(kollar, parcalar, detay, min(de, dt), kritik, m)
    As_uc = sum(alan(b[2]) for b in uc)
    kontroller.append(("Uç bölgeler: As ≥ max(ρmin·t·L, 4Ø14), ρ ≤ 0.03", 1.0, 1.0, True))
    ar = (detay[0].get("aralik") if detay else None) or {}
    if ar:
        kontroller.append((f"Uç bölge en küçük net aralık, bindirmede [mm] ≥ "
                           f"max({A['uc_net_min']:.0f}, Ø, 4/3·Dmax)", ar["net_min_b"],
                           net_min_etkin(de), ar["ok_min"]))
        kontroller.append(("Uç bölge komşu donatı en büyük net aralık [mm]", ar["net_max"],
                           A["uc_net_max"], ar["ok_max"]))
    if basit and gerekli:
        eksik = [q for q in gerekli if q["As_pmm"] > q["As_min"] * 1.001]
        kontroller.append(("Md/Mr P–M2–M3 (minimum donatı ile)", oran, 1.0, oran <= 1.0))
        if eksik:
            uyarilar.append("Minimum donatı PMM talebini karşılamıyor: " + ", ".join(
                f"B{q['bolge']} gerekli {q['As_gerek'] / 100:.1f} cm² (min {q['As_min'] / 100:.1f})"
                for q in eksik) + " – çizimde minimum donatı var; gerekli cm² değerlerine göre "
                "elle artırın.")
    else:
        kontroller.append(("Md/Mr P–M2–M3 lif analizi (en elverişsiz)", oran, 1.0, oran <= 1.0))
    gc = govde_ciroz_cok(kollar, gov, dh, sh, s_g, kritik)
    out = dict(pier=ad, tip="cok", kritik=kritik, kademe=kademe, kollar=kollar, parcalar=parcalar, detay=detay,
                de=de, dt=dt, kis=kis, ny=ny, ncol=ncol, s_uc=s_, uc=uc, govde=gov, dw=dw, sw=sw, sw_gercek=s_g, dh=dh, sh=sh,
                etr=etr, kesme=ks, Ve=kr["Ve"], Vd=kr["Vd"], Vr=kr["Vr"], Md_Mr=oran, lif=lk,
                talepler=talepler, kontroller=kontroller, uyarilar=uyarilar, As_uc=As_uc,
                nbar_uc=len(uc), gciroz=gc, rho_uc_min=rho_min, Nmax=Nmax, gerekli=gerekli,
                kol_talep=kol_tal, yontem="basit" if basit else "optimum")
    out["yatay"] = dict(zip(("cubuklar", "firketeler"), yatay_cubuklar_cok(out, m)))
    return out


def _parca_ekle(q, p):
    """p parçasını (q'dan sonra başlayan) q'ya katar: uçlar, yüzler, etriye uzantıları ve
    yoğunlaşma tarafı birlikte taşınır."""
    if p["s1"] >= q["s1"]:
        q["s1"] = p["s1"]
        q["yuz1"] = q["yuz1"] or p["yuz1"]
        q["hoop1"] = p.get("hoop1", 0.0)
    if p["tip"] not in q["tip"]:
        q["tip"] = q["tip"] + " + " + p["tip"]
    for k_ in ("sanal0", "sanal1"):
        if p.get(k_):
            q[k_] = True
    a, b = q.get("uc_taraf"), p.get("uc_taraf")
    if a is None:
        q["uc_taraf"] = b
    elif b is not None and a != b:
        q["uc_taraf"] = "iki"


def _parca_birlestir(parcalar, kollar=None):
    """Aynı koldaki çakışan parçaları birleştirir; birleşen parçaların bölge kimlikleri de
    birleştirilir (birleşim bölgesinin diğer kollardaki parçaları aynı bölgede kalır)."""
    ata = {}

    def bul(x):
        while ata.get(x, x) != x:
            x = ata[x]
        return x

    son = []
    for ki in sorted({p["kol"] for p in parcalar}):
        ps = sorted([p for p in parcalar if p["kol"] == ki], key=lambda p: p["s0"])
        # kollar verilirse: aralarında max(t, 300) mm'den az gövde kalan (iç içe / bitişik)
        # uç bölgeler de tek bölgede birleştirilir
        esik = max(kollar[ki].t, 300.0) if kollar is not None else 1.0
        for p in ps:
            if son and son[-1]["kol"] == ki and p["s0"] < son[-1]["s1"] + esik:
                q = son[-1]
                _parca_ekle(q, p)
                ra, rb = bul(q["bolge"]), bul(p["bolge"])
                if ra != rb:
                    ata[max(ra, rb)] = min(ra, rb)
            else:
                son.append(dict(p))
    ids = {}
    for p in son:
        p["bolge"] = ids.setdefault(bul(p["bolge"]), len(ids) + 1)
    return son


def govde_ciroz_cok(kollar, govde, dh, sh, sw, kritik=True):
    n, n_v, yog, gerek = ciroz_adimlari(sw, sh, kritik)
    # her kolda yüz çiftlerini bul (aynı s, ters w)
    ciftler = []
    for k in kollar:
        noktalar = []
        for u, v, d in govde:
            if k.icinde(u, v):
                s, w = k.yerel(u, v)
                if w < 0:
                    noktalar.append(float(s))
        noktalar.sort()
        for i, s in enumerate(noktalar):
            ciftler.append((k, s, "A" if i % n == 0 else ("B" if i % n == n // 2 else "")))
    if n == 1:
        ciftler = [(k, s_, "A") for k, s_, _ in ciftler]
    return dict(d=dh, ciftler=[c for c in ciftler if c[2]], s_duz=n_v * sh, adim=n, adim_v=n_v,
                adet_m2=yog, gerek=gerek)


def pier_tasarla_cok(p, m):
    z0 = p.katlar[0].z_alt
    Hw = p.katlar[-1].z_ust - z0
    kol0 = kollari_kur(p.katlar[0].kollar)
    lw0 = max(k.merkez_L for k in kol0)
    Hcr = min(max(lw0, Hw / 6.0), 2 * lw0)
    notlar = [f"Çok kollu kesit: {len(kol0)} kol ({', '.join(f'{k.ad}: {k.merkez_L:.0f}×{k.t:.0f}' for k in kol0)})",
              f"Hw = {Hw / 1000:.2f} m, en uzun kol ℓ = {lw0 / 1000:.2f} m",
              f"Hcr = min(max(ℓ, Hw/6), 2ℓ) = {Hcr / 1000:.2f} m",
              f"Lif modeli: 2B ağ {AYAR['lif_boyutu']:.0f} mm, P–M2–M3 (θ taraması "
              f"{360 / 72:.0f}°), beton TS 500 parabol-dikdörtgen, çelik elasto-plastik.",
              f"İşaret kabulü: M3 = {AYAR['M3_isaret']:+d}·(−ΣF·u), M2 = {AYAR['M2_isaret']:+d}·(ΣF·v) "
              f"– bir pier'de ETABS PMM/Section Designer ile karşılaştırarak doğrulayın.",
              "VARSAYIM: serbest uç bölge uzunluğu kolun kendi boyuna göre; T birleşiminde geçen "
              "kolda her iki yana max(t, 300) uzatıldı."]
    kritik_ve_kademe(p.katlar, z0, Hcr)
    notlar.append(f"Kademeli geçiş (TBDY 7.6.5.1): kritik bölge üstündeki {AYAR['kademe_kat']} "
                  f"katta uç bölge uzunluğu ve ρmin doğrusal azaltıldı.")
    kk = set(p.kesme_kombs or []) or {f[0] for k in p.katlar for f in k.kuvvetler}
    pm_k = set(p.pm_kombs or []) or {f[0] for k in p.katlar for f in k.kuvvetler}
    notlar.append("Kesme kombinasyonları: " + ", ".join(sorted(kk)[:12]))
    notlar.append("P–M talep kombinasyonları: " + ", ".join(sorted(pm_k)[:12])
                  + (f" … (+{len(pm_k) - 12})" if len(pm_k) > 12 else ""))

    def imza(k):
        return tuple(sorted((round(a[0]), round(a[1]), round(b[0]), round(b[1]), round(t))
                            for a, b, t in k.kollar)) + tuple(sorted(
            (round(q["u"]), round(q["v"]), round(q["b"])) for q in (k.kiris or [])))

    gruplar = []
    for k in p.katlar:
        anahtar = (k.kritik, round(k.kademe, 3), imza(k))
        if gruplar and gruplar[-1]["anahtar"] == anahtar:
            gruplar[-1]["katlar"].append(k)
        else:
            gruplar.append(dict(anahtar=anahtar, katlar=[k]))
    if AYAR["kesme_yontemi"].upper() == "1.2D":
        Ve_carpan = kesme_carpani_12D()
        notlar.append(kesme_notu())
    else:
        Ve_carpan = None if Hw / lw0 > 2 else 1.0
    sonuc = []
    for gi, g in enumerate(gruplar):
        kr, kd = g["anahtar"][0], g["anahtar"][1]
        kollar = kollari_kur(g["katlar"][0].kollar)
        talepler = talep_azalt_pmm([(f[2] * 1e3, f[5] * 1e6, f[3] * 1e6) for k in g["katlar"]
                                    for f in k.kuvvetler if f[0] in pm_k])
        print(f"    grup {gi + 1}/{len(gruplar)} ({g['katlar'][0].kat}–{g['katlar'][-1].kat}, "
              f"{len(talepler)} talep noktası, PMM) ...", flush=True)
        kat_kesme = [(k.kat, max([abs(f[4]) for f in k.kuvvetler if f[0] in kk] or [0]) * 1e3,
                      max([abs(f[6]) for f in k.kuvvetler if f[0] in kk] or [0]) * 1e3)
                     for k in g["katlar"]]
        h_max = max(k.z_ust - k.z_alt for k in g["katlar"])
        kiris_p = kiris_parcalari_kur(kollar, g["katlar"])
        if Ve_carpan is None:
            on = cok_grup_tasarla(p.ad, kollar, kr, talepler, kat_kesme, 1.0, h_max, m, kiris_p, kd)
            tb = [f for f in g["katlar"][0].kuvvetler if f[0] in kk and
                  f[1].lower().startswith("bot")] or g["katlar"][0].kuvvetler
            f = max(tb, key=lambda f: math.hypot(f[3], f[5]))
            dc = float(on["lif"].talep_orani([(f[2] * 1e3, f[5] * 1e6, f[3] * 1e6)])[0])
            oran = AYAR["Mp_Mr_orani"] / dc if dc > 0 else 1.0
            Ve_carpan = AYAR["beta_v"] * max(oran, 1.0)
            txt = f"Taban ({f[0]}): Md/Mr={dc:.2f} -> Mp/Md≈{oran:.2f}, βv·Mp/Md={Ve_carpan:.2f}"
            if AYAR["ve_ust_sinir_RD"] and Ve_carpan > AYAR["R"] / AYAR["D"]:
                Ve_carpan = AYAR["R"] / AYAR["D"]
                txt += f" -> (R/D)={Ve_carpan:.2f} (VARSAYIM)"
            notlar.append(txt)
        s = cok_grup_tasarla(p.ad, kollar, kr, talepler, kat_kesme, Ve_carpan, h_max, m, kiris_p, kd)
        s["katlar"] = [k.kat for k in g["katlar"]]
        s["z"] = (g["katlar"][0].z_alt, g["katlar"][-1].z_ust)
        s["etiket"] = f"G{gi + 1}"
        s["_katlar"] = g["katlar"]
        s["kiris_bilgi"] = sorted({q["aciklama"] for q in kiris_p})
        s["_param"] = dict(kollar=kollar, kr=kr, talepler=talepler, kat_kesme=kat_kesme,
                           h_max=h_max, kiris_p=kiris_p, kd=kd, imza=imza(g["katlar"][0])[:len(
                               g["katlar"][0].kollar)])
        sonuc.append(s)

    # ---- katlar arası tutarlılık: alt grup üst gruptan zayıf olamaz; üst grubun uç bölgeleri
    # alt grupta da bulunur (aynı kesit geometrisinde)
    for i in range(len(sonuc) - 2, -1, -1):
        gL, gU = sonuc[i], sonuc[i + 1]
        if gL["_param"]["imza"] != gU["_param"]["imza"]:
            continue
        zayif = (gL["As_uc"] < gU["As_uc"] - 1 or gL["de"] < gU["de"] or gL["ny"] < gU["ny"]
                 or gL["ncol"] < gU["ncol"] or gL["dt"] < gU["dt"])
        kapsar = all(any(q["kol"] == u["kol"] and q["s0"] <= u["s0"] + 1 and q["s1"] >= u["s1"] - 1
                         for q in gL["parcalar"])
                     for u in gU["parcalar"] if "kiriş" not in u["tip"])
        if not zayif and kapsar:
            continue
        print(f"    {gL['etiket']} üstteki {gU['etiket']}'den zayıf/kısa çıktı -> yeniden "
              f"tasarlanıyor", flush=True)
        P_ = gL["_param"]
        kis = dict(As_min=gU["As_uc"], de_min=gU["de"], ny_min=gU["ny"], ncol_min=gU["ncol"],
                   dt_min=gU["dt"],
                   parca_ek=[dict(u, bolge=5000 + j) for j, u in enumerate(gU["parcalar"])
                             if "kiriş" not in u["tip"]])
        eski = f"As={gL['As_uc']:.0f}"
        s_ = cok_grup_tasarla(p.ad, P_["kollar"], P_["kr"], P_["talepler"], P_["kat_kesme"],
                              Ve_carpan, P_["h_max"], m, P_["kiris_p"], P_["kd"], kis)
        for k_ in ("katlar", "z", "etiket", "_katlar", "kiris_bilgi", "_param"):
            s_[k_] = gL[k_]
        s_["uyarilar"].append(f"Bilgi: üstteki {gU['etiket']} daha güçlü/uzun uç bölgeli çıktığı "
                              f"için bu grup en az onun kadar donatıldı ({eski} -> "
                              f"As={s_['As_uc']:.0f}).")
        sonuc[i] = s_
    notlar.append("Katlar arası tutarlılık: alt grup üst gruptan zayıf olamaz (As, çap, sıra, kolon) "
                  "ve üst grubun uç bölgelerini kapsar.")
    # kat geçişleri (genel)
    for gL, gU in zip(sonuc[:-1], sonuc[1:]):
        gL["gecis"] = gecis_genel(gL, gU, m)
    if len(sonuc) > 1:
        notlar.append("Kat geçişlerinde üst grubun donatıları alt grubun planında gösterildi.")
    lw_etiket = lw0
    return dict(pier=p.ad, tip="cok", Hw=Hw, Hcr=Hcr, notlar=notlar, gruplar=sonuc,
                Ve_carpan=Ve_carpan, lw=lw_etiket)


# ---------------------------------------------------------------- kat geçişi (genel)
def _yerel_to_global(k, u, v):
    X0, Y0 = k.orijin
    a = math.radians(k.aci)
    return X0 + u * math.cos(a) - v * math.sin(a), Y0 + u * math.sin(a) + v * math.cos(a)


def _global_to_yerel(k, X, Y):
    X0, Y0 = k.orijin
    a = math.radians(k.aci)
    dx, dy = X - X0, Y - Y0
    return dx * math.cos(a) + dy * math.sin(a), -dx * math.sin(a) + dy * math.cos(a)


def gecis_genel(gL, gU, m):
    """Çok kollu gruplar arasında düz / kırım / filiz sınıflandırması (alt grubun yerel
    eksenlerinde)."""
    A = AYAR
    kL, kU = gL["_katlar"][-1], gU["_katlar"][0]
    alt = [(u, v, d) for u, v, d, *_ in gL["uc"]] + [(u, v, d) for u, v, d in gL["govde"]]
    ust = []
    for u, v, d, *_ in list(gU["uc"]) + [(a, b, c) for a, b, c in gU["govde"]]:
        X, Y = _yerel_to_global(kU, u, v)
        ust.append(_global_to_yerel(kL, X, Y) + (d,))
    e_max = A["birlesim_yuksekligi"] * A["kirim_egim_max"]
    ciftler = sorted((math.hypot(xu - xl, yu - yl), i, j)
                     for j, (xu, yu, du) in enumerate(ust) for i, (xl, yl, dl) in enumerate(alt)
                     if math.hypot(xu - xl, yu - yl) <= e_max + 1e-6)
    alt_bos, ust_bos = set(range(len(alt))), set(range(len(ust)))
    duz, kirim = [], []
    for e, i, j in ciftler:
        if i in alt_bos and j in ust_bos:
            alt_bos.discard(i)
            ust_bos.discard(j)
            (duz if e <= A["duz_tolerans"] else kirim).append(
                dict(xl=alt[i][0], yl=alt[i][1], dl=alt[i][2], xu=ust[j][0], yu=ust[j][1],
                     du=ust[j][2], e=e))
    filiz, disarida = [], 0
    for j in sorted(ust_bos):
        xu, yu, du = ust[j]
        ic = any(k.icinde(xu, yu, -A["paspayi"]) for k in gL["kollar"])
        disarida += (not ic)
        lb, l0 = kenetlenme(du, m)
        filiz.append(dict(xu=xu, yu=yu, du=du, gomulme=A["filiz_gomulme_katsayi"] * lb,
                          bindirme=l0, alt_kesitte=ic))
    biten = [dict(x=alt[i][0], y=alt[i][1], d=alt[i][2]) for i in sorted(alt_bos)]
    caplar = sorted({b["du"] for b in duz + kirim} | {f["du"] for f in filiz})
    boylar = {d: kenetlenme(d, m) for d in caplar}
    ust_kontur = []
    for k in gU["kollar"]:
        poly = []
        for u, v in k.koseler():
            X, Y = _yerel_to_global(kU, u, v)
            poly.append(_global_to_yerel(kL, X, Y))
        ust_kontur.append(poly)
    uyari = [f"{disarida} filiz alt kesitin dışına düşüyor – detay gerekli!"] if disarida else []
    return dict(duz=duz, kirim=kirim, filiz=filiz, biten=biten, e_max=e_max, boylar=boylar,
                ust_konturlar=ust_kontur, uyari=uyari, ust_etiket=gU["etiket"],
                kat=f"{kL.kat} → {kU.kat}",
                ozet=(f"üst {gU['etiket']}: {len(duz)} düz, {len(kirim)} kırım "
                      f"(e ≤ {e_max:.0f} mm), {len(filiz)} filiz, alttan {len(biten)} biter"))


def kiris_parcalari_kur(kollar, katlar):
    """Kirişlerin bağlandığı yerlerde, bağlandığı kol üzerinde uç bölge kurallarıyla donatılan
    parça: uzunluk = kiriş genişliği + 2·t (≥ 300 mm), kiriş eksenine ortalı."""
    if not AYAR["kiris_bolgesi"]:
        return []
    kir = list({(round(q["u"]), round(q["v"]), round(q["b"])): q for k in katlar
                for q in (k.kiris or [])}.values())
    parca = []
    for i, q in enumerate(kir):
        for ki, k in enumerate(kollar):
            if k.icinde(q["u"], q["v"], 60.0):
                s, _ = k.yerel(q["u"], q["v"])
                L = max(q["b"] + 2 * k.t, 300.0)
                s0, s1 = max(0.0, float(s) - L / 2), min(k.L, float(s) + L / 2)
                parca.append(dict(kol=ki, s0=s0, s1=s1, bolge=1000 + i, uc_taraf=None,
                                  hoop0=0.0, hoop1=0.0,
                                  tip=f"kiriş {q['ad']}", yuz0=s0 <= 0, yuz1=s1 >= k.L,
                                  aciklama=f"{q['ad']} (b={q['b']:.0f}) -> kol {k.ad}, "
                                           f"s=[{s0:.0f}, {s1:.0f}]"))
                break
    return parca


# ---------------------------------------------------------------- çok kollu çizimler
def _cok_metin(g):
    return dict(
        uc=(f"{_poz_str(g, 'uc')}Uç bölgeler: serbest uç yüzünde {g['ny']} Ø{g['de']}, küme "
            f"{g['ncol']} kolon; gerisi 2 yüzde Ø{g.get('dt', g['de'])} ≤{g['s_uc']:.0f} mm; ara sıra "
            f"yalnız kalınlık gerektirirse"),
        etr=(f"{_poz_str(g, 'etr')}Etriye Ø{g['etr']['d']}/{g['etr']['s']:.0f} + {_poz_str(g, 'ciroz')}"
             f"çiroz – kol: kalınlık doğr. ≤{g['etr']['n_y']}, boy doğr. {g['etr']['n_x']}"),
        gd=f"{_poz_str(g, 'gd')}Gövde düşey Ø{g['dw']}/{g['sw_gercek']:.0f} (2 yüz)",
        gy=(f"{_poz_str(g, 'gy')}Gövde yatay Ø{g['dh']}/{g['sh']:.0f} (2 yüz = 2 kol)"
            + (" + uçta U-firkete" if AYAR["yatay_uc_detay"] == "firkete" else "")),
        gc=(f"{_poz_str(g, 'gc')}Özel deprem çirozu Ø{g['gciroz']['d']} şaşırtmalı: her {g['gciroz']['adim']}. düşey, "
            f"düşeyde /{g['gciroz']['s_duz']:.0f} ({g['gciroz']['adet_m2']:.1f} adet/m² ≥ "
            f"{g['gciroz']['gerek']:.0f})"),
    )


def cok_plan_ciz(ax, g, fs=7.5, olcu=True):
    import matplotlib.patches as mp
    c = AYAR["paspayi"]
    det = g["etr"]["d"]
    kollar = g["kollar"]
    for k in kollar:
        ax.add_patch(mp.Polygon(k.koseler(), closed=True, fc="#eeeeee", ec="black", lw=1.2,
                                zorder=1))
    for p, x in zip(g["parcalar"], g["detay"]):
        k = kollar[p["kol"]]
        q = [k.nokta(p["s0"], -k.t / 2), k.nokta(p["s1"], -k.t / 2), k.nokta(p["s1"], k.t / 2),
             k.nokta(p["s0"], k.t / 2)]
        renk = "#d6eaf8" if "kiriş" in p["tip"] else "#fde8d6"
        ax.add_patch(mp.Polygon(q, closed=True, fc=renk, ec="#c0392b", lw=0.8, ls="--", zorder=1.5))
        # etriye(ler) (birleşimde karşı kolun içinden geçer; uzun bölgede iç içe) + çirozlar
        a0, a1 = x["hoop"][0] + det / 2, x["hoop"][1] - det / 2
        w0 = k.t / 2 - c - det / 2
        for i_h, (ha, hb) in enumerate(x["hooplar"]):
            off = det * 0.6 * (i_h % 2)
            hq = [k.nokta(ha + det / 2, -w0 + off), k.nokta(hb - det / 2, -w0 + off),
                  k.nokta(hb - det / 2, w0 - off), k.nokta(ha + det / 2, w0 - off)]
            ax.add_patch(mp.Polygon(hq, closed=True, fc="none", ec="#c0392b", lw=1.0, zorder=3))
        for s_ in x["ciroz_s"]:
            a_, b_ = k.nokta(s_, -w0), k.nokta(s_, w0)
            ax.plot([a_[0], b_[0]], [a_[1], b_[1]], color="#c0392b", lw=0.7, zorder=3)
        for w_ in x["ws"][1:-1]:
            a_, b_ = k.nokta(a0, w_), k.nokta(a1, w_)
            ax.plot([a_[0], b_[0]], [a_[1], b_[1]], color="#c0392b", lw=0.7, zorder=3)
        m_ = k.nokta((p["s0"] + p["s1"]) / 2, 0)
        ax.text(m_[0], m_[1], f"B{p['bolge']}", fontsize=fs - 1.5, color="#922b21", ha="center",
                va="center", zorder=8, clip_on=True,
                bbox=dict(fc="white", ec="none", alpha=0.6, pad=0.3))
    for cb in g["yatay"]["cubuklar"]:
        ax.plot([p_[0] for p_ in cb["pts"]], [p_[1] for p_ in cb["pts"]], color="#2e86c1", lw=0.6,
                zorder=2)
    for fk in g["yatay"]["firketeler"]:
        ax.plot([p_[0] for p_ in fk["pts"]], [p_[1] for p_ in fk["pts"]], color="#117a65", lw=0.9,
                zorder=2.5)
    for k, s_, sv in g["gciroz"]["ciftler"]:
        w0 = k.t / 2 - c - g["dh"] / 2
        a_, b_ = k.nokta(s_, -w0), k.nokta(s_, w0)
        ax.plot([a_[0], b_[0]], [a_[1], b_[1]], color="#7d3c98", lw=0.6,
                ls="-" if sv == "A" else (0, (3, 2)), zorder=3)
    _daire_topla(ax, [b[:3] for b in g["uc"]], "black", "black")
    _daire_topla(ax, g["govde"], "#2e86c1", "#1b4f72")
    if olcu:
        for k in kollar:
            a_, b_ = k.nokta(0, k.t / 2 + 180), k.nokta(k.L, k.t / 2 + 180)
            ax.annotate("", tuple(a_), tuple(b_), arrowprops=dict(arrowstyle="<->", lw=0.6))
            m_ = (a_ + b_) / 2 + k.n * 90
            ax.text(m_[0], m_[1], f"{k.ad}: {k.L:.0f}×{k.t:.0f}", fontsize=fs, ha="center",
                    va="center", rotation=(k.aci + 90) % 180 - 90, clip_on=True)
    if "gecis" in g:
        gecis_ciz_cok(ax, g["gecis"])
    ax.set_aspect("equal")
    ax.axis("off")
    ax.autoscale_view()


def gecis_ciz_cok(ax, gc):
    import matplotlib.patches as mp
    R = GECIS_RENK
    for poly in gc["ust_konturlar"]:
        q = poly + [poly[0]]
        ax.plot([p[0] for p in q], [p[1] for p in q], color=R["kontur"], ls=(0, (6, 3)), lw=0.9,
                zorder=5)
    from matplotlib.collections import PatchCollection, LineCollection
    if gc["duz"]:
        ax.add_collection(PatchCollection(
            [mp.Circle((b["xu"], b["yu"]), b["du"] / 2 + 14) for b in gc["duz"]], facecolor="none",
            edgecolor=R["duz"], linewidth=1.0, zorder=6))
    if gc["kirim"]:
        ax.add_collection(LineCollection([[(b["xl"], b["yl"]), (b["xu"], b["yu"])] for b in gc["kirim"]],
                                         colors=R["kirim"], linewidths=1.0, zorder=6))
        ax.add_collection(PatchCollection(
            [mp.Circle((b["xu"], b["yu"]), b["du"] / 2) for b in gc["kirim"]], facecolor="white",
            edgecolor=R["kirim"], linewidth=1.2, zorder=6))
    if gc["filiz"]:
        ax.add_collection(PatchCollection(
            [mp.Rectangle((f["xu"] - f["du"] / 2 - 12, f["yu"] - f["du"] / 2 - 12), f["du"] + 24,
                          f["du"] + 24) for f in gc["filiz"]], facecolor="none",
            edgecolor=R["filiz"], linewidth=1.3, zorder=6))
    for b in gc["biten"]:
        h = b["d"] / 2 + 4
        ax.plot([b["x"] - h, b["x"] + h], [b["y"] - h, b["y"] + h], color=R["biten"], lw=1.0)
        ax.plot([b["x"] - h, b["x"] + h], [b["y"] + h, b["y"] - h], color=R["biten"], lw=1.0)


def png_ciz_cok(ps, yol):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.gridspec import GridSpec
    from matplotlib.gridspec import GridSpecFromSubplotSpec
    gr = ps["gruplar"]
    fig = plt.figure(figsize=(16.5, 13 * len(gr)))
    gs = GridSpec(len(gr), 2, figure=fig, width_ratios=[1.35, 1], hspace=0.12, wspace=0.04)
    for i, g in enumerate(gr):
        sub = GridSpecFromSubplotSpec(2, 1, subplot_spec=gs[i, 0], height_ratios=[1.2, 1],
                                      hspace=0.12)
        ax = fig.add_subplot(sub[0])
        cok_plan_ciz(ax, g)
        # detay: en çok parçalı (birleşim) uç bölgesi
        axz = fig.add_subplot(sub[1])
        cok_plan_ciz(axz, g, fs=9, olcu=False)
        from collections import Counter
        bid = Counter(p["bolge"] for p in g["parcalar"]).most_common(1)[0][0]
        pts = []
        for p in g["parcalar"]:
            if p["bolge"] == bid:
                k = g["kollar"][p["kol"]]
                pts += [k.nokta(p["s0"], -k.t / 2), k.nokta(p["s1"], k.t / 2),
                        k.nokta(p["s0"], k.t / 2), k.nokta(p["s1"], -k.t / 2)]
        pts = np.array(pts)
        (u0, v0), (u1, v1) = pts.min(0) - 250, pts.max(0) + 250
        axz.set_xlim(u0, u1)
        axz.set_ylim(v0, v1)
        axz.set_title(f"Detay: B{bid} birleşim uç bölgesi", fontsize=9, loc="left")
        bolge = bolge_adi(g)
        ax.set_title(f"{ps['pier']} – {g['etiket']}: {bolge} | {g['katlar'][0]}–{g['katlar'][-1]} "
                     f"(z = {g['z'][0] / 1000:.2f} – {g['z'][1] / 1000:.2f} m) | mm",
                     fontsize=11, weight="bold", loc="left")
        axt = fig.add_subplot(gs[i, 1])
        axt.axis("off")
        t = _cok_metin(g)
        satir = [t["uc"], f"  toplam {g['nbar_uc']} çubuk, As = {g['As_uc']:.0f} mm²",
                 t["etr"], t["gd"], t["gy"], t["gc"],
                 f"Lif ağı: {g['lif'].n_lif} beton + {len(g['uc']) + len(g['govde'])} donatı lifi",
                 "", "UÇ BÖLGELER:"]
        for p, x in zip(g["parcalar"], g["detay"]):
            k = g["kollar"][p["kol"]]
            satir.append(f"  B{p['bolge']} {k.ad} [{p['s0']:.0f}–{p['s1']:.0f}] {p['tip']}: "
                         f"{len(x['ws'])}×{len(x['ss'])}")
        if g.get("kiris_bilgi"):
            satir += ["", "KİRİŞ BAĞLANTILARI:"] + [f"  {s}" for s in g["kiris_bilgi"]]
        satir += ["", "KESME TEVZİSİ (1.2D):"]
        for r in g["kesme"]:
            satir.append(f"  {r['yon']} {r['kat']:<8} Vd={r['Vd'] / 1e3:6.0f} Ve={r['Ve'] / 1e3:6.0f}"
                         f" -> Ø{r['d']}/{r['s']:.0f} {'✓' if r['ok'] else '✗'}")
        if "gecis" in g:
            gc = g["gecis"]
            satir += ["", f"ÜST KATA GEÇİŞ ({gc['kat']}): {len(gc['duz'])} düz, "
                          f"{len(gc['kirim'])} kırım, {len(gc['filiz'])} filiz, {len(gc['biten'])} biten"]
            satir += [f"  Ø{d_}: lb={lb:.0f} l0={l0:.0f}" for d_, (lb, l0) in gc["boylar"].items()]
            satir += ["  (yeşil ○ düz, turuncu → kırım, pembe □ filiz, gri × biten)"]
        satir += ["", "KONTROLLER:"]
        for ad, v, s, ok in g["kontroller"]:
            satir.append(f"{'✓' if ok else '✗'} {ad}: {sayi(v)} / {sayi(s)}")
        satir += ["⚠ " + u for u in g["uyarilar"]]
        axt.text(0, 1, "\n".join(satir), va="top", ha="left", fontsize=7.6,
                 family="DejaVu Sans Mono")
    fig.savefig(yol, dpi=140, bbox_inches="tight")
    plt.close(fig)


def pm_ciz_cok(ps, yol):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mp
    from matplotlib import cm, colors
    gr = ps["gruplar"]
    fig, axs = plt.subplots(len(gr), 4, figsize=(20, 5.2 * len(gr)), squeeze=False,
                            gridspec_kw=dict(width_ratios=[1, 1, 1, 1.25]))
    mm = malzeme()
    for i, g in enumerate(gr):
        lk = g["lif"]
        T = np.array(g["talepler"])
        dc = lk.talep_orani(g["talepler"])
        j = int(np.argmax(dc))
        Nd, M2d, M3d = T[j]
        # (a) Nd düzleminde M2–M3 konturu
        ax = axs[i, 0]
        x, y = lk.kontur(Nd)
        o = np.argsort(np.arctan2(y, x))
        ax.plot(np.r_[x[o], x[o][:1]] / 1e6, np.r_[y[o], y[o][:1]] / 1e6, color="#1f4e79", lw=1.8,
                label=f"Kapasite, N={Nd / 1e3:.0f} kN")
        yakin = np.abs(T[:, 0] - Nd) <= 0.15 * max(abs(Nd), 1e5)
        ax.scatter(T[yakin, 1] / 1e6, T[yakin, 2] / 1e6, s=12, c="#e67e22", label="Talepler (±%15 N)")
        ax.scatter([M2d / 1e6], [M3d / 1e6], s=70, facecolor="none", edgecolor="#c0392b", lw=1.8,
                   label=f"En elverişsiz Md/Mr={dc[j]:.2f}", zorder=5)
        ax.axhline(0, color="#999", lw=0.5)
        ax.axvline(0, color="#999", lw=0.5)
        ax.set_xlabel("M2 [kNm]")
        ax.set_ylabel("M3 [kNm]")
        ax.set_aspect("equal", adjustable="datalim")
        ax.grid(alpha=0.25)
        ax.legend(fontsize=7)
        ax.set_title(f"{ps['pier']} – {g['etiket']}: M2–M3 konturu", fontsize=9, loc="left")
        # (b), (c) meridyenler
        for kk, (phi, ad, idx) in enumerate(((math.pi / 2, "M3", 2), (0.0, "M2", 1))):
            ax = axs[i, 1 + kk]
            for sgn in (1, -1):
                Ns, Ms = lk.meridyen(phi if sgn > 0 else phi + math.pi)
                ax.plot(sgn * Ms / 1e6, Ns / 1e3, color="#1f4e79", lw=1.6)
            ax.scatter(T[:, idx] / 1e6, T[:, 0] / 1e3, s=10, c="#e67e22")
            ax.axhline(0, color="#999", lw=0.5)
            ax.axvline(0, color="#999", lw=0.5)
            ax.set_xlabel(f"{ad} [kNm]")
            ax.set_ylabel("N [kN] (basınç +)")
            ax.grid(alpha=0.25)
            ax.set_title(f"P–{ad} (diğer moment = 0)", fontsize=9, loc="left")
        # (d) gerilme haritası
        ax = axs[i, 3]
        du = lk.durum(Nd, M2d, M3d)
        if du is not None:
            ec, sc, es, ss, th, cc = du
            norm_c = colors.Normalize(0, 0.85 * mm["fcd"])
            from matplotlib.collections import PolyCollection
            renk = cm.Greys(norm_c(np.asarray(sc)))
            renk[np.asarray(sc) <= 0] = (1, 1, 1, 1)
            ax.add_collection(PolyCollection(lk._poly, facecolors=renk, edgecolors="#c8c8c8",
                                             linewidths=0.1))
            norm_s = colors.TwoSlopeNorm(vmin=-mm["fyd"], vcenter=0, vmax=mm["fyd"])
            ax.scatter(lk.bu, lk.bv, c=ss, cmap=cm.coolwarm_r, norm=norm_s, s=14, edgecolors="k",
                       linewidths=0.3, zorder=3)
        for k in g["kollar"]:
            ax.add_patch(mp.Polygon(k.koseler(), closed=True, fc="none", ec="black", lw=0.8))
        ax.set_aspect("equal")
        ax.axis("off")
        ax.set_title("En elverişsiz talepte lif gerilmeleri (gri beton basınç, kırmızı çekme, "
                     "mavi basınç)", fontsize=8, loc="left")
    fig.tight_layout()
    fig.savefig(yol, dpi=130)
    plt.close(fig)


def dxf_grup_cok(msp, g, ox, oy, baslik):
    c = AYAR["paspayi"]
    det = g["etr"]["d"]
    P = lambda q: (ox + float(q[0]), oy + float(q[1]))
    kollar = g["kollar"]
    for k in kollar:
        msp.add_lwpolyline([P(q) for q in k.koseler()], close=True, dxfattribs={"layer": "BETON"})
    for p, x in zip(g["parcalar"], g["detay"]):
        k = kollar[p["kol"]]
        for s_ in (p["s0"], p["s1"]):
            if 0 < s_ < k.L:
                msp.add_line(P(k.nokta(s_, -k.t / 2)), P(k.nokta(s_, k.t / 2)),
                             dxfattribs={"layer": "UC_BOLGE"})
        a0, a1 = x["hoop"][0] + det / 2, x["hoop"][1] - det / 2
        w0 = k.t / 2 - c - det / 2
        for i_h, (ha, hb) in enumerate(x["hooplar"]):
            off = det * (i_h % 2)
            msp.add_lwpolyline([P(k.nokta(ha + det / 2, -w0 + off)), P(k.nokta(hb - det / 2, -w0 + off)),
                                P(k.nokta(hb - det / 2, w0 - off)), P(k.nokta(ha + det / 2, w0 - off))],
                               close=True, dxfattribs={"layer": "ETRIYE", "const_width": det})
        for s_ in x["ciroz_s"]:
            msp.add_lwpolyline([P(k.nokta(s_, -w0)), P(k.nokta(s_, w0))],
                               dxfattribs={"layer": "CIROZ", "const_width": det})
        for w_ in x["ws"][1:-1]:
            msp.add_lwpolyline([P(k.nokta(a0, w_)), P(k.nokta(a1, w_))],
                               dxfattribs={"layer": "CIROZ", "const_width": det})
        m_ = k.nokta((p["s0"] + p["s1"]) / 2, k.t / 2 + 120)
        msp.add_text(f"B{p['bolge']}", height=60,
                     dxfattribs={"layer": "YAZI", "style": "TR"}).set_placement(P(m_))
        # kol numaraları: kalınlık doğrultusundaki kollar kesitin DIŞ yüzünün dışında,
        # boy doğrultusundakiler yalnız serbest uçta (uç yüzün dışında)
        yk = sorted([ha + det / 2 for ha, hb in x["hooplar"]] + [hb - det / 2 for ha, hb in x["hooplar"]]
                    + list(x["ciroz_s"]))
        sg = _dis_yuz(k, kollar, (p["s0"] + p["s1"]) / 2)
        _dxf_kol_no(msp, [(*P(k.nokta(s_, sg * (k.t / 2 + 55))), "orta") for s_ in yk], h=34.0)
        for uc_, s_b in ((0, a0 - 55), (1, a1 + 55)):
            ub = k.uclar[uc_]
            if ub["tip"] == "serbest" and abs((p["s0"], p["s1"])[uc_] - (0.0, k.L)[uc_]) < 1.0:
                _dxf_kol_no(msp, [(*P(k.nokta(s_b, w_)), "orta")
                                  for w_ in [-w0] + list(x["ws"][1:-1]) + [w0]], h=30.0)
    for cb in g["yatay"]["cubuklar"]:
        msp.add_lwpolyline([P(p_) for p_ in cb["pts"]], dxfattribs={"layer": "YATAY_GOVDE"})
    for fk in g["yatay"]["firketeler"]:
        msp.add_lwpolyline([P(p_) for p_ in fk["pts"]], dxfattribs={"layer": "FIRKETE"})
    for k in kollar:
        a_, b_ = k.nokta(0, k.t / 2), k.nokta(k.L, k.t / 2)
        msp.add_aligned_dim(p1=P(a_), p2=P(b_), distance=250,
                            override={"dimtxt": 60, "dimasz": 40, "dimdec": 0, "dimlfac": 1.0},
                            dxfattribs={"layer": "OLCU"}).render()
    for k, s_, sv in g["gciroz"]["ciftler"]:
        w0 = k.t / 2 - c - g["dh"] / 2
        msp.add_lwpolyline([P(k.nokta(s_, -w0)), P(k.nokta(s_, w0))],
                           dxfattribs={"layer": "GOVDE_CIROZ_" + sv, "const_width": g["gciroz"]["d"]})
    for katman, liste in (("DONATI_UC", [b[:3] for b in g["uc"]]), ("DONATI_GOVDE", g["govde"])):
        for u, v, d in liste:
            msp.add_circle(P((u, v)), d / 2, dxfattribs={"layer": katman})
            h = msp.add_hatch(dxfattribs={"layer": katman})
            h.paths.add_edge_path().add_arc(P((u, v)), d / 2, 0, 360)
    if "gecis" in g:
        gc = g["gecis"]
        for poly in gc["ust_konturlar"]:
            msp.add_lwpolyline([P(q) for q in poly], close=True, dxfattribs={"layer": "UST_KESIT"})
        for b in gc["duz"]:
            msp.add_circle(P((b["xu"], b["yu"])), b["du"] / 2 + 12, dxfattribs={"layer": "FILIZ_DUZ"})
        for b in gc["kirim"]:
            msp.add_line(P((b["xl"], b["yl"])), P((b["xu"], b["yu"])), dxfattribs={"layer": "FILIZ_KIRIM"})
            msp.add_circle(P((b["xu"], b["yu"])), b["du"] / 2, dxfattribs={"layer": "FILIZ_KIRIM"})
        for f in gc["filiz"]:
            h = f["du"] / 2 + 12
            msp.add_lwpolyline([P((f["xu"] - h, f["yu"] - h)), P((f["xu"] + h, f["yu"] - h)),
                                P((f["xu"] + h, f["yu"] + h)), P((f["xu"] - h, f["yu"] + h))],
                               close=True, dxfattribs={"layer": "FILIZ_EKIM"})
        for b in gc["biten"]:
            h = b["d"] / 2 + 5
            msp.add_line(P((b["x"] - h, b["y"] - h)), P((b["x"] + h, b["y"] + h)),
                         dxfattribs={"layer": "BITEN_DONATI"})
            msp.add_line(P((b["x"] - h, b["y"] + h)), P((b["x"] + h, b["y"] - h)),
                         dxfattribs={"layer": "BITEN_DONATI"})
    t = _cok_metin(g)
    umin = min(min(q[0] for q in k.koseler()) for k in kollar)
    vmin = min(min(q[1] for q in k.koseler()) for k in kollar)
    vmax = max(max(q[1] for q in k.koseler()) for k in kollar)
    yazi = [baslik, t["uc"], t["etr"], t["gd"], t["gy"], t["gc"]]
    if "gecis" in g:
        yazi.append("ÜST KATA GEÇİŞ " + g["gecis"]["kat"] + ": " + g["gecis"]["ozet"])
    msp.add_mtext("\\P".join(yazi), dxfattribs=dict(layer="YAZI", style="TR", char_height=70)
                  ).set_location(P((umin, vmax + 1400)))
    # basit yöntem: bölge ihtiyaçları ve kol talepleri
    for q in g.get("gerekli") or []:
        ps_ = [g["parcalar"][i] for i in q["parcalar"]]
        p0 = max(ps_, key=lambda p: p["s1"] - p["s0"])
        k = kollar[p0["kol"]]
        sg = _dis_yuz(k, kollar, (p0["s0"] + p0["s1"]) / 2)
        ok = q["yeterli"] and q["As_pmm"] <= q["As_min"] * 1.001
        txt = (f"B{q['bolge']}: min {q['As_min'] / 100:.1f} cm2 ({q['n_min']} cubuk)\\P"
               f"PMM gerekli: " + (f"{q['As_pmm'] / 100:.1f} cm2" if q["yeterli"] else "YETERSIZ KESIT")
               + ("" if ok else "\\PONERI: " + " / ".join(q["oneri"][:3]).replace("Ø", "%%c")))
        msp.add_mtext(txt, dxfattribs=dict(layer="GEREKLI_DONATI", style="TR", char_height=55,
                                           color=3 if ok else 1)
                      ).set_location(P(k.nokta((p0["s0"] + p0["s1"]) / 2, sg * (k.t / 2 + 420))),
                                     attachment_point=5)
    for q in g.get("kol_talep") or []:
        k = next((kk for kk in kollar if kk.ad == q["kol"]), None)
        if k is None:
            continue
        sg = _dis_yuz(k, kollar, k.L / 2)
        txt = (f"{q['kol']}: N = {q['N_bas'] / 1e3:.0f} / {q['N_cek'] / 1e3:.0f} kN\\P"
               f"|M| = {q['M_max'] / 1e6:.0f} kNm (N = {q['N_M'] / 1e3:.0f})")
        ac = _kp_okunur_aci(math.degrees(math.atan2(k.e[1], k.e[0])))
        msp.add_mtext(txt, dxfattribs=dict(layer="KOL_TALEP", style="TR", char_height=60,
                                           rotation=ac)
                      ).set_location(P(k.nokta(k.L / 2, -sg * (k.t / 2 + 750))), attachment_point=5)
    # gövde çirozu A/B harfleri (iç yüz tarafında)
    for k, s_, sv in g["gciroz"]["ciftler"]:
        sg = -_dis_yuz(k, kollar, s_)
        msp.add_text(sv, height=34, dxfattribs={"layer": "KOL_NO", "style": "TR"}
                     ).set_placement(P(k.nokta(s_, sg * (k.t / 2 + 55))), align=_hz("orta"))
    # kol / çiroz tablosu
    e, gc = g["etr"], g["gciroz"]
    sat = [["BÖLGE", "KOL", "UZUNLUK", "KOL (kalınlık doğr.)", "KOL (boy doğr.)",
            "KAPALI ETRİYE / seviye", "ÇİROZ / seviye"]]
    for p, x in zip(g["parcalar"], g["detay"]):
        k = kollar[p["kol"]]
        sat.append([f"B{p['bolge']} {p['tip'][:28]}", k.ad, f"{p['s1'] - p['s0']:.0f}",
                    f"{x.get('n_y', 0)}", f"{x.get('n_x', 0)}", f"{len(x['hooplar'])}",
                    f"{len(x['ciroz_s'])} (kalınlık) + {max(len(x['ws']) - 2, 0)} (boy)"])
    nA = sum(1 for c_ in gc["ciftler"] if c_[2] == "A")
    nB = sum(1 for c_ in gc["ciftler"] if c_[2] == "B")
    sat.append(["Uç bölgeler: enine", f"Ø{det}", "", "", "", f"düşeyde /{e['s']:.0f}", ""])
    sat.append(["Gövde", "tüm kollar", "", "–", "2 (yatay Ø%d)" % g["dh"], "–",
                f"Ø{gc['d']}: A seviyesi {nA}, B seviyesi {nB} adet"])
    sat.append(["Gövde çirozu", f"her {gc['adim']}. düşey", "", "", "",
                f"yatay /{g['sh']:.0f}", f"düşeyde /{gc['s_duz']:.0f}, {gc['adet_m2']:.1f}/m²"])
    umax = max(max(q[0] for q in k.koseler()) for k in kollar)
    x0, y0 = P((umax + 600, vmax + 300))
    msp.add_text("ETRİYE KOL / ÇİROZ TABLOSU (KOL_NO katmanı: plandaki kol numaraları)", height=65,
                 dxfattribs={"layer": "KOL_TABLO", "style": "TR"}).set_placement((x0, y0 + 40))
    _, y_son = _dxf_tablo(msp, x0, y0, sat)
    return min(vmin, y_son - oy), vmax


def dxf_ciz_cok(ps, yol):
    doc = _dxf_yeni()
    msp = doc.modelspace()
    oy = 0.0
    for g in ps["gruplar"]:
        bolge = bolge_adi(g)
        vmin, vmax = dxf_grup_cok(msp, g, 0.0, oy, f"{ps['pier']} - {g['etiket']} {bolge} "
                                  f"({g['katlar'][0]}-{g['katlar'][-1]})")
        oy -= (vmax - vmin) + 3000
    doc.saveas(yol)


def dxf_section_designer_cok(g, yol):
    import ezdxf
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 4
    doc.layers.add("SHAPE", color=7)
    doc.layers.add("REBAR", color=1)
    msp = doc.modelspace()
    for k in g["kollar"]:
        msp.add_lwpolyline([tuple(map(float, q)) for q in k.koseler()], close=True,
                           dxfattribs={"layer": "SHAPE"})
    for b in list(g["uc"]) + list(g["govde"]):
        msp.add_circle((float(b[0]), float(b[1])), b[2] / 2, dxfattribs={"layer": "REBAR"})
    doc.saveas(yol)


def gerekli_metin(g, kisa=False):
    """Basit yöntem: bölge bölge minimum / PMM gereği donatı ve kol talepleri (metin satırları)."""
    L = []
    if g.get("kol_talep"):
        L.append("  KOL TALEPLERİ (kesit kuvvetleri doğrusal-elastik dağılımla kollara; N basınç +):")
        for q in g["kol_talep"]:
            L.append(f"    {q['kol']} ({q['L']:.0f}×{q['t']:.0f}): N = {q['N_bas'] / 1e3:.0f} / "
                     f"{q['N_cek'] / 1e3:.0f} kN, |M| en büyük = {q['M_max'] / 1e6:.0f} kNm "
                     f"(N = {q['N_M'] / 1e3:.0f} kN)")
    if g.get("gerekli"):
        L.append("  UÇ BÖLGE DONATI İHTİYACI (tüm kesitin P–M2–M3 lif analizinden; çizimde minimum "
                 "donatı):")
        for q in g["gerekli"]:
            pmm = "aşılıyor (kesit yetersiz)" if not q["yeterli"] else f"{q['As_pmm'] / 100:.1f} cm²"
            durum = "minimum yeterli" if q["yeterli"] and q["As_pmm"] <= q["As_min"] * 1.001 else \
                "ARTIRILMALI -> öneri " + " / ".join(q["oneri"][:4])
            L.append(f"    B{q['bolge']} [{', '.join(q['kollar'])}] {q['tip'][:40]}: min {q['n_min']} "
                     f"çubuk = {q['As_min'] / 100:.1f} cm² | PMM gereği {pmm} | gerekli "
                     f"{q['As_gerek'] / 100:.1f} cm² – {durum}")
    return L


def rapor_yaz_cok(ps, yol):
    L = [f"PERDE {ps['pier']} (ÇOK KOLLU) – TBDY 2018 Bölüm 7.6", "=" * 70] + ps["notlar"] + [""]
    for g in ps["gruplar"]:
        t = _cok_metin(g)
        L.append(f"[{g['etiket']}] {bolge_adi(g)}  katlar: "
                 f"{', '.join(g['katlar'])}")
        L += ["  " + t[x] for x in ("uc", "etr", "gd", "gy", "gc")]
        L.append(f"  {g['etr']['not_']}")
        L.append(f"  Toplam uç bölge donatısı {g['nbar_uc']} çubuk, As = {g['As_uc']:.0f} mm²; "
                 f"Md/Mr = {g['Md_Mr']:.3f}")
        for p, x in zip(g["parcalar"], g["detay"]):
            k = g["kollar"][p["kol"]]
            L.append(f"    B{p['bolge']} {k.ad} s=[{p['s0']:.0f}, {p['s1']:.0f}] {p['tip']}: "
                     f"{len(x['ws'])} sıra × {len(x['ss'])} kolon")
        for s in g.get("kiris_bilgi", []):
            L.append("    Kiriş: " + s)
        L += gerekli_metin(g)
        L.append("  Kesme tevzisi (1.2D): yön | kat | Vd | Ve | yatay | Vr [kN]")
        for r in g["kesme"]:
            L.append(f"     {r['yon']} {r['kat']:<8} {r['Vd'] / 1e3:7.0f} {r['Ve'] / 1e3:7.0f} "
                     f"Ø{r['d']}/{r['s']:.0f} {r['Vr'] / 1e3:7.0f} {'OK' if r['ok'] else 'YOK'}  "
                     f"(kollar: {', '.join(r['kollar'])})")
        if "gecis" in g:
            gc = g["gecis"]
            L.append(f"  Üst kata geçiş ({gc['kat']}): {gc['ozet']}")
            L += [f"     Ø{d_}: lb={lb:.0f}, l0={l0:.0f}" for d_, (lb, l0) in gc["boylar"].items()]
            L += ["     UYARI: " + u for u in gc["uyari"]]
        for ad, v, s, ok in g["kontroller"]:
            L.append(f"   {'OK ' if ok else 'YOK'}  {ad}: {sayi(v)}  (sınır {sayi(s)})")
        L += ["   UYARI: " + u for u in g["uyarilar"]]
        L.append("")
    with open(yol, "w", encoding="utf-8") as f:
        f.write("\n".join(L))


# =====================================================================================
# 9c) BODRUM (TOPRAK) PERDELERİ – düzlem dışı eğilme, metre şerit tasarımı
# =====================================================================================
#   * Kuvvetler: pier'e ait alan elemanlarının shell iç kuvvetleri (ETABS AreaForceShell):
#     M11, M22, M12 (kNm/m), F11, F22 (kN/m, + çekme), V13, V23 (kN/m).
#   * Tasarım momentleri Wood–Armer ile: M* = M ± |M12|; her yüz ve her doğrultu için
#     kat bazında zarf.
#   * Düşey donatı M22'den (dış tabaka), yatay donatı M11'den (iç tabaka); membran çekmesi
#     iki yüze eşit dağıtılır. Alt sınır: toplam ρ ≥ 0.0025 her doğrultuda, aralık ≤ 250 mm.
#   * Yatay donatı ayrıca düzlem içi kesmeyi (pier V2, 1.2D) karşılar.
#   * Düzlem dışı kesme: V ≤ Vcr = 0.65·fctd·b·d (etriyesiz) kontrol edilir.

BODRUM_CAPLAR = [10, 12, 14, 16, 18, 20, 22]


def bodrum_As(M, d, m):
    """M: Nmm (1 m şerit), d: mm -> gerekli As (mm²/m). Yetersizse None."""
    b = 1000.0
    k = 0.85 * m["fcd"] * b * d
    x = 1 - 2 * M / (k * d)
    if x < 0:
        return None
    return k / m["fyd"] * (1 - math.sqrt(x))


def bodrum_donati_sec(As_gerek, s_max=250.0):
    """Tek yüz için en az alanlı (Ø, s): 1000·A/s ≥ As_gerek."""
    en = None
    for d in BODRUM_CAPLAR:
        for s in range(int(s_max), 99, -25):
            As = 1000 * alan(d) / s
            if As >= As_gerek - 1e-6:
                if en is None or As < en[2] - 1e-6:
                    en = (d, float(s), As)
                break
    return en


def bodrum_tasarla(p, m):
    A = AYAR
    c = A["paspayi"]
    kk = set(p.kesme_kombs or []) or {f[0] for k in p.katlar for f in k.kuvvetler}
    satirlar, uyarilar = [], []
    m0 = m
    for k in p.katlar:
        m = malzeme_kat(k, m0)
        bd = k.bodrum or {}
        t = bd.get("t") or k.bw
        L = bd.get("L") or k.lw
        dv0, dh0 = 14, 12
        d_v = t - c - dv0 / 2                 # düşey dış tabaka
        d_h = t - c - dv0 - dh0 / 2           # yatay iç tabaka
        As_min_yuz = 0.0025 * t * 1000 / 2
        # düzlem içi kesme -> yatay donatı alt sınırı
        Vd = max([abs(f[4]) for f in k.kuvvetler if f[0] in kk] or [0.0]) * 1e3
        Ach = t * L
        rho_sh = max(0.0025, (Vd / Ach - 0.65 * m["fctd"]) / m["fywd"]) if Ach > 0 else 0.0025
        As_sh_yuz = rho_sh * t * 1000 / 2
        sonuc = dict(kat=k.kat, t=t, L=L, Vd=Vd, rho_sh=rho_sh)
        for yon, Mp, Mn, T, d_ in (("düşey", bd.get("M22p", 0), bd.get("M22n", 0),
                                    bd.get("F22t", 0), d_v),
                                   ("yatay", bd.get("M11p", 0), bd.get("M11n", 0),
                                    bd.get("F11t", 0), d_h)):
            for yuz, Mv in (("+3", Mp), ("−3", Mn)):
                As_M = bodrum_As(abs(Mv) * 1e6, d_, m)
                if As_M is None:
                    uyarilar.append(f"{k.kat} {yon} {yuz}: M={Mv:.0f} kNm/m için kesit yetersiz – "
                                    f"kalınlık artırılmalı.")
                    As_M = 0.04 * t * 1000 / 2
                As_T = max(T, 0) * 1e3 / m["fyd"] / 2
                gerek = max(As_M + As_T, As_min_yuz, As_sh_yuz if yon == "yatay" else 0.0)
                sec = bodrum_donati_sec(gerek)
                sonuc[(yon, yuz)] = dict(M=Mv, T=T, As_M=As_M, As_T=As_T, gerek=gerek,
                                         d=sec[0], s=sec[1], As=sec[2],
                                         belirleyen=("moment" if As_M + As_T >= max(
                                             As_min_yuz, As_sh_yuz if yon == "yatay" else 0) else
                                                     ("düzlem içi kesme" if yon == "yatay" and
                                                      As_sh_yuz > As_min_yuz else "minimum")))
        V = max(abs(bd.get("V13", 0)), abs(bd.get("V23", 0))) * 1e3 / 1000   # N/mm
        Vcr = 0.65 * m["fctd"] * min(d_v, d_h)                               # N/mm
        sonuc["V"] = V * 1000 / 1e3
        sonuc["Vcr"] = Vcr * 1000 / 1e3
        sonuc["V_ok"] = V <= Vcr
        if not sonuc["V_ok"]:
            uyarilar.append(f"{k.kat}: düzlem dışı kesme V={V:.0f} kN/m > Vcr={Vcr:.0f} kN/m")
        satirlar.append(sonuc)
    return dict(pier=p.ad, tip="bodrum", satirlar=satirlar, uyarilar=uyarilar,
                katlar=p.katlar,
                notlar=["Bodrum perdesi: düzlem dışı eğilme (Wood–Armer), metre şerit; düşey "
                        "donatı dış tabaka, yatay donatı iç tabaka.",
                        "Yüz adları shell yerel 3 eksenine göredir (+3 / −3). Toprak tarafını "
                        "ETABS'te alan elemanı yerel eksenlerinden kontrol edin.",
                        "Düzlem içi uç bölge tasarımı bu modülde yapılmaz; gerekirse perde "
                        "normal modda ayrıca çalıştırılmalıdır.",
                        "Düzlem içi kesme: Vd (1.2D) büyütmesiz; yatay donatı alt sınırı olarak."])


def _bd_txt(r, yon, yuz, poz=False):
    q = r[(yon, yuz)]
    p = r.get("poz", {}).get((yon, yuz)) if poz else None
    return (f"P{p} " if p else "") + f"Ø{q['d']}/{q['s']:.0f}"


def bodrum_ciz(bs, yol_png, yol_dxf, yol_txt):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mp
    kat = bs["katlar"]
    sat = {r["kat"]: r for r in bs["satirlar"]}
    fig, (ax, axt) = plt.subplots(1, 2, figsize=(16, 3.2 + 2.2 * len(kat)),
                                  gridspec_kw=dict(width_ratios=[1, 1.4]))
    # duvar düşey kesiti (kalınlık 5 kat abartılı)
    ab = 5.0
    for k in kat:
        r = sat[k.kat]
        t = r["t"] * ab
        z0, z1 = k.z_alt, k.z_ust
        ax.add_patch(mp.Rectangle((0, z0), t, z1 - z0, fc="#eeeeee", ec="black", lw=1.2))
        c = AYAR["paspayi"] * ab
        for x, yuz, ha, xt in ((c + 20, "+3", "right", -80), (t - c - 20, "−3", "left", t + 80)):
            ax.plot([x, x], [z0 + 50, z1 - 50], color="#c0392b", lw=1.8)
            ax.text(xt, (z0 + z1) / 2 + 250, f"düşey {_bd_txt(r, 'düşey', yuz, True)}", ha=ha,
                    va="center", fontsize=9, color="#922b21")
            ax.text(xt, (z0 + z1) / 2 - 250, f"yatay {_bd_txt(r, 'yatay', yuz, True)}", ha=ha,
                    va="center", fontsize=9, color="#1b4f72")
            sy = r[("yatay", yuz)]["s"]
            for z in np.arange(z0 + sy / 2, z1, sy * 2):
                ax.add_patch(mp.Circle((x + (60 if yuz == "+3" else -60), z), 25, fc="#2e86c1"))
        ax.plot([-400, t + 400], [z1, z1], color="#7f8c8d", lw=0.8, ls="--")
        ax.text(t + 420, z1, f"{k.kat} üst {z1 / 1000:.2f} m", va="center", fontsize=8)
    tmax = max(r["t"] for r in bs["satirlar"]) * ab
    ztop = kat[-1].z_ust
    ax.text(-80, ztop + 250, "+3 YÜZÜ", ha="right", fontsize=10, weight="bold")
    ax.text(tmax + 80, ztop + 250, "−3 YÜZÜ", ha="left", fontsize=10, weight="bold")
    ax.set_ylim(kat[0].z_alt - 300, ztop + 600)
    ax.set_xlim(-2600, tmax + 2800)
    ax.set_aspect("auto")
    ax.axis("off")
    ax.set_title(f"{bs['pier']} – BODRUM PERDESİ düşey kesit (kalınlık ×{ab:.0f} abartılı)",
                 fontsize=11, weight="bold", loc="left")
    L = [f"BODRUM PERDESİ {bs['pier']}", ""]
    L.append(f"{'Kat':<8}{'t':>5} {'Yön':<7}{'Yüz':<4}{'M*':>7}{'T':>6}{'As,gerek':>9}"
             f"{'Donatı':>10}{'As':>7}  Belirleyen")
    for r in bs["satirlar"]:
        for yon in ("düşey", "yatay"):
            for yuz in ("+3", "−3"):
                q = r[(yon, yuz)]
                L.append(f"{r['kat']:<8}{r['t']:>5.0f} {yon:<7}{yuz:<4}{q['M']:>7.1f}{q['T']:>6.0f}"
                         f"{q['gerek']:>9.0f}{_bd_txt(r, yon, yuz):>10}{q['As']:>7.0f}  {q['belirleyen']}")
        L.append(f"{'':<8}düzlem dışı kesme V={r['V']:.1f} ≤ Vcr={r['Vcr']:.1f} kN/m "
                 f"{'✓' if r['V_ok'] else '✗'};  düzlem içi Vd={r['Vd'] / 1e3:.0f} kN, "
                 f"ρsh={r['rho_sh']:.4f}")
    L += [""] + bs["notlar"] + ["⚠ " + u for u in bs["uyarilar"]]
    L.insert(2, "M*: Wood–Armer tasarım momenti [kNm/m], T: membran çekmesi [kN/m], As [mm²/m]")
    axt.axis("off")
    axt.text(0, 1, "\n".join(L), va="top", ha="left", fontsize=8, family="DejaVu Sans Mono")
    fig.savefig(yol_png, dpi=140, bbox_inches="tight")
    plt.close(fig)
    with open(yol_txt, "w", encoding="utf-8") as f:
        f.write("\n".join(L).replace("✓", "OK").replace("✗", "YOK").replace("⚠", "UYARI:"))
    # DXF: gerçek ölçekli düşey kesit + tablo
    doc = _dxf_yeni()
    msp = doc.modelspace()
    yz = dict(layer="YAZI", style="TR", char_height=60)
    for k in kat:
        r = sat[k.kat]
        t = r["t"]
        c = AYAR["paspayi"]
        msp.add_lwpolyline([(0, k.z_alt), (t, k.z_alt), (t, k.z_ust), (0, k.z_ust)], close=True,
                           dxfattribs={"layer": "BETON"})
        for x, yuz, xt, ap in ((c + 7, "+3", -150, 6), (t - c - 7, "−3", t + 150, 4)):
            msp.add_line((x, k.z_alt + 50), (x, k.z_ust - 50), dxfattribs={"layer": "DONATI_UC"})
            sy = r[("yatay", yuz)]["s"]
            dy = r[("yatay", yuz)]["d"]
            xh = x + (dy if yuz == "+3" else -dy)
            for z in np.arange(k.z_alt + sy / 2, k.z_ust, sy):
                msp.add_circle((xh, z), dy / 2, dxfattribs={"layer": "YATAY_GOVDE"})
            msp.add_mtext(f"{yuz} yüzü\\Pdüşey {_bd_txt(r, 'düşey', yuz, True).replace('Ø', '%%c')}"
                          f"\\Pyatay {_bd_txt(r, 'yatay', yuz, True).replace('Ø', '%%c')}",
                          dxfattribs=yz).set_location(
                (xt, (k.z_alt + k.z_ust) / 2), attachment_point=ap)
        msp.add_text(f"{k.kat}  +{k.z_ust / 1000:.2f}", height=70,
                     dxfattribs={"layer": "YAZI", "style": "TR"}).set_placement((t + 1200, k.z_ust))
        msp.add_line((-300, k.z_ust), (t + 1100, k.z_ust), dxfattribs={"layer": "OLCU"})
    msp.add_mtext("\\P".join(x.replace("Ø", "%%c") for x in L), dxfattribs=dict(yz, char_height=45)
                  ).set_location((t + 3000, kat[-1].z_ust))
    doc.saveas(yol_dxf)


# =====================================================================================
# 9e) GÖVDE YATAY DONATISI – uç bölgede kenetlenme, U-firkete, köşe/T kancaları, ekler
# =====================================================================================
#   * Uç bölge etriyesi ile gövde yatay donatısı aynı (dış) tabakadadır ve farklı düşey
#     seviyelerdedir; uç bölge boyuna donatısı ikisinin de içinde kalır.
#   * Serbest uç: "firkete" (varsayılan) – yatay çubuklar uca kadar düz gider, uçta U-firkete
#     yatay çubuklarla ≥ 1.5·ℓb bindirilir (Şekil 7.11);  "kanca" – 90° kanca 12Ø.
#   * Köşe / T birleşimi: yatay çubuk karşı kolun dış yüzüne kadar uzanır ve karşı kol boyunca
#     ℓb uzunluğunda 90° kanca ile kenetlenir. Geçen kolda çubuk süreklidir.
#   * Çubuk boyu cubuk_max_boy'u aşarsa l0 = α1·ℓb bindirmeli eklerle bölünür.


def _yon_icine(k2, P, v, L=300.0):
    """v ya da −v'den, L uzunluğundaki kancanın k2 kolunun içinde kaldığı yönü döndürür."""
    pay = -AYAR["paspayi"]
    for sg in (1, -1):
        q = P + sg * v * L
        if k2.icinde(q[0], q[1], pay):
            return sg * v
    return v


def yatay_cubuklar_cok(g, m):
    """Çok kollu kesitte yatay çubuklar ve firketeler (plan geometrisi + ölçüler)."""
    A = AYAR
    c = A["paspayi"]
    dh = g["dh"]
    lb = kenetlenme(dh, m)[0]
    Lf = A["firkete_bindirme"] * lb
    kollar = g["kollar"]
    cubuklar, firketeler = [], []
    for i, k in enumerate(kollar):
        for sg in (-1, 1):
            w = sg * (k.t / 2 - c - dh / 2)
            uclar = []
            for uc in (0, 1):
                ub = k.uclar[uc]
                yon = -1 if uc == 0 else 1
                s_uc = 0.0 if uc == 0 else k.L
                if ub["tip"] == "serbest":
                    s_end = s_uc - yon * c
                    kanca = None
                    if A["yatay_uc_detay"] == "kanca":
                        kanca = (-sg * k.n, _kanca90(dh))
                elif ub["tip"] == "kose" and ub.get("birincil"):
                    s_end = s_uc - yon * c
                    k2 = kollar[ub["es"][0]]
                    kanca = (_yon_icine(k2, k.nokta(s_end, w), k2.e, lb), lb)
                else:
                    t_es = ub["t_es"]
                    s_end = s_uc + yon * (t_es - c - dh / 2)
                    k2 = kollar[ub["es"][0]]
                    if ub["tip"] == "kose":
                        v = _yon_icine(k2, k.nokta(s_end, w), k2.e, lb)
                    else:
                        v = k2.e * (1 if sg < 0 else -1)
                    kanca = (v, lb)
                uclar.append((s_end, kanca))
            (s0, h0), (s1, h1) = uclar
            P0, P1 = k.nokta(s0, w), k.nokta(s1, w)
            pts = [P0, P1]
            if h0:
                pts = [P0 + h0[0] * h0[1]] + pts
            if h1:
                pts = pts + [P1 + h1[0] * h1[1]]
            cubuklar.append(dict(kol=k.ad, pts=[tuple(p) for p in pts], a=abs(s1 - s0),
                                 k1=h0[1] if h0 else 0.0, k2=h1[1] if h1 else 0.0))
        if A["yatay_uc_detay"] == "firkete":
            for uc in (0, 1):
                if k.uclar[uc]["tip"] != "serbest":
                    continue
                yon = 1 if uc == 0 else -1
                s_e = (c + dh * 1.5) if uc == 0 else k.L - c - dh * 1.5
                w0 = k.t / 2 - c - dh * 1.5
                pts = [k.nokta(s_e + yon * Lf, -w0), k.nokta(s_e, -w0), k.nokta(s_e, w0),
                       k.nokta(s_e + yon * Lf, w0)]
                firketeler.append(dict(kol=k.ad, pts=[tuple(p) for p in pts], a=Lf,
                                       b=k.t - 2 * c - 2 * dh))
    return cubuklar, firketeler


def yatay_cubuklar_dik(g, m):
    """Dikdörtgen perdede yatay çubuklar ve uç firketeleri (sol-alt köşe orijinli plan)."""
    A = AYAR
    c = A["paspayi"]
    lw, bw, dh = g["lw"], g["bw"], g["dh"]
    lb = kenetlenme(dh, m)[0]
    Lf = A["firkete_bindirme"] * lb
    cubuklar, firketeler = [], []
    for y, sg in ((c + dh / 2, -1), (bw - c - dh / 2, 1)):
        pts = [(c, y), (lw - c, y)]
        k1 = k2 = 0.0
        if A["yatay_uc_detay"] == "kanca":
            k1 = k2 = _kanca90(dh)
            pts = [(c, y - sg * k1)] + pts + [(lw - c, y - sg * k2)]
        cubuklar.append(dict(pts=pts, a=lw - 2 * c, k1=k1, k2=k2))
    if A["yatay_uc_detay"] == "firkete":
        y0, y1 = c + dh * 1.5, bw - c - dh * 1.5
        for x, yon in ((c + dh * 1.5, 1), (lw - c - dh * 1.5, -1)):
            firketeler.append(dict(pts=[(x + yon * Lf, y0), (x, y0), (x, y1), (x + yon * Lf, y1)],
                                   a=Lf, b=bw - 2 * c - 2 * dh))
    return cubuklar, firketeler


def yatay_parcala(a, k1, k2, d, m):
    """Uzun yatay çubuğu bindirmeli eklerle böler. Döndürür: [(tip, ölçüler, adet)] – bir çubuk
    (bir yüz, bir seviye) için."""
    Lmax = AYAR["cubuk_max_boy"]
    top = a + k1 + k2
    if top <= Lmax:
        if k1 or k2:
            return [("yatay2", dict(a=a, k1=k1, k2=k2), 1)]
        return [("duz", dict(a=a), 1)]
    l0 = kenetlenme(d, m)[1]
    n = math.ceil((top - l0) / (Lmax - l0) - 1e-9)
    p = (a + (n - 1) * l0) / n            # düz kısım uzunluğu (her parça)
    out = []
    out.append(("yatay2", dict(a=p, k1=k1, k2=0.0), 1) if k1 else ("duz", dict(a=p), 1))
    if n > 2:
        out.append(("duz", dict(a=p), n - 2))
    out.append(("yatay2", dict(a=p, k1=0.0, k2=k2), 1) if k2 else ("duz", dict(a=p), 1))
    return out


# =====================================================================================
# 9f) PERDE BOY KESİTİ (düşey görünüş / çok kollu perdede kol açınımı)
# =====================================================================================
#   Kat kotları, Hcr, kademeli geçiş, uç bölgeler, etriye sıklaştırma, bindirme bölgeleri,
#   temel içi etriye bölgesi, gövde yatay donatısı ve grup özetleri. PNG ve DXF aynı ilkel
#   çizim listesinden üretilir.

class _Cizim:
    def __init__(self):
        self.p = []

    def cizgi(self, x1, y1, x2, y2, katman, renk="black", lw=0.6, ls="-"):
        self.p.append(("l", (x1, y1, x2, y2), katman, renk, lw, ls))

    def dikd(self, x, y, w, h, katman, renk="none", kenar="black", lw=0.6, ls="-", alfa=1.0):
        self.p.append(("r", (x, y, w, h), katman, renk, kenar, lw, ls, alfa))

    def yazi(self, x, y, t, h, katman="YAZI", renk="black", ha="left", va="center", pt=7):
        self.p.append(("t", (x, y), t, h, katman, renk, ha, va, pt))

    def kapsam(self):
        xs, ys = [], []
        for e in self.p:
            if e[0] == "l":
                xs += [e[1][0], e[1][2]]
                ys += [e[1][1], e[1][3]]
            elif e[0] == "r":
                xs += [e[1][0], e[1][0] + e[1][2]]
                ys += [e[1][1], e[1][1] + e[1][3]]
            else:
                xs.append(e[1][0])
                ys.append(e[1][1])
        return min(xs), max(xs), min(ys), max(ys)

    def png(self, yol, baslik):
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.patches as mp
        x0, x1, y0, y1 = self.kapsam()
        W, H = x1 - x0, y1 - y0
        fw = 16.0
        fh = max(6.0, min(80.0, fw * H / W))
        fig, ax = plt.subplots(figsize=(fw, fh))
        for e in self.p:
            if e[0] == "l":
                (a, b, c_, d), _, renk, lw, ls = e[1], e[2], e[3], e[4], e[5]
                ax.plot([a, c_], [b, d], color=renk, lw=lw, ls=ls)
            elif e[0] == "r":
                (x, y, w, h), _, renk, kenar, lw, ls, alfa = e[1:]
                ax.add_patch(mp.Rectangle((x, y), w, h, fc=renk, ec=kenar, lw=lw, ls=ls, alpha=alfa))
            else:
                (x, y), t, h, _, renk, ha, va, pt = e[1:]
                ax.text(x, y, t, fontsize=pt, color=renk, ha=ha, va=va)
        ax.set_xlim(x0 - 200, x1 + 200)
        ax.set_ylim(y0 - 200, y1 + 200)
        ax.set_aspect("equal")
        ax.axis("off")
        ax.set_title(baslik, fontsize=12, weight="bold", loc="left")
        fig.savefig(yol, dpi=90 if fh > 30 else 120, bbox_inches="tight")
        plt.close(fig)

    def dxf(self, yol):
        doc = _dxf_yeni()
        for ad, renk in (("BK_KAT", 8), ("BK_UC", 1), ("BK_ETRIYE", 1), ("BK_BOYUNA", 6),
                         ("BK_YATAY", 4), ("BK_BINDIRME", 30), ("BK_TEMEL", 9), ("BK_HCR", 1)):
            if ad not in doc.layers:
                doc.layers.add(ad, color=renk)
        doc.layers.get("BK_HCR").dxf.linetype = "DASHED"
        msp = doc.modelspace()
        for e in self.p:
            if e[0] == "l":
                a, b, c_, d = e[1]
                msp.add_line((a, b), (c_, d), dxfattribs={"layer": e[2]})
            elif e[0] == "r":
                x, y, w, h = e[1]
                msp.add_lwpolyline([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], close=True,
                                   dxfattribs={"layer": e[2]})
            else:
                (x, y), t, h, katman = e[1], e[2], e[3], e[4]
                ha = e[6]
                ap = {"left": 4, "center": 5, "right": 6}[ha]
                msp.add_mtext(t.replace("Ø", "%%c").replace("\n", "\\P"),
                              dxfattribs=dict(layer=katman, style="TR", char_height=h)
                              ).set_location((x, y), attachment_point=ap)
        doc.saveas(yol)


def _serit_bilgisi_dik(g):
    """Dikdörtgen perde: tek şerit. [(şerit adı, L, zonlar[(x0, x1, tip)], uç kolonlar,
    gövde kolonları, kalınlık)]"""
    lw, Lu = g["lw"], g["Lu"]
    zon = [(0.0, Lu, "uç"), (lw - Lu, lw, "uç")]
    zon += [(z["x0"], z["x1"], "kiriş") for z in g.get("kiris_bolgeleri", [])]
    uc_x = sorted({round(x) for x, y, d in g["uc"]} | {round(x) for x, y, d in g.get("kiris_bars", [])})
    gv_x = sorted({round(x) for x, y, d in g["govde"]})
    return [("", lw, zon, uc_x, gv_x, g["bw"])]


def _serit_bilgisi_cok(g):
    out = []
    for ki, k in enumerate(g["kollar"]):
        zon = []
        for p, x in zip(g["parcalar"], g["detay"]):
            if p["kol"] == ki:
                zon.append((p["s0"], p["s1"], "kiriş" if "kiriş" in p["tip"] else "uç"))
        uc_s, gv_s = set(), set()
        for u, v, d, *_ in g["uc"]:
            if k.icinde(u, v):
                uc_s.add(round(float(k.yerel(u, v)[0])))
        for u, v, d in g["govde"]:
            if k.icinde(u, v):
                gv_s.add(round(float(k.yerel(u, v)[0])))
        out.append((k.ad, k.L, zon, sorted(uc_s), sorted(gv_s), k.t))
    return out


def boy_kesit_ciz(ps, yol_png, yol_dxf, m):
    A = AYAR
    cz = _Cizim()
    gr = ps["gruplar"]
    cok = ps.get("tip") == "cok"
    serit_f = _serit_bilgisi_cok if cok else _serit_bilgisi_dik
    # şerit (kol) konumları: en geniş hallerine göre yan yana
    seritler0 = serit_f(gr[0])
    ns = len(seritler0)
    genislik = [0.0] * ns
    for g in gr:
        for i, sb in enumerate(serit_f(g)[:ns]):
            genislik[i] = max(genislik[i], sb[1])
    ara = 1500.0
    x_bas = [sum(genislik[:i]) + ara * i for i in range(ns)]
    Wtop = x_bas[-1] + genislik[-1]
    z0 = gr[0]["z"][0]
    zt = gr[-1]["z"][1]
    Hcr = ps["Hcr"]
    th = 120.0                                        # DXF yazı yüksekliği (mm)
    # temel
    h_tem = 800.0
    cz.dikd(-600, z0 - h_tem, Wtop + 1200, h_tem, "BK_TEMEL", renk="#e5e5e5", kenar="#7f7f7f")
    cz.yazi(-650, z0 - h_tem / 2, "TEMEL", th, "BK_TEMEL", ha="right", pt=8)
    # kat çizgileri
    katlar = [k for g in gr for k in g["_katlar"]]
    for k in katlar:
        cz.cizgi(-900, k.z_ust, Wtop + 900, k.z_ust, "BK_KAT", renk="#7f8c8d", lw=0.5, ls="--")
        cz.yazi(-950, k.z_ust, f"{k.kat}  +{k.z_ust / 1000:.2f}", th, "BK_KAT", ha="right", pt=7)
    cz.yazi(-950, z0, f"±{z0 / 1000:.2f}", th, "BK_KAT", ha="right", pt=7)
    # Hcr
    cz.cizgi(-1400, z0 + Hcr, Wtop + 1400, z0 + Hcr, "BK_HCR", renk="#c0392b", lw=1.2, ls="-.")
    cz.yazi(Wtop + 1450, z0 + Hcr, f"Hcr = {Hcr / 1000:.2f} m (kritik perde yüksekliği)", th,
            "BK_HCR", renk="#c0392b", pt=8)
    for gi, g in enumerate(gr):
        za, zb = g["z"]
        renk_uc = "#f5b7b1" if g["kritik"] else ("#fad7a0" if g.get("kademe", 0) > 0 else "#fdebd0")
        de = g["de"]
        lb, l0 = kenetlenme(de, m)
        dh, sh = g["dh"], g["sh"]
        det, sd = g["etr"]["d"], g["etr"]["s"]
        for i, (ad, L, zon, uc_x, gv_x, t) in enumerate(serit_f(g)[:ns]):
            xb = x_bas[i]
            cz.dikd(xb, za, L, zb - za, "BK_KAT", kenar="black", lw=1.0)
            for (s0, s1, tip) in zon:
                cz.dikd(xb + s0, za, s1 - s0, zb - za, "BK_UC",
                        renk="#aed6f1" if tip == "kiriş" else renk_uc, kenar="#c0392b", lw=0.5,
                        alfa=0.7)
                # etriyeler
                for k in g["_katlar"]:
                    z = k.z_alt + 50
                    while z < k.z_ust - 20:
                        cz.cizgi(xb + s0 + 20, z, xb + s1 - 20, z, "BK_ETRIYE", renk="#c0392b",
                                 lw=0.35)
                        z += sd
            # boyuna donatılar
            for x in uc_x:
                cz.cizgi(xb + x, za, xb + x, zb, "BK_BOYUNA", renk="black", lw=0.5)
            for x in gv_x:
                cz.cizgi(xb + x, za, xb + x, zb, "BK_BOYUNA", renk="#1b4f72", lw=0.3)
            # gövde yatay
            for k in g["_katlar"]:
                z = k.z_alt + sh / 2
                while z < k.z_ust:
                    cz.cizgi(xb + A["paspayi"], z, xb + L - A["paspayi"], z, "BK_YATAY",
                             renk="#5dade2", lw=0.25)
                    z += sh
            # bindirme bölgeleri: her grubun ilk katının altında (temel üstünde de filiz bindirmesi)
            for k in g["_katlar"]:
                for (s0, s1, tip) in zon:
                    cz.dikd(xb + s0, k.z_alt, s1 - s0, l0, "BK_BINDIRME", renk="none",
                            kenar="#e67e22", lw=0.8, ls="--")
            if ad:
                cz.yazi(xb + L / 2, zt + 250, f"{ad}  (L={L:.0f}, t={t:.0f})", th, pt=8, ha="center")
        # temel içi etriye (TBDY 7.6.5.2b)
        if gi == 0 and g["kritik"]:
            h_t = max(300.0, max(sb[5] for sb in serit_f(g)))
            for i, (ad, L, zon, *_r) in enumerate(serit_f(g)[:ns]):
                for (s0, s1, tip) in zon:
                    if tip != "uç":
                        continue
                    z = z0 - 50
                    while z > z0 - h_t - 1:
                        cz.cizgi(x_bas[i] + s0 + 20, z, x_bas[i] + s1 - 20, z, "BK_ETRIYE",
                                 renk="#c0392b", lw=0.5)
                        z -= sd
            cz.yazi(Wtop + 1450, z0 - h_t / 2,
                    f"Uç bölge etriyeleri temelde ≥ max(300, bw) = {h_t:.0f} mm devam eder", th,
                    "BK_TEMEL", renk="#c0392b", pt=7)
        # grup özeti (sağda)
        if cok:
            uc_t = f"uç: Ø{de}, uçta {g['ny']}×{g['ncol']} küme, {g['nbar_uc']} çubuk toplam"
        else:
            uc_t = f"uç: {g['nbar_uc']}Ø{de} (her uçta), Lu={g['Lu']:.0f}"
        gc = g["gciroz"]
        metin = (f"{g['etiket']} – {bolge_adi(g)}\n{g['katlar'][0]}–{g['katlar'][-1]}\n{uc_t}\n"
                 f"etriye Ø{det}/{sd:.0f}\nyatay Ø{dh}/{sh:.0f}"
                 + (" + U-firkete" if A["yatay_uc_detay"] == "firkete" else "") +
                 f"\nçiroz {gc['adet_m2']:.1f}/m² (≥{gc['gerek']:.0f})\nbindirme l0={l0:.0f} (Ø{de})")
        cz.yazi(Wtop + 1450, (za + zb) / 2, metin, th, pt=7)
    lej = ("Açıklama: pembe = kritik bölge uç bölgesi, turuncu = kademeli geçiş, açık = kritik üstü, "
           "mavi = kiriş bölgesi; kırmızı çizgiler etriye seviyeleri; turuncu kesikli = bindirme "
           "bölgesi (l0); siyah düşey = uç bölge boyuna, lacivert = gövde düşey, açık mavi = yatay")
    cz.yazi(0, z0 - h_tem - 400, lej, th * 0.8, pt=7)
    bas = f"{ps['pier']} – PERDE BOY KESİTİ" + (" (kol açınımı)" if cok else "")
    cz.png(yol_png, bas)
    cz.dxf(yol_dxf)


# =====================================================================================
# 9d) POZ, METRAJ VE AÇILIM (tekniker bilgileri)
# =====================================================================================
#   Her pier için bütün çubuklar kat kat üretilir, aynı şekil + çap + ölçüdeki çubuklar tek
#   poz altında toplanır. Boylar:
#     * boyuna donatı: kat yüksekliği + bindirme (l0) – düz devam
#                      kırımlı: (h − birleşim) + eğik kısım + l0 – kırım
#                      üstte devam etmeyen / son kat: h − 30 + 12Ø kanca
#       (temel filizleri dahil değildir)
#     * filiz ekimi: gömülme + l0
#     * etriye: dış ölçüler a×b, 135° iki kanca, kanca boyu max(10Ø, 100)
#     * çiroz: a + iki 135° kanca;   yatay gövde: a + iki 90° kanca (12Ø)

KG_M = lambda d: 0.006165 * d * d


def _kanca135(d):
    return max(10 * d, 100.0)


def _kanca90(d):
    return 12.0 * d


class PozDefteri:
    def __init__(self):
        self.pozlar = {}      # anahtar -> dict

    def ekle(self, tip, d, olcu, adet, aciklama, grup=None, kat=None):
        """olcu: şekil ölçüleri (mm) sözlüğü. Döndürür: poz no"""
        if adet <= 0:
            return None
        o = {k: int(round(v / 10.0) * 10) for k, v in olcu.items()}
        anahtar = (tip, int(d), tuple(sorted(o.items())))
        if anahtar not in self.pozlar:
            boy = _poz_boy(tip, d, o)
            self.pozlar[anahtar] = dict(no=len(self.pozlar) + 1, tip=tip, d=int(d), olcu=o,
                                        boy=boy, adet=0, aciklama=aciklama, katlar=set())
        p = self.pozlar[anahtar]
        p["adet"] += int(adet)
        if kat:
            p["katlar"].add(kat)
        return p["no"]

    def liste(self):
        out = sorted(self.pozlar.values(), key=lambda p: p["no"])
        for p in out:
            p["toplam_m"] = p["boy"] * p["adet"] / 1000.0
            p["kg"] = p["toplam_m"] * KG_M(p["d"])
        return out


def _poz_boy(tip, d, o):
    if tip in ("duz", "filiz"):
        return o["a"]
    if tip == "kancali":
        return o["a"] + o["k"]
    if tip == "kirimli":
        return o["a"] + math.hypot(o["e"], o["h"]) + o["b"]
    if tip == "etriye":
        return 2 * (o["a"] + o["b"]) - 4 * d + 2 * o["k"]
    if tip == "ciroz":
        return o["a"] + 2 * o["k"]
    if tip == "yatay":
        return o["a"] + 2 * o["k"]
    if tip == "yatay2":
        return o["a"] + o["k1"] + o["k2"]
    if tip == "firkete":
        return 2 * o["a"] + o["b"]
    return o.get("a", 0)


def _boyuna_pozlar(defter, g, k, son_kat_mi, gecis, bar_listesi, etiket, m, poz_kume):
    """Bir kattaki boyuna çubuklar. gecis: bu grubun üst geçişi (grubun son katında)."""
    h = k.z_ust - k.z_alt
    hb = AYAR["birlesim_yuksekligi"]
    harita = {}
    if gecis is not None:
        for b in gecis["duz"]:
            harita[(round(b["xl"]), round(b["yl"]))] = ("duz", b)
        for b in gecis["kirim"]:
            harita[(round(b["xl"]), round(b["yl"]))] = ("kirim", b)
        for b in gecis["biten"]:
            harita[(round(b["x"]), round(b["y"]))] = ("biten", b)
    sayac = {}
    for x, y, d in bar_listesi:
        lb, l0 = kenetlenme(d, m)
        if son_kat_mi:
            anahtar = ("kancali", d, dict(a=h - 30, k=_kanca90(d)))
        elif gecis is None:
            anahtar = ("duz", d, dict(a=h + l0))
        else:
            tur, b = harita.get((round(x), round(y)), ("biten", None))
            if tur == "duz":
                l0 = kenetlenme(max(d, b["du"]), m)[1]
                anahtar = ("duz", d, dict(a=h + l0))
            elif tur == "kirim":
                l0 = kenetlenme(max(d, b["du"]), m)[1]
                # kırım miktarı 25 mm'lik adımlara yuvarlanır (poz sayısını azaltmak için)
                anahtar = ("kirimli", d, dict(a=h - hb, e=math.ceil(b["e"] / 25.0) * 25.0,
                                              h=hb, b=l0))
            else:
                anahtar = ("kancali", d, dict(a=h - 30, k=_kanca90(d)))
        sayac[anahtar[:2] + (tuple(sorted(anahtar[2].items())),)] = \
            sayac.get(anahtar[:2] + (tuple(sorted(anahtar[2].items())),), 0) + 1
    for (tip, d, olcu), n in sayac.items():
        no = defter.ekle(tip, d, dict(olcu), n, etiket, kat=k.kat)
        poz_kume.add(no)
    if gecis is not None:
        for f in gecis["filiz"]:
            no = defter.ekle("filiz", f["du"], dict(a=f["gomulme"] + f["bindirme"]), 1,
                             "filiz ekimi", kat=k.kat)
            poz_kume.add(no)


def _yatay_pozlar(defter, g, h, m, kume, kat):
    n_sev = int(h // g["sh"]) + 1
    dh = g["dh"]
    for cb in g["yatay"]["cubuklar"]:
        ack = "gövde yatay" + (f" ({cb['kol']})" if cb.get("kol") else "")
        parcalar = yatay_parcala(cb["a"], cb["k1"], cb["k2"], dh, m)
        ekli = sum(n for _, _, n in parcalar) > 1
        for tip, olcu, n in parcalar:
            kume.add(defter.ekle(tip, dh, olcu, n * n_sev, ack + (" – bindirmeli ek" if ekli else ""),
                                 kat=kat))
    for fk in g["yatay"]["firketeler"]:
        kume.add(defter.ekle("firkete", dh, dict(a=fk["a"], b=fk["b"]), n_sev,
                             "uç U-firkete (≥1.5ℓb bindirme)", kat=kat))


def metraj_dik(ps, m):
    defter = PozDefteri()
    c = AYAR["paspayi"]
    gr = ps["gruplar"]
    for gi, g in enumerate(gr):
        poz = {x: set() for x in ("uc", "gd", "kb", "etr", "ciroz", "gy", "gc", "filiz")}
        katlar = g["_katlar"]
        for ki, k in enumerate(katlar):
            h = k.z_ust - k.z_alt
            son_grup_kat = ki == len(katlar) - 1
            en_ust = son_grup_kat and gi == len(gr) - 1
            gec = g.get("gecis") if son_grup_kat else None
            _boyuna_pozlar(defter, g, k, en_ust, gec, g["uc"], "uç bölge boyuna", m, poz["uc"])
            _boyuna_pozlar(defter, g, k, en_ust, gec, g["govde"], "gövde düşey", m, poz["gd"])
            if g.get("kiris_bars"):
                _boyuna_pozlar(defter, g, k, en_ust, gec, g["kiris_bars"], "kiriş bölgesi boyuna",
                               m, poz["kb"])
            # uç bölge etriye + çiroz (iki uç)
            de, ed = g["etr"]["d"], g["etr"]["s"]
            n_et = int(h // ed) + 1
            # kritik bölge etriyeleri temel içinde max(300, bw) boyunca devam eder (TBDY 7.6.5.2b)
            n_t = math.ceil(max(300.0, g["bw"]) / ed) + 1 if (gi == 0 and ki == 0 and g["kritik"]) else 0
            for ha, hb in g["etr"]["hooplar"]:
                poz["etr"].add(defter.ekle("etriye", de, dict(a=hb - ha - c, b=g["bw"] - 2 * c,
                                                             k=_kanca135(de)), 2 * n_et,
                                           "uç bölge etriyesi", kat=k.kat))
                if n_t:
                    poz["etr"].add(defter.ekle("etriye", de, dict(a=hb - ha - c, b=g["bw"] - 2 * c,
                                                                 k=_kanca135(de)), 2 * n_t,
                                               "uç bölge etriyesi (temel içi)", kat="TEMEL"))
            nc = len(g["etr"]["ciroz_x"])
            if nc:
                poz["ciroz"].add(defter.ekle("ciroz", de, dict(a=g["bw"] - 2 * c, k=_kanca135(de)),
                                             2 * nc * (n_et + n_t), "uç bölge çirozu", kat=k.kat))
            for y_, xa, xb in g["etr"]["ciroz_y"]:
                poz["ciroz"].add(defter.ekle("ciroz", de, dict(a=xb - xa + de, k=_kanca135(de)),
                                             2 * n_et, "uç bölge boyuna çiroz", kat=k.kat))
            for z in g.get("kiris_bolgeleri", []):
                for ha, hb in z["hooplar"]:
                    poz["etr"].add(defter.ekle("etriye", de, dict(a=hb - ha, b=g["bw"] - 2 * c,
                                                                 k=_kanca135(de)), n_et,
                                               "kiriş bölgesi etriyesi", kat=k.kat))
                if z["ciroz_x"]:
                    poz["ciroz"].add(defter.ekle("ciroz", de, dict(a=g["bw"] - 2 * c, k=_kanca135(de)),
                                                 len(z["ciroz_x"]) * n_et, "kiriş bölgesi çirozu",
                                                 kat=k.kat))
            # yatay gövde (2 yüz) + uç firketeleri
            _yatay_pozlar(defter, g, h, m, poz["gy"], k.kat)
            # gövde çirozu
            gc = g["gciroz"]
            n_sev = int(h // gc["s_duz"]) + 1
            adet = math.ceil(n_sev * (len(gc["xA"]) + len(gc["xB"])) / 2)
            poz["gc"].add(defter.ekle("ciroz", gc["d"], dict(a=g["bw"] - 2 * c, k=_kanca135(gc["d"])),
                                      adet, "gövde çirozu", kat=k.kat))
        g["poz"] = {k_: sorted(v for v in s if v) for k_, s in poz.items()}
    return defter.liste()


def metraj_cok(ps, m):
    defter = PozDefteri()
    c = AYAR["paspayi"]
    gr = ps["gruplar"]
    for gi, g in enumerate(gr):
        poz = {x: set() for x in ("uc", "gd", "etr", "ciroz", "gy", "gc")}
        katlar = g["_katlar"]
        uc3 = [(u, v, d) for u, v, d, *_ in g["uc"]]
        for ki, k in enumerate(katlar):
            h = k.z_ust - k.z_alt
            son_grup_kat = ki == len(katlar) - 1
            en_ust = son_grup_kat and gi == len(gr) - 1
            gec = g.get("gecis") if son_grup_kat else None
            _boyuna_pozlar(defter, g, k, en_ust, gec, uc3, "uç bölge boyuna", m, poz["uc"])
            _boyuna_pozlar(defter, g, k, en_ust, gec, g["govde"], "gövde düşey", m, poz["gd"])
            de, ed = g["etr"]["d"], g["etr"]["s"]
            n_et = int(h // ed) + 1
            n_t = math.ceil(max(300.0, max(kk.t for kk in g["kollar"])) / ed) + 1 \
                if (gi == 0 and ki == 0 and g["kritik"]) else 0
            for p, x in zip(g["parcalar"], g["detay"]):
                kol = g["kollar"][p["kol"]]
                ack = "kiriş bölgesi etriyesi" if "kiriş" in p["tip"] else "uç bölge etriyesi"
                for ha, hb in x["hooplar"]:
                    poz["etr"].add(defter.ekle("etriye", de, dict(a=hb - ha, b=kol.t - 2 * c,
                                                                 k=_kanca135(de)), n_et, ack, kat=k.kat))
                    if n_t and "kiriş" not in p["tip"]:
                        poz["etr"].add(defter.ekle("etriye", de, dict(a=hb - ha, b=kol.t - 2 * c,
                                                                     k=_kanca135(de)), n_t,
                                                   ack + " (temel içi)", kat="TEMEL"))
                n_c = len(x["ciroz_s"])
                if n_c:
                    poz["ciroz"].add(defter.ekle("ciroz", de, dict(a=kol.t - 2 * c, k=_kanca135(de)),
                                                 n_c * (n_et + (n_t if "kiriş" not in p["tip"] else 0)),
                                                 "uç bölge çirozu", kat=k.kat))
                if len(x["ws"]) > 2:
                    poz["ciroz"].add(defter.ekle("ciroz", de, dict(a=x["hoop"][1] - x["hoop"][0],
                                                                   k=_kanca135(de)),
                                                 (len(x["ws"]) - 2) * n_et, "uç bölge boyuna çiroz",
                                                 kat=k.kat))
            _yatay_pozlar(defter, g, h, m, poz["gy"], k.kat)
            gc = g["gciroz"]
            n_sev = int(h // gc["s_duz"]) + 1
            for kol in g["kollar"]:
                n_k = sum(1 for kk, s_, sv in gc["ciftler"] if kk is kol)
                poz["gc"].add(defter.ekle("ciroz", gc["d"], dict(a=kol.t - 2 * c, k=_kanca135(gc["d"])),
                                          math.ceil(n_sev * n_k / 2), "gövde çirozu", kat=k.kat))
        g["poz"] = {k_: sorted(v for v in s if v) for k_, s in poz.items()}
    return defter.liste()


def metraj_bodrum(bs, m):
    defter = PozDefteri()
    c = AYAR["paspayi"]
    for i, (k, r) in enumerate(zip(bs["katlar"], bs["satirlar"])):
        h = k.z_ust - k.z_alt
        L = r["L"]
        poz = {}
        for yuz in ("+3", "−3"):
            q = r[("düşey", yuz)]
            n = int((L - 2 * c) // q["s"]) + 1
            l0 = kenetlenme(q["d"], m)[1]
            if i == len(bs["katlar"]) - 1:
                no = defter.ekle("kancali", q["d"], dict(a=h - 30, k=_kanca90(q["d"])), n,
                                 f"düşey {yuz} yüzü", kat=k.kat)
            else:
                no = defter.ekle("duz", q["d"], dict(a=h + l0), n, f"düşey {yuz} yüzü", kat=k.kat)
            poz[("düşey", yuz)] = no
            q2 = r[("yatay", yuz)]
            n2 = int(h // q2["s"]) + 1
            poz[("yatay", yuz)] = defter.ekle("yatay", q2["d"], dict(a=L - 2 * c, k=_kanca90(q2["d"])),
                                              n2, f"yatay {yuz} yüzü", kat=k.kat)
        s_v = min(r[("düşey", "+3")]["s"], r[("düşey", "−3")]["s"])
        s_h = min(r[("yatay", "+3")]["s"], r[("yatay", "−3")]["s"])
        n_c = math.ceil(L * h / (2 * s_v * 2 * s_h))
        poz["çiroz"] = defter.ekle("ciroz", 10, dict(a=r["t"] - 2 * c, k=_kanca135(10)), n_c,
                                   "çiroz (şaşırtmalı, her 2. kesişim)", kat=k.kat)
        r["poz"] = poz
    return defter.liste()


# ---------------------------------------------------------------- DXF: tablo + açılımlar
def _tablo_ciz(msp, pozlar, x0, y0, baslik):
    kol = [("Poz", 260), ("Ø", 200), ("Şekil", 400), ("Boy (cm)", 360), ("Adet", 300),
           ("Top. boy (m)", 480), ("Ağırlık (kg)", 480), ("Açıklama", 1500)]
    hs = 110
    W = sum(w for _, w in kol)
    yz = dict(layer="METRAJ", style="TR")
    msp.add_text(baslik, height=90, dxfattribs=yz).set_placement((x0, y0 + 60))
    y = y0
    satirlar = [[k for k, _ in kol]]
    for p in pozlar:
        satirlar.append([f"P{p['no']}", f"%%c{p['d']}", _sekil_adi(p["tip"]), f"{p['boy'] / 10:.0f}",
                         f"{p['adet']}", f"{p['toplam_m']:.1f}", f"{p['kg']:.1f}", p["aciklama"]])
    # çap toplamları
    caplar = sorted({p["d"] for p in pozlar})
    satirlar.append(["", "", "", "", "", "", "", ""])
    for d in caplar:
        tm = sum(p["toplam_m"] for p in pozlar if p["d"] == d)
        satirlar.append(["", f"%%c{d}", "", "", "", f"{tm:.1f}", f"{tm * KG_M(d):.1f}", "çap toplamı"])
    toplam = sum(p["kg"] for p in pozlar)
    satirlar.append(["", "", "", "", "", "", f"{toplam:.1f}", "GENEL TOPLAM (kg)"])
    for i, sat in enumerate(satirlar):
        x = x0
        msp.add_line((x0, y), (x0 + W, y), dxfattribs={"layer": "METRAJ"})
        for (ad, w), metin in zip(kol, sat):
            msp.add_text(str(metin), height=60, dxfattribs=yz).set_placement((x + 25, y - hs + 30))
            x += w
        y -= hs
    msp.add_line((x0, y), (x0 + W, y), dxfattribs={"layer": "METRAJ"})
    x = x0
    for _, w in kol + [("", 0)]:
        msp.add_line((x, y0), (x, y), dxfattribs={"layer": "METRAJ"})
        x += w
    return y, W


def _sekil_adi(tip):
    return dict(duz="düz", filiz="filiz", kancali="kancalı", kirimli="kırımlı", etriye="etriye",
                ciroz="çiroz", yatay="yatay U", yatay2="yatay kancalı",
                firkete="U-firkete").get(tip, tip)


def _acilim_ciz(msp, p, x0, y0, w=1500, h=520):
    """Poz açılımı (şematik, ölçüler yazılı). x0, y0: hücre sol üst."""
    L = dict(layer="ACILIM")
    yz = dict(layer="ACILIM", style="TR")
    o = p["olcu"]
    cx, cy = x0 + w / 2, y0 - h / 2 - 40
    msp.add_text(f"P{p['no']}  {p['adet']}%%c{p['d']}  L={p['boy'] / 10:.0f} cm", height=60,
                 dxfattribs=yz).set_placement((x0, y0 - 60))
    t = p["tip"]
    if t in ("duz", "filiz"):
        msp.add_line((x0 + 100, cy), (x0 + w - 100, cy), dxfattribs=L)
        msp.add_text(f"{o['a'] / 10:.0f}", height=55, dxfattribs=yz).set_placement(
            (cx, cy + 40), align=TextEntityAlignment_center())
        if t == "filiz":
            msp.add_text("(gömülme + bindirme)", height=40, dxfattribs=yz).set_placement(
                (cx, cy - 90), align=TextEntityAlignment_center())
    elif t == "kancali":
        msp.add_lwpolyline([(x0 + 100, cy), (x0 + w - 150, cy), (x0 + w - 150, cy - 150)], dxfattribs=L)
        msp.add_text(f"{o['a'] / 10:.0f}", height=55, dxfattribs=yz).set_placement(
            (cx, cy + 40), align=TextEntityAlignment_center())
        msp.add_text(f"{o['k'] / 10:.0f}", height=45, dxfattribs=yz).set_placement(
            (x0 + w - 130, cy - 110))
    elif t == "kirimli":
        a_ = (w - 200) * 0.55
        msp.add_lwpolyline([(x0 + 100, cy - 60), (x0 + 100 + a_, cy - 60),
                            (x0 + 100 + a_ + 150, cy + 60), (x0 + w - 100, cy + 60)], dxfattribs=L)
        msp.add_text(f"{o['a'] / 10:.0f}", height=50, dxfattribs=yz).set_placement(
            (x0 + 100 + a_ / 2, cy - 30), align=TextEntityAlignment_center())
        msp.add_text(f"e={o['e'] / 10:.1f} / h={o['h'] / 10:.0f}", height=40, dxfattribs=yz).set_placement(
            (x0 + 100 + a_ + 75, cy - 150), align=TextEntityAlignment_center())
        msp.add_text(f"{o['b'] / 10:.0f}", height=50, dxfattribs=yz).set_placement(
            (x0 + w - 250, cy + 90), align=TextEntityAlignment_center())
    elif t == "etriye":
        oran = o["b"] / max(o["a"], 1)
        bw_ = min(w - 400, 900)
        bh_ = max(120, min(h - 200, bw_ * oran))
        x1, y1 = cx - bw_ / 2, cy - bh_ / 2
        msp.add_lwpolyline([(x1, y1), (x1 + bw_, y1), (x1 + bw_, y1 + bh_), (x1, y1 + bh_)],
                           close=True, dxfattribs=L)
        msp.add_line((x1, y1 + bh_), (x1 + 90, y1 + bh_ - 90), dxfattribs=L)
        msp.add_line((x1 + 60, y1 + bh_), (x1 + 150, y1 + bh_ - 90), dxfattribs=L)
        msp.add_text(f"{o['a'] / 10:.0f}", height=50, dxfattribs=yz).set_placement(
            (cx, y1 - 70), align=TextEntityAlignment_center())
        msp.add_text(f"{o['b'] / 10:.0f}", height=50, dxfattribs=yz).set_placement(
            (x1 + bw_ + 40, cy))
        msp.add_text(f"135° kanca {o['k'] / 10:.0f}", height=40, dxfattribs=yz).set_placement(
            (x1, y1 + bh_ + 30))
    elif t == "ciroz":
        msp.add_lwpolyline([(x0 + 250, cy - 90), (x0 + 200, cy), (x0 + w - 200, cy),
                            (x0 + w - 250, cy - 90)], dxfattribs=L)
        msp.add_text(f"{o['a'] / 10:.0f}  (135° kancalar {o['k'] / 10:.0f})", height=50,
                     dxfattribs=yz).set_placement((cx, cy + 40), align=TextEntityAlignment_center())
    elif t == "yatay":
        msp.add_lwpolyline([(x0 + 100, cy - 150), (x0 + 100, cy), (x0 + w - 100, cy),
                            (x0 + w - 100, cy - 150)], dxfattribs=L)
        msp.add_text(f"{o['a'] / 10:.0f}", height=55, dxfattribs=yz).set_placement(
            (cx, cy + 40), align=TextEntityAlignment_center())
        msp.add_text(f"{o['k'] / 10:.0f}", height=45, dxfattribs=yz).set_placement(
            (x0 + 130, cy - 120))
    elif t == "yatay2":
        k1, k2 = o["k1"], o["k2"]
        pts = [(x0 + 100, cy)] if not k1 else [(x0 + 100, cy - 150), (x0 + 100, cy)]
        pts += [(x0 + w - 100, cy)] + ([(x0 + w - 100, cy - 150)] if k2 else [])
        msp.add_lwpolyline(pts, dxfattribs=L)
        msp.add_text(f"{o['a'] / 10:.0f}", height=55, dxfattribs=yz).set_placement(
            (cx, cy + 40), align=TextEntityAlignment_center())
        if k1:
            msp.add_text(f"{k1 / 10:.0f}", height=45, dxfattribs=yz).set_placement((x0 + 130, cy - 120))
        if k2:
            msp.add_text(f"{k2 / 10:.0f}", height=45, dxfattribs=yz).set_placement(
                (x0 + w - 230, cy - 120))
    elif t == "firkete":
        bh = 180
        msp.add_lwpolyline([(x0 + w - 150, cy + bh / 2), (x0 + 150, cy + bh / 2),
                            (x0 + 150, cy - bh / 2), (x0 + w - 150, cy - bh / 2)], dxfattribs=L)
        msp.add_text(f"{o['a'] / 10:.0f}", height=50, dxfattribs=yz).set_placement(
            (cx, cy + bh / 2 + 30), align=TextEntityAlignment_center())
        msp.add_text(f"{o['b'] / 10:.0f}", height=45, dxfattribs=yz).set_placement((x0 + 40, cy))
    msp.add_lwpolyline([(x0, y0), (x0 + w, y0), (x0 + w, y0 - h), (x0, y0 - h)], close=True,
                       dxfattribs={"layer": "METRAJ"})


def TextEntityAlignment_center():
    from ezdxf.enums import TextEntityAlignment
    return TextEntityAlignment.CENTER


def metraj_dxf_ekle(yol_dxf, pozlar, baslik):
    """Var olan plan DXF'ine metraj tablosu ve açılımları ekler (çizimin sağına)."""
    import ezdxf
    doc = ezdxf.readfile(yol_dxf)
    for ad, renk in (("METRAJ", 7), ("ACILIM", 1)):
        if ad not in doc.layers:
            doc.layers.add(ad, color=renk)
    if "TR" not in doc.styles:
        doc.styles.add("TR", font="arial.ttf")
    msp = doc.modelspace()
    from ezdxf import bbox as _bb
    xs, ys = [], []
    for e in msp:
        if e.dxftype() in ("MTEXT", "TEXT"):
            try:
                ins = e.dxf.insert
                n = max(len(x) for x in e.plain_text().split("\n")) if e.dxftype() == "MTEXT" \
                    else len(e.dxf.text)
                hh = e.dxf.char_height if e.dxftype() == "MTEXT" else e.dxf.height
                xs += [ins.x - n * hh * 0.6, ins.x + n * hh * 0.6]
                ys.append(ins.y)
            except Exception:
                pass
            continue
        try:
            b = _bb.extents([e], fast=True)
            if b.has_data:
                xs += [b.extmin.x, b.extmax.x]
                ys += [b.extmin.y, b.extmax.y]
        except Exception:
            pass
    x0 = (max(xs) if xs else 0) + 1500
    y0 = max(ys) if ys else 0
    y_son, W = _tablo_ciz(msp, pozlar, x0, y0, f"{baslik} – DONATI METRAJI")
    # açılımlar: tablonun altında, 4 sütun
    msp.add_text("DONATI AÇILIMLARI (ölçüler cm, şematik)", height=90,
                 dxfattribs={"layer": "ACILIM", "style": "TR"}).set_placement((x0, y_son - 300))
    wc, hc = 1600, 600
    for i, p in enumerate(pozlar):
        r, cc = divmod(i, 4)
        _acilim_ciz(msp, p, x0 + cc * (wc + 100), y_son - 450 - r * (hc + 100), wc - 100, hc)
    notlar = ["NOTLAR:",
              f"Beton C{AYAR['fck']:.0f}, donatı B{AYAR['fyk']:.0f}C, net paspayı {AYAR['paspayi']:.0f} mm.",
              "Boyuna donatı boyları kat yüksekliği + bindirme (l0 = α1·lb) olarak verilmiştir.",
              "Kırımlı çubuklarda eğim ≤ 1/6 (birleşim bölgesi içinde).",
              "Etriye ve çiroz kancaları 135°, kanca boyu ≥ max(10Ø, 100 mm).",
              "Temel filizleri bu metraja dahil değildir."]
    n_sat = math.ceil(len(pozlar) / 4)
    msp.add_mtext("\\P".join(notlar), dxfattribs=dict(layer="METRAJ", style="TR", char_height=60)
                  ).set_location((x0, y_son - 600 - n_sat * (hc + 100)))
    doc.saveas(yol_dxf)


def metraj_csv(yol, pozlar):
    with open(yol, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Poz", "Çap", "Şekil", "Ölçüler (mm)", "Boy (cm)", "Adet", "Toplam boy (m)",
                    "Ağırlık (kg)", "Açıklama", "Katlar"])
        for p in pozlar:
            w.writerow([f"P{p['no']}", f"Ø{p['d']}", _sekil_adi(p["tip"]),
                        " ".join(f"{k}={v}" for k, v in p["olcu"].items()), f"{p['boy'] / 10:.0f}",
                        p["adet"], f"{p['toplam_m']:.1f}", f"{p['kg']:.1f}", p["aciklama"],
                        ", ".join(sorted(p["katlar"]))])
        w.writerow([])
        for d in sorted({p["d"] for p in pozlar}):
            tm = sum(p["toplam_m"] for p in pozlar if p["d"] == d)
            w.writerow(["", f"Ø{d}", "", "", "", "", f"{tm:.1f}", f"{tm * KG_M(d):.1f}", "çap toplamı"])
        w.writerow(["", "", "", "", "", "", "", f"{sum(p['kg'] for p in pozlar):.1f}", "GENEL TOPLAM"])


def _poz_str(g, anahtar):
    p = g.get("poz", {}).get(anahtar) or []
    return ("P" + "/P".join(str(x) for x in p) + " ") if p else ""


# =====================================================================================
# 10) ANA AKIŞ
# =====================================================================================
def _adim(p_ad, ad, fn, *args, **kw):
    """Bir çıktı adımını çalıştırır; hata olursa kaydedip devam eder."""
    t0 = time.time()
    print(f"    - {ad} ...", end="", flush=True)
    try:
        r = fn(*args, **kw)
        print(f" tamam ({time.time() - t0:.1f} s)", flush=True)
        return r
    except Exception:
        tb = traceback.format_exc()
        print(" HATA (ayrıntı hata_log.txt)", flush=True)
        with open(os.path.join(AYAR["cikti_klasoru"], "hata_log.txt"), "a", encoding="utf-8") as f:
            f.write(f"\n=== {p_ad} / {ad} ===\n{tb}")
        return None


def _pier_isle(p, m, kl, ozet):
    if not p.katlar or not any(k.kuvvetler for k in p.katlar):
        print(f"[{p.ad}] kuvvet verisi yok, atlandı.")
        return
    if p.bodrum:
        bs = bodrum_tasarla(p, m)
        pozlar = _adim(p.ad, "metraj", metraj_bodrum, bs, m) or []
        _adim(p.ad, "bodrum çizimi", bodrum_ciz, bs, os.path.join(kl, f"{p.ad}_bodrum.png"), os.path.join(kl, f"{p.ad}_bodrum.dxf"),
                   os.path.join(kl, f"{p.ad}_rapor.txt"))
        _adim(p.ad, "metraj DXF", metraj_dxf_ekle, os.path.join(kl, f"{p.ad}_bodrum.dxf"), pozlar, p.ad)
        _adim(p.ad, "metraj CSV", metraj_csv, os.path.join(kl, f"{p.ad}_metraj.csv"), pozlar)
        print(f"\n[{p.ad}] BODRUM PERDESİ")
        for r in bs["satirlar"]:
            print(f"  {r['kat']}: t={r['t']:.0f} | düşey +3 {_bd_txt(r, 'düşey', '+3')} "
                  f"−3 {_bd_txt(r, 'düşey', '−3')} | yatay +3 {_bd_txt(r, 'yatay', '+3')} "
                  f"−3 {_bd_txt(r, 'yatay', '−3')} | V {'OK' if r['V_ok'] else 'KONTROL!'}")
            ozet.append([p.ad, r["kat"], "Bodrum", r["kat"], f"{r['L']:.0f}", f"{r['t']:.0f}",
                         "", "", "", "", "", "",
                         f"+3:{_bd_txt(r, 'düşey', '+3')} −3:{_bd_txt(r, 'düşey', '−3')}",
                         f"+3:{_bd_txt(r, 'yatay', '+3')} −3:{_bd_txt(r, 'yatay', '−3')}",
                         "", "", "", "", "OK" if r["V_ok"] and not bs["uyarilar"] else "KONTROL!"])
        return ("bodrum", p, bs)
    if p.tip == "cok":
        ps = pier_tasarla_cok(p, m)
        pozlar = _adim(p.ad, "metraj", metraj_cok, ps, m) or []
        _adim(p.ad, "plan PNG", png_ciz_cok, ps, os.path.join(kl, f"{p.ad}_plan.png"))
        _adim(p.ad, "plan DXF", dxf_ciz_cok, ps, os.path.join(kl, f"{p.ad}_plan.dxf"))
        _adim(p.ad, "metraj DXF", metraj_dxf_ekle, os.path.join(kl, f"{p.ad}_plan.dxf"), pozlar, p.ad)
        _adim(p.ad, "metraj CSV", metraj_csv, os.path.join(kl, f"{p.ad}_metraj.csv"), pozlar)
        _adim(p.ad, "boy kesit", boy_kesit_ciz, ps, os.path.join(kl, f"{p.ad}_boykesit.png"),
                      os.path.join(kl, f"{p.ad}_boykesit.dxf"), m)
        _adim(p.ad, "PMM grafiği", pm_ciz_cok, ps, os.path.join(kl, f"{p.ad}_PMM.png"))
        for g in ps["gruplar"]:
            _adim(p.ad, "Section Designer DXF", dxf_section_designer_cok, g, os.path.join(kl, f"{p.ad}_{g['etiket']}_SD.dxf"))
        _adim(p.ad, "rapor", rapor_yaz_cok, ps, os.path.join(kl, f"{p.ad}_rapor.txt"))
        print(f"\n[{p.ad}] ÇOK KOLLU  Hw={ps['Hw'] / 1000:.2f} m  Hcr={ps['Hcr'] / 1000:.2f} m")
        for g in ps["gruplar"]:
            durum = "OK" if all(k[3] for k in g["kontroller"]) else "KONTROL!"
            print(f"  {g['etiket']} {bolge_adi(g, True):<10} {g['katlar'][0]}-"
                  f"{g['katlar'][-1]}: {len(g['kollar'])} kol | uç {g['nbar_uc']}Ø{g['de']} "
                  f"({g['ny']} sıra, ≤{g['s_uc']:.0f}) etr Ø{g['etr']['d']}/{g['etr']['s']:.0f} | "
                  f"Md/Mr={g['Md_Mr']:.2f} | {durum}")
            for q in g.get("gerekli") or []:
                if not q["yeterli"] or q["As_pmm"] > q["As_min"] * 1.001:
                    print(f"      B{q['bolge']} [{', '.join(q['kollar'])}]: min {q['As_min'] / 100:.1f} "
                          f"cm² -> PMM gerekli "
                          + (f"{q['As_pmm'] / 100:.1f} cm² (öneri {' / '.join(q['oneri'][:3])})"
                             if q["yeterli"] else "KESİT YETERSİZ"))
            ozet.append([p.ad, g["etiket"], bolge_adi(g, True),
                         f"{g['katlar'][0]}-{g['katlar'][-1]}", "çok kollu", "",
                         f"{ps['Hw'] / 1000:.2f}", f"{ps['Hcr'] / 1000:.2f}", "",
                         f"{g['nbar_uc']}Ø{g['de']} (toplam)", f"{g['As_uc']:.0f}",
                         f"Ø{g['etr']['d']}/{g['etr']['s']:.0f}",
                         f"Ø{g['dw']}/{g['sw_gercek']:.0f}", f"Ø{g['dh']}/{g['sh']:.0f}",
                         f"{g['Md_Mr']:.2f}", f"{g['Vd'] / 1e3:.0f}", f"{g['Ve'] / 1e3:.0f}",
                         f"{g['Vr'] / 1e3:.0f}", durum])
        return ("cok", p, ps)
    ps = pier_tasarla(p, m)
    pozlar = _adim(p.ad, "metraj", metraj_dik, ps, m) or []
    _adim(p.ad, "plan PNG", png_ciz, ps, os.path.join(kl, f"{p.ad}_plan.png"))
    _adim(p.ad, "plan DXF", dxf_ciz, ps, os.path.join(kl, f"{p.ad}_plan.dxf"))
    _adim(p.ad, "metraj DXF", metraj_dxf_ekle, os.path.join(kl, f"{p.ad}_plan.dxf"), pozlar, p.ad)
    _adim(p.ad, "metraj CSV", metraj_csv, os.path.join(kl, f"{p.ad}_metraj.csv"), pozlar)
    _adim(p.ad, "boy kesit", boy_kesit_ciz, ps, os.path.join(kl, f"{p.ad}_boykesit.png"),
                  os.path.join(kl, f"{p.ad}_boykesit.dxf"), m)
    _adim(p.ad, "P–M grafiği", pm_ciz, ps, os.path.join(kl, f"{p.ad}_PM.png"))
    for g in ps["gruplar"]:
        _adim(p.ad, "Section Designer DXF", dxf_section_designer, g, os.path.join(kl, f"{p.ad}_{g['etiket']}_SD.dxf"))
    _adim(p.ad, "rapor", rapor_yaz, ps, os.path.join(kl, f"{p.ad}_rapor.txt"))
    print(f"\n[{p.ad}] Hw={ps['Hw'] / 1000:.2f} m  Hcr={ps['Hcr'] / 1000:.2f} m")
    for g in ps["gruplar"]:
        durum = "OK" if all(k[3] for k in g["kontroller"]) else "KONTROL!"
        print(f"  {g['etiket']} {bolge_adi(g, True):<10} {g['katlar'][0]}-"
              f"{g['katlar'][-1]}: lw={g['lw']:.0f} bw={g['bw']:.0f} Lu={g['Lu']:.0f} | "
              f"uç {uc_txt(g)} etr Ø{g['etr']['d']}/{g['etr']['s']:.0f} | "
              f"gövde Ø{g['dw']}/{g['sw_gercek']:.0f} yatay Ø{g['dh']}/{g['sh']:.0f} | "
              f"Md/Mr={g['Md_Mr']:.2f} | {durum}")
        ozet.append([p.ad, g["etiket"], bolge_adi(g, True),
                     f"{g['katlar'][0]}-{g['katlar'][-1]}", f"{g['lw']:.0f}",
                     f"{g['bw']:.0f}", f"{ps['Hw'] / 1000:.2f}", f"{ps['Hcr'] / 1000:.2f}",
                     f"{g['Lu']:.0f}", uc_txt(g), f"{g['As_uc']:.0f}",
                     f"Ø{g['etr']['d']}/{g['etr']['s']:.0f}",
                     f"Ø{g['dw']}/{g['sw_gercek']:.0f}", f"Ø{g['dh']}/{g['sh']:.0f}",
                     f"{g['Md_Mr']:.2f}", f"{g['Vd'] / 1e3:.0f}", f"{g['Ve'] / 1e3:.0f}",
                     f"{g['Vr'] / 1e3:.0f}", durum])
    return ("dik", p, ps)


# =====================================================================================
# 12) KAT PLANI – bir kattaki bütün pier'lerin boyuna donatısı tek planda (global X-Y)
# =====================================================================================
#   Her pier, o katın ait olduğu tasarım grubunun kesitiyle ETABS global koordinatlarında
#   (ağırlık merkezi + yerel eksen açısı) çizilir: beton, uç bölge sınırları, etriye/çiroz,
#   uç bölge ve gövde boyuna donatıları, gövde çirozu, yatay donatı + firkete. Her pier'in
#   yanında etiketi, planın sağında kat donatı tablosu bulunur. Birim mm; model orijini korunur,
#   plan mimari/ETABS planının üstüne doğrudan oturur.

def _kp_T_dik(kv, lw, bw):
    X0, Y0 = kv.cg_alt if any(kv.cg_alt) else kv.cg_ust
    a = math.radians(kv.aci)
    ca, sa = math.cos(a), math.sin(a)

    def T(x, y=None):
        if y is None:
            x, y = x
        u, v = x - lw / 2, y - bw / 2
        return (X0 + u * ca - v * sa, Y0 + u * sa + v * ca)
    return T


def _kp_T_cok(kv):
    X0, Y0 = kv.orijin
    a = math.radians(kv.aci)
    ca, sa = math.cos(a), math.sin(a)

    def T(q, v=None):
        u, v = (q, v) if v is not None else (float(q[0]), float(q[1]))
        return (X0 + u * ca - v * sa, Y0 + u * sa + v * ca)
    return T


def _kp_pozsuz(metin):
    """Kat planında poz numaraları (pier'e özel) yazılmaz: 'P5/P6 Etriye' -> 'Etriye'."""
    import re
    return re.sub(r"\bP\d+(?:/P\d+)*\s", "", metin)


def _kp_daire(msp, P, d, katman):
    msp.add_circle(P, d / 2, dxfattribs={"layer": katman})
    h = msp.add_hatch(dxfattribs={"layer": katman})
    h.paths.add_edge_path().add_arc(P, d / 2, 0, 360)


def _kp_okunur_aci(aci):
    """Yazı açısı: -90..90 arasına indirgenir (ters yazı olmasın)."""
    a = (aci + 180.0) % 360.0 - 180.0
    if a > 90:
        a -= 180
    elif a < -90:
        a += 180
    return a


def _kp_ciz_dik(msp, g, kv):
    lw, bw, Lu = g["lw"], g["bw"], g["Lu"]
    c, det = AYAR["paspayi"], g["etr"]["d"]
    T = _kp_T_dik(kv, lw, bw)
    msp.add_lwpolyline([T(0, 0), T(lw, 0), T(lw, bw), T(0, bw)], close=True,
                       dxfattribs={"layer": "BETON"})
    for xb in (Lu, lw - Lu):
        msp.add_line(T(xb, 0), T(xb, bw), dxfattribs={"layer": "UC_BOLGE"})
    for cb in g["yatay"]["cubuklar"]:
        msp.add_lwpolyline([T(p_) for p_ in cb["pts"]], dxfattribs={"layer": "YATAY_GOVDE"})
    for fk in g["yatay"]["firketeler"]:
        msp.add_lwpolyline([T(p_) for p_ in fk["pts"]], dxfattribs={"layer": "FIRKETE"})
    hooplar = [(ha, hb) for ha, hb in g["etr"]["hooplar"]]
    hooplar += [(lw - hb, lw - ha) for ha, hb in g["etr"]["hooplar"]]
    for z in g.get("kiris_bolgeleri", []):
        hooplar += list(z["hooplar"])
    for i_h, (ha, hb) in enumerate(hooplar):
        off = det * (i_h % 2)
        a, b = ha + det / 2, hb - det / 2
        msp.add_lwpolyline([T(a, c + det / 2 + off), T(b, c + det / 2 + off),
                            T(b, bw - c - det / 2 - off), T(a, bw - c - det / 2 - off)],
                           close=True, dxfattribs={"layer": "ETRIYE", "const_width": det})
    xs_c = list(g["etr"]["ciroz_x"]) + [lw - x for x in g["etr"]["ciroz_x"]]
    for z in g.get("kiris_bolgeleri", []):
        xs_c += list(z["ciroz_x"])
    for x in xs_c:
        msp.add_lwpolyline([T(x, c + det / 2), T(x, bw - c - det / 2)],
                           dxfattribs={"layer": "CIROZ", "const_width": det})
    for y, xa, xb in g["etr"]["ciroz_y"]:
        for a_, b_ in ((c + det / 2, xb), (lw - xb, lw - c - det / 2)):
            msp.add_lwpolyline([T(a_, y), T(b_, y)], dxfattribs={"layer": "CIROZ", "const_width": det})
    for z in g.get("kiris_bolgeleri", []):
        for y in z["ys"][1:-1]:
            msp.add_lwpolyline([T(z["x0"] + det / 2, y), T(z["x1"] - det / 2, y)],
                               dxfattribs={"layer": "CIROZ", "const_width": det})
    gc = g["gciroz"]
    for katman, xs_ in (("GOVDE_CIROZ_A", gc["xA"]), ("GOVDE_CIROZ_B", gc["xB"])):
        for x in xs_:
            msp.add_lwpolyline([T(x, c + g["dh"] / 2), T(x, bw - c - g["dh"] / 2)],
                               dxfattribs={"layer": katman, "const_width": gc["d"]})
    for x, y, d in g["uc"]:
        _kp_daire(msp, T(x, y), d, "DONATI_UC")
    for x, y, d in g.get("kiris_bars", []):
        _kp_daire(msp, T(x, y), d, "DONATI_UC")
    for x, y, d in g["govde"]:
        _kp_daire(msp, T(x, y), d, "DONATI_GOVDE")
    # etiket: perdenin bir yüzünün dışında, perde doğrultusunda
    t = _metin_donati(g)
    yazi = [f"{g['_pier']} [{g['etiket']}]  {lw:.0f}x{bw:.0f}  {bolge_adi(g, True)}"] + \
        [_kp_pozsuz(x) for x in (t["uc"] + f", Lu={Lu:.0f}", t["etr"], t["gd"], t["gy"])]
    ac = _kp_okunur_aci(kv.aci)
    ters = abs(((kv.aci + 180) % 360 - 180) - ac) > 1   # yazı 180° çevrildiyse diğer yüz
    y_e = -250 if ters else bw + 250
    msp.add_mtext("\\P".join(yazi).replace("Ø", "%%c"),
                  dxfattribs=dict(layer="YAZI", style="TR", char_height=90, rotation=ac)
                  ).set_location(T(lw / 2, y_e), attachment_point=8)
    return [T(0, 0), T(lw, 0), T(lw, bw), T(0, bw)]


def _kp_ciz_cok(msp, g, kv):
    c, det = AYAR["paspayi"], g["etr"]["d"]
    T = _kp_T_cok(kv)
    kollar = g["kollar"]
    kose = []
    for k in kollar:
        q = [T(p_) for p_ in k.koseler()]
        kose += q
        msp.add_lwpolyline(q, close=True, dxfattribs={"layer": "BETON"})
    for p, x in zip(g["parcalar"], g["detay"]):
        k = kollar[p["kol"]]
        for s_ in (p["s0"], p["s1"]):
            if 0 < s_ < k.L:
                msp.add_line(T(k.nokta(s_, -k.t / 2)), T(k.nokta(s_, k.t / 2)),
                             dxfattribs={"layer": "UC_BOLGE"})
        a0, a1 = x["hoop"][0] + det / 2, x["hoop"][1] - det / 2
        w0 = k.t / 2 - c - det / 2
        for i_h, (ha, hb) in enumerate(x["hooplar"]):
            off = det * (i_h % 2)
            msp.add_lwpolyline([T(k.nokta(ha + det / 2, -w0 + off)), T(k.nokta(hb - det / 2, -w0 + off)),
                                T(k.nokta(hb - det / 2, w0 - off)), T(k.nokta(ha + det / 2, w0 - off))],
                               close=True, dxfattribs={"layer": "ETRIYE", "const_width": det})
        for s_ in x["ciroz_s"]:
            msp.add_lwpolyline([T(k.nokta(s_, -w0)), T(k.nokta(s_, w0))],
                               dxfattribs={"layer": "CIROZ", "const_width": det})
        for w_ in x["ws"][1:-1]:
            msp.add_lwpolyline([T(k.nokta(a0, w_)), T(k.nokta(a1, w_))],
                               dxfattribs={"layer": "CIROZ", "const_width": det})
    for cb in g["yatay"]["cubuklar"]:
        msp.add_lwpolyline([T(p_) for p_ in cb["pts"]], dxfattribs={"layer": "YATAY_GOVDE"})
    for fk in g["yatay"]["firketeler"]:
        msp.add_lwpolyline([T(p_) for p_ in fk["pts"]], dxfattribs={"layer": "FIRKETE"})
    for k, s_, sv in g["gciroz"]["ciftler"]:
        w0 = k.t / 2 - c - g["dh"] / 2
        msp.add_lwpolyline([T(k.nokta(s_, -w0)), T(k.nokta(s_, w0))],
                           dxfattribs={"layer": "GOVDE_CIROZ_" + sv, "const_width": g["gciroz"]["d"]})
    for u, v, d, *_ in g["uc"]:
        _kp_daire(msp, T(u, v), d, "DONATI_UC")
    for u, v, d in g["govde"]:
        _kp_daire(msp, T(u, v), d, "DONATI_GOVDE")
    t = _cok_metin(g)
    X = [q[0] for q in kose]
    Y = [q[1] for q in kose]
    yazi = [f"{g['_pier']} [{g['etiket']}]  {len(kollar)} kollu  {bolge_adi(g, True)}"] + \
        [_kp_pozsuz(x) for x in (t["uc"], t["etr"], t["gd"], t["gy"])]
    msp.add_mtext("\\P".join(yazi).replace("Ø", "%%c"),
                  dxfattribs=dict(layer="YAZI", style="TR", char_height=90)
                  ).set_location(((min(X) + max(X)) / 2, max(Y) + 250), attachment_point=8)
    return kose


def _kp_ciz_bodrum(msp, r, kv, ad):
    """Bodrum perdesi: beton + iki yüzde düşey donatı (+3 / −3) ve etiket. ETABS alan
    izleri varsa (U/H/L bodrum perdeleri) her kol kendi doğrultusunda çizilir."""
    izler = (kv.bodrum or {}).get("izler")
    if izler and len(izler) > 1:
        kose = []
        uclar_ = [q for a_, b_, _ in izler for q in (a_, b_)]
        for i, (a, b, t) in enumerate(izler):
            L_ = math.hypot(b[0] - a[0], b[1] - a[1])
            if L_ < 1.0:
                continue
            # köşe/T birleşiminde kol, komşunun yarı kalınlığı kadar uzatılır (köşe kapanır)
            e_ = ((b[0] - a[0]) / L_, (b[1] - a[1]) / L_)
            a0, b0 = list(a), list(b)
            for uc_, P_, sg in ((0, a, -1), (1, b, 1)):
                es = [izler[j][2] for j in range(len(izler)) if j != i
                      for q in izler[j][:2] if math.hypot(q[0] - P_[0], q[1] - P_[1]) < 5.0]
                if not es:
                    continue
                ilk = not any(math.hypot(q[0] - P_[0], q[1] - P_[1]) < 5.0
                              for j in range(i) for q in izler[j][:2])
                # birleşimi ilk sahiplenen kol dışa uzar, diğerleri onun yüzünde biter
                ek = max(es) / 2 if ilk else -max(es) / 2
                if uc_ == 0:
                    a0 = [a[0] - e_[0] * ek, a[1] - e_[1] * ek]
                else:
                    b0 = [b[0] + e_[0] * ek, b[1] + e_[1] * ek]
            a, b = tuple(a0), tuple(b0)
            L_ = math.hypot(b[0] - a[0], b[1] - a[1])
            kv_ = KatVerisi(kv.kat, kv.z_alt, kv.z_ust, L_, t,
                            cg_alt=((a[0] + b[0]) / 2, (a[1] + b[1]) / 2),
                            cg_ust=((a[0] + b[0]) / 2, (a[1] + b[1]) / 2),
                            aci=math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])))
            r_ = dict(r, L=L_, t=t)
            kose += _kp_ciz_bodrum_kol(msp, r_, kv_, ad, etiket=(i == 0))
        return kose
    return _kp_ciz_bodrum_kol(msp, r, kv, ad)


def _kp_ciz_bodrum_kol(msp, r, kv, ad, etiket=True):
    c = AYAR["paspayi"]
    L, t = r["L"], r["t"]
    T = _kp_T_dik(kv, L, t)
    msp.add_lwpolyline([T(0, 0), T(L, 0), T(L, t), T(0, t)], close=True,
                       dxfattribs={"layer": "BETON"})
    dy = max(r[("yatay", "+3")]["d"], r[("yatay", "−3")]["d"])
    for yuz, y_ in (("−3", None), ("+3", None)):
        q = r[("düşey", yuz)]
        y = (c + dy + q["d"] / 2) if yuz == "−3" else (t - c - dy - q["d"] / 2)
        n = max(1, int((L - 2 * c) // q["s"]))
        x0 = (L - n * q["s"]) / 2
        for i in range(n + 1):
            _kp_daire(msp, T(x0 + i * q["s"], y), q["d"], "DONATI_GOVDE")
        msp.add_line(T(c, (c + dy / 2) if yuz == "−3" else (t - c - dy / 2)),
                     T(L - c, (c + dy / 2) if yuz == "−3" else (t - c - dy / 2)),
                     dxfattribs={"layer": "YATAY_GOVDE"})
    yazi = [f"{ad} BODRUM PERDESİ  {L:.0f}x{t:.0f}",
            f"Düşey: +3 {_bd_txt(r, 'düşey', '+3')}, −3 {_bd_txt(r, 'düşey', '−3')}",
            f"Yatay: +3 {_bd_txt(r, 'yatay', '+3')}, −3 {_bd_txt(r, 'yatay', '−3')}"]
    ac = _kp_okunur_aci(kv.aci)
    if etiket:
        msp.add_mtext("\\P".join(yazi).replace("Ø", "%%c"),
                      dxfattribs=dict(layer="YAZI", style="TR", char_height=90, rotation=ac)
                      ).set_location(T(L / 2, t + 250), attachment_point=8)
    return [T(0, 0), T(L, 0), T(L, t), T(0, t)]


def _kp_kat_listesi(sonuclar):
    """Tüm pier'lerin katları (kotuna göre sıralı): [(kat, z_alt)]."""
    kz = {}
    for tip, p, r in sonuclar:
        for kv in p.katlar:
            kz.setdefault(kv.kat, kv.z_alt)
    return sorted(kz.items(), key=lambda t: t[1])


def kat_plani_ciz(sonuclar, kl):
    """Her kat için tek DXF + PNG: o kattaki bütün pier'lerin boyuna donatısı."""
    if not sonuclar:
        return []
    uretilen = []
    for kat, z in _kp_kat_listesi(sonuclar):
        doc = _dxf_yeni()
        msp = doc.modelspace()
        tablo = [["PIER", "GRUP", "KESİT", "UÇ BÖLGE (her uç)", "Lu", "ETRİYE", "GÖVDE DÜŞEY",
                  "GÖVDE YATAY", "Md/Mr"]]
        png = []
        tum = []
        for tip, p, r in sonuclar:
            kv = next((k for k in p.katlar if k.kat == kat), None)
            if kv is None:
                continue
            if tip == "bodrum":
                sat = next((s for s in r["satirlar"] if s["kat"] == kat), None)
                if sat is None:
                    continue
                kose = _kp_ciz_bodrum(msp, sat, kv, p.ad)
                kose_png = [kose[i:i + 4] for i in range(0, len(kose), 4)]
                tablo.append([p.ad, "bodrum", f"{sat['L']:.0f}x{sat['t']:.0f}", "–", "–", "–",
                              f"+3 {_bd_txt(sat, 'düşey', '+3')} / −3 {_bd_txt(sat, 'düşey', '−3')}",
                              f"+3 {_bd_txt(sat, 'yatay', '+3')} / −3 {_bd_txt(sat, 'yatay', '−3')}",
                              ""])
                png.append((p.ad, kose_png, []))
                tum += kose
                continue
            g = next((g for g in r["gruplar"] if kat in g["katlar"]), None)
            if g is None:
                continue
            g["_pier"] = p.ad
            if tip == "cok":
                kose = _kp_ciz_cok(msp, g, kv)
                T = _kp_T_cok(kv)
                bars = [T(u, v) for u, v, *_ in g["uc"]]
                kont = [[T(q) for q in k.koseler()] for k in g["kollar"]]
                tablo.append([p.ad, g["etiket"], f"{len(g['kollar'])} kollu",
                              f"{g['nbar_uc']}%%c{g['de']} (toplam)", "–",
                              f"%%c{g['etr']['d']}/{g['etr']['s']:.0f}",
                              f"%%c{g['dw']}/{g['sw_gercek']:.0f}", f"%%c{g['dh']}/{g['sh']:.0f}",
                              f"{g['Md_Mr']:.2f}"])
            else:
                kose = _kp_ciz_dik(msp, g, kv)
                T = _kp_T_dik(kv, g["lw"], g["bw"])
                bars = [T(x, y) for x, y, d in g["uc"]]
                kont = [kose]
                tablo.append([p.ad, g["etiket"], f"{g['lw']:.0f}x{g['bw']:.0f}",
                              uc_txt(g).replace("Ø", "%%c"), f"{g['Lu']:.0f}",
                              f"%%c{g['etr']['d']}/{g['etr']['s']:.0f}",
                              f"%%c{g['dw']}/{g['sw_gercek']:.0f}", f"%%c{g['dh']}/{g['sh']:.0f}",
                              f"{g['Md_Mr']:.2f}"])
            png.append((f"{p.ad} [{g['etiket']}]\n" + (f"uç {g['nbar_uc']}Ø{g['de']}" if tip == "cok"
                                                       else f"uç {uc_txt(g)}"), kont, bars))
            tum += kose
        if len(tablo) == 1:
            continue
        X = [q[0] for q in tum]
        Y = [q[1] for q in tum]
        x0, y0 = max(X) + 2500, max(Y) + 1500
        msp.add_text(f"KAT {kat} (z = {z / 1000:.2f} m) – PERDE BOYUNA DONATI PLANI", height=250,
                     dxfattribs={"layer": "YAZI", "style": "TR"}).set_placement((min(X), max(Y) + 2500))
        msp.add_text("Koordinatlar ETABS global X-Y (mm). Her pier bu katın tasarım grubuyla çizildi.",
                     height=120, dxfattribs={"layer": "YAZI", "style": "TR"}
                     ).set_placement((min(X), max(Y) + 2100))
        _dxf_tablo(msp, x0, y0, tablo, katman="KOL_TABLO", h=110.0)
        ad = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(kat))
        yol = os.path.join(kl, f"KAT_{ad}_plan.dxf")
        doc.saveas(yol)
        uretilen.append(yol)
        _kp_png(png, kat, z, os.path.join(kl, f"KAT_{ad}_plan.png"))
    return uretilen


def _kp_png(png, kat, z, yol):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mp
    tum = np.array([p_ for _, ks, _ in png for q in ks for p_ in q])
    gx, gy = max(np.ptp(tum[:, 0]), 1.0), max(np.ptp(tum[:, 1]), 1.0)
    W = 18.0
    H = float(np.clip(W * gy / gx + 2.5, 5.0, 18.0))
    fig, ax = plt.subplots(figsize=(W, H))
    for etiket, konturlar, bars in png:
        for q in konturlar:
            ax.add_patch(mp.Polygon(q, closed=True, fc="#d5d8dc", ec="black", lw=0.8))
        if bars:
            b = np.array(bars)
            ax.plot(b[:, 0], b[:, 1], ".", color="#c0392b", ms=2)
        allq = np.array([p_ for q in konturlar for p_ in q])
        ax.annotate(etiket, (allq[:, 0].mean(), allq[:, 1].max()), xytext=(0, 6),
                    textcoords="offset points", ha="center", fontsize=8, weight="bold")
    ax.set_aspect("equal", adjustable="datalim")
    ax.autoscale_view()
    ax.margins(0.06, 0.25)
    ax.set_title(f"KAT {kat} (z = {z / 1000:.2f} m) – perde boyuna donatı planı (ayrıntı DXF'te)",
                 pad=18)
    ax.set_xlabel("X [mm]")
    ax.set_ylabel("Y [mm]")
    ax.grid(True, lw=0.3, alpha=0.5)
    fig.tight_layout()
    fig.savefig(yol, dpi=130)
    plt.close(fig)


# =====================================================================================
# 13) SADE TASARIM – başlık boyu + donatı seçim algoritması (ödül–ceza) + yazılı kat DXF'i
# =====================================================================================
#   Akış (her tasarım grubu için):
#     1) Başlık (uç bölge) boyu Lu: TBDY 7.6.2.3 minimumu (kademeli geçiş dahil).
#     2) Gerekli boyuna donatı As,req: lif (P–M / P–M2–M3) analizinden – donatı başlık çevresine
#        eşit dağıtılmış kabulüyle – ve TBDY minimumu (ρmin·bw·ℓw, 4Ø14); büyük olan.
#     3) Donatı seçim algoritması: tüm çap–adet adayları -> kesin eleme -> ödül–ceza puanı ->
#        en yüksek puanlı aday; ilk 5 alternatif, elenenler ve gerekçeler rapora.
#        Başlıkta tek çap; çubuklar başlık çevresine (kolon gibi) dağıtılır; aralıklar NET
#        (yüzeyden yüzeye): uc_net_min ≤ net ≤ uc_net_max kesin sınır.
#     4) Uygun aday yoksa (ρ > %3 ya da sığmıyor) Lu 50 mm artırılır.
#     5) Seçilen düzen lif analiziyle doğrulanır; enine donatı: etriye + GEREKLİ kadar çiroz
#        (TBDY 7.6.5.2: a ≤ 25Ø, kritikte Ash) -> kalınlık / boy doğrultusu kol sayıları.
#   Çıktı: her kat için tek DXF (bütün pier'ler, yazılı), Section Designer DXF'leri, tek rapor.

SADE_CAPLAR = [16, 18, 20, 22, 25, 28, 32]
P_PHI = {16: 50, 18: 70, 20: 90, 22: 100, 25: 90, 28: 70, 32: 35}
AGIRLIK = dict(A=0.30, s=0.15, cong=0.15, n=0.10, phi=0.10, conf=0.10, bond=0.05, M=0.05)


def _tablo(x, satirlar, varsayilan=0.0):
    """satirlar: [(alt, üst, puan)] – alt ≤ x < üst."""
    for a, b, p in satirlar:
        if a <= x < b:
            return p
    return varsayilan


def puan_A(r):
    return _tablo(r, [(1.00, 1.03, 100), (1.03, 1.07, 90), (1.07, 1.10, 75), (1.10, 1.15, 50),
                      (1.15, 1.20, 30), (1.20, 1.30, 10)], 0)


def puan_s(net):
    """Net (yüzeyden yüzeye) aralık – kesin sınırlar [uc_net_min, uc_net_max] içinde."""
    return _tablo(net, [(60, 100.0001, 100), (50, 60, 80), (100.0001, 110, 80), (0, 50, 60),
                        (110, 1e9, 60)], 60)


def puan_n(n, n_ideal):
    q = n / max(n_ideal, 1e-9)
    return _tablo(q, [(0.85, 1.15, 100), (0.70, 0.85, 75), (1.15, 1.30, 75), (0.55, 0.70, 50),
                      (1.30, 1.50, 50)], 25)


def puan_cong(ci):
    return _tablo(ci, [(0, 0.55, 100), (0.55, 0.65, 90), (0.65, 0.75, 75), (0.75, 0.85, 50),
                       (0.85, 0.90, 25)], 5)


def puan_conf(st):
    return _tablo(st, [(150, 1e9, 100), (125, 150, 85), (100, 125, 65), (75, 100, 40)], 15)


def puan_bond(rb):
    return _tablo(rb, [(0, 0.5, 100), (0.5, 0.6, 90), (0.6, 0.7, 75), (0.7, 0.8, 60),
                       (0.8, 0.9, 40), (0.9, 1.0000001, 20)], 0)


def puan_M(rm):
    return _tablo(rm, [(1.0, 1.10, 100), (1.10, 1.20, 90), (1.20, 1.30, 70), (1.30, 1.50, 40)], 10)


def net_min_sinir(d):
    A = AYAR
    return max(A["uc_net_min"], 25.0, d, 4 / 3 * A["agrega_dmax"])


# ---------------------------------------------------------------- başlık çevresine yerleşim
def cevre_yerlesim(bw, Lu, d, n, det, dh):
    """Dikdörtgen perdenin SOL başlığında n adet Ød çubuğun çevreye (kolon gibi) yerleşimi.
    4 köşe + uzun yüzlerde kx'er, uç yüzde ky_u, iç (gövdeye bakan) yüzde ky_i ara çubuk;
    aralıklar olabildiğince eşit (tek fazla çubuk uç yüze). None: sığmıyor."""
    A = AYAR
    c = A["paspayi"]
    ic = c + max(det, dh)
    y0, y1 = ic + d / 2, bw - ic - d / 2
    x0 = c + max(det, dh * (2 if A["yatay_uc_detay"] == "firkete" else 1)) + d / 2
    x1 = Lu - det - d / 2
    Lx, Ly = x1 - x0, y1 - y0
    if n < 4 or Lx <= d or Ly <= d:
        return None
    r = n - 4
    en = None
    for kx in range(0, r // 2 + 1):
        kalan = r - 2 * kx
        ky_u = (kalan + 1) // 2
        ky_i = kalan // 2
        ccs = [Lx / (kx + 1), Ly / (ky_u + 1), Ly / (ky_i + 1)]
        key = (max(ccs) - min(ccs), -ky_u)
        if en is None or key < en[0]:
            en = (key, kx, ky_u, ky_i, ccs)
    _, kx, ky_u, ky_i, ccs = en
    xs = [x0 + i * Lx / (kx + 1) for i in range(kx + 2)]
    yu = [y0 + j * Ly / (ky_u + 1) for j in range(ky_u + 2)]
    yi = [y0 + j * Ly / (ky_i + 1) for j in range(ky_i + 2)]
    bars = [(x, y0, d) for x in xs] + [(x, y1, d) for x in xs]
    bars += [(x0, y, d) for y in yu[1:-1]] + [(x1, y, d) for y in yi[1:-1]]
    nets = [cc - d for cc in ccs]
    P = 2 * (Lx + Ly)
    return dict(bars=bars, n=n, d=d, kx=kx, ky_u=ky_u, ky_i=ky_i, net_min=min(nets),
                net_max=max(nets), net_ort=P / n - d, P=P, Lx=Lx, Ly=Ly, x0=x0, x1=x1,
                y0=y0, y1=y1, kolonlar=xs, uc_sira=yu, ic_sira=yi)


def _esdeger(bars_alan):
    """[(x, y, A)] -> [(x, y, Ø_eşdeğer)] (lif modeli alanı çaptan hesaplar)."""
    return [(x, y, math.sqrt(4 * a_ / math.pi)) for x, y, a_ in bars_alan if a_ > 0]


def _izgara(a, b, orta, sw, g_min=75.0):
    """[a, b] aralığında, 'orta' noktasına göre SABİT ızgaralı (aralık sw) konumlar. Izgara
    başlık boyundan bağımsız olduğu için üst katın gövde çubukları alt katınkilerle aynı yere
    gelir (düz devam). Uçta sw'den büyük boşluk kalırsa araya bir çubuk eklenir."""
    if b - a < 2 * g_min:
        return []
    k0 = math.ceil((a + g_min - orta) / sw - 1e-9)
    k1 = math.floor((b - g_min - orta) / sw + 1e-9)
    xs = [orta + k * sw for k in range(k0, k1 + 1)]
    if not xs:
        return [(a + b) / 2] if b - a > sw + 1e-6 else []
    if xs[0] - a > sw + 1.0:
        xs.insert(0, (a + xs[0]) / 2)
    if b - xs[-1] > sw + 1.0:
        xs.append((xs[-1] + b) / 2)
    return xs


def govde_bars_dik(lw, bw, x1, dw, sw, dh):
    c = AYAR["paspayi"]
    y_g = c + dh + dw / 2
    xs = _izgara(x1, lw - x1, lw / 2, sw)
    return [(x, y, dw) for x in xs for y in (y_g, bw - y_g)], float(sw)


def ayna(bars, lw):
    return list(bars) + [(lw - x, y, d) for x, y, d in bars]


# ---------------------------------------------------------------- sistem türü / kritik bölge /
#                                                   tasarım momenti / malzeme / ek kontroller
def sistem_ayarla(pier_ad):
    """Aktif pier'in sistem türü (TBDY 7.6.6.3, 7.6.7.1, 7.10) – kesme fonksiyonları bunu okur."""
    A = AYAR
    lst = A.get("bag_kirisli_pierler") or []
    bag = (pier_ad in lst) if lst else str(A.get("perde_sistemi", "bosluksuz")).lower().startswith("bag")
    sin = str(A.get("suneklik", "yuksek")).lower().startswith("s")
    A["_aktif"] = dict(bag=bag, sinirli=sin, vmax=0.65 if bag else 0.85,
                       katsayi=float(A.get("kesme_katsayi_bag", 1.4) if bag else A["kesme_katsayi"]))
    return A["_aktif"]


def _aktif():
    return AYAR.get("_aktif") or dict(bag=False, sinirli=False, vmax=0.85,
                                      katsayi=float(AYAR["kesme_katsayi"]))


_M_ONBELLEK = {}


def malzeme_kat(k, m):
    """Katın beton sınıfı ETABS'ten okunmuşsa (ve malzeme_etabs açıksa) o fck ile malzeme."""
    fck = getattr(k, "fck", None)
    if not AYAR.get("malzeme_etabs", True) or not fck:
        return m
    anahtar = (round(fck, 2), AYAR["fyk"], AYAR["fywk"], AYAR["gamma_c"], AYAR["gamma_s"])
    if anahtar not in _M_ONBELLEK:
        _M_ONBELLEK[anahtar] = malzeme(dict(AYAR, fck=float(fck)))
    return _M_ONBELLEK[anahtar]


def pm_satirlari(k):
    """P–M tasarımında kullanılacak satırlar: tasarım momenti diyagramı uygulanmışsa onlar."""
    return k.tasarim if getattr(k, "tasarim", None) else k.kuvvetler


def kritik_bolgeler(p, lw_fn):
    """TBDY 7.6.2.2: kritik perde yüksekliği bölgeleri. Başlangıç: temel üstü (ya da rijit
    bodrumlu binada zemin kat döşemesi – AYAR['kritik_baslangic_kat']); perdenin plandaki
    uzunluğunun %20'den fazla küçüldüğü her seviyede kritik yükseklik YENİDEN başlar.
    Katlara kritik / kademe atar; bölge listesini döndürür."""
    A = AYAR
    K = p.katlar
    i0 = 0
    ad = A.get("kritik_baslangic_kat")
    if ad:
        i0 = next((i for i, k in enumerate(K) if str(k.kat) == str(ad)), 0)
    segs = []

    def ekle(i, neden):
        z = K[i].z_alt
        Hw, lw = K[-1].z_ust - z, lw_fn(K[i])
        segs.append(dict(i=i, z_bas=z, Hw=Hw, lw=lw, Hcr=min(max(lw, Hw / 6.0), 2 * lw),
                         neden=neden, kat=K[i].kat))
    ekle(i0, "rijit bodrum – zemin kat döşemesi" if i0 > 0 else "perde tabanı")
    ref = lw_fn(K[i0])
    for i in range(i0 + 1, len(K)):
        lw = lw_fn(K[i])
        if lw < (1.0 - A.get("kesit_kuculme_orani", 0.20)) * ref - 1e-6:
            ekle(i, f"plan uzunluğu {ref:.0f} → {lw:.0f} mm (> %{A.get('kesit_kuculme_orani', 0.2) * 100:.0f} küçülme)")
            ref = lw
        elif lw > ref:
            ref = lw
    n = A["kademe_kat"]
    for k in K:
        k.kritik, k.kademe = False, 0.0
    for sg in segs:
        j = 0
        for i in range(sg["i"], len(K)):
            k = K[i]
            if (k.z_alt - sg["z_bas"]) < sg["Hcr"] - 1e-6:
                k.kritik, k.kademe = True, 1.0
            else:
                j += 1
                if not k.kritik:
                    k.kademe = max(k.kademe, max(0.0, (n + 1 - j) / (n + 1)) if n > 0 else 0.0)
    if i0 > 0:                       # bodrum katları: kritik bölge aşağıya doğru uzatılır
        tum = str(A.get("bodrumda_kritik", "tum")).lower().startswith("t")
        j = 0
        for i in range(i0 - 1, -1, -1):
            if tum or i == i0 - 1:
                K[i].kritik, K[i].kademe = True, 1.0
            else:
                j += 1
                K[i].kademe = max(0.0, (n + 1 - j) / (n + 1)) if n > 0 else 0.0
    return segs


def kritik_notlari(segs, p):
    L = []
    for sg in segs:
        L.append(f"Kritik bölge ({sg['neden']}, {sg['kat']} tabanından): Hw = {sg['Hw'] / 1000:.2f} m, "
                 f"lw = {sg['lw'] / 1000:.2f} m, Hcr = min(max(lw, Hw/6), 2lw) = {sg['Hcr'] / 1000:.2f} m, "
                 f"Hw/lw = {sg['Hw'] / sg['lw']:.2f}")
    if segs and segs[0]["i"] > 0:
        L.append("Rijit bodrum: Hw ve Hcr zemin kat döşemesinden ölçüldü; kritik bölge "
                 + ("bütün bodrum katlarına" if str(AYAR.get("bodrumda_kritik", "tum")).lower().startswith("t")
                    else "ilk bodrum katına") + " uzatıldı (TBDY 7.6.2.2).")
    ad = AYAR.get("kritik_baslangic_kat")
    if ad and segs and segs[0]["i"] == 0 and str(p.katlar[0].kat) != str(ad):
        L.append(f"UYARI: kritik_baslangic_kat = '{ad}' bu pier'in katları arasında yok – kritik yükseklik "
                 f"perde tabanından başlatıldı.")
    L.append("Kritik katlar: " + (", ".join(str(k.kat) for k in p.katlar if k.kritik) or "–"))
    return L


def tasarim_momentleri(p, segs, cok=False):
    """TBDY 7.6.6.1: Hw/lw > 2 ise kritik yükseklik boyunca taban momenti sabit; üstünde taban ve
    tepe momentlerini birleştiren doğruya paralel doğrusal diyagram. Her kombinasyon (kimlik)
    kendi taban/tepe momentiyle işlenir; eksenel kuvvet satırın kendi değeridir. Analiz momenti
    diyagramdan büyükse analiz momenti kalır. k.tasarim listesi doldurulur."""
    A = AYAR
    K = p.katlar
    for k in K:
        k.kuvvetler = satir_kimlikle(k.kuvvetler)
        k.tasarim = None
    notlar = []
    if not A.get("tasarim_momenti_diyagrami", True):
        return ["Tasarım momenti diyagramı KAPALI (AYAR): analiz momentleri doğrudan kullanıldı."]
    if _aktif()["sinirli"]:
        return ["Süneklik düzeyi sınırlı perde (TBDY 7.10): tasarım momenti diyagramı uygulanmadı."]
    Mt = {f[8]: (f[3], f[5]) for f in K[-1].kuvvetler if str(f[1]).lower().startswith("top")}
    kurallar = []
    for si, sg in enumerate(segs):
        if sg["Hw"] / sg["lw"] <= 2.0:
            notlar.append(f"{sg['kat']}: Hw/lw = {sg['Hw'] / sg['lw']:.2f} ≤ 2 – tasarım momentleri analiz "
                          f"momentlerine eşit alındı (TBDY 7.6.6.1).")
            continue
        Mb = {f[8]: (f[3], f[5]) for f in K[sg["i"]].kuvvetler if str(f[1]).lower().startswith("bot")}
        if Mb:
            kurallar.append(dict(sg, Mb=Mb, ilk=(si == 0)))
            notlar.append(f"{sg['kat']}: Hw/lw = {sg['Hw'] / sg['lw']:.2f} > 2 – kritik yükseklik "
                          f"({sg['Hcr'] / 1000:.2f} m) boyunca taban momenti sabit, üstünde taban–tepe "
                          f"doğrusuna paralel diyagram uygulandı (TBDY 7.6.6.1).")
    if not kurallar:
        return notlar
    buyuyen = 0
    for k in K:
        out = []
        for f in k.kuvvetler:
            z = k.z_alt if str(f[1]).lower().startswith("bot") else k.z_ust
            M3d, M2d = f[3], f[5]
            for q in kurallar:
                if f[8] not in q["Mb"]:
                    continue
                zr = z - q["z_bas"]
                if zr < -1e-6 and not (q["ilk"] and k.kritik):
                    continue                    # bu bölgenin altında (bodrumda yalnız kritik katlar)
                b3, b2 = q["Mb"][f[8]]
                t3, t2 = Mt.get(f[8], (0.0, 0.0))
                if zr <= q["Hcr"] + 1e-6:
                    d3, d2 = b3, b2
                else:
                    o = (zr - q["Hcr"]) / q["Hw"]
                    d3, d2 = b3 + (t3 - b3) * o, b2 + (t2 - b2) * o
                if abs(d3) > abs(M3d) + 1e-9:
                    M3d = math.copysign(abs(d3), M3d if abs(M3d) > 1e-9 else d3)
                if cok and abs(d2) > abs(M2d) + 1e-9:
                    M2d = math.copysign(abs(d2), M2d if abs(M2d) > 1e-9 else d2)
            buyuyen += (abs(M3d) > abs(f[3]) + 1e-6) or (abs(M2d) > abs(f[5]) + 1e-6)
            out.append(f[:3] + (M3d, f[4], M2d, f[6], f[7], f[8], f[3], f[5]))
        k.tasarim = out
    notlar.append(f"Tasarım momenti diyagramı {buyuyen} kuvvet satırında analiz momentini büyüttü.")
    return notlar


def kesme_surtunmesi(Ac, bars, Ve, Nmin, m):
    """TBDY 7.6.7.2 / TS 500 kesme sürtünmesi: temel bağlantısı ve yatay inşaat derzlerinde
    düşey donatı. Döndürür: (Vr, açıklama)."""
    A = AYAR
    mu = float(A.get("derz_mu", 1.0))
    As = sum(alan(b[2]) for b in bars)
    fyd = min(A["fyk"], 500.0) / A["gamma_s"]
    Vr = mu * As * fyd
    if A.get("derz_puruzlu", True):
        Vr += m["fctd"] * Ac
    if A.get("derz_eksenel", False) and Nmin > 0:
        Vr += mu * Nmin
    Vust = min(0.2 * m["fck"], 3.3 + 0.08 * m["fck"]) * Ac
    return min(Vr, Vust), (f"μ = {mu:g}, As = {As / 100:.0f} cm²"
                           + (", pürüzlü yüzey (fctd·Ac dahil)" if A.get("derz_puruzlu", True) else "")
                           + (", üst sınır belirleyici" if Vr > Vust else ""))


def dik_ek_kontroller(g, katlar, kk, m):
    """Dikdörtgen perdede zayıf eksen etkileri: P–M2–M3 (seçilen donatıyla), düzlem dışı kesme
    V3 ve burulma T. Sonuçlar kontrollere / uyarılara eklenir."""
    A = AYAR
    lw, bw = g["lw"], g["bw"]
    R = [f for k in katlar for f in pm_satirlari(k)]
    M2max = max([abs(f[5]) for f in R] or [0.0]) * 1e6
    M3max = max([abs(f[3]) for f in R] or [0.0]) * 1e6
    g["M2_max"] = M2max
    if M2max > max(0.02 * bw / lw * M3max, 1e3):
        try:
            kol = Kol((-lw / 2, 0.0), (1.0, 0.0), lw, bw, "K1")
            kol.merkez_L, kol.ic_dugum = lw, []
            uc = [(x - lw / 2, y - bw / 2, d) for x, y, d in g["uc"]]
            gov = [(x - lw / 2, y - bw / 2, d) for x, y, d in g["govde"]]
            lk2 = LifKesit2B([kol], uc, gov, m)
            tal = talep_azalt_pmm([(f[2] * 1e3, f[5] * 1e6, f[3] * 1e6) for f in R])
            o2 = float(np.max(lk2.talep_orani(tal)))
            g["Md_Mr_pmm"] = o2
            g["kontroller"].append(("Md/Mr P–M2–M3 (zayıf eksen momenti M2 dahil)", o2, 1.0, o2 <= 1.0))
            if o2 > 1.0:
                g["uyarilar"].append(f"Zayıf eksen momenti (M2,max = {M2max / 1e6:.0f} kNm) ile birlikte "
                                     f"Md/Mr = {o2:.2f} > 1: başlık donatısı yalnız M3'e göre seçildi – "
                                     f"donatı artırılmalı ya da M2'nin kaynağı (döşeme/kiriş) incelenmeli.")
        except Exception as e:      # noqa
            g["uyarilar"].append(f"P–M2–M3 kontrolü yapılamadı ({e}).")
    else:
        g["notlar_ek"] = [f"M2,max = {M2max / 1e6:.1f} kNm ihmal edilebilir düzeyde (≤ %2·(bw/lw)·M3,max): "
                          f"P–M2–M3 kontrolü yapılmadı."]
    K = [f for k in katlar for f in k.kuvvetler if f[0] in kk] or [f for k in katlar for f in k.kuvvetler]
    V3 = max([abs(f[6]) for f in K] or [0.0]) * 1e3
    d_ = bw - A["paspayi"] - g["dh"] - g["dw"] / 2
    Vcr3 = 0.65 * m["fctd"] * lw * d_
    g["kontroller"].append(("V3 ≤ 0.65·fctd·lw·d [kN] (düzlem dışı kesme, enine donatısız)", V3 / 1e3,
                            Vcr3 / 1e3, V3 <= Vcr3))
    Td = max([abs(f[7]) for f in K if len(f) > 7] or [0.0]) * 1e6
    S = bw * bw * lw / 3.0
    Tlim = 0.65 * m["fctd"] * S
    g["Td"], g["T_sinir"] = Td, Tlim
    if Td > Tlim:
        g["uyarilar"].append(f"Burulma: Td = {Td / 1e6:.0f} kNm > 0.65·fctd·S = {Tlim / 1e6:.0f} kNm (TS 500 "
                             f"8.2 ihmal sınırı) – burulma ihmal edilemez; yatay gövde donatısı kapalı etriye "
                             f"gibi düzenlenip TS 500 8.2'ye göre ayrıca kontrol edilmelidir.")


def cok_ek_kontroller(g, katlar, kk, m):
    kollar = g["kollar"]
    K = [f for k in katlar for f in k.kuvvetler if f[0] in kk] or [f for k in katlar for f in k.kuvvetler]
    Td = max([abs(f[7]) for f in K if len(f) > 7] or [0.0]) * 1e6
    S = sum(k.t * k.t * k.L / 3.0 for k in kollar)
    Tlim = 0.65 * m["fctd"] * S
    g["Td"], g["T_sinir"] = Td, Tlim
    if Td > Tlim:
        g["uyarilar"].append(f"Burulma: Td = {Td / 1e6:.0f} kNm > 0.65·fctd·ΣS = {Tlim / 1e6:.0f} kNm (TS 500 "
                             f"8.2 ihmal sınırı, açık kesit) – burulma ayrıca değerlendirilmelidir.")


def izleme_kur(g, katlar, pm_k, kk, n_ilk=5):
    """Belirleyici talepler özgün ETABS satırına kadar izlenir: kat, kombinasyon, konum, N, ham
    moment, tasarım momenti, oran. g['izleme'] = dict(pm=[...], kesme=[...], N=...)."""
    R, src = [], []
    for k in katlar:
        for f in pm_satirlari(k):
            if f[0] not in pm_k:
                continue
            ham3 = f[9] if len(f) > 9 else f[3]
            ham2 = f[10] if len(f) > 10 else f[5]
            src.append(dict(kat=k.kat, komb=f[8] if len(f) > 8 else f[0], konum=f[1], N=f[2],
                            M3=f[3], M3_ham=ham3, M2=f[5], M2_ham=ham2))
            R.append((f[2] * 1e3, f[3] * 1e6) if g["tip"] == "dik" else (f[2] * 1e3, f[5] * 1e6, f[3] * 1e6))
    pm = []
    if R:
        try:
            adim = max(1, len(R) // 6000 + (1 if len(R) % 6000 and len(R) > 6000 else 0))
            idx = list(range(0, len(R), adim)) if adim > 1 else list(range(len(R)))
            o = np.asarray(g["lif"].talep_orani([R[i] for i in idx]), float)
            for j in np.argsort(-o)[:n_ilk]:
                pm.append(dict(src[idx[int(j)]], oran=float(o[int(j)])))
        except Exception:
            pass
    kes = []
    for k in katlar:
        K = [f for f in k.kuvvetler if f[0] in kk] or list(k.kuvvetler)
        if not K:
            continue
        f2 = max(K, key=lambda f: abs(f[4]))
        kes.append(dict(kat=k.kat, komb=f2[8] if len(f2) > 8 else f2[0], konum=f2[1], V2=f2[4],
                        V3=max(abs(f[6]) for f in K)))
    fN = max(((k, f) for k in katlar for f in pm_satirlari(k) if f[0] in pm_k), key=lambda t: t[1][2],
             default=None)
    g["izleme"] = dict(pm=pm, kesme=kes,
                       N=None if fN is None else dict(kat=fN[0].kat, komb=fN[1][8] if len(fN[1]) > 8 else fN[1][0],
                                                      konum=fN[1][1], N=fN[1][2]))


def izleme_csv(sonuclar, yol):
    T = [["Pier", "Grup", "Kontrol", "Sıra", "Kat", "Kombinasyon / satır", "Konum", "N [kN]",
          "M3 ham [kNm]", "M3 tasarım [kNm]", "M2 ham [kNm]", "M2 tasarım [kNm]", "V [kN]",
          "Oran (talep/kapasite)"]]
    for tip, p, r in sonuclar:
        if tip == "bodrum":
            continue
        for g in r["gruplar"]:
            iz = g.get("izleme") or {}
            for i, q in enumerate(iz.get("pm", []), 1):
                T.append([p.ad, g["etiket"], "Eğilme (Md/Mr)", i, q["kat"], q["komb"], q["konum"],
                          f"{q['N']:.1f}", f"{q['M3_ham']:.1f}", f"{q['M3']:.1f}", f"{q['M2_ham']:.1f}",
                          f"{q['M2']:.1f}", "", f"{q['oran']:.3f}"])
            for q in iz.get("kesme", []):
                T.append([p.ad, g["etiket"], "Kesme (Vd)", "", q["kat"], q["komb"], q["konum"], "", "", "",
                          "", "", f"{abs(q['V2']):.1f}" + (f" / V3 {q['V3']:.1f}" if q.get("V3") else ""), ""])
            if iz.get("N"):
                q = iz["N"]
                T.append([p.ad, g["etiket"], "Eksenel (Nd,max)", "", q["kat"], q["komb"], q["konum"],
                          f"{q['N']:.1f}", "", "", "", "", "", ""])
    with open(yol, "w", newline="", encoding="utf-8-sig") as f:
        csv.writer(f, delimiter=";").writerows(T)


def cevre_yerlesim_tam(bw, lw, d, n, det, dh):
    """İki başlık birleştiğinde (gövde kalmıyor): n adet Ød bütün kesitin çevresine dizilir
    (kolon gibi). cevre_yerlesim'in 'iç yüzü' burada perdenin öbür ucudur."""
    A = AYAR
    x0 = A["paspayi"] + max(det, dh * (2 if A["yatay_uc_detay"] == "firkete" else 1)) + d / 2
    return cevre_yerlesim(bw, lw - x0 + det + d / 2, d, n, det, dh)


def As_gerek_dik(lw, bw, Lu, gov, talepler, m, det, dh, tek=False):
    """Başlık çevresine eşit dağıtılmış donatıyla, P–M taleplerini karşılayan en küçük As
    (her başlık, simetrik; tek=True: bütün kesit tek başlık, toplam As).
    Döndürür: (As_pmm mm², yeterli mi)."""
    if tek:
        ref = None
        for n_ in (24, 16, 12, 8, 4):
            ref = cevre_yerlesim_tam(bw, lw, 16, n_, det, dh)
            if ref:
                break
        Lu = lw
    else:
        ref = cevre_yerlesim(bw, Lu, 20, 12, det, dh) or cevre_yerlesim(bw, Lu, 16, 4, det, dh)
    if ref is None:
        return float("inf"), False
    pts = [(x, y) for x, y, _ in ref["bars"]]

    def oran(As):
        a_ = As / len(pts)
        uc = _esdeger([(x, y, a_) for x, y in pts])
        if not tek:
            uc = ayna(uc, lw)
        lk = LifKesit(lw, bw, uc, list(gov), m)
        r = lk.talep_orani(talepler)
        return float(np.max(r)) if len(r) else 0.0

    if oran(0.0) <= 1.0:
        return 0.0, True
    hi = max(1000.0, 0.005 * Lu * bw)
    while oran(hi) > 1.0:
        hi *= 2
        if hi > 0.08 * Lu * bw:
            return hi, False
    lo = 0.0
    for _ in range(14):
        md = (lo + hi) / 2
        if oran(md) <= 1.0:
            hi = md
        else:
            lo = md
    return hi, True


# ---------------------------------------------------------------- donatı seçim algoritması
def donati_sec(As_req, n_min, ga, m, kritik, bw, l_mevcut, pm_fonk=None, rho_alan=None,
               sinir_temel=None):
    """Gönderilen algoritma. ga(d, n) -> geometri dict (net_min, net_max, net_ort, P) ya da None.
    pm_fonk(d, n) -> Mr/Md oranı (R_M) ya da None. rho_alan: ρ için başlık alanı (mm²).
    Döndürür: dict(sirali=[aday...], elenen=[(etiket, neden)], secim=aday|None)."""
    A = AYAR
    adaylar, elenen = [], []
    n_max = A.get("uc_n_max", 80)
    for d in A.get("uc_caplari_sade", SADE_CAPLAR):
        a1 = alan(d)
        n_bas = max(4, n_min, math.ceil(As_req / a1 - 1e-9))
        gecen = sik = 0
        for n in range(n_bas, n_max + 1):
            As_p = n * a1
            et = f"{n}Ø{d}"
            if gecen >= 10:
                break                                     # bu çapta yeterince aday toplandı
            g_ = ga(d, n)
            if g_ is None:
                if not any(e_[0].endswith(f"Ø{d}") and "sığmıyor" in e_[1] for e_ in elenen):
                    elenen.append((et, "kesite sığmıyor / köşe sayısından az çubuk"))
                continue
            if g_["net_min"] < net_min_sinir(d) - 0.5:
                elenen.append((et, f"net aralık {g_['net_min']:.0f} < {net_min_sinir(d):.0f} mm"))
                sik += 1
                if sik >= 3:
                    break                                 # daha çok çubuk daha sık
                continue
            sik = 0
            if g_["net_max"] > A["uc_net_max"] + 0.5:
                elenen.append((et, f"net aralık {g_['net_max']:.0f} > {A['uc_net_max']:.0f} mm "
                                   f"(geometrik minimum sağlanmıyor)"))
                continue
            if rho_alan and As_p / rho_alan > A["rho_uc_max"] + 1e-9:
                elenen.append((et, f"ρ = {As_p / rho_alan:.4f} > {A['rho_uc_max']}"))
                break
            lb, l0 = kenetlenme(d, m)
            if l0 > l_mevcut:
                elenen.append((et, f"bindirme l0 = {l0:.0f} > mevcut {l_mevcut:.0f} mm"))
                break
            if sinir_temel and lb > sinir_temel:
                elenen.append((et, f"temelde kenetlenme lb = {lb:.0f} > {sinir_temel:.0f} mm"))
                break
            st = min(150.0, 6 * d, bw / 3.0) if kritik else min(bw, 200.0)
            if st < 50:
                elenen.append((et, f"sargı aralığı {st:.0f} < 50 mm (uygulanamaz)"))
                continue
            rA = As_p / max(As_req, 1e-9)       # (geometrik minimum sonra düzeltilir)
            n_ideal = g_["P"] / (d + 80.0)
            CI = (n * d + (n - 1) * net_min_sinir(d)) / g_["P"]
            Rb = l0 / l_mevcut
            P_ = dict(A=puan_A(rA), s=puan_s(g_["net_ort"]), cong=puan_cong(CI),
                      n=puan_n(n, n_ideal), phi=P_PHI.get(d, 50), conf=puan_conf(st),
                      bond=puan_bond(Rb))
            adaylar.append(dict(et=et, d=d, n=n, As=As_p, rA=rA, CI=CI, lb=lb, l0=l0, Rb=Rb,
                                st=st, n_ideal=n_ideal, geo=g_, P=P_, RM=None))
            gecen += 1
    # Geometrik minimum (net ≤ uc_net_max) As,req'yi aşıyorsa yakınlık, sığan en küçük
    # donatıya göre ölçülür (aksi hâlde bütün adaylar "fazla donatı" sayılır ve çap puanı
    # gereksiz yere iri çapı seçtirir).
    As_ref = As_req
    if adaylar:
        As_geo = min(a_["As"] for a_ in adaylar)
        if As_geo > As_req * 1.0:
            As_ref = As_geo
        for a_ in adaylar:
            a_["rA"] = a_["As"] / max(As_req, 1e-9)
            a_["rA_ref"] = a_["As"] / max(As_ref, 1e-9)
            a_["P"]["A"] = puan_A(a_["rA_ref"])
    # P_M (yalnız A-puanı > 0 olanlarda – pahalı hesap)
    if pm_fonk is not None:
        for a_ in adaylar:
            if a_["P"]["A"] > 0 or len(adaylar) < 8:
                a_["RM"] = pm_fonk(a_["d"], a_["n"])
        yetmez = [a_ for a_ in adaylar if a_["RM"] is not None and a_["RM"] < 0.999]
        for a_ in yetmez:
            elenen.append((a_["et"], f"lif analizi: Mr/Md = {a_['RM']:.3f} < 1 (kapasite yetmiyor)"))
        adaylar = [a_ for a_ in adaylar if a_ not in yetmez]
    for a_ in adaylar:
        w = dict(AGIRLIK)
        if a_["RM"] is None:
            w.pop("M")
        else:
            a_["P"]["M"] = puan_M(a_["RM"])
        top = sum(w.values())
        a_["puan"] = sum(w[k] * a_["P"][k] for k in w) / top
    adaylar.sort(key=lambda a_: (-a_["puan"], a_["As"]))
    for i, a_ in enumerate(adaylar):
        a_["deger"] = _degerlendir(a_, i)
    return dict(sirali=adaylar, elenen=elenen, secim=adaylar[0] if adaylar else None,
                As_req=As_req, As_ref=As_ref)


def _degerlendir(a_, i):
    if i == 0:
        return "Optimum"
    notlar = []
    if a_["rA"] > 1.10:
        notlar.append("fazla As")
    if a_["geo"]["net_ort"] > 100:
        notlar.append("seyrek dağılım")
    if a_["geo"]["net_ort"] < 55:
        notlar.append("sık dağılım")
    if a_["n"] < 0.85 * a_["n_ideal"]:
        notlar.append("az çubuk")
    if a_["n"] > 1.15 * a_["n_ideal"]:
        notlar.append("çok çubuk")
    if a_["CI"] > 0.75:
        notlar.append("sıkışık")
    if not notlar:
        notlar.append("çok iyi alternatif" if i == 1 else "iyi alternatif")
    return " / ".join(notlar)


def secim_aciklama(sec, ikinci=None):
    """Birinci (ve ikinci) çözüm için kısa mühendislik açıklaması."""
    if sec is None:
        return ["Uygun aday bulunamadı."]
    g_ = sec["geo"]
    geo_not = (" (fazlalık, net aralık ≤ " + f"{AYAR['uc_net_max']:.0f}" + " mm geometrik minimumundan "
               "kaynaklanır)") if sec["rA"] > 1.15 else ""
    L = [f"{sec['et']} optimum çözüm olarak seçildi (puan {sec['puan']:.1f}). Sağlanan donatı "
         f"alanı gerekli değerin %{(sec['rA'] - 1) * 100:.1f} üzerindedir{geo_not}. Çubuklar başlık çevresine "
         f"net {g_['net_min']:.0f}–{g_['net_max']:.0f} mm aralıkla dağılır (ideal çubuk sayısı "
         f"≈{sec['n_ideal']:.0f}); sıkışıklık indeksi CI = {sec['CI']:.2f}, bindirme l0 = "
         f"{sec['l0']:.0f} mm (l0/l_mevcut = {sec['Rb']:.2f}), gerekli sargı aralığı ≤ "
         f"{sec['st']:.0f} mm"
         + (f", Mr/Md = {sec['RM']:.2f}" if sec["RM"] else "") + "."]
    if ikinci is not None:
        fark = []
        for k, ad in (("A", "As yakınlığı"), ("s", "dağılım"), ("cong", "sıkışıklık"),
                      ("n", "çubuk adedi"), ("phi", "çap"), ("conf", "sargı"),
                      ("bond", "kenetlenme"), ("M", "moment kapasitesi")):
            if k in sec["P"] and k in ikinci["P"] and ikinci["P"][k] < sec["P"][k] - 1e-6:
                fark.append(ad)
        L.append(f"{ikinci['et']} yakın alternatiftir (puan {ikinci['puan']:.1f}); "
                 + (f"{', '.join(fark)} bakımından geride kaldı." if fark else
                    "puanı az farkla düşük kaldı."))
    return L


def secim_tablosu(sec_sonuc, n_ilk=5, n_elenen=12):
    L = ["    Sıra  Donatı   As[cm²] As/Areq Fazla  Net aralık[mm]   CI   l0/lmev  Mr/Md  "
         "PA PS PC Pn Pø Psr Pk PM  Puan  Değerlendirme"]
    for i, a_ in enumerate(sec_sonuc["sirali"][:n_ilk], 1):
        P_ = a_["P"]
        L.append(f"    {i:>4}  {a_['et']:<8} {a_['As'] / 100:7.2f} {a_['rA']:7.3f} "
                 f"%{(a_['rA'] - 1) * 100:4.1f}  {a_['geo']['net_min']:4.0f}/{a_['geo']['net_ort']:4.0f}"
                 f"/{a_['geo']['net_max']:4.0f}   {a_['CI']:.2f}  {a_['Rb']:.2f}    "
                 f"{(format(a_['RM'], '.2f') if a_['RM'] else '  – ')}  {P_.get('A', 0):3.0f}{P_.get('s', 0):3.0f}{P_.get('cong', 0):3.0f}"
                 f"{P_.get('n', 0):3.0f}{P_.get('phi', 0):3.0f}{P_.get('conf', 0):4.0f}{P_.get('bond', 0):3.0f}"
                 f"{P_.get('M', 0):3.0f}  "
                 f"{a_['puan']:5.1f}  {a_['deger']}")
    if sec_sonuc.get("As_ref", 0) > sec_sonuc.get("As_req", 0) * 1.001:
        L.append(f"    Not: net aralık ≤ {AYAR['uc_net_max']:.0f} mm geometrik minimumu As,req'yi aşıyor; "
                 f"As yakınlığı sığan en küçük donatıya ({sec_sonuc['As_ref'] / 100:.1f} cm²) göre puanlandı.")
    if sec_sonuc["elenen"]:
        import re
        grup = {}
        for e, n in sec_sonuc["elenen"]:
            anahtar = re.sub(r"[\d.]+ ?(mm|>|<)", r"… \1", n)
            anahtar = re.sub(r"= [\d.]+", "= …", anahtar)
            grup.setdefault(anahtar, []).append((e, n))
        L.append(f"    ELENENLER ({len(sec_sonuc['elenen'])} aday):")
        for k_, v in grup.items():
            L.append(f"      {len(v):>3} × {k_} – ör. " + "; ".join(f"{e}: {n}" for e, n in v[:3]))
    return L


# ---------------------------------------------------------------- enine donatı (kol sayısı)
def sargi_dik(lay, Lu, bw, kritik, m):
    """Seçilen çevre yerleşimi için etriye + gerekli çiroz. En hafif geçerli çap seçilir.
    Döndürür: dict(d, s, n_y, n_x, hooplar, ciroz_x, n_y_ash, n_y_a, n_x_a, ...)."""
    A = AYAR
    c = A["paspayi"]
    d_l = lay["d"]
    d0 = A["etriye_capi_kritik"] if kritik else A["etriye_capi_ust"]
    adaylar = [d for d in A["etriye_caplari"] if d >= max(8, d0)]
    s_max = min(150.0, 6 * d_l, bw / 3.0) if kritik else min(bw, 200.0)
    kol = lay["kolonlar"]
    ara_sira = len(lay["uc_sira"]) - 2          # uç yüzdeki ara çubuklar (boy doğr. çiroz yeri)
    gecerli = []
    for d in adaylar:
        hp, kenar = etriye_bolumle(kol, c, Lu, d_l, d)
        sabit = sorted({kol[i] for i in kenar})
        a_kal0 = bw - 2 * c - d
        s = math.floor(s_max / A["s_yuvarlama"]) * A["s_yuvarlama"]
        while s >= 50:
            n_ash_y = n_ash_x = 0
            if kritik:
                n_ash_y = math.ceil((2 / 3) * 0.075 * s * (Lu - c) * m["fck"] / A["fywk"] / alan(d)
                                    - 1e-9)
                n_ash_x = math.ceil((2 / 3) * 0.075 * s * (bw - 2 * c) * m["fck"] / A["fywk"]
                                    / alan(d) - 1e-9)
            cx = ciroz_konumlari(kol, sabit, 25 * d, n_ash_y - 2 * len(hp) + len(sabit))
            tum = sorted(set(round(x, 1) for x in sabit + cx))
            a_boy = max(np.diff(tum)) if len(tum) > 1 else 0.0
            n_y = 2 * len(hp) + len(cx)
            n_x_a = 2 + max(0, math.ceil(a_kal0 / (25 * d) - 1e-9) - 1)
            n_x = max(n_x_a, n_ash_x, 2)
            ok = a_boy <= 25 * d + 1e-6 and n_y * alan(d) >= (n_ash_y - 1e-9) * alan(d) and \
                n_x - 2 <= ara_sira
            if ok:
                n_y_a = 2 * len(hp) + len(ciroz_konumlari(kol, sabit, 25 * d, 0))
                gecerli.append(dict(d=d, s=s, n_y=n_y, n_x=n_x, hooplar=hp, ciroz_x=cx,
                                    n_y_ash=n_ash_y, n_y_a=n_y_a, n_x_ash=n_ash_x, n_x_a=n_x_a,
                                    a_boy=float(a_boy), a_kal=float(a_kal0 / (n_x - 1)),
                                    s_max=s_max))
                break
            s -= A["s_yuvarlama"]
    if not gecerli:
        d = adaylar[-1]
        return dict(d=d, s=50.0, n_y=len(kol) + 2, n_x=len(lay["uc_sira"]), hooplar=[(c, Lu)],
                    ciroz_x=list(kol), n_y_ash=0, n_y_a=0, n_x_ash=0, n_x_a=0, a_boy=0, a_kal=0,
                    s_max=s_max, uyari="enine donatı koşulu sağlanamadı – elle kontrol edin")
    en = min(gecerli, key=lambda r: ((r["n_y"] * bw + r["n_x"] * Lu) * alan(r["d"]) / r["s"],
                                     r["d"]))
    en["alternatifler"] = [(r["d"], r["s"], r["n_y"], r["n_x"]) for r in gecerli]
    return en


def sargi_metni(e):
    t = (f"Ø{e['d']}/{e['s']:.0f}; kalınlık doğr. {e['n_y']} kol (Ash: {e['n_y_ash']}, a ≤ 25Ø: "
         f"{e['n_y_a']}), boy doğr. {e['n_x']} kol (Ash: {e['n_x_ash']}, a ≤ 25Ø: {e['n_x_a']})")
    if e.get("alternatifler") and len(e["alternatifler"]) > 1:
        t += "; seçenekler: " + ", ".join(f"Ø{d}/{s:.0f} → {ny}+{nx} kol"
                                          for d, s, ny, nx in e["alternatifler"])
    return t


# ---------------------------------------------------------------- dikdörtgen perde grubu
def grup_sade_dik(ad, lw, bw, kritik, kademe, talepler, kat_kesme, Ve_carpan, h_kat, m,
                  kirisler=None, kis=None, temel=False):
    A = AYAR
    kis = dict(kis or {})
    kontroller, uyarilar = [], []
    bw_min = max(200.0, h_kat / 20) if A["ozel_kosul_7613"] else max(250.0, h_kat / 16)
    kontroller.append(("lw/bw ≥ 6 (TBDY 7.6.1.1)", lw / bw, 6.0, lw / bw >= 6.0))
    kontroller.append((f"bw ≥ {bw_min:.0f} mm (TBDY 7.6.1.2/3)", bw, bw_min, bw >= bw_min - 1e-6))
    Nmax = max(n for n, _ in talepler)
    kontroller.append(("Nd,max ≤ 0.35·Ac·fck [kN]", Nmax / 1e3, 0.35 * bw * lw * m["fck"] / 1e3,
                       Nmax <= 0.35 * bw * lw * m["fck"]))
    dw, sw, _ = govde_sec(bw, 0.0025, A["s_govde_max"])
    ks = kesme_tevzi(lw, bw, kat_kesme, Ve_carpan, m)
    kr = max(ks, key=lambda r: (r["rho"], -r["s"]))
    dh, sh = kr["d"], kr["s"]
    kontroller.append((f"Ve ≤ {_aktif()['vmax']:g}·Ach·√fck [kN] (TBDY 7.6.7.1)", kr["Ve"] / 1e3, kr["Vmax"] / 1e3,
                       kr["Ve"] <= kr["Vmax"]))
    kontroller.append(("Ve ≤ Vr [kN] (en elverişsiz kat)", kr["Ve"] / 1e3, kr["Vr"] / 1e3,
                       kr["Ve"] <= kr["Vr"] * 1.0001))
    det = A["etriye_capi_kritik"] if kritik else A["etriye_capi_ust"]
    rho_min = rho_uc_min(kritik, kademe)
    As_min = max(rho_min * bw * lw, 4 * alan(14), kis.get("As_min", 0.0))
    Lu0 = max(uc_bolge_uzunlugu(lw, bw, kritik, kademe), kis.get("Lu_min", 0.0))
    l_mev = h_kat - A["birlesim_yuksekligi"]
    temel_h = A.get("temel_yuksekligi") if temel else None
    sinir_t = (temel_h - A["paspayi"] - 50) if temel_h else None
    Lu, secim, sonuc, As_pmm, yeterli = Lu0, None, None, None, True
    bosluk = max(bw, 300.0)          # iki başlık arasında kalması gereken en az gövde
    tek = False
    while Lu <= 0.40 * lw + 1e-6 and lw - 2 * Lu >= bosluk - 1e-6:
        gov, s_g = govde_bars_dik(lw, bw, Lu - det, dw, sw, dh)
        As_pmm, yeterli = As_gerek_dik(lw, bw, Lu, gov, talepler, m, det, dh)
        As_req = max(As_pmm, As_min)

        def ga(d, n, Lu=Lu):
            return cevre_yerlesim(bw, Lu, d, n, det, dh)

        def pm(d, n, Lu=Lu, gov=gov):
            lay = cevre_yerlesim(bw, Lu, d, n, det, dh)
            lk = LifKesit(lw, bw, ayna(lay["bars"], lw), list(gov), m)
            r = lk.talep_orani(talepler)
            mx = float(np.max(r)) if len(r) else 0.0
            return 1.0 / mx if mx > 0 else 99.0

        sonuc = donati_sec(As_req, 4, ga, m, kritik, bw, l_mev, pm, rho_alan=Lu * bw,
                           sinir_temel=sinir_t)
        if sonuc["secim"] is not None and yeterli:
            secim = sonuc["secim"]
            break
        Lu += A["Lu_yuvarlama"]
    if secim is None:
        # BAŞLIKLAR İÇ İÇE / BİTİŞİK: iki başlık arasında gövde kalmıyor (ya da başlık 0.4·lw'ye
        # kadar uzatıldığı hâlde yetmedi) -> bütün kesit TEK başlık, donatı çevreye dizilir
        As_t, yet_t = As_gerek_dik(lw, bw, lw, [], talepler, m, det, dh, tek=True)
        As_req_t = max(As_t, 2 * As_min)

        def ga_t(d, n):
            return cevre_yerlesim_tam(bw, lw, d, n, det, dh)

        def pm_t(d, n):
            lay = cevre_yerlesim_tam(bw, lw, d, n, det, dh)
            r = LifKesit(lw, bw, list(lay["bars"]), [], m).talep_orani(talepler)
            mx = float(np.max(r)) if len(r) else 0.0
            return 1.0 / mx if mx > 0 else 99.0

        son_t = donati_sec(As_req_t, 4, ga_t, m, kritik, bw, l_mev, pm_t, rho_alan=lw * bw,
                           sinir_temel=sinir_t)
        ic_ice = lw - 2 * Lu0 < bosluk - 1e-6
        if son_t["secim"] is not None and yet_t:
            tek, secim, sonuc, As_pmm, yeterli = True, son_t["secim"], son_t, As_t, True
            uyarilar.append(
                (f"Bilgi: iki başlık (2×{Lu0:.0f} mm) iç içe/bitişik – arada {max(lw - 2 * Lu0, 0):.0f} mm "
                 f"< {bosluk:.0f} mm gövde kalıyor; " if ic_ice else
                 "Bilgi: başlıklar 0.4·lw'ye kadar uzatıldığı hâlde yetmedi; ")
                + "bütün kesit TEK başlık olarak donatıldı (donatı çevreye dizildi, gövde donatısı yok).")
        elif ic_ice:
            tek, sonuc, As_pmm, yeterli = True, son_t, As_t, yet_t
            uyarilar.append("Başlıklar iç içe ve tüm kesit tek başlık olarak da yetmiyor (ρ ≤ %3 / "
                            "kapasite) – kesit büyütülmeli; sığan en çok donatı yazıldı.")
        else:
            uyarilar.append("Uygun başlık donatısı bulunamadı (ρ ≤ %3, Lu ≤ 0.4lw) – kesit "
                            "büyütülmeli; en çok donatılı sığan düzen yazıldı.")
        if secim is None:
            Lu = Lu0

            def _y(d, n):
                return cevre_yerlesim_tam(bw, lw, d, n, det, dh) if tek else \
                    cevre_yerlesim(bw, Lu, d, n, det, dh)
            rho_a = lw * bw if tek else Lu * bw
            en = None
            for d in A.get("uc_caplari_sade", SADE_CAPLAR):
                for n in range(4, A.get("uc_n_max", 80) + 1):
                    lay = _y(d, n)
                    if lay is None:
                        continue
                    if lay["net_min"] < net_min_sinir(d) - 0.5 or n * alan(d) / rho_a > A["rho_uc_max"]:
                        continue
                    if en is None or n * alan(d) > en["As"]:
                        en = dict(et=f"{n}Ø{d}", d=d, n=n, As=n * alan(d), rA=0, geo=lay, puan=0,
                                  RM=None, P={})
            if en is None:
                raise RuntimeError(f"{lw:.0f}×{bw:.0f} kesitte başlığa donatı dizilemedi.")
            secim = en
    elif Lu > Lu0:
        uyarilar.append(f"Bilgi: başlık boyu {Lu0:.0f} mm'den {Lu:.0f} mm'ye uzatıldı "
                        f"(ρ ≤ %3 / yerleşim / kapasite).")

    def yer(det_):
        return cevre_yerlesim_tam(bw, lw, secim["d"], secim["n"], det_, dh) if tek else \
            cevre_yerlesim(bw, Lu, secim["d"], secim["n"], det_, dh)

    lay = yer(det)
    if tek:
        Lu = lw / 2
        gov, s_g = [], float(sw)
    else:
        gov, s_g = govde_bars_dik(lw, bw, lay["x1"], dw, sw, dh)
    uc = list(lay["bars"]) if tek else ayna(lay["bars"], lw)
    lk = LifKesit(lw, bw, uc, gov, m)
    oran = float(np.max(lk.talep_orani(talepler)))
    L_sargi = lw - A["paspayi"] if tek else Lu
    etr = sargi_dik(lay, L_sargi, bw, kritik, m)
    if etr["d"] != det:                           # etriye çapı değiştiyse yerleşimi yenile
        lay2 = yer(etr["d"])
        if lay2 and lay2["net_min"] >= net_min_sinir(secim["d"]) - 0.5:
            lay = lay2
            uc = list(lay["bars"]) if tek else ayna(lay["bars"], lw)
            lk = LifKesit(lw, bw, uc, gov, m)
            oran = float(np.max(lk.talep_orani(talepler)))
    As_min_k = 2 * As_min if tek else As_min
    A_bas = lw * bw if tek else Lu * bw
    kontroller.append((f"Uç bölge As ≥ max({rho_min:.4f}·bw·lw, 4Ø14) [mm²]"
                       + (" (iki uç toplamı)" if tek else ""), secim["As"],
                       As_min_k, secim["As"] >= As_min_k - 1e-6))
    kontroller.append(("Uç bölge ρ ≤ 0.03", secim["As"] / A_bas, A["rho_uc_max"],
                       secim["As"] / A_bas <= A["rho_uc_max"] + 1e-9))
    kontroller.append(("Uç bölge net aralık [mm] (en küçük)", lay["net_min"],
                       net_min_sinir(secim["d"]), lay["net_min"] >= net_min_sinir(secim["d"]) - 0.5))
    kontroller.append(("Uç bölge net aralık [mm] (en büyük)", lay["net_max"], A["uc_net_max"],
                       lay["net_max"] <= A["uc_net_max"] + 0.5))
    kontroller.append(("Md/Mr lif analizi (seçilen donatı)", oran, 1.0, oran <= 1.0))
    Ve_mx = max(r_["Ve"] for r_ in ks)
    Vsf, sf_not = kesme_surtunmesi(lw * bw, list(uc) + list(gov), Ve_mx, min(n for n, _ in talepler), m)
    kontroller.append((f"Kesme sürtünmesi Ve ≤ Vr,sf [kN] (TBDY 7.6.7.2 – derz; {sf_not})", Ve_mx / 1e3,
                       Vsf / 1e3, Ve_mx <= Vsf * 1.0001))
    if etr.get("uyari"):
        uyarilar.append(etr["uyari"])
    gc_n, gc_v, gc_yog, gc_gerek = ciroz_adimlari(s_g, sh, kritik)
    A_govde = max(lw - 2 * Lu, 0) * h_kat / 1e6
    kb, kb_ar = [], []
    for q in sorted([] if tek else (kirisler or []), key=lambda q: q["u"]):
        x = q["u"] + lw / 2
        Lk = max(q["b"] + 2 * bw, 300.0)
        x0, x1 = max(x - Lk / 2, Lu), min(x + Lk / 2, lw - Lu)
        if x1 - x0 < 0.5 * Lk:
            continue                 # başlığın içinde kalıyor: başlık sargısı zaten var
        if kb_ar and x0 <= kb_ar[-1]["x1"] + 1:      # iç içe giren kiriş bölgeleri birleşir
            kb_ar[-1]["x1"] = max(kb_ar[-1]["x1"], x1)
            kb_ar[-1]["ad"] += "+" + str(q["ad"])
        else:
            kb_ar.append(dict(ad=str(q["ad"]), x0=x0, x1=x1))
    for q in kb_ar:
        x0, x1 = q["x0"], q["x1"]
        kol_k = [xx for xx, yy, dd in gov if x0 <= xx <= x1 and yy < bw / 2]
        hp, kenar = etriye_bolumle(kol_k or [x0, x1], x0, x1, dw, etr["d"])
        cx = ciroz_konumlari(kol_k, [x0 + etr["d"], x1 - etr["d"]], 25 * etr["d"], 0)
        kb.append(dict(ad=q["ad"], x0=x0, x1=x1, n_y=2 * len(hp) + len(cx), n_x=etr["n_x"]))
    return dict(tip="dik", pier=ad, lw=lw, bw=bw, Lu=Lu, tek=tek, kritik=kritik, kademe=kademe,
                secim=secim, sec_sonuc=sonuc, As_pmm=As_pmm, As_min=As_min, pmm_yeterli=yeterli,
                lay=lay, uc=uc, govde=gov, dw=dw, sw=s_g, dh=dh, sh=sh, etr=etr, kesme=ks,
                Ve=kr["Ve"], Vr=kr["Vr"], Vd=kr["Vd"], Md_Mr=oran, lif=lk,
                ciroz=dict(d=dh, adim=gc_n, adim_v=gc_v, s_duz=gc_v * sh, adet_m2=gc_yog,
                           gerek=gc_gerek, adet_kat=math.ceil(gc_yog * A_govde)),
                kiris_bolgeleri=kb, kontroller=kontroller, uyarilar=uyarilar, Nmax=Nmax,
                l_mev=l_mev)


def pier_sade_dik(p, m):
    sistem_ayarla(p.ad)
    segs = kritik_bolgeler(p, lambda k: k.lw)
    Hw, Hcr = segs[0]["Hw"], segs[0]["Hcr"]
    notlar = kritik_notlari(segs, p)
    notlar += tasarim_momentleri(p, segs, cok=False)
    kk = set(p.kesme_kombs or []) or {f[0] for k in p.katlar for f in k.kuvvetler}
    pm_k = set(p.pm_kombs or []) or {f[0] for k in p.katlar for f in k.kuvvetler}
    Ve_carpan = kesme_carpani_12D() if AYAR["kesme_yontemi"].upper() == "1.2D" else 1.0
    notlar.append(kesme_notu() if AYAR["kesme_yontemi"].upper() == "1.2D" else
                  "Kesme: Ve = Vd (kesme_yontemi TBDY sade modda desteklenmez)")
    gruplar = []
    for k in p.katlar:
        mk = malzeme_kat(k, m)
        anahtar = (k.kritik, round(k.kademe, 3), round(k.lw), round(k.bw),
                   tuple(sorted((round(q["u"]), round(q["b"])) for q in (k.kiris or []))),
                   round(mk["fck"], 1))
        if gruplar and gruplar[-1]["anahtar"] == anahtar:
            gruplar[-1]["katlar"].append(k)
        else:
            gruplar.append(dict(anahtar=anahtar, katlar=[k], m=mk))
    fckler = sorted({g["m"]["fck"] for g in gruplar})
    notlar.append("Beton: " + ", ".join(f"C{f:.0f}" for f in fckler)
                  + (" (ETABS pier malzemesinden, kat kat)" if any(k.fck for k in p.katlar) and
                     AYAR.get("malzeme_etabs", True) else " (AYAR)"))
    out = []
    for gi, g in enumerate(gruplar):
        kr, kd, lw, bw = g["anahtar"][:4]
        mg = g["m"]
        tal = talep_azalt_pm([(f[2] * 1e3, f[3] * 1e6) for k in g["katlar"] for f in pm_satirlari(k)
                              if f[0] in pm_k])
        kat_kesme = [(k.kat, max([abs(f[4]) for f in k.kuvvetler if f[0] in kk] or [0]) * 1e3)
                     for k in g["katlar"]]
        h_max = max(k.z_ust - k.z_alt for k in g["katlar"])
        kir = list({(round(q["u"]), round(q["b"])): q for k in g["katlar"]
                    for q in (k.kiris or [])}.values())
        print(f"    grup {gi + 1}/{len(gruplar)} ({g['katlar'][0].kat}–{g['katlar'][-1].kat}, "
              f"{len(tal)} talep, C{mg['fck']:.0f}) ...", flush=True)
        par = dict(lw=lw, bw=bw, kr=kr, kd=kd, tal=tal, kat_kesme=kat_kesme, h=h_max, kir=kir,
                   temel=(gi == 0), m=mg)
        s = grup_sade_dik(p.ad, lw, bw, kr, kd, tal, kat_kesme, Ve_carpan, h_max, mg, kir,
                          temel=(gi == 0))
        s.update(katlar=[k.kat for k in g["katlar"]], _katlar=g["katlar"], etiket=f"G{gi + 1}",
                 _par=par, m=mg)
        out.append(s)
    # alt grup üst gruptan zayıf olamaz (aynı kesitte): Lu ve As
    for i in range(len(out) - 2, -1, -1):
        gL, gU = out[i], out[i + 1]
        if (gL["lw"], gL["bw"]) != (gU["lw"], gU["bw"]):
            continue
        As_L = gL["secim"]["As"] / (2 if gL.get("tek") else 1)       # uç başına
        As_U = gU["secim"]["As"] / (2 if gU.get("tek") else 1)
        if gL["Lu"] >= gU["Lu"] - 1 and As_L >= As_U - 1:
            continue
        P_ = gL["_par"]
        kis = dict(Lu_min=max(gL["Lu"], gU["Lu"]), As_min=As_U)
        s = grup_sade_dik(p.ad, P_["lw"], P_["bw"], P_["kr"], P_["kd"], P_["tal"], P_["kat_kesme"],
                          Ve_carpan, P_["h"], P_["m"], P_["kir"], kis, temel=P_["temel"])
        s["uyarilar"].append(f"Bilgi: üstteki {gU['etiket']} daha güçlü/uzun başlıklı olduğu için "
                             f"bu grup en az onun kadar alındı.")
        for k_ in ("katlar", "_katlar", "etiket", "_par", "m"):
            s[k_] = gL[k_]
        out[i] = s
    for g in out:
        try:
            dik_ek_kontroller(g, g["_katlar"], kk, g["m"])
            izleme_kur(g, g["_katlar"], pm_k, kk)
        except Exception as e:      # noqa
            g["uyarilar"].append(f"Ek kontroller / izleme yapılamadı: {e}")
    return dict(pier=p.ad, tip="dik", Hw=Hw, Hcr=Hcr, notlar=notlar, gruplar=out, bolgeler_kritik=segs)


# ---------------------------------------------------------------- çok kollu (U/H/L/T) bölgeler
def _parca_kenarlari(kollar, parcalar, pi, d, det, dh):
    """Bir parçanın donatı çizgileri (yerel s, w): yüzler ve (sanal olmayan) uç yüzler."""
    A = AYAR
    c = A["paspayi"]
    p = parcalar[pi]
    k = kollar[p["kol"]]
    e_yuz = c + max(det, dh) + d / 2
    w0 = k.t / 2 - e_yuz
    e_uc = c + max(det, dh * (2 if A["yatay_uc_detay"] == "firkete" else 1)) + d / 2
    bas_b = (p.get("sanal0") or not p["yuz0"]) and p["s0"] < 1.0 and \
        _birlesim_kapsanir(kollar, parcalar, pi, 0)
    son_b = (p.get("sanal1") or not p["yuz1"]) and p["s1"] > k.L - 1.0 and \
        _birlesim_kapsanir(kollar, parcalar, pi, 1)
    a = p["s0"] - e_yuz if bas_b else p["s0"] + (e_uc if p["yuz0"] else det + d / 2)
    b = p["s1"] + e_yuz if son_b else p["s1"] - (e_uc if p["yuz1"] else det + d / 2)
    return k, a, b, w0, bas_b, son_b


def bolge_yerlesim(kollar, parcalar, pis, d, n, det, dh):
    """Bölgedeki (pis) parçaların donatı çizgilerine n adet Ød: köşeler + uzunlukla orantılı
    eşit aralık. Birleşimdeki sanal uçlar (karşı kolun sırası) çubuk almaz."""
    kenar, koseler = [], []
    parca_bilgi = {}
    for pi in pis:
        k, a, b, w0, bas_b, son_b = _parca_kenarlari(kollar, parcalar, pi, d, det, dh)
        if (bas_b or son_b) and len(pis) > 1 and b - a < 2 * (net_min_sinir(d) + d):
            continue            # çok kısa kanat: kendi çubuk sırası sığmaz (karşı kolun sırası yeter)
        if b - a <= d or w0 <= 0:
            return None
        parca_bilgi[pi] = dict(k=k, a=a, b=b, w0=w0, bas_b=bas_b, son_b=son_b)
        for w_ in (-w0, w0):
            kenar.append((pi, (a, w_), (b, w_), "yuz"))
        if not bas_b:
            kenar.append((pi, (a, -w0), (a, w0), "kal"))
            koseler += [(pi, a, -w0), (pi, a, w0)]
        if not son_b:
            kenar.append((pi, (b, -w0), (b, w0), "kal"))
            koseler += [(pi, b, -w0), (pi, b, w0)]
    if not parca_bilgi:
        return None
    uzun = [math.hypot(q[2][0] - q[1][0], q[2][1] - q[1][1]) for q in kenar]
    Ltop = sum(uzun)
    r = n - len(koseler)
    if r < 0:
        return None
    # kenar başına ara çubuk sayısı: net aralık [net_min, net_max] (yüzlerde uc_net_max,
    # kalınlık doğrultusundaki uç yüzlerde uc_net_max_kalinlik); kalan çubuklar en seyrek kenara
    A = AYAR
    nmin = net_min_sinir(d)
    kmin, kmax = [], []
    for q, L_ in zip(kenar, uzun):
        nmx = A["uc_net_max"] if q[3] == "yuz" else A["uc_net_max_kalinlik"]
        kmin.append(max(0, math.ceil(L_ / (nmx + d) - 1e-9) - 1))
        kmax.append(max(0, math.floor(L_ / (nmin + d) + 1e-9) - 1))
    if sum(kmin) > r or sum(kmax) < r:
        return None
    k_ = list(kmin)
    for _ in range(r - sum(k_)):
        i = max((i for i in range(len(kenar)) if k_[i] < kmax[i]),
                key=lambda i: (uzun[i] / (k_[i] + 1), kenar[i][3] == "yuz"))
        k_[i] += 1
    pts = [(pi, s_, w_) for pi, s_, w_ in koseler]
    nets, nets_yuz = [], []
    for (pi, P1, P2, tur), L_, kk in zip(kenar, uzun, k_):
        for j in range(1, kk + 1):
            t_ = j / (kk + 1)
            pts.append((pi, P1[0] + t_ * (P2[0] - P1[0]), P1[1] + t_ * (P2[1] - P1[1])))
        nets.append(L_ / (kk + 1) - d)
        if tur == "yuz":
            nets_yuz.append(L_ / (kk + 1) - d)
    bars = []
    for pi, s_, w_ in pts:
        u, v = parca_bilgi[pi]["k"].nokta(s_, w_)
        bars.append((float(u), float(v), d, pi))
    # bölge içi (farklı parçalar arası) en küçük net aralık
    net_min = min(nets)
    if len(bars) > 1:
        B = np.array([b_[:2] for b_ in bars])
        D = np.hypot(B[:, None, 0] - B[None, :, 0], B[:, None, 1] - B[None, :, 1])
        np.fill_diagonal(D, 1e9)
        net_min = min(net_min, float(D.min()) - d)
    return dict(bars=bars, n=n, d=d, net_min=net_min, net_max=max(nets_yuz or nets),
                net_ort=Ltop / n - d, P=Ltop, parca=parca_bilgi)


def bolge_detay(kollar, parcalar, pis, lay, det):
    """cok_etriye için parça detayları (kolon s konumları, sıralar, etriye sınırları)."""
    c = AYAR["paspayi"]
    detay = []
    for pi in pis:
        p = parcalar[pi]
        pb = lay["parca"][pi]
        k = pb["k"]
        ss, ws = set(), {round(-pb["w0"], 1), round(pb["w0"], 1)}
        for u, v, d, pj in lay["bars"]:
            if pj != pi:
                continue
            s_, w_ = k.yerel(u, v)
            s_, w_ = float(s_), float(w_)
            if abs(abs(w_) - pb["w0"]) < 1.0:
                ss.add(round(s_, 1))
            else:
                ws.add(round(w_, 1))
        ss = sorted(ss)
        bos = set()
        if pb["bas_b"]:
            ss = [pb["a"]] + ss
            bos.add(0)
        if pb["son_b"]:
            ss = ss + [pb["b"]]
            bos.add(len(ss) - 1)
        h0 = p["s0"] - p.get("hoop0", 0.0) + (c if p["yuz0"] else 0.0)
        h1 = p["s1"] + p.get("hoop1", 0.0) - (c if p["yuz1"] else 0.0)
        detay.append(dict(ss=ss, ws=sorted(ws), hoop=(h0, h1), bos=bos, kume=[], tam=set()))
    return detay


def _bolge_uzat(kollar, parcalar, pis, adim):
    """Bölgenin (pis) gövdeye bakan bir sınırını adim kadar uzatır: en uzun koldaki, gövde
    tarafında yer olan parça seçilir. Komşu parçayla arada ≥ max(t, 300) gövde kalır; kolun
    ucuna değen parça kol boyunun %45'ini geçmez. Uzatılamazsa False."""
    adaylar = []
    for i in pis:
        p = parcalar[i]
        k = kollar[p["kol"]]
        bosluk = max(k.t, 300.0)
        ayni = [q for j, q in enumerate(parcalar) if q["kol"] == p["kol"] and j != i]
        sinir_ust = min([q["s0"] for q in ayni if q["s0"] >= p["s1"] - 1] + [k.L + bosluk]) - bosluk
        sinir_alt = max([q["s1"] for q in ayni if q["s1"] <= p["s0"] + 1] + [-bosluk]) + bosluk
        tavan = 0.45 * k.L if (p["s0"] < 1 or p["s1"] > k.L - 1) else k.L
        if p["s1"] < k.L - 1 and p["s1"] + adim <= min(sinir_ust, k.L) + 1e-6 and \
                (p["s1"] + adim - p["s0"]) <= tavan + 1e-6:
            adaylar.append((k.merkez_L, i, 1))
        if p["s0"] > 1 and p["s0"] - adim >= max(sinir_alt, 0.0) - 1e-6 and \
                (p["s1"] - p["s0"] + adim) <= tavan + 1e-6:
            adaylar.append((k.merkez_L, i, 0))
    if not adaylar:
        return False
    _, i, yon = max(adaylar)
    if yon == 1:
        parcalar[i]["s1"] += adim
    else:
        parcalar[i]["s0"] -= adim
    if "uzatıldı" not in parcalar[i]["tip"]:
        parcalar[i]["tip"] += " (ρ ≤ %3 için uzatıldı)"
    return True


def grup_sade_cok(ad, kollar, kritik, kademe, talepler, kat_kesme, Ve_carpan, h_kat, m,
                  kiris_parcalari=None, kis=None, temel=False):
    A = AYAR
    kis = dict(kis or {})
    kontroller, uyarilar = [], []
    tmin = min(k.t for k in kollar)
    bw_min = max(200.0, h_kat / 20) if A["ozel_kosul_7613"] else max(250.0, h_kat / 16)
    kontroller.append((f"bw ≥ {bw_min:.0f} mm (en ince kol)", tmin, bw_min, tmin >= bw_min - 1e-6))
    Ac = sum(k.L * k.t for k in kollar)
    Nmax = max(t[0] for t in talepler)
    kontroller.append(("Nd,max ≤ 0.35·Ac·fck [kN]", Nmax / 1e3, 0.35 * Ac * m["fck"] / 1e3,
                       Nmax <= 0.35 * Ac * m["fck"]))
    rho_min = rho_uc_min(kritik, kademe)
    dw, sw, _ = govde_sec(tmin, 0.0025, A["s_govde_max"])
    ks = cok_kesme(kollar, kat_kesme, Ve_carpan, m)
    kr = max(ks, key=lambda r: (r["rho"], -r["s"]))
    dh, sh = kr["d"], kr["s"]
    for yon in ("V2", "V3"):
        r_ = [r for r in ks if r["yon"] == yon]
        if r_:
            g_ = max(r_, key=lambda r: r["Ve"])
            kontroller.append((f"Ve ≤ {_aktif()['vmax']:g}·Ach·√fck [kN] ({yon})", g_["Ve"] / 1e3, g_["Vmax"] / 1e3,
                               g_["Ve"] <= g_["Vmax"]))
            kontroller.append((f"Ve ≤ Vr [kN] ({yon}, en elverişsiz kat)", g_["Ve"] / 1e3,
                               g_["Vr"] / 1e3, g_["Ve"] <= g_["Vr"] * 1.0001))
    det = A["etriye_capi_kritik"] if kritik else A["etriye_capi_ust"]
    ham = cok_bolgeler(kollar, kritik, 0.0, kademe) + list(kiris_parcalari or [])
    parcalar = _parca_birlestir(ham, kollar)
    for p_ in parcalar:
        if " + " in p_["tip"]:
            uyarilar.append(f"Bilgi: {kollar[p_['kol']].ad} kolunda uç bölgeler iç içe/bitişik "
                            f"(arada gövde kalmıyor) – tek bölgede birleştirildi: {p_['tip']} "
                            f"[{p_['s0']:.0f}–{p_['s1']:.0f} mm].")
    # referans (minimum) yerleşim: gövde çubukları ve PMM gereği için
    ref = None
    for de_ in (14, 16, 18, 20):
        ref = cok_yerlesim(kollar, parcalar, de_, 2, 1, 134.0, dw, sw, det, dh, de_)
        if ref:
            break
    if ref is None:
        # referans düzen yalnız gövde çubukları ve PMM ölçeklemesi içindir: aralık sınırları
        # gevşetilerek kurulur (asıl başlık donatısı aşağıda bölge bölge, kurallara göre seçilir)
        eski = {k_: A[k_] for k_ in ("uc_net_max", "uc_net_max_kalinlik", "uc_net_min")}
        try:
            A.update(uc_net_max=1e6, uc_net_max_kalinlik=1e6, uc_net_min=0.0)
            for de_ in (14, 16):
                ref = cok_yerlesim(kollar, parcalar, de_, 2, 1, 134.0, dw, sw, det, dh, de_)
                if ref:
                    break
        finally:
            A.update(eski)
    if ref is None:
        raise RuntimeError("Çok kollu kesitte referans yerleşim kurulamadı. Kollar: " + "; ".join(
            f"{k.ad} L={k.L:.0f} t={k.t:.0f} uçlar={k.uclar[0]['tip']}/{k.uclar[1]['tip']}"
            for k in kollar) + " | parçalar: " + "; ".join(
            f"K{p_['kol'] + 1} {p_['s0']:.0f}-{p_['s1']:.0f} ({p_['tip']})" for p_ in parcalar))
    gov, s_g = ref[1], ref[2]
    print("      PMM: bölge bölge gerekli donatı...", flush=True)
    gerek = bolge_gerekli_donati(kollar, parcalar, ref[0], gov, m, talepler)
    l_mev = h_kat - A["birlesim_yuksekligi"]
    temel_h = A.get("temel_yuksekligi") if temel else None
    sinir_t = (temel_h - A["paspayi"] - 50) if temel_h else None
    bolgeler = []
    uzatildi = False
    for q in gerek:
        pis = q["parcalar"]
        t_z = min(kollar[parcalar[i]["kol"]].t for i in pis)
        if all(parcalar[i]["tip"].startswith("kiriş") for i in pis):
            req_min = 4 * alan(14)
        else:
            req_min = max(rho_min * max(kollar[parcalar[i]["kol"]].t * kollar[parcalar[i]["kol"]].merkez_L
                                        for i in pis), 4 * alan(14))
        req_min = max(req_min, kis.get("As_min_z", {}).get(q["bolge"], 0.0))
        As_pmm = q["As_pmm"] if q["yeterli"] else float("inf")
        As_req = max(As_pmm if math.isfinite(As_pmm) else 0.0, req_min)
        alan_z = sum((parcalar[i]["s1"] - parcalar[i]["s0"]) * kollar[parcalar[i]["kol"]].t
                     for i in pis)

        def ga(d, n, pis=pis):
            return bolge_yerlesim(kollar, parcalar, pis, d, n, det, dh)

        if all(parcalar[i]["tip"].startswith("kiriş") for i in pis):
            # kiriş bağlantı bölgesi: yalnız sargı (TBDY 7.6.2.4); boyuna donatı gövde düzeninde
            ss_ = dict(sirali=[], elenen=[], secim=None, As_req=0.0, As_ref=0.0)
            for n in range(4, 200):
                lay_ = ga(dw, n)
                if lay_ and lay_["net_max"] <= A["s_govde_max"] - dw + 0.5:
                    a_ = dict(et=f"{n}Ø{dw}", d=dw, n=n, As=n * alan(dw), rA=1.0, rA_ref=1.0,
                              CI=0.0, lb=0, l0=0, Rb=0, st=0, n_ideal=n, geo=lay_, P={}, RM=None,
                              puan=0.0, deger="kiriş bölgesi – gövde donatısı düzeninde")
                    ss_["sirali"], ss_["secim"] = [a_], a_
                    break
            As_req = 0.0
        else:
            ss_ = donati_sec(As_req, 4, ga, m, kritik, t_z, l_mev, None, rho_alan=alan_z,
                             sinir_temel=sinir_t)
            # uygun aday yoksa (ρ > %3 / sığmıyor) bölge gövdeye doğru 50'şer mm uzatılır
            uz0 = {i: (parcalar[i]["s0"], parcalar[i]["s1"]) for i in pis}
            adim = 0
            while ss_["secim"] is None and adim < 60 and _bolge_uzat(kollar, parcalar, pis,
                                                                      A["Lu_yuvarlama"]):
                adim += 1
                alan_z = sum((parcalar[i]["s1"] - parcalar[i]["s0"]) * kollar[parcalar[i]["kol"]].t
                             for i in pis)
                ss_ = donati_sec(As_req, 4, ga, m, kritik, t_z, l_mev, None, rho_alan=alan_z,
                                 sinir_temel=sinir_t)
            if adim:
                uzatildi = True
                uyarilar.append(f"Bilgi: B{q['bolge']} uç bölgesi " + ", ".join(
                    f"{kollar[parcalar[i]['kol']].ad} {uz0[i][1] - uz0[i][0]:.0f}→"
                    f"{parcalar[i]['s1'] - parcalar[i]['s0']:.0f} mm" for i in pis
                    if (parcalar[i]["s0"], parcalar[i]["s1"]) != uz0[i]) +
                    (" uzatıldı (ρ ≤ %3 / yerleşim)." if ss_["secim"] else
                     " uzatıldı ama yine de uygun donatı bulunamadı."))
            if ss_["secim"] is None:
                # yine yoksa: kurallara uyan, sığan en çok donatılı düzen (kesit yetersiz)
                en = None
                for d_ in A.get("uc_caplari_sade", SADE_CAPLAR):
                    for n_ in range(4, A.get("uc_n_max", 80) + 1):
                        lay_ = ga(d_, n_)
                        if lay_ is None:
                            continue
                        if lay_["net_min"] < net_min_sinir(d_) - 0.5 or \
                                n_ * alan(d_) / alan_z > A["rho_uc_max"] + 1e-9:
                            break
                        if lay_["net_max"] <= A["uc_net_max"] + 0.5 and \
                                (en is None or n_ * alan(d_) > en["As"]):
                            en = dict(et=f"{n_}Ø{d_}", d=d_, n=n_, As=n_ * alan(d_), rA=0.0,
                                      rA_ref=0.0, CI=0.0, lb=0, l0=0, Rb=0, st=0, n_ideal=n_,
                                      geo=lay_, P={}, RM=None, puan=0.0,
                                      deger="YETERSİZ – sığan en çok donatı")
                if en is not None:
                    ss_["sirali"], ss_["secim"] = [en], en
                    uyarilar.append(f"B{q['bolge']}: gereken {As_req / 100:.1f} cm² sığmıyor "
                                    f"(ρ ≤ %3) – kesit büyütülmeli; sığan en çok donatı "
                                    f"({en['et']}) yazıldı.")
        bolgeler.append(dict(bolge=q["bolge"], pis=pis, tip=q["tip"], kollar=q["kollar"],
                             As_pmm=As_pmm, As_min=req_min, As_req=As_req, sonuc=ss_,
                             secim=ss_["secim"], sira=0, alan=alan_z, t=t_z))
        if ss_["secim"] is None:
            uyarilar.append(f"B{q['bolge']}: uygun donatı bulunamadı (ρ ≤ %3 / yerleşim) – "
                            f"bölge uzatılmalı ya da kesit büyütülmeli.")
    # bütün kesitle doğrulama; yetmezse PMM oranı en yüksek bölge bir üst adaya
    def tum_bars():
        out_ = []
        for z in bolgeler:
            if z["secim"] is None:
                continue
            lay = bolge_yerlesim(kollar, parcalar, z["pis"], z["secim"]["d"], z["secim"]["n"],
                                 det, dh)
            z["lay"] = lay
            out_ += lay["bars"]
        return out_

    if uzatildi:        # uzayan bölgelerin içinde kalan gövde çubukları çıkarılır
        gov = [b_ for b_ in gov if not any(
            kollar[p_["kol"]].icinde(b_[0], b_[1]) and
            p_["s0"] - 25 <= float(kollar[p_["kol"]].yerel(b_[0], b_[1])[0]) <= p_["s1"] + 25
            for p_ in parcalar)]
    uc = tum_bars()
    lk = LifKesit2B(kollar, uc, gov, m)
    oran = float(np.max(lk.talep_orani(talepler)))
    deneme = 0
    while oran > 1.0 and deneme < 25:
        adaylar_z = [z for z in bolgeler if z["secim"] is not None and
                     z["sira"] + 1 < len(z["sonuc"]["sirali"])]
        if not adaylar_z:
            break
        z = max(adaylar_z, key=lambda z: (z["As_pmm"] if math.isfinite(z["As_pmm"]) else 1e12)
                / z["secim"]["As"])
        buyuk = [i for i, a_ in enumerate(z["sonuc"]["sirali"]) if a_["As"] > z["secim"]["As"] + 1]
        if not buyuk:
            break
        z["sira"] = min(buyuk, key=lambda i: (z["sonuc"]["sirali"][i]["As"], -z["sonuc"]["sirali"][i]["puan"]))
        z["secim"] = z["sonuc"]["sirali"][z["sira"]]
        z["not"] = "tüm kesit PMM doğrulaması için bir üst aday alındı"
        uc = tum_bars()
        lk = LifKesit2B(kollar, uc, gov, m)
        oran = float(np.max(lk.talep_orani(talepler)))
        deneme += 1
    # enine donatı (bölge bölge)
    etr_top = None
    for z in bolgeler:
        if z["secim"] is None:
            continue
        pis_ = [i for i in z["pis"] if i in z["lay"]["parca"]]      # çubuk sırası olan parçalar
        pz = [parcalar[i] for i in pis_]
        dz = bolge_detay(kollar, parcalar, pis_, z["lay"], det)
        e = cok_etriye(kollar, pz, dz, z["secim"]["d"], kritik, m)
        z["etr"] = e
        z["detay"] = dz
        z["kol"] = [(kollar[p["kol"]].ad, x["n_y"], x["n_x"]) for p, x in zip(pz, dz)]
        kiris_z = all(parcalar[i]["tip"].startswith("kiriş") for i in z["pis"])
        if not kiris_z and (etr_top is None or e["s"] < etr_top["s"] or e["d"] > etr_top["d"]):
            etr_top = e
    kontroller.append(("Md/Mr P–M2–M3 (seçilen donatı, tüm kesit)", oran, 1.0, oran <= 1.0))
    Ve_kat = {}
    for r_ in ks:
        Ve_kat[r_["kat"]] = Ve_kat.get(r_["kat"], 0.0) + r_["Ve"] ** 2
    Ve_mx = math.sqrt(max(Ve_kat.values())) if Ve_kat else 0.0
    Vsf, sf_not = kesme_surtunmesi(Ac, list(uc) + list(gov), Ve_mx, min(t[0] for t in talepler), m)
    kontroller.append((f"Kesme sürtünmesi √(Ve2²+Ve3²) ≤ Vr,sf [kN] (TBDY 7.6.7.2 – derz; {sf_not})",
                       Ve_mx / 1e3, Vsf / 1e3, Ve_mx <= Vsf * 1.0001))
    gc = govde_ciroz_cok(kollar, gov, dh, sh, s_g, kritik)
    L_govde = sum(max(k.L - sum(p["s1"] - p["s0"] for p in parcalar if kollar[p["kol"]] is k), 0)
                  for k in kollar)
    gc["adet_kat"] = math.ceil(gc["adet_m2"] * L_govde * h_kat / 1e6)
    return dict(tip="cok", pier=ad, kollar=kollar, parcalar=parcalar, kritik=kritik,
                kademe=kademe, bolgeler=bolgeler, uc=uc, govde=gov, dw=dw, sw=s_g, dh=dh, sh=sh,
                etr=etr_top, kesme=ks, Ve=kr["Ve"], Vr=kr["Vr"], Vd=kr["Vd"], Md_Mr=oran, lif=lk,
                ciroz=gc, kontroller=kontroller, uyarilar=uyarilar, Nmax=Nmax, l_mev=l_mev,
                kol_talep=kol_talepleri(kollar, talepler))


def pier_sade_cok(p, m):
    sistem_ayarla(p.ad)

    def lw_kat(k):
        return max(q.merkez_L for q in kollari_kur(k.kollar))
    segs = kritik_bolgeler(p, lw_kat)
    Hw, Hcr = segs[0]["Hw"], segs[0]["Hcr"]
    kol0 = kollari_kur(p.katlar[0].kollar)
    notlar = [f"Çok kollu kesit: {len(kol0)} kol ({', '.join(f'{k.ad}: {k.merkez_L:.0f}×{k.t:.0f}' for k in kol0)})"]
    notlar += kritik_notlari(segs, p)
    notlar += tasarim_momentleri(p, segs, cok=True)
    kk = set(p.kesme_kombs or []) or {f[0] for k in p.katlar for f in k.kuvvetler}
    pm_k = set(p.pm_kombs or []) or {f[0] for k in p.katlar for f in k.kuvvetler}
    Ve_carpan = kesme_carpani_12D() if AYAR["kesme_yontemi"].upper() == "1.2D" else 1.0
    notlar.append(kesme_notu() if AYAR["kesme_yontemi"].upper() == "1.2D" else "Kesme: Ve = Vd")

    def imza(k):
        return tuple(sorted((round(a[0]), round(a[1]), round(b[0]), round(b[1]), round(t))
                            for a, b, t in k.kollar)) + tuple(sorted(
            (round(q["u"]), round(q["v"]), round(q["b"])) for q in (k.kiris or [])))

    gruplar = []
    for k in p.katlar:
        mk = malzeme_kat(k, m)
        anahtar = (k.kritik, round(k.kademe, 3), imza(k), round(mk["fck"], 1))
        if gruplar and gruplar[-1]["anahtar"] == anahtar:
            gruplar[-1]["katlar"].append(k)
        else:
            gruplar.append(dict(anahtar=anahtar, katlar=[k], m=mk))
    fckler = sorted({g["m"]["fck"] for g in gruplar})
    notlar.append("Beton: " + ", ".join(f"C{f:.0f}" for f in fckler)
                  + (" (ETABS pier malzemesinden, kat kat)" if any(k.fck for k in p.katlar) and
                     AYAR.get("malzeme_etabs", True) else " (AYAR)"))
    out = []
    for gi, g in enumerate(gruplar):
        kr, kd = g["anahtar"][0], g["anahtar"][1]
        mg = g["m"]
        kollar = kollari_kur(g["katlar"][0].kollar)
        tal = talep_azalt_pmm([(f[2] * 1e3, f[5] * 1e6, f[3] * 1e6) for k in g["katlar"]
                               for f in pm_satirlari(k) if f[0] in pm_k])
        print(f"    grup {gi + 1}/{len(gruplar)} ({g['katlar'][0].kat}–{g['katlar'][-1].kat}, "
              f"{len(tal)} talep, PMM, C{mg['fck']:.0f}) ...", flush=True)
        kat_kesme = [(k.kat, max([abs(f[4]) for f in k.kuvvetler if f[0] in kk] or [0]) * 1e3,
                      max([abs(f[6]) for f in k.kuvvetler if f[0] in kk] or [0]) * 1e3)
                     for k in g["katlar"]]
        h_max = max(k.z_ust - k.z_alt for k in g["katlar"])
        kiris_p = kiris_parcalari_kur(kollar, g["katlar"])
        par = dict(kollar=kollar, kr=kr, kd=kd, tal=tal, kat_kesme=kat_kesme, h=h_max, kiris_p=kiris_p,
                   temel=(gi == 0), m=mg, imza=g["anahtar"][2])
        s = grup_sade_cok(p.ad, kollar, kr, kd, tal, kat_kesme, Ve_carpan, h_max, mg, kiris_p,
                          temel=(gi == 0))
        s.update(katlar=[k.kat for k in g["katlar"]], _katlar=g["katlar"], etiket=f"G{gi + 1}",
                 _par=par, m=mg)
        out.append(s)

    # KATLAR BOYUNCA SÜREKLİLİK: aynı kesitte alt grubun her uç bölgesi, üstündeki grubun aynı
    # yerdeki uç bölgesinden az donatılı olamaz (bölgeler ağırlık merkezleriyle eşlenir)
    def merkez(g_, z):
        P = [g_["kollar"][g_["parcalar"][i]["kol"]].nokta((g_["parcalar"][i]["s0"] + g_["parcalar"][i]["s1"]) / 2, 0.0)
             for i in z["pis"]]
        return (float(np.mean([q[0] for q in P])), float(np.mean([q[1] for q in P])))
    for i in range(len(out) - 2, -1, -1):
        gL, gU = out[i], out[i + 1]
        if gL["_par"]["imza"] != gU["_par"]["imza"]:
            continue
        gerek = {}
        for zU in gU["bolgeler"]:
            if not zU["secim"] or all(gU["parcalar"][j]["tip"].startswith("kiriş") for j in zU["pis"]):
                continue
            cU = merkez(gU, zU)
            zL = min((z for z in gL["bolgeler"] if z["secim"]),
                     key=lambda z: math.hypot(merkez(gL, z)[0] - cU[0], merkez(gL, z)[1] - cU[1]), default=None)
            if zL is None or math.hypot(merkez(gL, zL)[0] - cU[0], merkez(gL, zL)[1] - cU[1]) > 600.0:
                continue
            if zL["secim"]["As"] < zU["secim"]["As"] - 1:
                gerek[zL["bolge"]] = max(gerek.get(zL["bolge"], 0.0), zU["secim"]["As"])
        if not gerek:
            continue
        P_ = gL["_par"]
        print(f"    {gL['etiket']}: üstteki {gU['etiket']} ile süreklilik için yeniden tasarlanıyor...", flush=True)
        s = grup_sade_cok(p.ad, P_["kollar"], P_["kr"], P_["kd"], P_["tal"], P_["kat_kesme"], Ve_carpan,
                          P_["h"], P_["m"], P_["kiris_p"], kis=dict(As_min_z=gerek), temel=P_["temel"])
        s["uyarilar"].append("Bilgi: " + ", ".join(f"B{b_}" for b_ in sorted(gerek)) + f" bölgeleri üstteki "
                             f"{gU['etiket']} grubundan az donatılı çıktığı için en az onun kadar alındı "
                             f"(katlar boyunca süreklilik).")
        for k_ in ("katlar", "_katlar", "etiket", "_par", "m"):
            s[k_] = gL[k_]
        out[i] = s
    for g in out:
        try:
            cok_ek_kontroller(g, g["_katlar"], kk, g["m"])
            izleme_kur(g, g["_katlar"], pm_k, kk)
        except Exception as e:      # noqa
            g["uyarilar"].append(f"Ek kontroller / izleme yapılamadı: {e}")
    return dict(pier=p.ad, tip="cok", Hw=Hw, Hcr=Hcr, notlar=notlar, gruplar=out, bolgeler_kritik=segs)


# ---------------------------------------------------------------- çıktılar
def _mt(msp, txt, P, h, katman="YAZI", aci=0.0, ek=5, renk=None):
    at = dict(layer=katman, style="TR", char_height=h, rotation=aci)
    if renk is not None:
        at["color"] = renk
    msp.add_mtext(txt.replace("Ø", "%%c"), dxfattribs=at).set_location(P, attachment_point=ek)


def _durum(g):
    return "OK" if all(k[3] for k in g["kontroller"]) else "KONTROL"


def kat_dxf_sade(sonuclar, kl, cakisma=None):
    """Her kat için tek DXF: bütün pier'ler global konumda; başlık ve gövdeye yazılı bilgi."""
    uretilen = []
    for kat, z in _kp_kat_listesi(sonuclar):
        doc = _dxf_yeni()
        for ad_, renk in (("BASLIK", 1), ("BASLIK_YAZI", 2), ("GOVDE_YAZI", 4), ("PIER_ADI", 3),
                          ("KIRIS_BOLGESI", 5), ("CAKISMA", 6)):
            if ad_ not in doc.layers:
                doc.layers.add(ad_, color=renk)
        msp = doc.modelspace()
        tum, tablo = [], [["PIER", "GRUP", "KESİT", "BAŞLIK Lu", "BAŞLIK DONATISI", "ETRİYE",
                           "KOL (kal./boy)", "GÖVDE DÜŞEY", "TEVZİ", "ÇİROZ", "Md/Mr", "DURUM"]]
        for tip, p, r in sonuclar:
            kv = next((k for k in p.katlar if k.kat == kat), None)
            if kv is None:
                continue
            if tip == "bodrum":
                sat = next((s_ for s_ in r["satirlar"] if s_["kat"] == kat), None)
                if sat:
                    kose = _kp_ciz_bodrum(msp, sat, kv, p.ad)
                    tum += kose
                    tablo.append([p.ad, "bodrum", f"{sat['L']:.0f}x{sat['t']:.0f}", "–", "–", "–", "–",
                                  f"+3 {_bd_txt(sat, 'düşey', '+3')} / −3 {_bd_txt(sat, 'düşey', '−3')}",
                                  f"+3 {_bd_txt(sat, 'yatay', '+3')} / −3 {_bd_txt(sat, 'yatay', '−3')}",
                                  "–", "–", "OK" if sat["V_ok"] else "KONTROL"])
                continue
            g = next((g for g in r["gruplar"] if kat in g["katlar"]), None)
            if g is None:
                continue
            if g["tip"] == "dik":
                tum += _kat_dik_yaz(msp, p.ad, g, kv)
                tablo.append([p.ad, g["etiket"], f"{g['lw']:.0f}x{g['bw']:.0f}",
                              "tüm kesit" if g.get("tek") else f"{g['Lu']:.0f}",
                              g["secim"]["et"] + (" (toplam)" if g.get("tek") else " (her uç)"), f"Ø{g['etr']['d']}/{g['etr']['s']:.0f}",
                              f"{g['etr']['n_y']} / {g['etr']['n_x']}",
                              "–" if g.get("tek") else f"Ø{g['dw']}/{g['sw']:.0f}",
                              f"Ø{g['dh']}/{g['sh']:.0f}", f"{g['ciroz']['adet_kat']} ad./kat",
                              f"{g['Md_Mr']:.2f}", _durum(g)])
            else:
                tum += _kat_cok_yaz(msp, p.ad, g, kv)
                bt = ", ".join(f"B{z['bolge']}:{z['secim']['et']}" for z in g["bolgeler"] if z["secim"])
                tablo.append([p.ad, g["etiket"], f"{len(g['kollar'])} kollu", "min", bt,
                              f"Ø{g['etr']['d']}/{g['etr']['s']:.0f}" if g["etr"] else "–",
                              "bölgede", f"Ø{g['dw']}/{g['sw']:.0f}", f"Ø{g['dh']}/{g['sh']:.0f}",
                              f"{g['ciroz']['adet_kat']} ad./kat", f"{g['Md_Mr']:.2f}", _durum(g)])
        if len(tablo) == 1:
            continue
        X = [q[0] for q in tum]
        Y = [q[1] for q in tum]
        ck = [c for c in (cakisma or []) if c["kat"] == kat]
        for i, c in enumerate(ck, 1):
            msp.add_lwpolyline(c["poly"], close=True, dxfattribs={"layer": "CAKISMA", "const_width": 12})
            cx_ = sum(q[0] for q in c["poly"]) / len(c["poly"])
            cy_ = sum(q[1] for q in c["poly"]) / len(c["poly"])
            msp.add_circle((cx_, cy_), 350.0, dxfattribs={"layer": "CAKISMA"})
            _mt(msp, f"Ç{i}", (cx_ + 380, cy_ + 380), 120, "CAKISMA", 0.0, ek=7)
        if ck:
            _dxf_tablo(msp, max(X) + 2500, min(max(Y) + 1500 - (len(tablo) + 3) * 110 * 1.9, min(Y)),
                       [["NO", "BAŞLIK ÇAKIŞMASI", "A", "B", "ORTAK ALAN"]] +
                       [[f"Ç{i}", c["tur"], c["a"], c["b"], f"{c['alan'] / 100:.0f} cm²"]
                        for i, c in enumerate(ck, 1)], katman="CAKISMA", h=110.0)
        msp.add_text(f"KAT {kat} (z = {z / 1000:.2f} m) – PERDE DONATI BİLGİLERİ", height=250,
                     dxfattribs={"layer": "YAZI", "style": "TR"}).set_placement((min(X), max(Y) + 2600))
        msp.add_text("Başlıkta: toplam boyuna donatı, etriye, kalınlık/boy doğrultusu kol sayısı. "
                     "Gövdede: düşey, tevzi (yatay) donatısı ve çiroz adedi. Ayrıntı: perde_rapor.txt",
                     height=110, dxfattribs={"layer": "YAZI", "style": "TR"}
                     ).set_placement((min(X), max(Y) + 2200))
        _dxf_tablo(msp, max(X) + 2500, max(Y) + 1500, tablo, katman="KOL_TABLO", h=110.0)
        ad = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(kat))
        yol = os.path.join(kl, f"KAT_{ad}.dxf")
        doc.saveas(yol)
        uretilen.append(yol)
    return uretilen


def _kat_dik_yaz(msp, pad, g, kv):
    lw, bw, Lu = g["lw"], g["bw"], g["Lu"]
    T = _kp_T_dik(kv, lw, bw)
    ac = _kp_okunur_aci(kv.aci)
    msp.add_lwpolyline([T(0, 0), T(lw, 0), T(lw, bw), T(0, bw)], close=True,
                       dxfattribs={"layer": "BETON"})
    h = float(np.clip(min(Lu / 11, bw / 6.0), 22, 90))
    for x0, x1 in (((0, lw),) if g.get("tek") else ((0, Lu), (lw - Lu, lw))):
        msp.add_lwpolyline([T(x0, 0), T(x1, 0), T(x1, bw), T(x0, bw)], close=True,
                           dxfattribs={"layer": "BASLIK"})
        e = g["etr"]
        _mt(msp, f"{g['secim']['et']}\\PØ{e['d']}/{e['s']:.0f}\\Pkal. {e['n_y']} kol\\Pboy {e['n_x']} kol",
            T((x0 + x1) / 2, bw / 2), h, "BASLIK_YAZI", ac)
        _mt(msp, "TÜM KESİT BAŞLIK (başlıklar birleşti)" if g.get("tek") else f"Lu={Lu:.0f}",
            T((x0 + x1) / 2, -h * 1.6), h * 0.9, "BASLIK_YAZI", ac)
    for z in g.get("kiris_bolgeleri", []):
        msp.add_lwpolyline([T(z["x0"], 0), T(z["x1"], 0), T(z["x1"], bw), T(z["x0"], bw)],
                           close=True, dxfattribs={"layer": "KIRIS_BOLGESI"})
        _mt(msp, f"KİRİŞ {z['ad']}\\PØ{g['etr']['d']}/{g['etr']['s']:.0f}\\Pkal. {z['n_y']} kol",
            T((z["x0"] + z["x1"]) / 2, bw + h * 2.5), h * 0.9, "BASLIK_YAZI", ac)
    gc = g["ciroz"]
    if not g.get("tek"):
        _mt(msp, govde_metni(g), T(lw / 2, -h * 4.2), h, "GOVDE_YAZI", ac)
    else:
        _mt(msp, f"tevzi Ø{g['dh']}/{g['sh']:.0f} (2 yüz)", T(lw / 2, -h * 4.2), h, "GOVDE_YAZI", ac)
    _mt(msp, f"{pad} [{g['etiket']}] {lw:.0f}x{bw:.0f} {bolge_adi_sade(g)} – Md/Mr={g['Md_Mr']:.2f}"
             + ("" if _durum(g) == "OK" else " – KONTROL!"),
        T(lw / 2, bw + h * 8), h * 1.3, "PIER_ADI", ac)
    return [T(0, 0), T(lw, 0), T(lw, bw), T(0, bw)]


def govde_metni(g):
    gc = g["ciroz"]
    return (f"GÖVDE: düşey Ø{g['dw']}/{g['sw']:.0f} (2 yüz)\\Ptevzi Ø{g['dh']}/{g['sh']:.0f} (2 yüz)"
            f"\\Pçiroz Ø{gc['d']}: {gc['adet_kat']} adet/kat ({gc['adet_m2']:.1f}/m²)")


def bolge_adi_sade(g):
    if g["kritik"]:
        return "Kritik"
    return f"Geçiş %{g['kademe'] * 100:.0f}" if g["kademe"] > 0 else "Kritik üstü"


def _kat_cok_yaz(msp, pad, g, kv):
    T = _kp_T_cok(kv)
    kollar, parcalar = g["kollar"], g["parcalar"]
    kose = []
    for k in kollar:
        q = [T(p_) for p_ in k.koseler()]
        kose += q
        msp.add_lwpolyline(q, close=True, dxfattribs={"layer": "BETON"})
    tmin = min(k.t for k in kollar)
    h = float(np.clip(tmin / 5.2, 22, 70))
    for z in g["bolgeler"]:
        for i in z["pis"]:
            p = parcalar[i]
            k = kollar[p["kol"]]
            q = [T(k.nokta(s_, w_)) for s_, w_ in ((p["s0"], -k.t / 2), (p["s1"], -k.t / 2),
                                                   (p["s1"], k.t / 2), (p["s0"], k.t / 2))]
            msp.add_lwpolyline(q, close=True, dxfattribs={"layer": "BASLIK"})
        p0 = max((parcalar[i] for i in z["pis"]), key=lambda p: p["s1"] - p["s0"])
        k = kollar[p0["kol"]]
        ac = _kp_okunur_aci(kv.aci + math.degrees(math.atan2(k.e[1], k.e[0])))
        if z["secim"] is None:
            txt = f"B{z['bolge']}: UYGUN DONATI YOK"
        else:
            e = z.get("etr") or {}
            kol_t = ", ".join(f"{a} {ny}/{nx}" for a, ny, nx in z.get("kol", []))
            txt = (f"B{z['bolge']} {z['secim']['et']}\\PØ{e.get('d', '-')}/{e.get('s', 0):.0f}"
                   f"\\Pkol kal./boy: {kol_t}")
        _mt(msp, txt, T(k.nokta((p0["s0"] + p0["s1"]) / 2, 0.0)), h, "BASLIK_YAZI", ac)
        sg = _dis_yuz(k, kollar, (p0["s0"] + p0["s1"]) / 2)
        _mt(msp, f"L={p0['s1'] - p0['s0']:.0f}", T(k.nokta((p0["s0"] + p0["s1"]) / 2,
                                                           sg * (k.t / 2 + h * 1.5))),
            h * 0.9, "BASLIK_YAZI", ac)
    # gövde yazısı: en uzun gövde aralığının ortasında, kesitin iç tarafında
    en_ = None
    for k in kollar:
        kap = sorted((p["s0"], p["s1"]) for p in parcalar if kollar[p["kol"]] is k)
        bas = 0.0
        for s0, s1 in kap + [(k.L, k.L)]:
            if s0 - bas > (en_[3] - en_[2] if en_ else 0):
                en_ = (k, None, bas, s0)
            bas = max(bas, s1)
    if en_:
        k, _, a_, b_ = en_
        sg = -_dis_yuz(k, kollar, (a_ + b_) / 2)
        ac = _kp_okunur_aci(kv.aci + math.degrees(math.atan2(k.e[1], k.e[0])))
        _mt(msp, govde_metni(g), T(k.nokta((a_ + b_) / 2, sg * (k.t / 2 + h * 3.5))), h,
            "GOVDE_YAZI", ac)
    X = [q[0] for q in kose]
    Y = [q[1] for q in kose]
    _mt(msp, f"{pad} [{g['etiket']}] {len(kollar)} kollu {bolge_adi_sade(g)} – Md/Mr={g['Md_Mr']:.2f}"
             + ("" if _durum(g) == "OK" else " – KONTROL!"),
        ((min(X) + max(X)) / 2, max(Y) + 400), h * 1.4, "PIER_ADI", 0.0, ek=8)
    return kose


def sd_dxf_sade(ps, kl):
    """ETABS/SAP2000 Section Designer DXF içe aktarma kuralına göre (CSI): katman adı = malzeme.
      Concrete : kesit dış hattı (kapalı POLYLINE)
      Rebar    : her boyuna donatı bir POINT (tek tek donatı); çap katman adında da yazılı
                 (Rebar_D16 gibi değil – CSI yalnız 'Rebar' katmanını tanır; çaplar ayrıca
                 <ad>_donatilar.txt dosyasında ve Reference katmanındaki dairelerde)
      Reference: donatıların gerçek çaplı daireleri (yalnız görsel referans)
    Birim mm, orijin kesitin ağırlık merkezi. Her grup için ayrıca donatı listesi (x, y, Ø)."""
    import ezdxf
    klasor = os.path.join(kl, "SectionDesigner")
    os.makedirs(klasor, exist_ok=True)
    out = []
    for g in ps["gruplar"]:
        doc = ezdxf.new("R12")
        doc.header["$INSUNITS"] = 4
        for ad_, renk in (("Concrete", 7), ("Rebar", 1), ("Reference", 8)):
            doc.layers.add(ad_, color=renk)
        msp = doc.modelspace()
        if g["tip"] == "dik":
            cx, cy = g["lw"] / 2, g["bw"] / 2
            msp.add_polyline2d([(-cx, -cy), (cx, -cy), (cx, cy), (-cx, cy)], close=True,
                               dxfattribs={"layer": "Concrete"})
            bars = [(x - cx, y - cy, d) for x, y, d in list(g["uc"]) + list(g["govde"])]
        else:
            for k in g["kollar"]:
                msp.add_polyline2d([tuple(map(float, q)) for q in k.koseler()], close=True,
                                   dxfattribs={"layer": "Concrete"})
            bars = [(float(b[0]), float(b[1]), b[2]) for b in list(g["uc"]) + list(g["govde"])]
        for x, y, d in bars:
            msp.add_point((x, y), dxfattribs={"layer": "Rebar"})
            msp.add_circle((x, y), d / 2, dxfattribs={"layer": "Reference"})
        ad = f"{ps['pier']}_{g['etiket']}_{g['katlar'][0]}-{g['katlar'][-1]}"
        yol = os.path.join(klasor, ad + ".dxf")
        doc.saveas(yol)
        cap = {}
        for x, y, d in bars:
            cap[d] = cap.get(d, 0) + 1
        with open(os.path.join(klasor, ad + "_donatilar.txt"), "w", encoding="utf-8") as f:
            f.write(f"{ad} – donatı listesi (mm, orijin = ağırlık merkezi)\n")
            f.write("Özet: " + ", ".join(f"{n}Ø{d}" for d, n in sorted(cap.items())) + "\n")
            f.write("x\ty\tØ\n")
            for x, y, d in bars:
                f.write(f"{x:.1f}\t{y:.1f}\t{d}\n")
        out.append(yol)
    return out


def rapor_sade(sonuclar, yol, cakisma=None):
    A = AYAR
    L = ["PERDE TASARIM RAPORU – TBDY 2018 Bölüm 7.6 / TS 500", "=" * 78,
         "Donatı seçimi: ödül–ceza algoritması (ağırlıklar: As yakınlığı %30, dağılım %15, "
         "sıkışıklık %15, çubuk adedi %10, çap %10, sargı %10, kenetlenme %5, gereksiz moment %5).",
         f"Kesin sınırlar: As ≥ As,req; net aralık (yüzeyden yüzeye) ≥ max({A['uc_net_min']:.0f}, Ø, 25, "
         f"4/3·Dmax) ve ≤ {A['uc_net_max']:.0f} mm; ρ ≤ {A['rho_uc_max']}; bindirme l0 ≤ kat net "
         f"yüksekliği (h − {A['birlesim_yuksekligi']:.0f}); başlıkta tek çap "
         f"{'/'.join(str(d) for d in A.get('uc_caplari_sade', SADE_CAPLAR))}.",
         "Dağılım puanı net aralığa göre: 60–100 mm → 100; 50–60 / 100–110 → 80; 45–50 / 110–120 → 60. "
         "Çubuk adedi puanı dinamik: n / n_ideal (n_ideal = çevre / (Ø + 80 mm)).",
         "Tablo sütunları: Net aralık = en küçük/ortalama/en büyük; PA..PM alt puanlar.", ""]
    a_ = A
    L += ["HESAP KABULLERİ", "-" * 60,
          f"Sistem: {a_.get('perde_sistemi')} perde" + (f" (bağ kirişli pier'ler: {', '.join(a_['bag_kirisli_pierler'])})"
                                                      if a_.get("bag_kirisli_pierler") else "")
          + f", süneklik düzeyi {a_.get('suneklik')}; D = {a_['D']:g}.",
          "Tasarım momenti: TBDY 7.6.6.1 – Hw/lw > 2 ise kritik yükseklik boyunca taban momenti sabit, üstünde "
          "taban–tepe doğrusuna paralel diyagram (her kombinasyon kendi taban/tepe momentiyle)."
          if a_.get("tasarim_momenti_diyagrami", True) else "Tasarım momenti diyagramı KAPALI.",
          "Kritik yükseklik: TBDY 7.6.2.2 – başlangıç "
          + (f"zemin kat '{a_['kritik_baslangic_kat']}' (rijit bodrum)" if a_.get("kritik_baslangic_kat") else
             "perde tabanı (temel üstü)") + "; plan uzunluğu > %20 küçülürse yeniden başlar.",
          f"Kesme sürtünmesi (TBDY 7.6.7.2 / TS 500): μ = {a_.get('derz_mu', 1.0):g}, "
          + ("pürüzlendirilmiş derz (fctd·Ac katkısı var)" if a_.get("derz_puruzlu", True) else "pürüzsüz derz")
          + ("; kalıcı basınç dahil" if a_.get("derz_eksenel") else "; eksenel basınç katkısı alınmadı (güvenli taraf)")
          + "; üst sınır min(0.2·fck, 3.3 + 0.08·fck)·Ac.",
          "Malzeme: beton sınıfı " + ("ETABS pier malzemesinden kat kat okunur" if a_.get("malzeme_etabs", True)
                                      else f"AYAR (C{a_['fck']:.0f})") + f"; donatı fyk = {a_['fyk']:.0f} MPa.",
          "Dikdörtgen perdede M2, V3 ve T: seçilen donatıyla P–M2–M3 kontrolü, düzlem dışı kesme ve burulma "
          "ihmal sınırı (TS 500 8.2) raporlanır; donatı M3'e göre seçilir.",
          "Belirleyici talepler talep_izleme.csv dosyasında kat / kombinasyon / konum ile verilir.", ""]
    if cakisma is not None:
        L += cakisma_metni(cakisma)
    for tip, p, r in sonuclar:
        if tip == "bodrum":
            L.append(f"### {p.ad} – BODRUM PERDESİ")
            for s_ in r["satirlar"]:
                L.append(f"  {s_['kat']}: t={s_['t']:.0f} düşey +3 {_bd_txt(s_, 'düşey', '+3')} "
                         f"−3 {_bd_txt(s_, 'düşey', '−3')}; yatay +3 {_bd_txt(s_, 'yatay', '+3')} "
                         f"−3 {_bd_txt(s_, 'yatay', '−3')}; V {'OK' if s_['V_ok'] else 'KONTROL'}")
            L += ["  UYARI: " + u for u in r.get("uyarilar", [])] + [""]
            continue
        L.append(f"### {p.ad}")
        L += ["  " + n for n in r["notlar"]]
        for g in r["gruplar"]:
            L.append(f"  [{g['etiket']}] {bolge_adi_sade(g)} – katlar {', '.join(g['katlar'])}"
                     + (f" – C{g['m']['fck']:.0f}" if g.get("m") else ""))
            if g["tip"] == "dik":
                s_ = g["sec_sonuc"]
                if g.get("tek"):
                    L.append(f"    Kesit {g['lw']:.0f}×{g['bw']:.0f}, BAŞLIKLAR BİRLEŞTİ – tüm kesit tek başlık; "
                             f"As,req = max(PMM {g['As_pmm'] / 100:.1f}, min 2×{g['As_min'] / 100:.1f}) = "
                             f"{s_.get('As_req', 0) / 100:.1f} cm² (toplam)")
                else:
                    L.append(f"    Kesit {g['lw']:.0f}×{g['bw']:.0f}, başlık Lu = {g['Lu']:.0f} mm; "
                             f"As,req = max(PMM {g['As_pmm'] / 100:.1f}, min {g['As_min'] / 100:.1f}) = "
                             f"{s_['As_req'] / 100:.1f} cm² (her uç)")
                L += secim_tablosu(s_)
                L += ["    " + t for t in secim_aciklama(s_["secim"], s_["sirali"][1] if len(s_["sirali"]) > 1 else None)]
                L.append(f"    SEÇİLEN: {'tüm kesitte' if g.get('tek') else 'her uçta'} {g['secim']['et']} (As = {g['secim']['As'] / 100:.1f} cm²); "
                         f"Md/Mr = {g['Md_Mr']:.3f}")
                L.append("    Enine donatı: " + sargi_metni(g["etr"]))
                for z in g["kiris_bolgeleri"]:
                    L.append(f"    Kiriş bölgesi {z['ad']}: x=[{z['x0']:.0f}, {z['x1']:.0f}], "
                             f"Ø{g['etr']['d']}/{g['etr']['s']:.0f}, kalınlık doğr. {z['n_y']} kol")
            else:
                for q in g.get("kol_talep", []):
                    L.append(f"    {q['kol']} ({q['L']:.0f}×{q['t']:.0f}): N = {q['N_bas'] / 1e3:.0f} / "
                             f"{q['N_cek'] / 1e3:.0f} kN, |M| = {q['M_max'] / 1e6:.0f} kNm")
                for z in g["bolgeler"]:
                    pmm = f"{z['As_pmm'] / 100:.1f}" if math.isfinite(z["As_pmm"]) else "kesit yetersiz"
                    L.append(f"    B{z['bolge']} [{', '.join(z['kollar'])}] {z['tip'][:50]}: As,req = "
                             f"max(PMM {pmm}, min {z['As_min'] / 100:.1f}) = {z['As_req'] / 100:.1f} cm²")
                    L += secim_tablosu(z["sonuc"])
                    if z["secim"]:
                        L += ["    " + t for t in secim_aciklama(z["secim"], z["sonuc"]["sirali"][1]
                                                               if len(z["sonuc"]["sirali"]) > 1 else None)]
                        L.append(f"    SEÇİLEN: {z['secim']['et']}" + (f" ({z['not']})" if z.get("not") else "")
                                 + f"; etriye Ø{z['etr']['d']}/{z['etr']['s']:.0f}; kol (kalınlık/boy): "
                                 + ", ".join(f"{a}: {ny}/{nx}" for a, ny, nx in z.get("kol", [])))
                L.append(f"    Tüm kesit Md/Mr = {g['Md_Mr']:.3f}")
            gc = g["ciroz"]
            L.append(f"    Gövde: düşey Ø{g['dw']}/{g['sw']:.0f} (2 yüz), tevzi Ø{g['dh']}/{g['sh']:.0f} (2 yüz), "
                     f"çiroz Ø{gc['d']} {gc['adet_m2']:.1f}/m² (≥ {gc['gerek']:.0f}) → {gc['adet_kat']} adet/kat")
            L.append("    Kesme: kat | Vd | Ve | yatay | Vr [kN]")
            for k_ in g["kesme"]:
                L.append(f"      {k_.get('yon', '')} {k_['kat']:<8} {k_['Vd'] / 1e3:7.0f} {k_['Ve'] / 1e3:7.0f} "
                         f"Ø{k_['d']}/{k_['s']:.0f} {k_['Vr'] / 1e3:7.0f} {'OK' if k_['ok'] else 'YOK'}")
            for ad_, v, s2, ok in g["kontroller"]:
                L.append(f"    {'OK ' if ok else 'YOK'} {ad_}: {sayi(v)} (sınır {sayi(s2)})")
            L += ["    " + t for t in g.get("notlar_ek", [])]
            if g.get("Td") is not None:
                L.append(f"    Burulma: Td,max = {g['Td'] / 1e6:.1f} kNm; ihmal sınırı 0.65·fctd·S = "
                         f"{g['T_sinir'] / 1e6:.1f} kNm (TS 500 8.2) → "
                         + ("ihmal edilebilir" if g["Td"] <= g["T_sinir"] else "İHMAL EDİLEMEZ"))
            iz = g.get("izleme") or {}
            if iz.get("pm"):
                L.append("    BELİRLEYİCİ TALEPLER (özgün ETABS satırı; M3 ham → tasarım momenti diyagramı sonrası):")
                for q in iz["pm"]:
                    L.append(f"      {q['kat']:<8} {str(q['konum']):<7} {q['komb']}: N = {q['N']:.0f} kN, "
                             f"M3 = {q['M3_ham']:.0f} → {q['M3']:.0f} kNm"
                             + (f", M2 = {q['M2_ham']:.0f} → {q['M2']:.0f} kNm" if g["tip"] != "dik" else "")
                             + f"  ⇒ talep/kapasite = {q['oran']:.3f}")
            if iz.get("kesme"):
                L.append("    Kesmede belirleyici satırlar: " + "; ".join(
                    f"{q['kat']} {q['komb']} ({q['konum']}) V2 = {abs(q['V2']):.0f} kN" for q in iz["kesme"][:6]))
            if iz.get("N"):
                q = iz["N"]
                L.append(f"    Nd,max satırı: {q['kat']} {q['komb']} ({q['konum']}) N = {q['N']:.0f} kN")
            L += ["    UYARI: " + u for u in g["uyarilar"]]
            L.append("")
    with open(yol, "w", encoding="utf-8") as f:
        f.write("\n".join(L))


# =====================================================================================
# 14) ETABS'E YAZMA – Section Designer pier kesitleri + "Reinforcement to be Checked"
# =====================================================================================
# Yalnız şu ETABS tablolarına dokunulur (Edit > Interactive Database Editing ile aynı yol,
# ETABS v20 / v21 / v22 / v23 ortak API'si):
#   * Pier Section Definitions - General Pier                 (kesit adı + beton)
#   * Section Designer Shapes - Concrete Polygon / Single Bar  (kesit şekli + donatılar)
#   * Shear Wall Pier Design Overwrites - <yönetmelik>          (pier/kat -> kesit, Check)
# Duvar (Wall) kesitleri, alan elemanlarına atanmış kesitler, pier etiketleri, yükler ve
# analiz verisi DEĞİŞTİRİLMEZ. Önceki çalıştırmadan kalan, adı AYAR["sd_onek"] ile başlayan
# kesitler yenilenir; kullanıcının kendi kesitlerine dokunulmaz.
def _dt_coz(r):
    """DatabaseTables Get...Array dönüşünü sürümden bağımsız çözer.
    Döndürür: (tablo sürümü, alan anahtarları, kayıtlar[list[list[str]]], hata kodu)."""
    r = list(r) if isinstance(r, (list, tuple)) else [r]
    hata = 0
    if r and isinstance(r[-1], int) and not isinstance(r[-1], bool):
        hata, r = r[-1], r[:-1]
    seq = [list(x) for x in r if isinstance(x, (list, tuple))]
    ints = [x for x in r if isinstance(x, int) and not isinstance(x, bool)]
    if not seq:
        return (ints[0] if ints else 1), [], [], hata
    alanlar = [str(a) for a in (seq[-2] if len(seq) >= 2 else seq[-1])]
    veri = seq[-1] if len(seq) >= 2 else []
    nf = len(alanlar)
    kayit = [["" if v is None else str(v) for v in veri[i * nf:(i + 1) * nf]]
             for i in range(len(veri) // nf)] if nf else []
    return (ints[0] if ints else 1), alanlar, kayit, hata


def _sor_eh(soru, varsayilan=True):
    try:
        c = input(soru + (" (E/h): " if varsayilan else " (e/H): ")).strip().lower()
    except EOFError:
        return varsayilan
    if not c:
        return varsayilan
    return c[0] in ("e", "y")


class _Tablolar:
    """ETABS DatabaseTables için ince sarmalayıcı."""

    def __init__(self, sm):
        self.sm, self.dt = sm, sm.DatabaseTables
        r = api(self.dt.GetAvailableTables, 0, [], [], [])
        self.anahtarlar = [str(x) for x in r[1]]
        self.surum = {}

    def bul(self, *parcalar, tercih=None):
        aday = [k for k in self.anahtarlar if all(p.upper() in k.upper() for p in parcalar)]
        if tercih:
            aday.sort(key=lambda k: 0 if tercih.upper() in k.upper() else 1)
        return aday[0] if aday else None

    def yazilabilir(self, key):
        try:
            f = api(self.dt.GetAllFieldsInTable, key, 0, 0, [], [], [], [], [])
            return [str(k) for k, i in zip(f[2], f[6]) if i]
        except Exception:
            return None

    def oku(self, key):
        son = None
        for grup in ("All", ""):
            try:
                ver, al, kay, h = _dt_coz(api(self.dt.GetTableForEditingArray, key, grup, 0, [], 0, []))
            except Exception as e:      # noqa
                son = e
                continue
            if h == 0 or al:
                self.surum[key] = ver
                return al, kay
        raise RuntimeError(f"'{key}' tablosu okunamadı ({son})")

    def oku_gosterim(self, key):
        ver, al, kay, h = _dt_coz(api(self.dt.GetTableForDisplayArray, key, [], "All", 0, [], 0, []))
        return al, kay

    def yaz(self, key, alanlar, kayitlar):
        """kayitlar: list[dict] – yalnız ETABS'in içe aldığı (yazılabilir) alanlar gönderilir."""
        yaz_al = self.yazilabilir(key) or list(alanlar)
        yaz_al = [a for a in alanlar if a in yaz_al] or list(alanlar)
        veri = [str(k.get(a, "")) for k in kayitlar for a in yaz_al]
        r = api(self.dt.SetTableForEditingArray, key, self.surum.get(key, 1), yaz_al,
                len(kayitlar), veri)
        h = r[-1] if isinstance(r, (list, tuple)) else r
        if h not in (0, None):
            raise RuntimeError(f"'{key}' tablosuna yazılamadı (kod {h})")

    def uygula(self):
        r = api(self.dt.ApplyEditedTables, True, 0, 0, 0, 0, "")
        r = list(r) if isinstance(r, (list, tuple)) else [r]
        ints = [x for x in r if isinstance(x, int) and not isinstance(x, bool)]
        log = next((x for x in r if isinstance(x, str)), "")
        while len(ints) < 5:
            ints.append(0)
        return dict(olumcul=ints[0], hata=ints[1], uyari=ints[2], bilgi=ints[3], ret=ints[-1],
                    log=log)

    def iptal(self):
        try:
            self.dt.CancelTableEditing()
        except Exception:
            pass


def _dict_kayit(alanlar, kay):
    return [dict(zip(alanlar, k)) for k in kay]


def _sd_kesitleri(sonuclar):
    """Tasarım sonuçlarından ETABS'e yazılacak kesitler (pier yerel 2–3 düzleminde, mm,
    orijin kesit ağırlık merkezi: SD'de X = pier yerel 2 (perde boyu), Y = yerel 3)."""
    onek = AYAR.get("sd_onek", "PT_")
    out = []
    for tip, p, r in sonuclar:
        if tip == "bodrum":
            continue
        for g in r["gruplar"]:
            ad = f"{onek}{p.ad}_{g['etiket']}"
            if g["tip"] == "dik":
                cx, cy = g["lw"] / 2, g["bw"] / 2
                pol = [[(-cx, -cy), (cx, -cy), (cx, cy), (-cx, cy)]]
                bars = [(x - cx, y - cy, d) for x, y, d in list(g["uc"]) + list(g["govde"])]
            else:
                pol = _birlesik_poligon([[tuple(map(float, q)) for q in k.koseler()]
                                         for k in g["kollar"]])
                bars = [(float(b[0]), float(b[1]), b[2]) for b in list(g["uc"]) + list(g["govde"])]
            # aynı noktada iki çubuk olmasın (kol birleşimlerinde)
            tekil = {}
            for x, y, d in bars:
                k_ = (round(x), round(y))
                if k_ not in tekil or d > tekil[k_][2]:
                    tekil[k_] = (x, y, d)
            pol = [q if sum(q[i][0] * q[i - 1][1] - q[i - 1][0] * q[i][1] for i in range(len(q))) <= 0
                   else q[::-1] for q in pol]        # saat yönünün tersi (CCW)
            out.append(dict(ad=ad, pier=p.ad, katlar=list(g["katlar"]), pol=pol,
                            bars=list(tekil.values()), etiket=g["etiket"]))
    return out


def _birlesik_poligon(dortgenler):
    """Kol dikdörtgenlerinin birleşimi (shapely varsa); yoksa dikdörtgenler ayrı ayrı yazılır
    (Section Designer'da üstteki şekil (Z sırası) alttakinin yerini alır)."""
    try:
        from shapely.geometry import Polygon
        from shapely.geometry.polygon import orient
        from shapely.ops import unary_union
        u = unary_union([Polygon(q) for q in dortgenler]).buffer(0)
        gs = list(u.geoms) if hasattr(u, "geoms") else [u]
        if any(len(g_.interiors) for g_ in gs):      # kapalı kutu: delik yazılamaz
            return dortgenler
        return [[(float(x), float(y)) for x, y in list(orient(g_.simplify(0.5), 1.0).exterior.coords)[:-1]]
                for g_ in gs]
    except Exception:
        return dortgenler


def _beton_malzemesi(sm, pier, kat):
    try:
        g = api(sm.PierLabel.GetSectionProperties, pier, 0, [], [], [], [], [], [], [], [],
                [], [], [], [], [], [], [])
        katlar, mat = list(g[1]), list(g[9])
        return str(mat[katlar.index(kat)] if kat in katlar else mat[0])
    except Exception:
        return None


def _donati_malzemesi(T):
    key = T.bul("Material Properties", "Rebar")
    if not key:
        return None
    al, kay = T.oku(key)
    K = _dict_kayit(al, kay)
    if not K:
        return None

    def fy(k):
        try:
            return abs(float(k.get("Fy", 0)) - AYAR["fyk"])
        except ValueError:
            return 1e9
    return min(K, key=fy).get("Material")


def _cubuk_adlari(T):
    """ETABS'te tanımlı donatı çapları: {Ø(mm): ad}."""
    key = T.bul("Reinforcing Bar Sizes")
    out = {}
    if not key:
        return out
    try:
        al, kay = T.oku(key)
    except Exception:
        try:
            al, kay = T.oku_gosterim(key)
        except Exception:
            return out
    up = [a.upper() for a in al]
    i_ad = next((i for i, a in enumerate(up) if a in ("NAME", "BARSIZE", "SIZE", "BAR", "BARNAME")), 0)
    i_d = next((i for i, a in enumerate(up) if "DIA" in a), None)
    if i_d is None:
        return out
    for row in kay:
        try:
            d = float(row[i_d])
        except (ValueError, IndexError):
            continue
        out.setdefault(int(round(d)), row[i_ad])
    return out


def _model_yedekle(sm):
    import shutil
    try:
        fn = sm.GetModelFilename(True)
        fn = fn[0] if isinstance(fn, (list, tuple)) else fn
        if fn and os.path.isfile(fn):
            kok, uz = os.path.splitext(fn)
            hedef = f"{kok}_perde_yedek_{time.strftime('%Y%m%d_%H%M')}{uz}"
            shutil.copy2(fn, hedef)
            return hedef
    except Exception:
        pass
    return None


def etabs_sd_yaz(sonuclar, onay=None):
    """Seçilen donatıyı ETABS'e General Pier Section (Section Designer) olarak yazar ve ilgili
    pier/katlara 'Reinforcement to be Checked' ile atar. onay: None -> sorar, True -> sormaz."""
    A = AYAR
    kl = A["cikti_klasoru"]
    kesitler = _sd_kesitleri(sonuclar)
    if not kesitler:
        print("ETABS'e yazılacak pier kesiti yok (bodrum perdeleri yazılmaz).")
        return False
    L = [f"ETABS'e yazma – {time.strftime('%d.%m.%Y %H:%M')}"]
    sm = etabs_baglan()
    eski_birim = sm.GetPresentUnits()
    sm.SetPresentUnits(9)                       # N_mm_C: tablolar mm ile okunur/yazılır
    T = None
    try:
        try:
            v = sm.GetVersion()
            L.append(f"ETABS sürümü: {v[0] if isinstance(v, (list, tuple)) else v}")
        except Exception:
            pass
        T = _Tablolar(sm)
        k_tanim = T.bul("Pier Section Definitions", "General")
        k_poli = T.bul("Section Designer Shapes", "Concrete Polygon")
        k_bar = T.bul("Section Designer Shapes", "Single Bar")
        k_ow = T.bul("Shear Wall Pier Design Overwrites", tercih="TS 500")
        eksik = [ad for ad, k in (("Pier Section Definitions - General Pier", k_tanim),
                                  ("Section Designer Shapes - Concrete Polygon", k_poli),
                                  ("Section Designer Shapes - Reinforcing - Single Bar", k_bar),
                                  ("Shear Wall Pier Design Overwrites", k_ow)) if not k]
        if eksik:
            print("Bu ETABS sürümünde gerekli tablolar bulunamadı: " + "; ".join(eksik))
            print("Section Designer DXF'leri ve donatı listeleriyle elle girebilirsiniz.")
            return False
        d_mat = _donati_malzemesi(T)
        if not d_mat:
            print("Modelde donatı (Rebar) malzemesi bulunamadı – yazma iptal.")
            return False
        cubuk = _cubuk_adlari(T)
        for s in kesitler:
            s["beton"] = _beton_malzemesi(sm, s["pier"], s["katlar"][0]) or "C30"
        # ---------------- özet ve onay
        print("\nETABS'e yazılacak pier kesitleri (Section Designer, 'Reinforcement to be Checked'):")
        for s in kesitler:
            cap = {}
            for _, _, d in s["bars"]:
                cap[d] = cap.get(d, 0) + 1
            satir = (f"  {s['ad']:<18} {s['katlar'][0]}–{s['katlar'][-1]} ({len(s['katlar'])} kat)  "
                     f"{s['beton']}/{d_mat}  " + " + ".join(f"{n}Ø{d}" for d, n in sorted(cap.items(), reverse=True)))
            print(satir)
            L.append(satir)
        print("  Yalnız pier kontrol kesitleri ve pier tasarım atamaları değişir; duvar kesitleri ve "
              "atamaları, pier etiketleri değişmez.")
        if onay is None and not _sor_eh("Bu kesitler ETABS'e yazılsın mı (duvar kesitleri değişmez)?",
                                         varsayilan=False):
            print("ETABS'e yazılmadı.")
            return False
        # ---------------- kilit
        try:
            kilit = sm.GetModelIsLocked()
            kilit = kilit[0] if isinstance(kilit, (list, tuple)) else kilit
        except Exception:
            kilit = False
        if kilit:
            print("Model kilitli (analiz sonuçları var). Pier kesiti TANIMLAMAK için ETABS kilidin "
                  "açılmasını ister; kilit açılınca analiz sonuçları silinir (kesitler ve atamalar "
                  "değişmez, sonra analiz tekrar çalıştırılır).")
            if onay is None and not _sor_eh("Kilit açılsın mı?"):
                print("ETABS'e yazılmadı.")
                return False
            sm.SetModelIsLocked(False)
            L.append("Model kilidi açıldı.")
        yedek = _model_yedekle(sm)
        if yedek:
            print(f"Model dosyası yedeklendi: {yedek}")
            L.append(f"Yedek: {yedek}")
        else:
            print("Uyarı: model dosyası yedeklenemedi (kaydedilmemiş model?). Devam ediliyor.")
        yeni_adlar = {s["ad"] for s in kesitler}
        onek = A.get("sd_onek", "PT_")
        bizim_pier = {s["pier"] for s in kesitler}

        def bizim_mi(ad):          # yenilenecek (önceki çalıştırmadan kalan) kesit mi?
            return ad in yeni_adlar or any(ad.startswith(f"{onek}{p_}_") for p_ in bizim_pier)

        # ---------------- 1. aşama: kesit tanımları + SD şekilleri
        al, kay = T.oku(k_tanim)
        K = [k for k in _dict_kayit(al, kay) if (k.get("Name") or "").strip()
             and not bizim_mi(k.get("Name", ""))]
        yabanci_mat = {k.get("Name"): k.get("Material") for k in K}
        if K:
            L.append("Modelde bulunan diğer pier kesitleri (korunur): " + ", ".join(str(k.get("Name")) for k in K))
        for s in kesitler:
            K.append(dict(Name=s["ad"], Material=s["beton"]))
        T.yaz(k_tanim, al or ["Name", "Material"], K)
        sd_tablolar = [k for k in T.anahtarlar if k.upper().startswith("SECTION DESIGNER SHAPES")]
        for key in sd_tablolar:
            al, kay = T.oku(key)
            K0 = [k for k in _dict_kayit(al, kay) if (k.get("SectionName") or "").strip()]
            K = [k for k in K0 if not (k.get("SectionType", "").upper() == "PIER"
                                       and bizim_mi(k.get("SectionName", "")))]
            # Başkasına ait (elle tanımlanmış) kesitlerin şekilleri aynen geri yazılır. ETABS bu
            # kayıtlarda bazı alanları boş verebiliyor; boş alan içe aktarmada hata olduğu için
            # eksikler tamamlanır (şekil adı, malzeme, Z sırası...).
            if K:
                L.append(f"{key}: korunan {len(K)} yabancı kayıt; ilk kayıt (ham): {K[0]}")
            say_ = {}
            for k in K:
                sec_ = k.get("SectionName")
                if "SectionType" in k and not (k.get("SectionType") or "").strip():
                    k["SectionType"] = "Pier" if sec_ in yabanci_mat else "Frame"
                if "ShapeName" in k and not (k.get("ShapeName") or "").strip():
                    if key == k_poli:
                        k["ShapeName"] = "Polygon1"          # aynı kesitin adsız noktaları tek poligon
                    else:
                        say_[sec_] = say_.get(sec_, 0) + 1
                        k["ShapeName"] = f"S{say_[sec_]}"
                varsay = dict(Reinforcing="No", RebarMat=d_mat, Color="Gray4", ZOrder="1",
                              Material=(d_mat if "REINFORCING" in key.upper() else
                                        (yabanci_mat.get(sec_) or "")))
                for a_, v_ in varsay.items():
                    if a_ in k and not (k.get(a_) or "").strip() and v_:
                        k[a_] = v_
                if "RebarSize" in k and not (k.get("RebarSize") or "").strip():
                    k["RebarSize"] = "User"
            if key == k_poli:
                al = al or ["SectionType", "SectionName", "ShapeName", "Material", "X", "Y",
                            "Reinforcing", "RebarMat", "Color", "ZOrder"]
                for s in kesitler:
                    for j, pol in enumerate(s["pol"], 1):
                        for x, y in pol:
                            K.append(dict(SectionType="Pier", SectionName=s["ad"], ShapeName=f"Poligon{j}",
                                          Material=s["beton"], X=f"{x:.1f}", Y=f"{y:.1f}",
                                          Reinforcing="No", RebarMat=d_mat, Color="Gray4", ZOrder=str(j)))
            elif key == k_bar:
                al = al or ["SectionType", "SectionName", "ShapeName", "Material", "XCenter",
                            "YCenter", "RebarSize", "Area", "ZOrder"]
                for s in kesitler:
                    z0 = len(s["pol"])
                    for j, (x, y, d) in enumerate(s["bars"], 1):
                        ad_ = cubuk.get(int(round(d)))
                        K.append(dict(SectionType="Pier", SectionName=s["ad"], ShapeName=f"D{j}",
                                      Material=d_mat, XCenter=f"{x:.1f}", YCenter=f"{y:.1f}",
                                      RebarSize=ad_ if ad_ else "User", Area=f"{alan(d):.1f}",
                                      ZOrder=str(z0 + j)))
            elif len(K) == len(K0):
                continue                                   # bu tabloda bizim kayıt yok
            T.yaz(key, al, K)
        r1 = T.uygula()
        L.append(f"1. aşama (kesitler): ölümcül {r1['olumcul']}, hata {r1['hata']}, uyarı {r1['uyari']}")
        L.append(r1["log"] or "")
        if r1["olumcul"] or r1["hata"]:
            # Hata bizim kesitlerimizde mi? Tablolar yeniden okunup her kesitin poligon noktası ve
            # donatı sayısı beklenenle karşılaştırılır; tamamsa (hata başka kayıtlardaysa) devam.
            eksik_k = []
            try:
                alp, kayp = T.oku(k_poli)
                alb, kayb = T.oku(k_bar)
                np_, nb_ = {}, {}
                for k in _dict_kayit(alp, kayp):
                    np_[k.get("SectionName")] = np_.get(k.get("SectionName"), 0) + 1
                for k in _dict_kayit(alb, kayb):
                    nb_[k.get("SectionName")] = nb_.get(k.get("SectionName"), 0) + 1
                for s in kesitler:
                    if np_.get(s["ad"], 0) != sum(len(q) for q in s["pol"]) or \
                            nb_.get(s["ad"], 0) != len(s["bars"]):
                        eksik_k.append(f"{s['ad']} (poligon {np_.get(s['ad'], 0)}/{sum(len(q) for q in s['pol'])}, "
                                       f"donatı {nb_.get(s['ad'], 0)}/{len(s['bars'])})")
            except Exception as e:      # noqa
                eksik_k = [f"doğrulama yapılamadı: {e}"]
            if r1["olumcul"] or eksik_k:
                T.iptal()
                L.append("Eksik yazılan kesitler: " + "; ".join(eksik_k[:20]))
                print("!!! Kesitler ETABS'e yazılamadı – ayrıntı etabs_yaz_log.txt")
                return False
            L.append("İçe aktarma hataları programın kesitleriyle ilgili değil (hepsi eksiksiz yazıldı) – "
                     "devam edildi.")
            print(f"  Uyarı: ETABS {r1['hata']} kayıt hatası bildirdi ama programın {len(kesitler)} kesiti "
                  f"eksiksiz yazıldı (hata, modeldeki başka bir pier kesitinin kaydında) – devam ediliyor.")
        print(f"  {len(kesitler)} pier kesiti tanımlandı.")
        # ---------------- 2. aşama: pier tasarım atamaları (Check)
        al, kay = T.oku(k_ow)
        K = _dict_kayit(al, kay)
        tip_d = next((k.get("PierSecType") for k in K if "GENERAL" in k.get("PierSecType", "").upper()),
                     "General Reinforcing Section")
        chk = next((k.get("DesignCheck") for k in K if "CHECK" in k.get("DesignCheck", "").upper()),
                   "Check")
        atama = {(kat, s["pier"]): s["ad"] for s in kesitler for kat in s["katlar"]}
        bulunan = set()
        for k in K:
            a_ = atama.get((k.get("Story"), k.get("Pier")))
            if a_:
                k.update(PierSecType=tip_d, PierSecBot=a_, PierSecTop=a_, DesignCheck=chk)
                bulunan.add((k.get("Story"), k.get("Pier")))
        for (kat, pier), a_ in atama.items():
            if (kat, pier) in bulunan:
                continue
            sablon = next((k for k in K if k.get("Pier") == pier), K[0] if K else {})
            yeni = dict(sablon)
            yeni.update(Story=kat, Pier=pier, PierSecType=tip_d, PierSecBot=a_, PierSecTop=a_,
                        DesignCheck=chk)
            K.append(yeni)
        T.yaz(k_ow, al, K)
        r2 = T.uygula()
        L.append(f"2. aşama (atamalar): ölümcül {r2['olumcul']}, hata {r2['hata']}, uyarı {r2['uyari']}")
        L.append(r2["log"] or "")
        if r2["olumcul"] or r2["hata"]:
            T.iptal()
            print("!!! Pier atamaları yazılamadı (kesitler tanımlı kaldı) – ayrıntı etabs_yaz_log.txt")
            return False
        print(f"  {len(atama)} pier/kat ataması yapıldı ({tip_d}, {chk}).")
        # ---------------- analiz + kontrol (isteğe bağlı)
        if _sor_eh("Analiz tekrar çalıştırılıp Shear Wall Design/Check başlatılsın mı?"):
            try:
                print("  Analiz çalışıyor...", flush=True)
                sm.Analyze.RunAnalysis()
                bas = False
                dsw = getattr(sm, "DesignShearWall", None)
                for fn in ("StartDesign", "StartWallDesign"):
                    if dsw is not None and hasattr(dsw, fn):
                        try:
                            getattr(dsw, fn)()
                            bas = True
                            break
                        except Exception:
                            pass
                if bas:
                    _dc_ozet(T, kesitler, L)
                else:
                    print("  Analiz bitti. ETABS'te Design > Shear Wall Design > Start Design/Check "
                          "komutunu çalıştırın.")
            except Exception as e:
                print(f"  Analiz/kontrol başlatılamadı ({e}); ETABS'te elle çalıştırın.")
        else:
            print("  ETABS'te: Analyze > Run Analysis, ardından Design > Shear Wall Design > "
                  "Start Design/Check.")
        return True
    except Exception as e:
        if T is not None:
            T.iptal()
        L.append(traceback.format_exc())
        print(f"!!! ETABS'e yazma hatası: {e} – ayrıntı etabs_yaz_log.txt")
        return False
    finally:
        try:
            sm.SetPresentUnits(eski_birim)
        except Exception:
            pass
        try:
            os.makedirs(kl, exist_ok=True)
            with open(os.path.join(kl, "etabs_yaz_log.txt"), "w", encoding="utf-8") as f:
                f.write("\n".join(str(x) for x in L))
        except Exception:
            pass


def _dc_ozet(T, kesitler, L):
    """Check sonrası pier D/C oranları (ETABS özet tablosundan)."""
    key = T.bul("Shear Wall Pier Design Summary", tercih="TS 500")
    if not key:
        return
    try:
        al, kay = T.oku_gosterim(key)
    except Exception:
        return
    K = _dict_kayit(al, kay)
    bizim = {(kat, s["pier"]) for s in kesitler for kat in s["katlar"]}
    print("  ETABS kontrol sonucu (D/C):")
    for k in K:
        if (k.get("Story"), k.get("Pier")) in bizim:
            t = f"    {k.get('Pier'):<8} {k.get('Story'):<10} {k.get('Station', ''):<7} D/C = {k.get('DCRatio', '')}"
            print(t)
            L.append(t)


# =====================================================================================
# 15) BAŞLIK ÇAKIŞMA DENETİMİ, KAT GEÇİŞLERİ (DÜZ / KRANK / FİLİZ) VE KLASÖR DÜZENİ
# =====================================================================================
def _p_alan(q):
    return 0.5 * sum(q[i - 1][0] * q[i][1] - q[i][0] * q[i - 1][1] for i in range(len(q)))


def _p_kirp(konu, kirp):
    """Sutherland–Hodgman: konu ∩ kirp (kirp dışbükey çokgen)."""
    if _p_alan(kirp) < 0:
        kirp = kirp[::-1]
    out = [tuple(q) for q in konu]
    for i in range(len(kirp)):
        a, b = kirp[i - 1], kirp[i]
        if not out:
            break
        giris, out = out, []

        def ic(p_):
            return (b[0] - a[0]) * (p_[1] - a[1]) - (b[1] - a[1]) * (p_[0] - a[0]) >= -1e-9

        def kes(p_, q_):
            den = (a[0] - b[0]) * (p_[1] - q_[1]) - (a[1] - b[1]) * (p_[0] - q_[0])
            if abs(den) < 1e-12:
                return q_
            t = ((a[0] - p_[0]) * (p_[1] - q_[1]) - (a[1] - p_[1]) * (p_[0] - q_[0])) / den
            return (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))

        for j in range(len(giris)):
            p_, q_ = giris[j - 1], giris[j]
            if ic(q_):
                if not ic(p_):
                    out.append(kes(p_, q_))
                out.append(q_)
            elif ic(p_):
                out.append(kes(p_, q_))
    return out


def _pier_bolge_poligonlari(tip, p, r, kat):
    """Bir pier'in o kattaki beton dış hatları ve uç bölge (başlık) dörtgenleri – global mm.
    Döndürür: ([beton poligonu], [(etiket, poligon)])."""
    if tip == "bodrum":
        return [], []
    kv = next((k for k in p.katlar if k.kat == kat), None)
    g = next((g for g in r["gruplar"] if kat in g["katlar"]), None)
    if kv is None or g is None:
        return [], []
    if g["tip"] == "dik":
        lw, bw, Lu = g["lw"], g["bw"], g["Lu"]
        T = _kp_T_dik(kv, lw, bw)

        def dort(x0, x1):
            return [T(x0, 0), T(x1, 0), T(x1, bw), T(x0, bw)]
        if g.get("tek"):
            bol = [("tüm kesit", dort(0, lw))]
        else:
            bol = [("1. uç başlığı", dort(0, Lu)), ("2. uç başlığı", dort(lw - Lu, lw))]
            bol += [(f"kiriş bölgesi {z['ad']}", dort(z["x0"], z["x1"])) for z in g.get("kiris_bolgeleri", [])]
        return [dort(0, lw)], bol
    T = _kp_T_cok(kv)
    kollar, parcalar = g["kollar"], g["parcalar"]
    beton = [[T(q) for q in k.koseler()] for k in kollar]
    bol = []
    for z in g["bolgeler"]:
        for i in z["pis"]:
            p_ = parcalar[i]
            k = kollar[p_["kol"]]
            bol.append((f"B{z['bolge']}", [T(k.nokta(s_, w_)) for s_, w_ in (
                (p_["s0"], -k.t / 2), (p_["s1"], -k.t / 2), (p_["s1"], k.t / 2), (p_["s0"], k.t / 2))]))
    return beton, bol


def baslik_cakisma_denetimi(sonuclar, esik=5000.0):
    """FARKLI pier'lerin uç bölgeleri planda iç içe giriyor mu? (Aynı perdenin kendi başlıkları
    tasarım sırasında birleştirilir.) İki tür kayıt:
      BAŞLIK–BAŞLIK : iki pier'in uç bölgeleri üst üste (aynı betona iki ayrı başlık donatısı)
      BAŞLIK–PERDE  : bir pier'in uç bölgesi diğer pier'in betonunun içine giriyor
    esik: dikkate alınan en küçük ortak alan (mm²)."""
    out = []
    for kat, _ in _kp_kat_listesi(sonuclar):
        V = [(p.ad,) + _pier_bolge_poligonlari(tip, p, r, kat) for tip, p, r in sonuclar]
        V = [v for v in V if v[1]]
        kutu = []
        for ad, bet, bol in V:
            P = [q for poly in bet for q in poly]
            kutu.append((min(q[0] for q in P), min(q[1] for q in P), max(q[0] for q in P),
                         max(q[1] for q in P)))
        for i in range(len(V)):
            for j in range(i + 1, len(V)):
                a, b = kutu[i], kutu[j]
                if a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1]:
                    continue
                ai, bet_i, bol_i = V[i]
                aj, bet_j, bol_j = V[j]
                ortak = set()
                for ei, qi in bol_i:
                    for ej, qj in bol_j:
                        ks = _p_kirp(qi, qj)
                        al = abs(_p_alan(ks)) if len(ks) >= 3 else 0.0
                        if al > esik:
                            ortak.update([(ai, ei), (aj, ej)])
                            out.append(dict(kat=kat, tur="BAŞLIK–BAŞLIK", a=f"{ai} {ei}",
                                            b=f"{aj} {ej}", alan=al, poly=ks))
                for a1, bol1, a2, bet2 in ((ai, bol_i, aj, bet_j), (aj, bol_j, ai, bet_i)):
                    for e1, q1 in bol1:
                        if (a1, e1) in ortak:
                            continue
                        for q2 in bet2:
                            ks = _p_kirp(q1, q2)
                            al = abs(_p_alan(ks)) if len(ks) >= 3 else 0.0
                            if al > esik:
                                out.append(dict(kat=kat, tur="BAŞLIK–PERDE", a=f"{a1} {e1}",
                                                b=f"{a2} betonu", alan=al, poly=ks))
    return out


def cakisma_metni(cakisma):
    """Aynı çakışma birçok katta tekrar eder: kat aralığıyla tek satırda."""
    L = ["BAŞLIK (UÇ BÖLGE) ÇAKIŞMA DENETİMİ", "-" * 60,
         "1) Aynı perdenin iki başlığı iç içe/bitişikse (arada max(bw, 300) mm'den az gövde): bütün "
         "kesit TEK başlık olarak donatılır (raporda 'BAŞLIKLAR BİRLEŞTİ').",
         "2) Çok kollu (U/H/L/T) perdede aynı kol üzerindeki bitişik uç bölgeler tek bölgede "
         "birleştirilir; %3 için uzatılan bölge komşu bölgeye max(t, 300) mm'den fazla yaklaşamaz.",
         "3) FARKLI pier'lerin uç bölgeleri planda üst üste geliyorsa aşağıda listelenir – program "
         "bunları birleştirmez (her pier kendi kuvvetiyle tasarlanır):", ""]
    if not cakisma:
        return L + ["   Farklı pier'ler arasında çakışan uç bölge YOK.", ""]
    grup = {}
    for c in cakisma:
        grup.setdefault((c["tur"], c["a"], c["b"]), []).append(c)
    for (tur, a, b), v in grup.items():
        katlar = [c["kat"] for c in v]
        L.append(f"   {tur}: {a}  ×  {b}  – ortak alan {max(c['alan'] for c in v) / 100:.0f} cm² – "
                 f"katlar: {katlar[0]}" + (f" … {katlar[-1]} ({len(katlar)} kat)" if len(katlar) > 1 else ""))
    L += ["", "   ÖNERİ: Köşede/T'de birleşen perdeler ETABS'te TEK pier etiketiyle tanımlanırsa program "
              "birleşimi tek uç bölgesi olarak (U/L/T kesit, PMM ile) donatır. Ayrı pier bırakılacaksa "
              "ortak bölgeye iki başlığın donatısı ÜST ÜSTE konmamalı; büyük olanı esas alınıp diğeri "
              "elle düzenlenmelidir.", ""]
    return L


# ---------------------------------------------------------------- kat geçişleri
def _gecis_denetle(gc, m):
    """Geçişte donatı çakışmaları: çap büyümesi, filiz–alt donatı, krank yolları, krank hedefi,
    bindirme bölgesinde çift çubuk aralığı, kesit dışına düşen filiz."""
    A = AYAR
    for lst in (gc["duz"], gc["kirim"]):
        for b in list(lst):
            if b["du"] > b["dl"] + 0.5:          # üst çap daha büyük: alt donatı devam ettirilemez
                lst.remove(b)
                lb, l0 = kenetlenme(b["du"], m)
                gc["filiz"].append(dict(xu=b["xu"], yu=b["yu"], du=b["du"],
                                        gomulme=A["filiz_gomulme_katsayi"] * lb, bindirme=l0,
                                        alt_kesitte=True, neden=f"çap büyüyor Ø{b['dl']}→Ø{b['du']}"))
                gc["biten"].append(dict(x=b["xl"], y=b["yl"], d=b["dl"]))
    duz, kirim, filiz, biten = gc["duz"], gc["kirim"], gc["filiz"], gc["biten"]
    cak = []
    alt = [(b["xl"], b["yl"], b["dl"], "devam eden") for b in duz + kirim] + \
          [(b["x"], b["y"], b["d"], "biten") for b in biten]
    for f in filiz:
        if not f.get("alt_kesitte", True):
            cak.append(dict(x=f["xu"], y=f["yu"], tur="FİLİZ KESİT DIŞINDA",
                            aciklama=f"Ø{f['du']} filiz alt kesitin betonuna gömülemiyor (üst kesit taşıyor)"))
        en = None
        for x, y, d, tur in alt:
            net = math.hypot(f["xu"] - x, f["yu"] - y) - (f["du"] + d) / 2
            if en is None or net < en[0]:
                en = (net, d, tur)
        if en and en[0] < 25.0:
            cak.append(dict(x=f["xu"], y=f["yu"],
                            tur="FİLİZ ÇAKIŞIYOR" if en[0] < 0 else "FİLİZ SIKIŞIK",
                            aciklama=f"Ø{f['du']} filiz ile alttaki {en[2]} Ø{en[1]} arası net "
                                     f"{en[0]:.0f} mm" + (" (üst üste)" if en[0] < 0 else " < 25 mm")))

    def kesisir(p1, p2, p3, p4):
        def yon(a_, b_, c_):
            return (b_[0] - a_[0]) * (c_[1] - a_[1]) - (b_[1] - a_[1]) * (c_[0] - a_[0])
        d1, d2, d3, d4 = yon(p3, p4, p1), yon(p3, p4, p2), yon(p1, p2, p3), yon(p1, p2, p4)
        return d1 * d2 < -1e-9 and d3 * d4 < -1e-9
    for i in range(len(kirim)):
        a = kirim[i]
        for j in range(i + 1, len(kirim)):
            b = kirim[j]
            if abs(a["xl"] - b["xl"]) > 400 or abs(a["yl"] - b["yl"]) > 400:
                continue
            if kesisir((a["xl"], a["yl"]), (a["xu"], a["yu"]), (b["xl"], b["yl"]), (b["xu"], b["yu"])):
                cak.append(dict(x=(a["xu"] + b["xu"]) / 2, y=(a["yu"] + b["yu"]) / 2,
                                tur="KRANKLAR KESİŞİYOR",
                                aciklama=f"iki krankın yolu planda kesişiyor (e = {a['e']:.0f} ve "
                                         f"{b['e']:.0f} mm) – birini filize çevirin"))
        for b in biten:
            net = math.hypot(a["xu"] - b["x"], a["yu"] - b["y"]) - (a["dl"] + b["d"]) / 2
            if net < 25.0:
                cak.append(dict(x=a["xu"], y=a["yu"], tur="KRANK SIKIŞIK",
                                aciklama=f"krank ucu ile alttan biten Ø{b['d']} arası net {net:.0f} mm"
                                         + (" (üst üste)" if net < 0 else " < 25 mm")))
    ust = [(b["xu"], b["yu"], b["du"]) for b in duz + kirim] + [(f["xu"], f["yu"], f["du"]) for f in filiz]
    bmin, bsik = None, 0
    if len(ust) > 1:
        C = np.asarray(ust, float)
        iu, ju = np.triu_indices(len(ust), 1)
        net = np.hypot(C[iu, 0] - C[ju, 0], C[iu, 1] - C[ju, 1]) - (C[iu, 2] + C[ju, 2])
        bmin, bsik = float(net.min()), int((net < 25.0 - 1e-6).sum())
    gc.update(cakisma=cak, bind_net_min=bmin, bind_sik=bsik)
    gc["ozet"] = (f"{len(duz)} düz devam, {len(kirim)} krank (e ≤ {gc['e_max']:.0f} mm, en büyük "
                  f"{max([k['e'] for k in kirim], default=0):.0f}), {len(filiz)} filiz, alttan "
                  f"{len(biten)} donatı biter; {len(cak)} çakışma/uyarı")
    return gc


def gecisleri_hesapla(p, r, m):
    """Pier'in ardışık kat grupları arasındaki donatı aktarımı (alt grubun yerel ekseninde)."""
    out = []
    G = r["gruplar"]
    for gL, gU in zip(G[:-1], G[1:]):
        kL, kU = gL["_katlar"][-1], gU["_katlar"][0]
        if gL["tip"] == "dik":
            cL = kL.cg_ust if any(kL.cg_ust) else kL.cg_alt
            cU = kU.cg_alt if any(kU.cg_alt) else kU.cg_ust
            a = math.radians(kL.aci)
            dX, dY = cU[0] - cL[0], cU[1] - cL[1]
            boy, kal = dX * math.cos(a) + dY * math.sin(a), -dX * math.sin(a) + dY * math.cos(a)
            gc = gecis_analizi(gL, gU, boy, kal, gL.get("m") or m)
            gc["kat"] = f"{kL.kat} → {kU.kat}"
            gc["alt_konturlar"] = [[(0, 0), (gL["lw"], 0), (gL["lw"], gL["bw"]), (0, gL["bw"])]]
            gc["ust_konturlar"] = [gc["ust_kontur"]]
        else:
            gc = gecis_genel(gL, gU, gL.get("m") or m)
            gc["alt_konturlar"] = [[tuple(map(float, q)) for q in k.koseler()] for k in gL["kollar"]]
        gc.update(alt_etiket=gL["etiket"], ust_etiket=gU["etiket"], alt_katlar=gL["katlar"],
                  ust_katlar=gU["katlar"])
        out.append(_gecis_denetle(gc, gL.get("m") or m))
    return out


def filiz_dxf(p, r, gecisler, yol, m):
    """Pier'in bütün kat geçişleri tek DXF'te, alt alta: alt kesit (sürekli), üst kesit (kesikli),
    düz / krank / filiz / biten donatılar ve çakışma işaretleri + tablolar."""
    doc = _dxf_yeni()
    if "CAKISMA" not in doc.layers:
        doc.layers.add("CAKISMA", color=1)
    msp = doc.modelspace()
    oy = 0.0
    for gi, gc in enumerate(gecisler):
        P = [q for poly in gc["alt_konturlar"] + gc["ust_konturlar"] for q in poly]
        x0, y0 = min(q[0] for q in P), min(q[1] for q in P)
        x1, y1 = max(q[0] for q in P), max(q[1] for q in P)
        dx, dy = -x0, oy - y1

        def K(x, y):
            return (x + dx, y + dy)
        for poly in gc["alt_konturlar"]:
            msp.add_lwpolyline([K(*q) for q in poly], close=True, dxfattribs={"layer": "BETON"})
        for poly in gc["ust_konturlar"]:
            msp.add_lwpolyline([K(*q) for q in poly], close=True, dxfattribs={"layer": "UST_KESIT"})
        for b in gc["duz"]:
            msp.add_circle(K(b["xu"], b["yu"]), b["du"] / 2, dxfattribs={"layer": "FILIZ_DUZ"})
        for b in gc["kirim"]:
            msp.add_circle(K(b["xl"], b["yl"]), b["dl"] / 2, dxfattribs={"layer": "FILIZ_KIRIM"})
            msp.add_circle(K(b["xu"], b["yu"]), b["du"] / 2, dxfattribs={"layer": "FILIZ_KIRIM"})
            msp.add_line(K(b["xl"], b["yl"]), K(b["xu"], b["yu"]), dxfattribs={"layer": "FILIZ_KIRIM"})
        for f in gc["filiz"]:
            c_, r_ = K(f["xu"], f["yu"]), f["du"] / 2
            msp.add_circle(c_, r_, dxfattribs={"layer": "FILIZ_EKIM"})
            msp.add_line((c_[0] - 1.6 * r_, c_[1]), (c_[0] + 1.6 * r_, c_[1]), dxfattribs={"layer": "FILIZ_EKIM"})
            msp.add_line((c_[0], c_[1] - 1.6 * r_), (c_[0], c_[1] + 1.6 * r_), dxfattribs={"layer": "FILIZ_EKIM"})
        for b in gc["biten"]:
            c_, r_ = K(b["x"], b["y"]), b["d"] / 2
            msp.add_circle(c_, r_, dxfattribs={"layer": "BITEN_DONATI"})
            for sx in (1, -1):
                msp.add_line((c_[0] - 1.3 * r_, c_[1] - sx * 1.3 * r_), (c_[0] + 1.3 * r_, c_[1] + sx * 1.3 * r_),
                             dxfattribs={"layer": "BITEN_DONATI"})
        for i, c in enumerate(gc["cakisma"], 1):
            c_ = K(c["x"], c["y"])
            msp.add_circle(c_, 45.0, dxfattribs={"layer": "CAKISMA"})
            _mt(msp, str(i), (c_[0] + 55, c_[1] + 55), 40, "CAKISMA", 0.0, ek=7)
        _mt(msp, f"{p.ad}  KAT GEÇİŞİ {gc['kat']}  (alt {gc['alt_etiket']} → üst {gc['ust_etiket']})",
            (0, oy + 520), 110, "YAZI", 0.0, ek=7)
        _mt(msp, gc["ozet"], (0, oy + 330), 70, "YAZI", 0.0, ek=7)
        # tablolar (kesitin sağında)
        tx = (x1 - x0) + 900
        lb_ = {d: kenetlenme(d, m) for d in sorted({b["du"] for b in gc["duz"] + gc["kirim"]} |
                                                    {f["du"] for f in gc["filiz"]} |
                                                    {b["d"] for b in gc["biten"]})}
        T1 = [["İŞARET", "TÜR", "ADET", "AÇIKLAMA"],
              ["daire (yeşil)", "DÜZ DEVAM", len(gc["duz"]), "alt donatı aynı yerde üst kata çıkar, bindirmeli ek"],
              ["iki daire + çizgi", "KRANK", len(gc["kirim"]),
               f"alt donatı {AYAR['birlesim_yuksekligi']:.0f} mm döşeme/kiriş içinde ≤ 1/6 eğimle kırılır (e ≤ {gc['e_max']:.0f} mm)"],
              ["daire + artı", "FİLİZ", len(gc["filiz"]), "alt perdeye gömülen ayrı çubuk (gömme = lb), üstte bindirme l0"],
              ["daire + çarpı", "BİTEN", len(gc["biten"]), "alt donatı üstte devam etmez: döşemede lb kadar ya da 90° kancayla kenetlenir"],
              ["kırmızı daire + no", "ÇAKIŞMA", len(gc["cakisma"]), "aşağıdaki listede"]]
        _, ys = _dxf_tablo(msp, tx, oy + 200, T1, katman="KOL_TABLO", h=60.0)
        T2 = [["Ø", "lb [mm]", "bindirme l0 [mm]", "krank boyu ≥ [mm]"]] + \
             [[f"Ø{d}", f"{v[0]:.0f}", f"{v[1]:.0f}", f"{AYAR['birlesim_yuksekligi']:.0f}"] for d, v in lb_.items()]
        _, ys = _dxf_tablo(msp, tx, ys - 150, T2, katman="KOL_TABLO", h=60.0)
        if gc.get("bind_net_min") is not None:
            _mt(msp, f"Bindirme bölgesinde (çift çubuk) en küçük net aralık: {gc['bind_net_min']:.0f} mm"
                     + (f" – {gc['bind_sik']} çiftte < 25 mm: ekleri şaşırtın" if gc["bind_sik"] else ""),
                (tx, ys - 120), 60, "CAKISMA" if gc["bind_sik"] else "YAZI", 0.0, ek=7)
            ys -= 260
        if gc["cakisma"]:
            T3 = [["NO", "TÜR", "AÇIKLAMA"]] + [[i, c["tur"], c["aciklama"]] for i, c in
                                                 enumerate(gc["cakisma"][:40], 1)]
            _, ys = _dxf_tablo(msp, tx, ys - 100, T3, katman="CAKISMA", h=60.0)
        oy = min(oy - (y1 - y0) - 1400, ys - 900)
    doc.saveas(yol)
    return yol


def filiz_rapor(tum, yol, m):
    """tum: [(p, r, gecisler)] -> metin raporu + CSV özet + çubuk listesi."""
    A = AYAR
    L = ["KAT GEÇİŞLERİ – FİLİZ / KRANK / DONATI ÇAKIŞMALARI", "=" * 78,
         f"Sınıflandırma (üst grubun her boyuna donatısı, alt grubun planında):",
         f"  DÜZ    : alttaki donatının üstünde (kaçıklık ≤ {A['duz_tolerans']:.0f} mm) – düz devam, bindirmeli ek",
         f"  KRANK  : alt donatı döşeme/kiriş yüksekliği ({A['birlesim_yuksekligi']:.0f} mm) içinde ≤ 1/6 eğimle "
         f"kırılarak üst konuma gelir (kaçıklık ≤ {A['birlesim_yuksekligi'] * A['kirim_egim_max']:.0f} mm)",
         "  FİLİZ  : karşılayan alt donatı yok ya da üst çap daha büyük – ALT PERDE BETONU DÖKÜLMEDEN ÖNCE "
         "yerleştirilen, alt perdeye lb gömülen ayrı çubuk (sonradan ekilen/kimyasal ankraj DEĞİLDİR; filiz "
         "unutulur ve sonradan ekilecekse ankraj hesabı ayrıca yapılmalıdır)",
         "  BİTEN  : üstte karşılığı olmayan alt donatı – döşemede kenetlenerek biter",
         "Çakışma denetimi: filiz–alt donatı net aralığı (< 25 mm), kesişen krank yolları, krank ucu–biten "
         "donatı, kesit dışına düşen filiz, bindirme bölgesinde çift çubuklar arası net aralık (< 25 mm).",
         f"Bindirme l0 = {A['alfa1_bindirme']:g}·lb (TS 500 Denk. 9.2), lb = 0.12·fyd/fctd·Ø ≥ 20Ø.",
         "Ek seviyesi: bütün ekler döşeme üstünde aynı kesitte kabul edilmiştir (l0 buna göre). Şaşırtma "
         "isteniyorsa ya da bindirme bölgesinde net aralık yetmiyorsa çubukların yarısı döşeme üstünde, diğer "
         "yarısı en az 1.5·l0 yukarıda eklenir (TS 500: ek merkezleri arası ≥ 1.5·l0).",
         "KAPSAM DIŞI: bindirme boyunca sargı (etriye sıklaştırma) hesabı, temel içi sargı devamı ve boy kesit "
         "çizimi bu çıktıda yoktur; proje detayında ayrıca çözülmelidir.", ""]
    ozet = [["Pier", "Geçiş", "Alt grup", "Üst grup", "Düz", "Krank", "Krank e_max[mm]", "Filiz",
             "Biten", "Çakışma/uyarı", "Bindirme net min[mm]"]]
    liste = [["Pier", "Geçiş", "Tür", "x_alt", "y_alt", "Ø_alt", "x_üst", "y_üst", "Ø_üst", "e[mm]",
              "gömme lb[mm]", "bindirme l0[mm]", "not"]]
    for p, r, gecisler in tum:
        L.append(f"### {p.ad}")
        g0 = r["gruplar"][0]
        caplar = sorted({b[2] for b in list(g0["uc"]) + list(g0["govde"])})
        L.append(f"  Temel filizleri ({g0['etiket']}, {g0['katlar'][0]}): bütün boyuna donatı temelden filiz; "
                 + ", ".join(f"Ø{d}: lb = {kenetlenme(d, m)[0]:.0f}, l0 = {kenetlenme(d, m)[1]:.0f} mm" for d in caplar)
                 + (f" (temel yüksekliği {A['temel_yuksekligi']:.0f} mm)" if A.get("temel_yuksekligi") else
                    " – temelde düz kenetlenme sığmıyorsa 90° kanca"))
        for g in r["gruplar"]:
            if len(g["katlar"]) > 1:
                L.append(f"  {g['etiket']} içi katlar ({g['katlar'][0]} … {g['katlar'][-1]}): donatı düzeni aynı – "
                         f"hepsi düz devam, her katta bindirmeli ek (l0 ≤ {g['l_mev']:.0f} mm mevcut boy).")
        if not gecisler:
            L += ["  Kat grubu tek: grup geçişi yok.", ""]
            continue
        for gc in gecisler:
            L.append(f"  GEÇİŞ {gc['kat']}  ({gc['alt_etiket']} → {gc['ust_etiket']}): {gc['ozet']}")
            if gc["kirim"]:
                es = sorted(k["e"] for k in gc["kirim"])
                L.append(f"    Krank kaçıklıkları: en küçük {es[0]:.0f}, en büyük {es[-1]:.0f} mm "
                         f"(eğim 1/{A['birlesim_yuksekligi'] / max(es[-1], 1e-9):.1f})")
            if gc["filiz"]:
                cf = {}
                for f in gc["filiz"]:
                    cf[f["du"]] = cf.get(f["du"], 0) + 1
                L.append("    Filizler: " + ", ".join(
                    f"{n}Ø{d} (gömme {kenetlenme(d, m)[0] * A['filiz_gomulme_katsayi']:.0f}, bindirme "
                    f"{kenetlenme(d, m)[1]:.0f}, toplam boy ≈ {kenetlenme(d, m)[0] * A['filiz_gomulme_katsayi'] + A['birlesim_yuksekligi'] + kenetlenme(d, m)[1]:.0f} mm)"
                    for d, n in sorted(cf.items())))
                ned = {}
                for f in gc["filiz"]:
                    if f.get("neden"):
                        ned[f["neden"]] = ned.get(f["neden"], 0) + 1
                for k_, n in ned.items():
                    L.append(f"      {n} filiz: {k_}")
            if gc["biten"]:
                cb = {}
                for b in gc["biten"]:
                    cb[b["d"]] = cb.get(b["d"], 0) + 1
                L.append("    Biten alt donatılar: " + ", ".join(f"{n}Ø{d}" for d, n in sorted(cb.items()))
                         + " – döşeme içinde lb ya da 90° kanca.")
            if gc.get("bind_net_min") is not None:
                L.append(f"    Bindirme bölgesinde çift çubuklar arası en küçük net aralık: {gc['bind_net_min']:.0f} mm"
                         + (f" – {gc['bind_sik']} çiftte < 25 mm → ekleri şaşırtın (yarısı bir üst seviyede)."
                            if gc["bind_sik"] else " (uygun)."))
            for i, c in enumerate(gc["cakisma"], 1):
                L.append(f"    ÇAKIŞMA {i}: {c['tur']} – {c['aciklama']} [x={c['x']:.0f}, y={c['y']:.0f}]")
            ozet.append([p.ad, gc["kat"], gc["alt_etiket"], gc["ust_etiket"], len(gc["duz"]), len(gc["kirim"]),
                         f"{max([k['e'] for k in gc['kirim']], default=0):.0f}", len(gc["filiz"]),
                         len(gc["biten"]), len(gc["cakisma"]),
                         "" if gc.get("bind_net_min") is None else f"{gc['bind_net_min']:.0f}"])
            for b in gc["duz"]:
                liste.append([p.ad, gc["kat"], "DÜZ", f"{b['xl']:.0f}", f"{b['yl']:.0f}", b["dl"], f"{b['xu']:.0f}",
                              f"{b['yu']:.0f}", b["du"], f"{b['e']:.0f}", "", f"{kenetlenme(b['du'], m)[1]:.0f}", ""])
            for b in gc["kirim"]:
                liste.append([p.ad, gc["kat"], "KRANK", f"{b['xl']:.0f}", f"{b['yl']:.0f}", b["dl"], f"{b['xu']:.0f}",
                              f"{b['yu']:.0f}", b["du"], f"{b['e']:.0f}", "", f"{kenetlenme(b['du'], m)[1]:.0f}", ""])
            for f in gc["filiz"]:
                liste.append([p.ad, gc["kat"], "FİLİZ", "", "", "", f"{f['xu']:.0f}", f"{f['yu']:.0f}", f["du"], "",
                              f"{f['gomulme']:.0f}", f"{f['bindirme']:.0f}",
                              f.get("neden", "") + ("" if f.get("alt_kesitte", True) else " KESİT DIŞI")])
            for b in gc["biten"]:
                liste.append([p.ad, gc["kat"], "BİTEN", f"{b['x']:.0f}", f"{b['y']:.0f}", b["d"], "", "", "", "",
                              f"{kenetlenme(b['d'], m)[0]:.0f}", "", ""])
        L.append("")
    with open(yol, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    kl = os.path.dirname(yol)
    for ad_, T in (("filiz_ozet.csv", ozet), ("filiz_cubuk_listesi.csv", liste)):
        with open(os.path.join(kl, ad_), "w", newline="", encoding="utf-8-sig") as f:
            csv.writer(f, delimiter=";").writerows(T)


def bodrum_filiz_rapor(bodrumlar, yol, m):
    """Bodrum perdelerinde katlar arası düşey donatı uyumu (hasır düzeni: Ø/s, iki yüz)."""
    A = AYAR
    e_max = A["birlesim_yuksekligi"] * A["kirim_egim_max"]
    L = ["BODRUM PERDELERİ – KATLAR ARASI DÜŞEY DONATI (FİLİZ) UYUMU", "=" * 78,
         "Düşey donatı her yüzde Ø/s düzenindedir. Üst kat donatısı alt katın donatısıyla aynı aralıkta "
         "(ya da katı aralıkta) ise düz bindirme yapılır; değilse alt kattan üst katın düzeninde filiz bırakılır.",
         f"Kalınlık değişiminde yüz kaçıklığı ≤ {e_max:.0f} mm ise krank (≤ 1/6), fazlaysa filiz.", ""]
    ozet = [["Pier", "Geçiş", "Yüz", "Alt", "Üst", "Durum"]]
    for tip, p, r in bodrumlar:
        L.append(f"### {p.ad}")
        S = r["satirlar"]
        if S:
            q = S[0]
            L.append(f"  Temel filizleri ({q['kat']}): +3 yüz {_bd_txt(q, 'düşey', '+3')}, −3 yüz "
                     f"{_bd_txt(q, 'düşey', '−3')}; lb = {kenetlenme(q[('düşey', '+3')]['d'], m)[0]:.0f} / "
                     f"{kenetlenme(q[('düşey', '−3')]['d'], m)[0]:.0f} mm, bindirme l0 = "
                     f"{kenetlenme(q[('düşey', '+3')]['d'], m)[1]:.0f} / {kenetlenme(q[('düşey', '−3')]['d'], m)[1]:.0f} mm")
        for a, b in zip(S[:-1], S[1:]):
            dt = a["t"] - b["t"]
            for yuz in ("+3", "−3"):
                qa, qb = a[("düşey", yuz)], b[("düşey", yuz)]
                lb, l0 = kenetlenme(qb["d"], m)
                oran = qb["s"] / qa["s"]
                if abs(dt) > 1 and abs(dt) / 2 > e_max:
                    durum = (f"FİLİZ: kalınlık {a['t']:.0f}→{b['t']:.0f} mm (yüz kaçıklığı {abs(dt) / 2:.0f} > "
                             f"{e_max:.0f} mm, eksenler ortalı kabul) – üst donatı Ø{qb['d']}/{qb['s']:.0f} için filiz, "
                             f"gömme {lb:.0f}, bindirme {l0:.0f} mm")
                elif qb["d"] > qa["d"]:
                    durum = (f"FİLİZ: üst çap büyük (Ø{qa['d']}→Ø{qb['d']}) – Ø{qb['d']}/{qb['s']:.0f} filiz, "
                             f"gömme {lb:.0f}, bindirme {l0:.0f} mm")
                elif abs(oran - round(oran)) < 1e-6 and oran >= 1:
                    durum = ("DÜZ DEVAM" + ("" if round(oran) == 1 else f": alt donatının her {round(oran)}. çubuğu devam eder, diğerleri döşemede biter")
                             + (f"; kalınlık değişimi için krank e = {abs(dt) / 2:.0f} mm" if abs(dt) > 1 else "")
                             + f"; bindirme l0 = {l0:.0f} mm")
                else:
                    durum = (f"FİLİZ: aralık uyuşmuyor ({qa['s']:.0f}→{qb['s']:.0f} mm) – üst donatı Ø{qb['d']}/{qb['s']:.0f} "
                             f"için alt kattan filiz, gömme {lb:.0f}, bindirme {l0:.0f} mm; alt donatı döşemede biter")
                L.append(f"  {a['kat']} → {b['kat']}  yüz {yuz}: Ø{qa['d']}/{qa['s']:.0f} → Ø{qb['d']}/{qb['s']:.0f}  {durum}")
                ozet.append([p.ad, f"{a['kat']} → {b['kat']}", yuz, f"Ø{qa['d']}/{qa['s']:.0f}",
                             f"Ø{qb['d']}/{qb['s']:.0f}", durum])
        L.append("")
    with open(yol, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    with open(os.path.join(os.path.dirname(yol), "bodrum_filiz_ozet.csv"), "w", newline="",
              encoding="utf-8-sig") as f:
        csv.writer(f, delimiter=";").writerows(ozet)


def _dosya_adi(ad):
    return "".join(ch if ch.isalnum() or ch in "-_ ." else "_" for ch in str(ad)).strip() or "model"


def cikti_klasorleri(kok):
    """<kok>/Perdeler/{Doneler, Filiz_Cakismalari} ve <kok>/Bodrum_Perdeleri/{...}"""
    return dict(kok=kok,
                p_done=os.path.join(kok, "Perdeler", "Doneler"),
                p_filiz=os.path.join(kok, "Perdeler", "Filiz_Cakismalari"),
                b_done=os.path.join(kok, "Bodrum_Perdeleri", "Doneler"),
                b_filiz=os.path.join(kok, "Bodrum_Perdeleri", "Filiz_Cakismalari"))


def calistir_sade(pierler):
    m = malzeme()
    AYAR["cikti_klasoru"] = AYAR.get("cikti_klasoru") or _dosya_adi(MODEL_ADI or "perde_cikti")
    kl = AYAR["cikti_klasoru"]
    K = cikti_klasorleri(kl)
    os.makedirs(kl, exist_ok=True)
    t_bas = time.time()
    sonuclar = []

    def hata(baslik, tb):
        with open(os.path.join(kl, "hata_log.txt"), "a", encoding="utf-8") as f:
            f.write(f"\n=== {baslik} ===\n{tb}")

    for i, p in enumerate(pierler, 1):
        t0 = time.time()
        print(f"\n>>> [{i}/{len(pierler)}] {p.ad} ({len(p.katlar)} kat, "
              f"{sum(len(k.kuvvetler) for k in p.katlar)} kuvvet satırı)...", flush=True)
        try:
            if not p.katlar or not any(k.kuvvetler for k in p.katlar):
                print("  kuvvet yok, atlandı")
                continue
            if p.bodrum:
                r = bodrum_tasarla(p, m)
                sonuclar.append(("bodrum", p, r))
            elif p.tip == "cok":
                r = pier_sade_cok(p, m)
                sonuclar.append(("cok", p, r))
            else:
                r = pier_sade_dik(p, m)
                sonuclar.append(("dik", p, r))
            if not p.bodrum:
                os.makedirs(K["p_done"], exist_ok=True)
                sd_dxf_sade(r, K["p_done"])
                for g in r["gruplar"]:
                    if g["tip"] == "dik":
                        bt = (f"TEK BAŞLIK {g['secim']['et']}" if g.get("tek") else
                              f"Lu={g['Lu']:.0f} {g['secim']['et']}")
                    else:
                        bt = " ".join(f"B{z['bolge']}:{z['secim']['et'] if z['secim'] else '-'}"
                                      for z in g["bolgeler"])
                    print(f"  {g['etiket']} {bolge_adi_sade(g):<12} {g['katlar'][0]}-{g['katlar'][-1]}: "
                          f"{bt} | etr " + (f"Ø{g['etr']['d']}/{g['etr']['s']:.0f}" if g["etr"] else "-")
                          + (f" kol {g['etr']['n_y']}/{g['etr']['n_x']}" if g["tip"] == "dik" else "")
                          + f" | Md/Mr={g['Md_Mr']:.2f} | {_durum(g)}", flush=True)
            print(f"<<< {p.ad} bitti ({time.time() - t0:.1f} s)", flush=True)
        except Exception:
            tb = traceback.format_exc()
            print(f"!!! {p.ad} HATA – ayrıntı hata_log.txt\n{tb.splitlines()[-1]}", flush=True)
            hata(p.ad, tb)
    perde = [s for s in sonuclar if s[0] != "bodrum"]
    bodrum = [s for s in sonuclar if s[0] == "bodrum"]
    if perde:
        cak = []
        try:
            cak = baslik_cakisma_denetimi(perde)
            with open(os.path.join(K["p_done"], "baslik_cakismalari.txt"), "w", encoding="utf-8") as f:
                f.write("\n".join(cakisma_metni(cak)))
            n_c = len({(c["tur"], c["a"], c["b"]) for c in cak})
            print(f"\nBaşlık çakışma denetimi: farklı pier'ler arasında {n_c} çakışma"
                  + (" – ayrıntı baslik_cakismalari.txt ve KAT DXF'lerinde CAKISMA katmanı" if n_c else ""))
        except Exception:
            hata("başlık çakışma denetimi", traceback.format_exc())
        try:
            kp = kat_dxf_sade(perde, K["p_done"], cak)
            print(f"{len(kp)} kat DXF'i (perdeler): " + ", ".join(os.path.basename(x) for x in kp))
        except Exception:
            tb = traceback.format_exc()
            print("!!! kat DXF HATA\n" + tb.splitlines()[-1])
            hata("kat DXF", tb)
        rapor_sade(perde, os.path.join(K["p_done"], "perde_rapor.txt"), cak)
        try:
            izleme_csv(perde, os.path.join(K["p_done"], "talep_izleme.csv"))
        except Exception:
            hata("talep izleme", traceback.format_exc())
        # kat geçişleri: filiz / krank / çakışma
        os.makedirs(K["p_filiz"], exist_ok=True)
        tum = []
        for tip, p, r in perde:
            try:
                gec = gecisleri_hesapla(p, r, m)
                tum.append((p, r, gec))
                if gec:
                    filiz_dxf(p, r, gec, os.path.join(K["p_filiz"], f"{_dosya_adi(p.ad)}_filiz.dxf"), m)
            except Exception:
                tb = traceback.format_exc()
                print(f"!!! {p.ad} filiz/krank çıktısı HATA\n" + tb.splitlines()[-1])
                hata(f"{p.ad} / filiz", tb)
        try:
            filiz_rapor(tum, os.path.join(K["p_filiz"], "filiz_rapor.txt"), m)
            n_cak = sum(len(gc["cakisma"]) for _, _, g_ in tum for gc in g_)
            print(f"Kat geçişleri: {sum(len(g_) for _, _, g_ in tum)} geçiş, {n_cak} donatı çakışması/uyarısı "
                  f"(Perdeler/Filiz_Cakismalari)")
        except Exception:
            hata("filiz raporu", traceback.format_exc())
    if bodrum:
        os.makedirs(K["b_done"], exist_ok=True)
        os.makedirs(K["b_filiz"], exist_ok=True)
        try:
            kp = kat_dxf_sade(bodrum, K["b_done"])
            print(f"{len(kp)} kat DXF'i (bodrum perdeleri)")
            rapor_sade(bodrum, os.path.join(K["b_done"], "bodrum_rapor.txt"))
            bodrum_filiz_rapor(bodrum, os.path.join(K["b_filiz"], "bodrum_filiz_rapor.txt"), m)
        except Exception:
            tb = traceback.format_exc()
            print("!!! bodrum çıktıları HATA\n" + tb.splitlines()[-1])
            hata("bodrum çıktıları", tb)
    print(f"Çıktılar: {os.path.abspath(kl)}")
    print("  Perdeler/Doneler            : KAT_*.dxf, perde_rapor.txt, talep_izleme.csv, baslik_cakismalari.txt, SectionDesigner/")
    print("  Perdeler/Filiz_Cakismalari  : <PIER>_filiz.dxf, filiz_rapor.txt, filiz_ozet.csv, filiz_cubuk_listesi.csv")
    if bodrum:
        print("  Bodrum_Perdeleri/Doneler, Bodrum_Perdeleri/Filiz_Cakismalari")
    print(f"Toplam süre: {time.time() - t_bas:.0f} s", flush=True)
    return sonuclar


def calistir(pierler):
    if str(AYAR.get("cikti_modu", "sade")).lower() == "sade":
        return calistir_sade(pierler)
    AYAR["cikti_klasoru"] = AYAR.get("cikti_klasoru") or (MODEL_ADI or "perde_cikti")
    m = malzeme()
    kl = AYAR["cikti_klasoru"]
    os.makedirs(kl, exist_ok=True)
    ozet = []
    sonuclar = []
    t_bas = time.time()
    for i, p in enumerate(pierler, 1):
        t0 = time.time()
        print(f"\n>>> [{i}/{len(pierler)}] {p.ad} tasarlanıyor ({len(p.katlar)} kat, "
              f"{sum(len(k.kuvvetler) for k in p.katlar)} kuvvet satırı)...", flush=True)
        try:
            r_ = _pier_isle(p, m, kl, ozet)
            if r_:
                sonuclar.append(r_)
            print(f"<<< {p.ad} bitti ({time.time() - t0:.1f} s)", flush=True)
        except Exception:
            tb = traceback.format_exc()
            print(f"!!! {p.ad} tasarımında HATA – ayrıntı {kl}/hata_log.txt; diğer pier'lere "
                  f"devam ediliyor.\n{tb.splitlines()[-1]}", flush=True)
            with open(os.path.join(kl, "hata_log.txt"), "a", encoding="utf-8") as f:
                f.write(f"\n=== {p.ad} / tasarım ===\n{tb}")
            ozet.append([p.ad, "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "",
                         "HATA"])
    with open(os.path.join(kl, "perde_ozet.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Pier", "Grup", "Bölge", "Katlar", "lw[mm]", "bw[mm]", "Hw[m]", "Hcr[m]",
                    "Lu[mm]", "Uç donatı(her uç)", "As_uç[mm²]", "Etriye", "Gövde düşey",
                    "Gövde yatay", "Md/Mr", "Vd[kN]", "Ve[kN]", "Vr[kN]", "Durum"])
        w.writerows(ozet)
    if AYAR.get("kat_plani", True) and sonuclar:
        print("Kat planları çiziliyor (her kat: bütün pier'ler tek planda)...", flush=True)
        try:
            kp = kat_plani_ciz(sonuclar, kl)
            print(f"  {len(kp)} kat planı: " + ", ".join(os.path.basename(x) for x in kp), flush=True)
        except Exception:
            tb = traceback.format_exc()
            print("!!! kat planı çiziminde HATA – ayrıntı hata_log.txt\n" + tb.splitlines()[-1])
            with open(os.path.join(kl, "hata_log.txt"), "a", encoding="utf-8") as f:
                f.write(f"\n=== kat planı ===\n{tb}")
    print(f"\nÇıktılar: {os.path.abspath(kl)}")


    print(f"Toplam süre: {time.time() - t_bas:.0f} s", flush=True)


if __name__ == "__main__":
    for _akis in (sys.stdout, sys.stderr):
        try:
            _akis.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="ETABS pier -> TBDY 2018 perde donatı planı")
    ap.add_argument("--demo", action="store_true", help="örnek veriyle çalıştır")
    ap.add_argument("--piers", nargs="*", help="pier adları (verilmezse ETABS seçimi)")
    ap.add_argument("--tum", action="store_true", help="modeldeki tüm pier'ler")
    ap.add_argument("--bodrum", nargs="*", default=[], help="bodrum (toprak) perdesi pier adları")
    ap.add_argument("--fck", type=float)
    ap.add_argument("--fyk", type=float)
    ap.add_argument("--cikti", help="çıktı klasörü")
    ap.add_argument("--D", type=float, help="dayanım fazlalığı katsayısı D (kesme: Ve = 1.2·D·Vd)")
    ap.add_argument("--pm", help="P–M kombinasyonları (virgülle ad/ad parçası), ör. ENV-DEP")
    ap.add_argument("--kesme", help="kesme kombinasyonları, ör. ENV-DEP-1.2D")
    ap.add_argument("--sorma", action="store_true", help="kombinasyon sormadan öneri/son seçim")
    ap.add_argument("--sistem", choices=["bosluksuz", "bag_kirisli"],
                    help="perde sistemi: boşluksuz (1.2D, 0.85) / bağ kirişli (1.4D, 0.65)")
    ap.add_argument("--bag_kirisli", nargs="*", help="yalnız bu pier'ler bağ kirişli sayılır")
    ap.add_argument("--suneklik", choices=["yuksek", "sinirli"], help="süneklik düzeyi")
    ap.add_argument("--zemin_kat", help="rijit bodrumlu binada zemin katın adı (Hw, Hcr buradan ölçülür)")
    ap.add_argument("--etabs_yaz", choices=["e", "h"],
                    help="tasarım sonunda donatıyı ETABS'e Section Designer pier kesiti olarak yaz (e) / yazma (h)")
    ap.add_argument("--buyutulmus", choices=["e", "h"],
                    help="kesme kombinasyonları 1.2·D ile zaten büyütülmüş mü (e: Ve=Vd, h: Ve=1.2·D·Vd)")
    a = ap.parse_args()
    if a.fck:
        AYAR["fck"] = a.fck
        AYAR["malzeme_etabs"] = False
    if a.sistem:
        AYAR["perde_sistemi"] = a.sistem
    if a.bag_kirisli:
        AYAR["bag_kirisli_pierler"] = list(a.bag_kirisli)
    if a.suneklik:
        AYAR["suneklik"] = a.suneklik
    if a.zemin_kat:
        AYAR["kritik_baslangic_kat"] = a.zemin_kat
    if a.fyk:
        AYAR["fyk"] = AYAR["fywk"] = a.fyk
    if a.cikti:
        AYAR["cikti_klasoru"] = a.cikti
    if a.D:
        AYAR["D"] = a.D
    if a.sorma:
        AYAR["kombinasyon_sor"] = False
    if a.buyutulmus:
        AYAR["kesme_komb_buyutulmus"] = a.buyutulmus == "e"
    if a.pm:
        AYAR["kombinasyonlar"] = ["§" + t.strip() for t in a.pm.split(",") if t.strip()]
    if a.kesme:
        AYAR["kesme_kombinasyonlari"] = ["§" + t.strip() for t in a.kesme.split(",") if t.strip()]
    try:
        if a.demo:
            MODEL_ADI = "DEMO_MODEL"
        veri = ornek_veri() if a.demo else etabs_oku(a.piers, a.tum, a.bodrum)
    except Exception as e:
        print("HATA (ETABS okuma):", e)
        traceback.print_exc()
        sys.exit(1)
    sonuc_ = calistir(veri)
    if a.etabs_yaz:
        AYAR["etabs_yaz"] = a.etabs_yaz == "e"
    if not a.demo and sonuc_ and str(AYAR.get("cikti_modu", "sade")).lower() == "sade" \
            and AYAR.get("etabs_yaz", "sor"):
        etabs_sd_yaz(sonuc_, onay=True if AYAR["etabs_yaz"] is True else None)
