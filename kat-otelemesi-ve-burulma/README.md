# Kat ötelemesi ve burulma düzensizliği kontrolü

Açık olan ETABS modeline bağlanır, TBDY 2018'e göre etkin göreli kat ötelemesi
(`drift_kontrol.py`) ve A1 burulma düzensizliği (`burulma_kontrol.py`) kontrolünü
yapar, sonucu Excel'e yazar.

Ayırt edici yanı, kat ötelemelerini ETABS'ın `Story Drifts` tablosundan değil,
**nokta yer değiştirmelerinden** hesaplamasıdır. Üst kattaki her nokta için alt kat
yer değiştirmesi, alt katta aynı konumda nokta varsa doğrudan oradan; yoksa (plan
küçülmesi, aks kayması, çıkma) alt katın rijit cisim hareketinden en küçük kareler
uyumuyla alınır. Böylece her katın uç noktaları o katın kendi planından belirlenir.

### Kullanım
1. ETABS'ta model açık ve analiz yapılmış olsun.
2. Dosyanın başındaki `KULLANICI GİRDİLERİ` bölümünü doldurun.
3. `python drift_kontrol.py` veya `python burulma_kontrol.py`

Üç dosya da aynı klasörde olmalıdır; `etabs_ortak.py` ikisinin ortak modülüdür.
Çıktı: `drift_kontrol.xlsx` / `burulma_kontrol.xlsx` (Özet, Kat detayı, Nokta
detayı, Uyarılar sayfaları).

### Kurulum
```
pip install comtypes numpy pandas openpyxl
```

> **Sorumluluk reddi.** Bu araçlar mühendislik yardımcısıdır; hesap, tasarım ve
> kontrol sorumluluğunu üstlenmez. Üretilen sonuçlar, ilgili yönetmelik ve proje
> koşullarına göre kullanıcı tarafından doğrulanmadan hiçbir projede
> kullanılmamalıdır. Yazar, kullanımdan doğabilecek hiçbir zarardan sorumlu
> tutulamaz.

