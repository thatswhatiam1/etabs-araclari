# ETABS Araçları

Yapı ve deprem mühendisliği işlerinde kendi ihtiyacım için yazdığım, ETABS ile
çalışan Python araçları. Her klasör kendi başına çalışır ve kendi açıklamasını
içerir.

| Araç | Ne yapar |
|---|---|
| [`kat-otelemesi-ve-burulma`](kat-otelemesi-ve-burulma/) | TBDY 2018 etkin göreli kat ötelemesi ve A1 burulma düzensizliği kontrolü, nokta yer değiştirmelerinden |
| [`perde-kesme-dc-plani`](perde-kesme-dc-plani/) | Pier kesme kuvvetlerinin kat planında renkli d/c görseli |
| [`perde-donatisi-tbdy`](perde-donatisi-tbdy/) | TBDY 2018 Bölüm 7.6'ya göre perde uç bölgesi ve donatı tasarımı, DXF çıktısı |
| [`deprem-kaydi-donusturucu`](deprem-kaydi-donusturucu/) | AFAD, K-NET, PEER NGA ve Yeni Zelanda ivme kayıtlarını ortak formata çevirir |

Derlenmiş programlar (ETABS eklentileri) [Releases](../../releases) bölümünde.

## Gereksinimler

Python 3.9+. ETABS'a bağlanan araçlar Windows'ta, ETABS kurulu bir makinede ve
model açıkken çalışır. **Desteklenen ETABS sürümleri: 22 ve 23.** Bağlantı
ETABSv1 API üzerinden kurulur; daha eski sürümlerde denenmemiştir. Her aracın gerektirdiği kütüphaneler kendi klasöründeki
açıklamada yazılıdır.

> **Sorumluluk reddi.** Bu araçlar mühendislik yardımcısıdır; hesap, tasarım ve
> kontrol sorumluluğunu üstlenmez. Üretilen sonuçlar, ilgili yönetmelik ve proje
> koşullarına göre kullanıcı tarafından doğrulanmadan hiçbir projede
> kullanılmamalıdır. Yazar, kullanımdan doğabilecek hiçbir zarardan sorumlu
> tutulamaz.

## Lisans

MIT. Ayrıntı için [LICENSE](LICENSE).
