# ProcVigil HTTP isteği (webhook) örneği

Bu klasör, ProcVigil'ın **olay anında belirlenen URL'ye HTTP isteği atma** (notify)
ve **HTTP health-check** özelliklerini canlı olarak gösteren çalışır bir demo
içerir.

## Dosyalar

- `webhook_receiver.py` — gelen webhook isteklerini terminale yazan basit
  HTTP sunucu. `/health` ucunda 200 döner (health-check demosu için).
- `http-example.yml` — bilerek çöken bir iş (`flaky-job`) ve health-check'li bir
  servis (`healthy-service`) tanımlayan örnek config.

## Çalıştırma (2 terminal)

**Terminal 1 — webhook alıcıyı başlat:**

```bash
python3 examples/webhook_receiver.py
# Webhook alıcı dinliyor: http://127.0.0.1:9000
```

**Terminal 2 — ProcVigil'ı örnek config ile çalıştır:**

```bash
# YAML örnek config; PyYAML gerekir (apt install python3-yaml ya da pip install pyyaml)
python3 -m procvigil run -c examples/http-example.yml
```

## Ne göreceksin?

Terminal 1'de (webhook alıcı) şuna benzer bir akış belirir:

```
[22:31:05] WEBHOOK  program=healthy-service  event=start    state=RUNNING  exit=None
[22:31:07] WEBHOOK  program=flaky-job        event=start    state=RUNNING  exit=None
[22:31:09] WEBHOOK  program=flaky-job        event=exit     state=EXITED   exit=1
[22:31:09] WEBHOOK  program=flaky-job        event=crash    state=EXITED   exit=1   beklenmedik çıkış (exitcode=1), yeniden başlatılıyor
[22:31:12] WEBHOOK  program=flaky-job        event=restart  state=RUNNING  exit=1
[22:31:14] WEBHOOK  program=flaky-job        event=exit     state=EXITED   exit=1
...
```

- `flaky-job` her ~4 saniyede bir çöküp yeniden başladığı için sürekli
  `exit → crash → restart` webhook'ları akar.
- `healthy-service` başlangıçta bir `start` webhook'u gönderir; health-check
  `/health`'ten 200 aldığı için sağlıklı kalır (bildirim atılmaz).

Durdurmak için her iki terminalde `Ctrl+C`.

## Kendi webhook'unu denemek

`http-example.yml` içindeki `notify.url` alanını kendi adresinle değiştir
(örn. bir Slack/Discord webhook'u veya kendi API'n). Gönderilen JSON gövdesi:

```json
{
  "program": "flaky-job",
  "instance": "flaky-job",
  "event": "crash",
  "state": "EXITED",
  "hostname": "...",
  "pid": 12345,
  "exitcode": 1,
  "message": "beklenmedik çıkış (exitcode=1), yeniden başlatılıyor",
  "timestamp": 1718480000.123
}
```
