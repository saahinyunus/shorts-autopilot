# Otopilot kurulumu (GitHub Actions)

Hedef: siz yokken sistem her gün **2 kanal x 3 video = 6 Shorts** üretsin, SEO'yu ayarlasın, YouTube'a
**zamanlanmış** olarak yüklesin (önce private, belirlenen saatte YouTube kendisi yayına alır).

```
her gün 03:23 UTC
  plan    -> her kanalın önümüzdeki ~28 saatlik slotlarını bulur + her slota yeni konu seçer (Gemini)
  produce -> slot başına 1 iş, paralel: script -> ses -> altyazı zamanlaması -> render -> YouTube'a yükleme
  record  -> yüklenenleri state/history.json'a yazar (aynı konu/slot bir daha seçilmez) ve commit'ler
  alert   -> bir şey patlarsa GitHub'da issue açar (size e-posta gelir)
```

Slot saatleri `channels/*.json` içinde, New York saatine göre: **08:00, 13:00, 19:00**.

---

## 0. Başka bir YouTube otomasyonunuz varsa: çakışma olur mu?

Doğru kurulursa **çakışmaz**. Üç ayrı şey var:

| | Çakışır mı? | Ne yapmalı |
|---|---|---|
| **Google Cloud projesi / YouTube API kotası** | Aynı projeyi kullanırsanız **kota ortak** olur (günde 100 yükleme, 10.000 birim) ve biri diğerini tüketir. | Bu otomasyon için **yeni, ayrı bir proje** açın. Eski otomasyonun projesine dokunmayın. |
| **Giriş (OAuth) anahtarları** | Her proje/istemci kendi anahtarını üretir. Yeni projede giriş yapmak eskisini **bozmaz**. (Sadece *aynı* istemci kimliğiyle hesap başına 100'den fazla giriş yaparsanız en eskisi iptal olur.) | Ayrı proje = ayrı istemci = sorun yok. |
| **Aynı YouTube kanalına iki otomasyon** | Sorun **kanalda** başlar: iki sistem aynı saatlerde yüklerse ya da aynı konuyu işlerse hem tekrar olur hem YouTube bunu spam gibi görebilir. | Mümkünse **farklı kanallar** kullanın. Aynı kanalı paylaşacaksanız eski videoların başlıklarını `channels/<kanal>.json` içindeki `already_covered` listesine yazın (aşağıya bakın). |

Gemini anahtarı da ayrı olduğu için (başka hesaptan aldınız) oradan da çakışma yok.

## 1. Google Cloud ayarı (bir kez, adım adım)

**Hangi Google hesabıyla?** Proje hangi hesapta açılırsa açılsın fark etmez: bu sadece "uygulamanın
tanımıdır". Videoları hangi hesap/kanal yükleyecekse, 2. adımda **o hesapla giriş yaparsınız**. Eski
otomasyonunuzun kurulu olduğu Gmail ile de açabilirsiniz; **yeter ki yeni bir proje olsun**.

1. <https://console.cloud.google.com> adresine gidin, istediğiniz Google hesabıyla giriş yapın.
2. Sayfanın üstündeki proje seçiciye tıklayın -> **New project** -> ad: `shorts-autopilot` -> **Create**.
   Sonra aynı yerden bu yeni projeyi **seçili** hale getirin (başlıkta adı görünmeli).
3. Sol üst menü (☰) -> **APIs & Services -> Library** -> arama kutusuna `YouTube Data API v3` yazın ->
   sonuca tıklayın -> **Enable**.
4. Sol menü (☰) -> **Google Auth platform** (bulamazsanız arama çubuğuna "Google Auth platform" yazın).
   İlk kez açılıyorsa **Get started** çıkar:
   - **App information**: App name `shorts-autopilot`, User support email = kendi e-postanız -> Next.
   - **Audience**: **External** seçin -> Next.
   - **Contact information**: e-postanız -> Next.
   - Sözleşme kutusunu işaretleyin -> **Continue -> Create**.
5. Aynı menüde **Data Access** -> **Add or remove scopes** -> filtre kutusuna `youtube.upload` yazın ->
   `.../auth/youtube.upload` satırını işaretleyin -> **Update -> Save**.
6. **ÇOK ÖNEMLİ - Audience** sayfası -> "Publishing status" bölümünde **Publish app** (Yayınla) -> onaylayın.
   Durum **"In production"** olmalı. "Testing" kalırsa Google yenileme anahtarını **7 günde iptal eder** ve
   otopilot bir hafta sonra sessizce durur. (Doğrulanmamış uygulama uyarısı normaldir; kendi kullanımınız için
   sorun değildir, yalnızca kullanıcı sınırı 100'dür.)
7. **Clients** -> **Create client** -> Application type: **Desktop app** -> ad: `shorts-desktop` -> **Create**
   -> çıkan pencerede **Download JSON**. Dosyayı proje klasörüne **`client_secret.json`** adıyla koyun
   (git'e girmez, kimseyle paylaşmayın).

> Arayüz zamanla değişir; adlar tam tutmazsa üstteki arama çubuğuna ilgili adı (ör. "Audience",
> "Clients") yazın. Takıldığınız ekranın görüntüsünü bana gönderin, birlikte çözeriz.

## 2. Kanallara giriş (bilgisayarınızda, bir kez)

```bash
.venv\Scripts\activate
python engine/youtube_auth.py login --channel psychology
python engine/youtube_auth.py login --channel money
```

Her komut tarayıcı açar. **O kanalın sahibi Google hesabıyla** giriş yapın (iki kanal farklı Gmail'lerdeyse
her seferinde ilgili hesabı seçin). "Google hasn't verified this app" uyarısı çıkar: **Advanced ->
Go to shorts-autopilot (unsafe)** -> izni onaylayın. Hesap seçerken kanal listesi çıkarsa **doğru kanalı**
seçin. Sonra tüm girişleri tek parça alın:

```bash
python engine/youtube_auth.py export
```

Çıkan tek satırlık JSON'u kopyalayın (3. adımda GitHub secret olacak).

## 3. GitHub deposu (public)

1. GitHub'da **public** bir depo oluşturun ve bu klasörü yükleyin. `.gitignore` sayesinde `.env`,
   `token_*.json`, `client_secret*.json` yüklenmez. **Yüklemeden önce `git status` ile kontrol edin.**
2. Depo -> **Settings -> Secrets and variables -> Actions -> New repository secret**:

   | Secret | Değer |
   |---|---|
   | `GEMINI_API_KEY` | Gemini anahtarınız (<https://aistudio.google.com/apikey>) |
   | `YOUTUBE_TOKENS_JSON` | 2. adımdaki `export` çıktısı |

3. **Settings -> Actions -> General -> Workflow permissions**: "Read and write permissions" seçin
   (geçmiş dosyasını commit edebilmek için). "Allow GitHub Actions to create and approve pull requests"
   gerekmez.

> Public depoda secret'lar güvendedir (loglarda maskelenir, fork'lar göremez). Yine de kimseyi
> `Collaborator` yapmayın ve workflow dosyalarına `pull_request_target` eklemeyin.

## 4. İlk deneme: DRY RUN (yükleme yok)

**Actions -> "Daily Shorts autopilot" -> Run workflow**: `dry_run` işaretli kalsın, `channels` kutusuna
`money` yazın. Sonuç:

- Planlama ve üretim çalışır, **hiçbir şey yüklenmez**.
- Çalışma bitince sayfanın altındaki **Artifacts** bölümünden `video-...` dosyasını indirip izleyin.
- **Bu adımda bakmanız gereken:** Edge TTS (ses) GitHub sunucularında çalışıyor mu? Microsoft'un resmi
  olmayan servisi bulut IP'lerini zaman zaman reddedebilir. Çalışmazsa log'da `Edge TTS failed` görürsünüz;
  o durumda bana haber verin, yedek çözüm kurarız.

Render süresi: 60 FPS videolar GitHub'ın 4 çekirdekli makinesinde yaklaşık **10-20 dk** sürer. Slot başına ayrı
iş paralel çalıştığı için 6 video yine de ~30-40 dk'da biter. Public depoda Actions dakikaları ücretsizdir.

## 5. İlk gerçek yükleme

`dry_run` işaretini **kaldırarak** tek bir kanalla çalıştırın (`channels = money`). 3 video yüklenir.
**YouTube Studio -> İçerik** bölümünde videoların **"Zamanlandı" (Scheduled)** olarak göründüğünü, doğru
saatleri ve açıklama/etiketleri kontrol edin.

**Videolar "Zamanlandı" yerine kilitli/private görünüyorsa:** Google bazı API projelerinin yüklemelerini
doğrulama (audit) bitene kadar private'a kilitleyebilir. Güncel YouTube API belgesi bunu açıkça yazmıyor,
bu yüzden ilk gerçek yüklemede test etmemiz gerekiyor. Gerekirse YouTube API Services "audit" formunu
doldurmanız gerekir (süreç birkaç hafta sürebilir). O zamana kadar videolar private yüklenir ve
Studio'dan elle yayına alabilirsiniz.

Sorun yoksa zamanlanmış çalışma kendiliğinden devreye girer: **her gün 03:23 UTC** (New York 22:23).

## 6. Günlük işletme

- **Hangi videolar yayında?** `state/history.json` (her gün otomatik commit edilir) ve YouTube Studio.
- **Konu/ses/saat değiştirmek:** `channels/psychology.json`, `channels/money.json`
  (`niche`, `rules`, `slots`, `timezone`, `edge_tts_voice`, `hashtags`...). Sonraki çalışmada geçerli olur.
- **Üçüncü kanal:** `channels/yenikanal.json` ekleyin, `python engine/youtube_auth.py login --channel yenikanal`,
  `export` çıktısını secret'a güncelleyin.
- **Durdurmak:** Actions -> workflow -> `...` -> "Disable workflow".
- **Hata olursa:** GitHub depoda "Autopilot problem" issue'su açar ve e-posta gönderir.
- **60 gün kuralı:** Public depolarda 60 gün etkinlik olmazsa zamanlanmış iş kapanır. Günlük
  `state/history.json` commit'i bunu engeller; yine de bir şey 60 gün boyunca hiç yüklenmezse Actions'tan elle
  açmanız gerekebilir.
- **Gemini anahtarı limiti dolarsa:** Planlama başarısız olur ve issue açılır. Farklı bir Google hesabı/projesi
  ile alınan anahtarı secret olarak güncelleyin.

## Konular tekrar etmesin

Planlayıcı her slot için konuyu seçerken şunların **hiçbirini** tekrarlamaz:

- o kanalın **bütün** geçmişi (`state/history.json`, hem konu hem başlık),
- **diğer kanalın** videoları (örn. "decoy pricing" iki kanalda birden çıkmaz),
- aynı çalışmada başka slota seçilen konular,
- kanal dosyasındaki `already_covered` listesi.

Benzerlik kontrolü kelime benzerliğiyle yapılır ve model de ayrıca "bunları tekrarlama" diye uyarılır; tekrar
gelirse otomatik yeniden sorulur. Sınırı bilin: farklı kelimelerle anlatılan **aynı fikri** her zaman
yakalayamaz (ör. "sinemada patlamış mısır neden pahalı" / "sinemalar parayı patlamış mısırdan kazanır").

**Eski videolarınız veya diğer otomasyonunuz aynı kanala yazıyorsa**, onların başlıklarını bilgisayarınızda
`channels/<kanal>.json` dosyasına ekleyin:

```json
"already_covered": ["Eski video başlığı 1", "Eski video başlığı 2"]
```

## Kotalar ve maliyet

| Kaynak | Limit | Sizin kullanımınız |
|---|---|---|
| YouTube yükleme | günde 100 `videos.insert` (1 birim) | 6 |
| Gemini ücretsiz katman | proje başına günlük/dakikalık limit | ~8-25 istek/gün |
| GitHub Actions (public) | ücretsiz | ~1-2 saat/gün |
| Edge TTS, faster-whisper, Remotion | ücretsiz | - |

Toplam maliyet: **0 TL**.

## Dikkat edilmesi gerekenler (ciddi)

- **İçerik politikası:** YouTube, yüksek hacimli, tekrar eden "seri üretim" içeriği para kazanma programından
  hariç tutabilir ve spam olarak değerlendirebilir. Günde 6 yapay zeka videosu bu riski artırır. İlk
  haftalarda videoları düzenli izleyin; farklı konular, farklı anlatım ve gerçek katma değer önemlidir.
  Yapay içerik bildirimi (`containsSyntheticMedia`) otomatik açıktır; kapatmayın.
- **Doğruluk:** Yapay zekanın bazı iddiaları hatalı olabilir. Prompt'lar kaynaksız istatistiği engellemeye
  çalışır ama garanti değildir. Para kanalında "yatırım tavsiyesi değildir" notu açıklamaya otomatik eklenir ve
  model belirli hisse/kripto önermemeye yönlendirilir.
- **Telif/ToS:** Edge TTS resmi olmayan bir Microsoft servisidir; ticari kullanım koşullarını kendiniz
  değerlendirin. Daha güvenli seçenek ücretli bir TTS'e (`TTS_PROVIDER=elevenlabs`) geçmektir.
