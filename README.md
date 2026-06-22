# ProcVigil

Supervisor mantığında çalışan **hafif bir süreç gözcüsü (process supervisor)**.
Linux sunucular, özellikle **Ubuntu** için tasarlanmıştır ve `systemd` servisi
olarak çalışır.

ProcVigil, verdiğiniz komutu **bir kez başlatır ve sürekli ayakta tutar**. Süreç
ölürse saniyesinde yakalayıp yeniden kaldırır. Felsefe supervisor ile birebir
aynıdır: ProcVigil "bu süreç şu an canlı mı?" sorusuyla ilgilenir; her dakika
"hadi bir daha çalış" demez — nöbetçi gibi işin başında bekler.

Ek olarak, bir olay gerçekleştiği **an** (başlama, çökme, yeniden başlatma,
fatal) belirlediğiniz HTTP parametrelerine istek atar (webhook) ve isteğe bağlı
HTTP health-check ile sürecin gerçekten sağlıklı olup olmadığını denetler.

## Özellikler

- **Keep-alive süreç yönetimi** — supervisor durum makinesi (`STARTING → RUNNING
  → EXITED/BACKOFF → FATAL`).
- **Retry / backoff** — başarısız başlatmalarda üstel geri çekilme (`backoff_base`,
  `backoff_max`, `startretries`).
- **Çoklu job** — birden fazla programı paralel yönetir; her program için
  `numprocs` ile birden fazla instance.
- **Loglama** — her sürecin stdout/stderr çıktısı ayrı, döndürülen (rotating)
  dosyalara; daemon olayları `journald`'ye.
- **Boot'ta otomatik başlama + çökünce restart** — `systemd` ile (`Restart=always`).
- **Config reload** — `systemctl reload procvigil` (SIGHUP) ile kesintisiz yeniden
  yükleme; sadece değişen programlar yeniden kurulur.
- **HTTP bildirim (webhook)** — olay anında belirlenen URL'ye istek.
- **HTTP health-check** — PID dışında canlılık denetimi; sağlıksızsa restart/notify.
- **pvctl** — `supervisorctl` muadili: Unix soketi üzerinden tek tek
  programları canlı `status/start/stop/restart/reload/tail`.
- **Observability** — opsiyonel `/metrics` (Prometheus) ve `/status` (JSON) endpoint'i.

> Ayrıntılı kullanım, komut tanımlama ve sorun giderme için:
> **[docs/KULLANIM.md](docs/KULLANIM.md)**

## Uzaktan kurulum (tek satır)

`apt-get install supervisor` benzeri — sunucuya SSH ile bağlanıp tek komutla kur:

```bash
curl -fsSL https://raw.githubusercontent.com/sercanaydin/ProcVigil/main/scripts/remote-install.sh | sudo bash
```

Bu script depoyu indirir (git ya da tarball), işletim sistemini algılar ve doğru
kurulum scriptini (Linux: `install.sh`, macOS: `install-macos.sh`) çalıştırır.
Farklı dal/depo için: `PROCVIGIL_REPO` ve `PROCVIGIL_BRANCH` ortam değişkenleri.

## Hızlı kurulum (Ubuntu / Linux)

```bash
sudo ./scripts/install.sh
# config'i düzenle
sudo nano /etc/procvigil/procvigil.json
sudo systemctl reload procvigil   # veya: sudo systemctl start procvigil
```

Kurulum scripti şunları yapar: uygulamayı `/opt/procvigil` altına kopyalar
(**venv/pip yok** — saf stdlib, sistem `python3` ile çalışır), örnek config'i
`/etc/procvigil/procvigil.json` olarak yerleştirir ve `systemd` unit'ini etkinleştirir.

> **Bağımlılık yok:** ProcVigil yalnızca Python standart kütüphanesini kullanır;
> webhook/health-check `urllib` ile yapılır, config JSON olarak yerleşik okunur.
> Config'i YAML yazmak istersen `sudo apt install python3-yaml` kurup `.yml`
> dosyası kullanabilirsin (pip gerekmez).

## Hızlı kurulum (macOS / launchd)

ProcVigil macOS'ta da çalışır; sadece servis katmanı `systemd` yerine `launchd`'dir.

```bash
sudo ./scripts/install-macos.sh
sudo nano /etc/procvigil/procvigil.json
sudo launchctl kickstart -k system/com.procvigil.daemon   # başlat / yeniden başlat
```

