"""Ommaviy rasmlarni qayta kodlash: EXIF (GPS, qurilma) olib tashlanadi, orientatsiya qo'llanadi,
thumbnail (WebP) yasaladi. Dekompressiya bombasiga qarshi piksel chegarasi."""

from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

from app.core.purposes import JPEG, PNG, WEBP

MAX_PIXELS = 40_000_000  # ~ 8000×5000
_FORMATS = {JPEG: "JPEG", PNG: "PNG", WEBP: "WEBP"}


class InvalidImage(ValueError):
    pass


@dataclass(frozen=True)
class Processed:
    original: bytes
    thumbnail: bytes


def process(data: bytes, content_type: str, thumb_size: int) -> Processed:
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        with Image.open(io.BytesIO(data)) as img:
            if img.width * img.height > MAX_PIXELS:
                raise InvalidImage("rasm juda katta")
            img.load()
            clean = ImageOps.exif_transpose(img) or img
            if content_type == JPEG and clean.mode not in ("RGB", "L"):
                clean = clean.convert("RGB")
            out = io.BytesIO()
            params: dict[str, object] = {"optimize": True}
            if content_type in (JPEG, WEBP):
                params["quality"] = 88
            # `exif` berilmaydi — metadata yangi faylga o'tmaydi.
            clean.save(out, format=_FORMATS[content_type], **params)

            thumb = clean.copy()
            thumb.thumbnail((thumb_size, thumb_size))
            if thumb.mode not in ("RGB", "RGBA"):
                thumb = thumb.convert("RGBA" if "A" in thumb.mode else "RGB")
            thumb_out = io.BytesIO()
            thumb.save(thumb_out, format="WEBP", quality=80)
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError) as exc:
        raise InvalidImage(str(exc)) from exc
    return Processed(original=out.getvalue(), thumbnail=thumb_out.getvalue())
