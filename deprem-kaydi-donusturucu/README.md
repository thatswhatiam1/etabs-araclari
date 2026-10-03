# Deprem kaydı dönüştürücü

Farklı kurumların ivme kaydı formatlarını ortak bir metin dosyasına çevirir.
Arayüzü vardır, komut satırı gerektirmez.

Desteklenen formatlar:

| Kaynak | Uzantı |
|---|---|
| AFAD / TADAS | `.asc` |
| K-NET (Japonya) | `.EW` `.NS` `.UD` |
| PEER NGA | `.AT2` |
| Yeni Zelanda | `.V2A` |

Çıktı: zaman (s) ve ivme (mm/s²) sütunlu `.txt` dosyaları. Format tespiti
otomatiktir. Yalnız standart kütüphane kullanır, kurulum gerektirmez.

### Kullanım
```
python deprem_donusturucu.py
```

> **Sorumluluk reddi.** Bu araçlar mühendislik yardımcısıdır; hesap, tasarım ve
> kontrol sorumluluğunu üstlenmez. Üretilen sonuçlar, ilgili yönetmelik ve proje
> koşullarına göre kullanıcı tarafından doğrulanmadan hiçbir projede
> kullanılmamalıdır. Yazar, kullanımdan doğabilecek hiçbir zarardan sorumlu
> tutulamaz.