| İşlem          | Linux (systemd)             | macOS (launchd)                                        |
|----------------|-----------------------------|--------------------------------------------------------|
| Başlat/yeniden | `systemctl restart procvigil`  | `launchctl kickstart -k system/com.procvigil.daemon`      |
| Config reload  | `systemctl reload procvigil`   | `launchctl kill -HUP system/com.procvigil.daemon`         |
| Durum          | `systemctl status procvigil`   | `launchctl print system/com.procvigil.daemon`             |
| Durdur         | `systemctl stop procvigil`     | `launchctl bootout system/com.procvigil.daemon`           |
| Daemon logları | `journalctl -u procvigil -f`   | `tail -f /usr/local/var/log/procvigil/procvigil.daemon.log`  |

## Manuel çalıştırma (geliştirme)

Kurulum gerekmez — sadece `python3` yeterli (bağımlılık yok):

```bash
# Config'i doğrula
python3 -m procvigil check -c config/procvigil.json

# Daemon'u ön planda çalıştır
python3 -m procvigil run -c config/procvigil.json
```

## Yapılandırma

Config **JSON** (yerleşik, bağımlılıksız) veya **YAML** (PyYAML kuruluysa) olabilir.
Tam örnekler: [`config/procvigil.json`](config/procvigil.json) ve
[`config/procvigil.yml`](config/procvigil.yml).

### `procvigil:` (daemon ayarları)

| Alan       | Varsayılan          | Açıklama                              |
|------------|---------------------|---------------------------------------|
| `logdir`   | `/var/log/procvigil`   | Program loglarının yazılacağı dizin   |
| `loglevel` | `info`              | `debug` / `info` / `warning` / `error`|
| `hostname` | sistemin hostname'i | Webhook payload'ında gönderilir       |

### `programs:` (her bir gözetilen program)

| Alan             | Varsayılan | Açıklama                                                        |
|------------------|------------|----------------------------------------------------------------|
| `name`           | (zorunlu)  | Benzersiz program adı                                          |
| `command`        | (zorunlu)  | Çalıştırılacak komut                                           |
| `directory`      | —          | Çalışma dizini (cwd)                                           |
| `user`           | —          | Bu kullanıcı olarak çalıştır (ProcVigil root olmalı)             |
| `environment`    | `{}`       | Ek ortam değişkenleri                                         |
| `autostart`      | `true`     | Daemon başlarken otomatik başlat                              |
| `autorestart`    | `always`   | `always` / `unexpected` / `never`                            |
| `exitcodes`      | `[0]`      | "Normal" sayılan çıkış kodları (`unexpected` politikası için) |
| `startsecs`      | `3`        | RUNNING sayılmak için kesintisiz ayakta kalma süresi (sn)     |
| `startretries`   | `5`        | FATAL'a düşmeden önce başlatma deneme sayısı                  |
| `backoff_base`   | `1.0`      | Üstel backoff taban süresi (sn)                              |
| `backoff_max`    | `30.0`     | Maksimum backoff süresi (sn)                                  |
| `stopsignal`     | `TERM`     | Durdurma sinyali (`TERM`, `INT`, `QUIT`, ...)                |
| `stopwaitsecs`   | `10`       | SIGKILL'e geçmeden önce bekleme süresi (sn)                  |
| `numprocs`       | `1`        | Paralel instance sayısı                                       |
| `stdout_logfile` | otomatik   | stdout log dosyası (yoksa `logdir` altına yazılır)           |
| `stderr_logfile` | otomatik   | stderr log dosyası                                            |

### `healthcheck:` (opsiyonel — HTTP canlılık denetimi)

| Alan                  | Varsayılan | Açıklama                                       |
|-----------------------|------------|------------------------------------------------|
| `url`                 | (zorunlu)  | Denetlenecek HTTP adresi                       |
| `method`              | `GET`      | HTTP metodu                                    |
| `interval`            | `30`       | Denetim aralığı (sn)                           |
| `timeout`             | `5`        | İstek zaman aşımı (sn)                          |
| `expect_status`       | `[200]`    | Sağlıklı sayılan durum kodları                 |
| `unhealthy_threshold` | `3`        | Aksiyon almadan önce ardışık başarısızlık sayısı |
| `on_unhealthy`        | `restart`  | `restart` / `notify` / `nothing`              |

