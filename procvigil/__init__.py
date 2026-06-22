"""ProcVigil - supervisor mantığında çalışan hafif bir süreç gözcüsü.

Komutları bir kez başlatıp sürekli ayakta tutar, ölürlerse anında yeniden
kaldırır. Olay anında (start/exit/crash/restart/fatal) belirlenen HTTP
parametrelerine istek atabilir ve HTTP health-check ile canlılık denetler.
"""

__version__ = "0.1.0"
