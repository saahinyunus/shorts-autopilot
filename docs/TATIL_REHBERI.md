# Tatil rehberi (sistem siz yokken)

## Kendi kendine yaptıkları
- Her gün iki kez (03:23 ve 07:23 UTC) videoları planlar, üretir, YouTube'a zamanlı yükler. Günde kanal başı 2 video: 08:00 ve 19:00 New York saati.
- Bir şey ters giderse kendini toparlar: üretimi 3 kez dener, yapay zekâ sağlayıcısını değiştirir, hazır yedek senaryoya düşer, ikinci günlük çalıştırma kaçan slotu kurtarmaya çalışır.
- Yayına giren her videonun altına ilk yorumu yazar, her gece 00:00'da (TSİ) Telegram'a günlük özet gönderir.
- GitHub çalışmasa bile **Cloudflare üzerindeki bekçi** her 10 dakikada kontrol eder ve sorun varsa Telegram'dan 🚨 uyarı yollar.

## Normal günde Telegram'da göreceğiniz mesajlar
Plan, her video için "Video yüklendi ve planlandı", "Günlük çalıştırma tamamlandı" (günde iki kez), videoların yayını sonrası "İlk yorum yazıldı", gece "Günlük özet". Bunlar normal, bir şey yapmanız gerekmez.

## 🚨 Uyarı gelirse
| Uyarı | Anlamı | Ne yapılır |
|---|---|---|
| "slotu için video planlanmamış" | Otopilot çalışmadı ya da başarısız oldu | GitHub uygulaması → repo → Actions → **Daily Shorts autopilot** → **Run workflow**: *Dry run işaretini kaldırın*, *Lead hours* = `1`. Sonuç birkaç dakika içinde Telegram'a düşer. |
| "yayın saati geçti ama video herkese açık değil" | YouTube videoyu tutmuş ya da kilitlemiş olabilir | YouTube Studio → İçerik'ten videoya bakın, gerekirse elle yayınlayın. |
| "YouTube girişi çalışmıyor" | Token geçersiz (parola değişimi, izin geri alındı) | Bilgisayardan `python engine/youtube_auth.py login --channel money` (ve psychology), sonra yeni token'ı GitHub ve Cloudflare secret'larına yazın. Claude'dan yardım isteyebilirsiniz. |
| "son kalp atışı ... saat önce" | GitHub Actions çalışmıyor | Actions sayfasında workflow'un devre dışı kalıp kalmadığına bakın (**Enable workflow**). |
| 🔴 "Günlük çalıştırmada sorun var" | Bir adım başarısız oldu | Mesajdaki bağlantıdan çalıştırmaya bakın. Çoğunlukla bir sonraki çalıştırma kendini düzeltir. |

## Acil durdurma
- **Yeni yüklemeleri durdurmak:** GitHub uygulaması → Actions → **Daily Shorts autopilot** → ⋯ → **Disable workflow**. (Bekçi bu durumda boş slot uyarıları göndermeye devam eder, normaldir.)
- **Zamanlanmış bir videoyu durdurmak:** YouTube Studio → videoyu seçin → Görünürlük'ü "Gizli"ye çevirin, zamanlamayı kaldırın.
- **Yayındaki videoyu gizlemek:** Studio → Görünürlük → Gizli.

## Dikkat
- Claude yalnızca siz yazdığınızda çalışır, kendiliğinden uyanıp kontrol edemez. Bekçi bu yüzden vardır. Önemli bir 🚨 gelirse Claude'a yazın.
- Tatil boyunca kodda değişiklik yapılmaz (kod dondurma), çünkü her değişiklik yeni bir risktir.
- GitHub'daki 4 eski "Autopilot problem" issue'su eski hatalardır, yok sayın. Yeni bir issue açılırsa gerçek bir sorun var demektir.
