# ProcVigil Kullanım Kılavuzu

Bu doküman ProcVigil'ın **nasıl kurulacağını**, **komutların (program/job) nasıl
tanımlanacağını** ve **nasıl kullanılacağını** baştan sona anlatır.

İçindekiler:

1. [ProcVigil nedir, nasıl çalışır?](#1-procvigil-nedir-nasıl-çalışır)
2. [Temel kavramlar](#2-temel-kavramlar)
3. [Kurulum](#3-kurulum)
   - [Ubuntu / Linux (systemd)](#31-ubuntu--linux-systemd)
   - [macOS (launchd)](#32-macos-launchd)
   - [Manuel / geliştirme modu](#33-manuel--geliştirme-modu)
4. [Komut (program) tanımlama](#4-komut-program-tanımlama)
5. [Servis kullanımı (komutlar)](#5-servis-kullanımı-komutlar)
6. [Loglar](#6-loglar)
7. [HTTP bildirimleri ve health-check](#7-http-bildirimleri-ve-health-check)
8. [Hazır senaryolar (cookbook)](#8-hazır-senaryolar-cookbook)
9. [Sorun giderme](#9-sorun-giderme)

---

## 1. ProcVigil nedir, nasıl çalışır?

ProcVigil, **supervisor mantığında bir süreç gözcüsüdür**. Tanımladığın komutu bir
kez başlatır ve **sürekli ayakta tutar**. Süreç ölürse (çökme, kill, exit)
saniyesinde yakalar ve yeniden başlatır.

> Felsefe: ProcVigil "bu süreç **şu an** canlı mı?" sorusuyla ilgilenir. Her dakika
> gelip "hadi bir daha çalış" demez — bir kere çalıştırdığı işin başında nöbetçi
> gibi bekler; iş ölürse anında kaldırır.

Bunun üzerine üç ekstra yetenek:

- **Retry / backoff** — sürekli hemen ölen bir süreci sonsuza kadar denemez;
  belirli sayıda denemeden sonra `FATAL` durumuna alır.
- **HTTP bildirim (webhook)** — bir olay olduğu **an** (başlama, çökme, yeniden
  başlatma, fatal) belirttiğin URL'ye HTTP isteği atar.
- **HTTP health-check** — sürecin PID'i ayakta olsa bile gerçekten "sağlıklı"
  olup olmadığını bir URL'yi yoklayarak denetler.

İki katmanlı koruma vardır:

```
systemd / launchd   →   ProcVigil daemon'unu ayakta tutar (gözcünün gözcüsü)
        │
   ProcVigil daemon     →   senin programlarını ayakta tutar
```

---

## 2. Temel kavramlar

| Kavram         | Açıklama                                                                 |
|----------------|--------------------------------------------------------------------------|
| **program**    | Gözetilecek bir komut tanımı (config'teki `programs:` altındaki her öğe). |
| **instance**   | Bir programın çalışan kopyası. `numprocs: 4` → 4 instance.               |
| **durum (state)** | Her instance'ın o anki hâli (aşağıdaki durum makinesi).               |
| **daemon**     | ProcVigil'ın kendisi; tüm programları yöneten ana süreç.                     |

### Durum makinesi

```
            başlat
  STOPPED ──────────▶ STARTING ──(startsecs ayakta kaldı)──▶ RUNNING
     ▲                   │                                       │
     │      startsecs    │ erken öldü                            │ çıktı
     │      dolmadan     ▼                                       ▼
     │      öldü      BACKOFF ──(startretries tükendi)─────────▶ FATAL
     │                   │
     │                   └──(bekle, yeniden dene)──▶ STARTING
     │
     └──────── stop / shutdown ◀── STOPPING ◀── EXITED
```

- **STARTING**: Süreç başlatıldı, `RUNNING` sayılmak için `startsecs` kadar
  kesintisiz ayakta kalması bekleniyor.
- **RUNNING**: Stabil, ayakta.
- **BACKOFF**: `startsecs` dolmadan öldü → başarısız başlatma sayılır; bir süre
  bekleyip yeniden denenir (üstel backoff).
- **FATAL**: `startretries` kadar deneme tükendi, vazgeçildi.
- **EXITED**: `RUNNING` iken çıktı → `autorestart` politikasına göre yeniden
  başlatılır.

---

## 3. Kurulum

### 3.0 Uzaktan kurulum (tek satır)

Sunucuda en hızlı yol (`apt-get install supervisor` benzeri):

```bash
curl -fsSL https://raw.githubusercontent.com/sercanaydin/ProcVigil/main/scripts/remote-install.sh | sudo bash
```

Depoyu indirip işletim sistemini algılar ve uygun kurulum scriptini çalıştırır.
Aşağıdaki manuel yöntemler de geçerlidir.

### 3.1 Ubuntu / Linux (systemd)

```bash
# Proje dizininde
sudo ./scripts/install.sh
```

Bu script şunları yapar:

1. `python3` yoksa kurar (`apt`). **venv/pip yoktur** — bağımlılık gerekmez.
2. Uygulamayı `/opt/procvigil` altına kopyalar; servis sistem `python3`'ü
   `PYTHONPATH=/opt/procvigil` ile çalıştırır.
3. Örnek config'i `/etc/procvigil/procvigil.json` olarak yerleştirir (varsa korur).
4. `systemd` unit'ini `/etc/systemd/system/procvigil.service` olarak kurar ve
   etkinleştirir (`enable`).

Sonra config'i düzenleyip servisi başlat:

```bash
sudo nano /etc/procvigil/procvigil.json
sudo systemctl start procvigil
sudo systemctl status procvigil
```

### 3.2 macOS (launchd)

```bash
sudo ./scripts/install-macos.sh
```

Bu script `systemd` yerine **launchd** kullanır:

1. `python3` kontrol eder (yoksa `brew install python` veya
   `xcode-select --install` önerir). **venv/pip yoktur.**
2. Uygulamayı `/opt/procvigil` altına kopyalar (saf stdlib).
3. Config'i `/etc/procvigil/procvigil.json`, logları `/usr/local/var/log/procvigil`.
4. LaunchDaemon'u `/Library/LaunchDaemons/com.procvigil.daemon.plist` olarak yükler
   (`launchctl bootstrap`).

Sonra:

```bash
sudo nano /etc/procvigil/procvigil.json
sudo launchctl kickstart -k system/com.procvigil.daemon
```

> **systemd ↔ launchd karşılığı**
>
> | İşlem            | Linux (systemd)              | macOS (launchd)                                    |
> |------------------|------------------------------|----------------------------------------------------|
> | Başlat/yeniden   | `systemctl restart procvigil`   | `sudo launchctl kickstart -k system/com.procvigil.daemon` |
> | Durum            | `systemctl status procvigil`    | `sudo launchctl print system/com.procvigil.daemon`    |
> | Config reload    | `systemctl reload procvigil`    | `sudo launchctl kill -HUP system/com.procvigil.daemon`|
> | Durdur           | `systemctl stop procvigil`      | `sudo launchctl bootout system/com.procvigil.daemon`  |
> | Boot'ta başlat   | `systemctl enable procvigil`    | (LaunchDaemon `RunAtLoad` ile otomatik)            |
> | Daemon logları   | `journalctl -u procvigil -f`    | `tail -f /usr/local/var/log/procvigil/procvigil.daemon.log` |

### 3.3 Manuel / geliştirme modu

Servis kurmadan, herhangi bir makinede (Linux veya macOS). Kurulum/bağımlılık
gerekmez — sadece `python3`:

```bash
# Config'i doğrula
python3 -m procvigil check -c config/procvigil.json

# Daemon'u ön planda çalıştır (Ctrl+C ile durur)
python3 -m procvigil run -c config/procvigil.json
```

> YAML config (`config/procvigil.yml`) kullanmak istersen önce PyYAML kur:
> `pip install pyyaml` ya da `apt install python3-yaml`. JSON için gerek yoktur.

---

## 4. Komut (program) tanımlama

Komutlar config'teki `programs:` listesine eklenir. Her program bir `name` ve
`command` ister; gerisi opsiyoneldir.

> **Config formatı:** Varsayılan `procvigil.json` (JSON, bağımlılıksız). Aşağıdaki
> örnekler okunabilirlik için YAML gösterir; aynı yapıyı JSON'a birebir
> çevirebilirsin (örnek: [`config/procvigil.json`](../config/procvigil.json)).
> YAML kullanmak için PyYAML gerekir (`apt install python3-yaml`).

### En basit tanım

```yaml
programs:
  - name: my-worker
    command: /usr/bin/python3 /opt/app/worker.py
```

Bu kadarı yeterlidir: `my-worker` başlatılır ve ölürse hep yeniden kaldırılır.

### Tüm alanlar

```yaml
programs:
  - name: my-worker            # (zorunlu) benzersiz ad
    command: php artisan queue:work --tries=3   # (zorunlu) çalıştırılacak komut
    directory: /var/www/app    # çalışma dizini (cwd)
    user: www-data             # bu kullanıcı olarak çalıştır (daemon root olmalı)
    environment:               # ek ortam değişkenleri
      APP_ENV: production
      REDIS_HOST: 127.0.0.1

    autostart: true            # daemon açılınca otomatik başlasın mı
    autorestart: always        # always | unexpected | never
    exitcodes: [0]             # "normal" çıkış kodları (unexpected politikası için)

    startsecs: 3               # bu kadar saniye ayakta kalırsa RUNNING sayılır
    startretries: 5            # bu kadar başarısız denemeden sonra FATAL
    backoff_base: 1.0          # backoff taban süresi (1, 2, 4, 8... saniye)
    backoff_max: 30.0          # maksimum backoff süresi

    stopsignal: TERM           # durdururken gönderilecek sinyal
    stopwaitsecs: 10           # SIGKILL'e geçmeden önce beklenecek süre

    numprocs: 1                # kaç paralel instance çalışsın

    stdout_logfile: /var/log/procvigil/my-worker.out.log   # opsiyonel
    stderr_logfile: /var/log/procvigil/my-worker.err.log   # opsiyonel
    logfile_maxbytes: 10485760 # tek log dosyası max boyutu (rotation)
    logfile_backups: 5         # kaç eski log dosyası saklansın
```

### Alanların anlamı (en kritikleri)

- **`command`**: Komut bir kabuk satırı gibi yazılır ama `shell` ile değil,
  doğrudan exec edilir. Yani `&&`, `|`, `>` gibi kabuk operatörleri **çalışmaz**.
  Bunlara ihtiyacın varsa `command: bash -c '... && ...'` kullan.
- **`autorestart`**:
  - `always` → her çıkışta yeniden başlat (en yaygın).
  - `unexpected` → yalnızca `exitcodes` dışında bir kodla çıkarsa başlat.
  - `never` → asla yeniden başlatma (tek seferlik işler).
- **`startsecs`**: Süreç bu kadar saniye yaşamadan ölürse "başlatma başarısız"
  sayılır ve `startretries` sayacı işler. Hızlı açılan servisler için küçük
  (1-3), ağır açılanlar için büyük tut.
- **`numprocs`**: Aynı komutu N kez çalıştırır (ör. 4 queue worker). Instance'lar
  `name:0`, `name:1` ... şeklinde adlandırılır; logları da ayrı dosyalara gider.

### Birden fazla komut çalıştırma (zincir)

ProcVigil komutu kabuk üzerinden çalıştırmaz. Zincir/pipe gerekiyorsa:

```yaml
  - name: chained
    command: bash -lc 'cd /opt/app && exec ./run.sh'
```

`exec` kullanmak önemlidir: böylece `bash` yerine asıl süreç PID sahibi olur ve
ProcVigil onu doğru izler/durdurur.

### Değişiklikten sonra

Config'i değiştirdikten sonra **reload** et (servisi tamamen yeniden başlatmana
gerek yok):

```bash
# Linux
sudo systemctl reload procvigil
# macOS
sudo launchctl kill -HUP system/com.procvigil.daemon
```

Reload akıllıdır: yalnızca **eklenen / silinen / değişen** programlar etkilenir;
dokunulmayan programlar çalışmaya kesintisiz devam eder.

---

## 5. Servis kullanımı (komutlar)

### CLI (her platformda)

```bash
python3 -m procvigil check -c /etc/procvigil/procvigil.json   # config'i doğrula
python3 -m procvigil run   -c /etc/procvigil/procvigil.json   # daemon'u ön planda çalıştır
python3 -m procvigil --version
```

`check` çıktısı, tanımlı programları ve özetlerini listeler — config'i kaydetmeden
önce hata yakalamak için idealdir.

### pvctl — canlı program yönetimi (supervisorctl muadili)

Daemon, config'teki `socket` yolunda bir Unix soketi dinler. `pvctl` (veya
`python -m procvigil ctl ...`) ile **daemon'u yeniden başlatmadan** tek tek
programları yönetebilirsin:

```bash
pvctl status                 # tüm program/instance durumları (tablo)
pvctl start  laravel-queue   # bir programı başlat
pvctl stop   laravel-queue   # bir programı durdur (diğerleri etkilenmez)
pvctl restart laravel-queue  # yeniden başlat
pvctl reload                 # config'i yeniden yükle
pvctl tail   laravel-queue -n 50      # son 50 stdout satırı
pvctl tail   laravel-queue -e -n 50   # stderr logu
pvctl ping                   # daemon canlı mı?
```

> `pvctl` kısayolu install scripti tarafından `/usr/local/bin/pvctl` olarak kurulur
> (sistem `python3` + `PYTHONPATH` sarmalayıcısı). Kurmadan kullanmak için:
> `python3 -m procvigil ctl status`. Özel soket yolu için: `pvctl -s /yol/sock status`
> veya `-c /etc/procvigil/procvigil.json` ile config'ten türetilir.

**Sudo'suz erişim (Linux):** systemd unit'i, kontrol soketini `/run/procvigil/`
altında `procvigil` grubuna açacak şekilde ayarlıdır (`RuntimeDirectory=procvigil`,
setgid). Bir kullanıcının `sudo` olmadan `pvctl` kullanabilmesi için onu bu
gruba ekle:

```bash
sudo usermod -aG procvigil <kullanici>   # sonra yeniden oturum aç
pvctl status                       # artık sudo gerekmez
```

`RuntimeDirectory`, dizini servis başlayınca oluşturur ve durunca otomatik siler;
böylece kalıntı soket dosyası kalmaz.

Örnek `status` çıktısı:

```
PROGRAM     DURUM         PID  BAŞLATMA  EXIT
worker-a    RUNNING      8074         1  -
worker-b:0  RUNNING      8073         1  -
worker-b:1  RUNNING      8075         1  -
```

### Linux (systemd)

```bash
sudo systemctl start procvigil       # başlat
sudo systemctl stop procvigil        # durdur
sudo systemctl restart procvigil     # tamamen yeniden başlat
sudo systemctl reload procvigil      # config reload (SIGHUP) — kesintisiz
sudo systemctl status procvigil      # durum
sudo systemctl enable procvigil      # boot'ta başlat
sudo systemctl disable procvigil     # boot'ta başlatma
journalctl -u procvigil -f           # daemon loglarını canlı izle
```

### macOS (launchd)

```bash
sudo launchctl kickstart -k system/com.procvigil.daemon   # başlat / yeniden başlat
sudo launchctl kill -HUP system/com.procvigil.daemon      # config reload
sudo launchctl print system/com.procvigil.daemon          # durum / detay
sudo launchctl bootout system/com.procvigil.daemon        # durdur + kaldır
sudo launchctl bootstrap system /Library/LaunchDaemons/com.procvigil.daemon.plist  # yükle
tail -f /usr/local/var/log/procvigil/procvigil.daemon.log    # daemon loglarını izle
```

---

## 6. Loglar

İki tür log vardır:

1. **Daemon logu** — ProcVigil'ın kendi olayları (başlatma, çökme, restart, health,
   reload). Durum geçişlerini buradan takip edersin.
   - Linux: `journalctl -u procvigil -f`
   - macOS: `/usr/local/var/log/procvigil/procvigil.daemon.log`
   - Geliştirme: doğrudan terminale (stdout).

2. **Program logları** — her sürecin kendi `stdout`/`stderr` çıktısı. Varsayılan
   olarak `logdir` altına `<program>.out.log` ve `<program>.err.log` şeklinde
   yazılır, boyut tabanlı döndürme (rotation) ile.

```bash
tail -f /var/log/procvigil/my-worker.out.log     # stdout
tail -f /var/log/procvigil/my-worker.err.log     # stderr
```

`numprocs > 1` ise loglar `my-worker_0.out.log`, `my-worker_1.out.log` ... olarak
ayrılır.

---

## 7. HTTP bildirimleri ve health-check

### Bildirim (webhook) — "olay anında parametreye istek atma"

Bir program durumu değiştiğinde ProcVigil, config'teki URL'ye otomatik HTTP isteği
atar:

```yaml
  - name: critical-job
    command: /opt/app/job
    notify:
      url: https://hooks.slack.com/services/XXX/YYY/ZZZ
      method: POST
      headers:
        Authorization: "Bearer TOKEN"
      events: [start, crash, restart, fatal]   # hangi olaylarda
      retries: 2
```

Gönderilen JSON payload:

```json
{
  "program": "critical-job",
  "instance": "critical-job",
  "event": "crash",
  "state": "BACKOFF",
  "hostname": "web-01",
  "pid": 12345,
  "exitcode": 1,
  "message": "beklenmedik çıkış (exitcode=1), yeniden başlatılıyor",
  "timestamp": 1718480000.123
}
```

Geçerli olaylar: `start`, `exit`, `crash`, `restart`, `fatal`, `unhealthy`.

### HTTP health-check

PID ayakta olsa bile süreç "donmuş" olabilir. Health-check bir URL'yi periyodik
yoklar; ardışık başarısızlık eşiği aşılırsa aksiyon alır:

```yaml
  - name: api-server
    command: /opt/api/server --port 8080
    healthcheck:
      url: http://127.0.0.1:8080/health
      interval: 15             # 15 sn'de bir kontrol
      timeout: 5
      expect_status: [200]
      unhealthy_threshold: 3   # 3 ardışık başarısızlıkta
      on_unhealthy: restart    # restart | notify | nothing
```

---

### Metrics / Observability (opsiyonel)

Config'te açarsan ProcVigil bir HTTP endpoint sunar:

```yaml
procvigil:
  metrics:
    enabled: true
    host: 127.0.0.1
    port: 9100
```

- `GET /metrics` — Prometheus text formatı (`procvigil_up`,
  `procvigil_instance_running`, `procvigil_instance_starts_total`,
  `procvigil_instance_state_info`).
- `GET /status` — JSON durum (izleme/script için).

```bash
curl -s http://127.0.0.1:9100/metrics
curl -s http://127.0.0.1:9100/status
```

Prometheus scrape örneği:

```yaml
scrape_configs:
  - job_name: procvigil
    static_configs:
      - targets: ["sunucu-adresi:9100"]
```

## 8. Hazır senaryolar (cookbook)

### Laravel queue worker (4 paralel)

```yaml
  - name: laravel-queue
    command: php /var/www/app/artisan queue:work --sleep=3 --tries=3 --max-time=3600
    directory: /var/www/app
    user: www-data
    numprocs: 4
    autorestart: always
    stopwaitsecs: 15
    environment:
      APP_ENV: production
```

### Node.js / WebSocket sunucusu + health-check

```yaml
  - name: ws-server
    command: node /opt/ws/server.js
    directory: /opt/ws
    user: appuser
    autorestart: always
    healthcheck:
      url: http://127.0.0.1:3000/health
      interval: 10
      on_unhealthy: restart
```

### Python arka plan servisi (beklenen çıkışları yok say)

```yaml
  - name: ingest
    command: /usr/bin/python3 /opt/jobs/ingest.py
    autorestart: unexpected
    exitcodes: [0, 2]          # 0 ve 2 normal kabul edilir, restart yok
    startretries: 10
    backoff_base: 1.0
    backoff_max: 30.0
```

### Tek seferlik / restart edilmeyen iş

```yaml
  - name: migrate
    command: php /var/www/app/artisan migrate --force
    directory: /var/www/app
    autorestart: never
```

---

## 9. Sorun giderme

| Belirti | Olası neden / çözüm |
|---------|---------------------|
| Program **FATAL** oluyor | `startsecs` dolmadan ölüyor. Program loglarına (`*.err.log`) bak; eksik bağımlılık, yanlış path veya izin sorunu olabilir. `startretries` / `startsecs` ayarla. |
| `command not found` benzeri hata | `command` doğrudan exec edilir, kabuk yoktur. Tam yol ver (`/usr/bin/php`) veya `bash -lc '...'` kullan. |
| `&&`, `|`, `>` çalışmıyor | Aynı sebep. `command: bash -c '...'` içine al. |
| `user:` çalışmıyor | ProcVigil'ın **root** çalışması gerekir. systemd unit'i ve launchd plist'i zaten root ile çalışacak şekilde. |
| Config reload sonrası değişiklik uygulanmadı | JSON/YAML söz dizimi hatası olabilir; reload başarısızsa eski config korunur. `python3 -m procvigil check -c ...` ile doğrula, daemon logunu kontrol et. |
| Süreç durmuyor / asılı kalıyor | `stopsignal` programın anladığı sinyal mi? `stopwaitsecs` sonrası ProcVigil otomatik `SIGKILL` gönderir. |
| Webhook gitmiyor | `events` listesinde ilgili olay var mı? Daemon logunda `notify` satırlarına bak; URL/timeout/header'ları kontrol et. |
| macOS'ta servis yüklenmiyor | Plist `root:wheel` sahipli ve `644` olmalı. Kurulum scripti bunu ayarlar; manuel kopyaladıysan `sudo chown root:wheel ... && sudo chmod 644 ...`. |

### Hızlı sağlık kontrolü

```bash
# Config geçerli mi?
python3 -m procvigil check -c /etc/procvigil/procvigil.json

# Daemon ne yapıyor? (Linux)
journalctl -u procvigil -n 100 --no-pager

# Daemon ne yapıyor? (macOS)
tail -n 100 /usr/local/var/log/procvigil/procvigil.daemon.log
```
