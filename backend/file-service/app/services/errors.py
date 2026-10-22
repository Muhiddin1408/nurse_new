from __future__ import annotations

from medbron_common.errors import Conflict, Forbidden, NotFound, TooManyRequests, Unprocessable


class FileNotFound(NotFound):
    code = "FILE_NOT_FOUND"
    message = "Fayl topilmadi"


class UnknownPurpose(Unprocessable):
    code = "UNKNOWN_PURPOSE"
    message = "Noma'lum fayl turi (purpose)"


class ContentTypeNotAllowed(Unprocessable):
    code = "CONTENT_TYPE_NOT_ALLOWED"
    message = "Bu fayl formati qabul qilinmaydi"


class FileTooLarge(Unprocessable):
    code = "FILE_TOO_LARGE"
    message = "Fayl hajmi ruxsat etilganidan katta"


class FileRejected(Unprocessable):
    code = "FILE_REJECTED"
    message = "Fayl tekshiruvdan o'tmadi"


class UploadMissing(Conflict):
    code = "UPLOAD_MISSING"
    message = "Fayl hali yuklanmagan"


class InvalidFileState(Conflict):
    code = "INVALID_FILE_STATE"
    message = "Faylning hozirgi holatida bu amalni bajarib bo'lmaydi"


class AttachNotAllowed(Forbidden):
    code = "ATTACH_NOT_ALLOWED"
    message = "Bu servis bu turdagi faylni biriktira olmaydi"


class TooManyPendingUploads(TooManyRequests):
    code = "TOO_MANY_PENDING_UPLOADS"
    message = "Tugallanmagan yuklashlar juda ko'p"
