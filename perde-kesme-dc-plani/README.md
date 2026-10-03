# Perde kesme kuvveti ve d/c plan görseli

Açık ETABS modelinden seçilen kattaki pier kesmelerini okur, planda
renklendirilmiş bir d/c görseli ve Excel tablosu üretir.

Verdiğiniz adın load case mi, pattern mı yoksa kombinasyon mu olduğunu kendisi
bulur; gerekli analizin yapılıp yapılmadığını kontrol eder (`--run` verilirse
analizi başlatır); çıktı seçimini ETABS arayüzündeki seçimlerden bağımsız olarak
kendisi yapar.

### Kullanım
```
python etabs_pier_dc.py --kat "Kat3" --durum "EX"
```

### Kurulum
```
pip install comtypes pandas openpyxl matplotlib
```

> **Sorumluluk reddi.** Bu araçlar mühendislik yardımcısıdır; hesap, tasarım ve
> kontrol sorumluluğunu üstlenmez. Üretilen sonuçlar, ilgili yönetmelik ve proje
> koşullarına göre kullanıcı tarafından doğrulanmadan hiçbir projede
> kullanılmamalıdır. Yazar, kullanımdan doğabilecek hiçbir zarardan sorumlu
> tutulamaz.

