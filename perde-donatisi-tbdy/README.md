# TBDY 2018 perde uç bölgesi ve donatı tasarımı

ETABS'teki pier'lerin kuvvet ve geometrisini okur, TBDY 2018 Bölüm 7.6'ya göre
perde uç (başlık) bölgelerini ve donatıyı tasarlar; plan donatı çizimini (DXF),
metrajı ve açılımları üretir.

Kapsam:
- Dikdörtgen perde: lif modeli ile P–M3, uçta küme yerleşimi
- Çok kollu perde (U/H/L/T): alan elemanlarından kesit, 2B lif ağı ile P–M2–M3
- Bodrum (toprak) perdesi: shell M11/M22/M12, Wood–Armer ile metre şerit tasarımı
- Perdeye bağlanan kirişlerin bölgeleri ve kat geçişleri (düz / kırım / filiz)

Bu, depodaki en kapsamlı ve en dikkatli kullanılması gereken araçtır. Çıktısı
doğrudan projeye aktarılacak bir donatı tasarımı değil, kontrol edilecek bir
öneridir.

### Kullanım
```
python perde_tbdy.py --kat "Kat3"
python perde_tbdy.py --bodrum
```

### Kurulum
```
pip install comtypes numpy matplotlib ezdxf
```

> **Sorumluluk reddi.** Bu araçlar mühendislik yardımcısıdır; hesap, tasarım ve
> kontrol sorumluluğunu üstlenmez. Üretilen sonuçlar, ilgili yönetmelik ve proje
> koşullarına göre kullanıcı tarafından doğrulanmadan hiçbir projede
> kullanılmamalıdır. Yazar, kullanımdan doğabilecek hiçbir zarardan sorumlu
> tutulamaz.

