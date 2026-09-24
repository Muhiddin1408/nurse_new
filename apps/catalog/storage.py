"""Maxfiy fayllar ombori (shifokor hujjatlari).

Lokal/oddiy deploy: `PRIVATE_MEDIA_ROOT` — veb-server orqali BERILMAYDIGAN katalog.
Prodda S3/MinIO: `PRIVATE_STORAGE_BACKEND` ga storages backend yo'li beriladi
(bucket ochiq EMAS) — kod o'zgarmaydi, chunki fayl faqat imzolangan havola
orqali, ilova ichidan o'qiladi.
"""

import os

from django.conf import settings
from django.core.files.storage import FileSystemStorage, storages
from django.utils.module_loading import import_string


class PrivateFileSystemStorage(FileSystemStorage):
    """Joylashuv har murojaatda sozlamadan o'qiladi (model yuklangan paytda muzlatilmaydi)."""

    def __init__(self):
        super().__init__(base_url=None)

    @property
    def base_location(self):
        return str(settings.PRIVATE_MEDIA_ROOT)

    @property
    def location(self):
        return os.path.abspath(self.base_location)

    def url(self, name):  # hech qachon ochiq URL yo'q
        raise NotImplementedError("Maxfiy fayl: faqat imzolangan havola orqali")


def private_storage():
    backend = getattr(settings, "PRIVATE_STORAGE_BACKEND", "")
    if backend:
        if backend in getattr(settings, "STORAGES", {}):
            return storages[backend]
        return import_string(backend)()
    return PrivateFileSystemStorage()