> Not: health-check'in `interval`'i sürecin **canlılığını izlemek** içindir;
> supervisor'ın "süreci sürekli ayakta tut" felsefesiyle tutarlıdır. Komutu
> periyodik olarak yeniden tetiklemez.

### `notify:` (opsiyonel — olay webhook'u)

Bir olay gerçekleştiği an config'teki URL'ye HTTP isteği atılır. Payload örneği:

```json
{
  "program": "laravel-queue",
  "instance": "laravel-queue:0",
  "event": "crash",
  "state": "BACKOFF",
  "hostname": "web-01",
  "pid": 12345,
  "exitcode": 1,
  "message": "beklenmedik çıkış (exitcode=1), yeniden başlatılıyor",
  "timestamp": 1718480000.123
}
```

| Alan            | Varsayılan                          | Açıklama                              |
|-----------------|-------------------------------------|---------------------------------------|
| `url`           | (zorunlu)                           | İstek atılacak adres                  |
| `method`        | `POST`                              | HTTP metodu                           |
| `headers`       | `{}`                                | Ek başlıklar (örn. Authorization)     |
| `timeout`       | `5`                                 | İstek zaman aşımı (sn)                 |
| `events`        | `[start, crash, restart, fatal]`    | Hangi olaylarda tetiklensin           |
| `retries`       | `2`                                 | Başarısız istekte tekrar sayısı       |
| `retry_backoff` | `1.0`                               | Tekrarlar arası bekleme katsayısı     |

Geçerli olaylar: `start`, `exit`, `crash`, `restart`, `fatal`, `unhealthy`.

### Çalışan HTTP örneği

`examples/` altında, webhook'ların gerçekten aktığını görebileceğin çalışır bir
demo vardır (webhook alıcı + örnek config):

```bash
# Terminal 1 — webhook isteklerini yakalayan alıcı
python3 examples/webhook_receiver.py

# Terminal 2 — olayları tetikleyen örnek config ile ProcVigil
python -m procvigil run -c examples/http-example.yml
```

Ayrıntılar: [`examples/README.md`](examples/README.md).

## Canlı yönetim — pvctl

Daemon'u yeniden başlatmadan tek tek programları yönet (`supervisorctl` gibi):

```bash
pvctl status                  # tüm durumlar (tablo)
pvctl restart laravel-queue   # tek programı yeniden başlat
pvctl stop  laravel-queue     # durdur (diğerleri çalışmaya devam eder)
pvctl tail  laravel-queue -n 50
pvctl reload                  # config'i yeniden yükle
```

Kurmadan: `python3 -m procvigil ctl status`. Ayrıntı: [docs/KULLANIM.md](docs/KULLANIM.md).

## Testler

Çalışma-zamanı bağımlılığı olmasa da test/lint araçları (pytest, ruff) ve YAML
config testleri için PyYAML gerekir; bunları bir geliştirme venv'ine kurun:

```bash
python3 -m venv .devenv && .devenv/bin/pip install -r requirements-dev.txt
.devenv/bin/ruff check procvigil tests
.devenv/bin/pytest
```

Testler config doğrulamasını, webhook/health-check'i (yerel HTTP sunucusuyla),
süreç durum makinesini (RUNNING/FATAL/restart/shutdown) ve supervisor reload
mantığını kapsar.

## Çalıştırma komutları

```bash
sudo systemctl start procvigil       # başlat
sudo systemctl status procvigil      # durum
sudo systemctl reload procvigil      # config reload (SIGHUP)
sudo systemctl restart procvigil     # tamamen yeniden başlat
sudo systemctl stop procvigil        # durdur
journalctl -u procvigil -f           # daemon loglarını izle
```

## Durum makinesi

```
            başlat
  STOPPED ──────────▶ STARTING ──(startsecs ayakta)──▶ RUNNING
     ▲                   │                                 │
     │      startsecs    │ çıktı                           │ çıktı
     │      dolmadan     ▼                                 ▼
     │      öldü      BACKOFF ──(startretries tükendi)──▶ FATAL
     │                   │                                 
     │                   └──(yeniden dene)──▶ STARTING     
     │                                                     
     └──────────── stop / shutdown ◀── STOPPING ◀── EXITED
```

## Gereksinimler

- Python 3.10+ (**başka hiçbir çalışma-zamanı bağımlılığı yok** — saf stdlib)
- Linux + `systemd` (Ubuntu 20.04+) veya macOS + `launchd`
- (Opsiyonel) YAML config için `PyYAML` (`apt install python3-yaml`)
