"""Turns uploaded images into the Responses API's multimodal parts.

Refactored from a Chainlit-shaped version that duck-typed `.name`/`.path`/`.mime`
off a framework element and assumed the upload had already been spooled to disk.
The signature is now plain `(filename, content_type, data)`, which a FastAPI
UploadFile provides directly and a test can construct in one line.

Limits are enforced HERE, server-side. The previous version also declared them
in a UI config file — and that config turned out to be shadowed at runtime, so
the limits were silently inert while appearing to be set. Client-side limits are
a courtesy; this is the enforcement.
"""

from __future__ import annotations

import base64

SUPPORTED_IMAGE_MIMES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
MAX_IMAGE_BYTES = 15 * 1024 * 1024
MAX_IMAGES_PER_TURN = 5

_EXTENSION_MIMES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def _resolve_mime(filename: str, declared: str | None) -> str | None:
    """Trust the declared type only if we support it; otherwise fall back to the
    extension. Browsers are inconsistent about content_type on some platforms."""
    if declared in SUPPORTED_IMAGE_MIMES:
        return declared
    lowered = (filename or "").lower()
    for ext, mime in _EXTENSION_MIMES.items():
        if lowered.endswith(ext):
            return mime
    return None


def collect_images(
    uploads: list[tuple[str, str | None, bytes]],
) -> tuple[list[dict], list[str]]:
    """(usable_images, skip_reasons).

    Each usable image is {"data_url": "data:<mime>;base64,...", "name": str},
    which is what agent.build_user_input expects. Skips are returned rather than
    raised so one bad file does not lose the rep's whole message — they are shown
    alongside the answer.
    """
    images: list[dict] = []
    skipped: list[str] = []

    for filename, declared, data in uploads:
        name = filename or "attachment"

        if len(images) >= MAX_IMAGES_PER_TURN:
            skipped.append(f"{name}: over the {MAX_IMAGES_PER_TURN}-image limit for one message")
            continue

        mime = _resolve_mime(name, declared)
        if mime is None:
            skipped.append(f"{name}: not a supported image (PNG, JPEG, WEBP or GIF only)")
            continue

        if not data:
            skipped.append(f"{name}: file was empty")
            continue

        if len(data) > MAX_IMAGE_BYTES:
            # Both figures use the same divisor as the limit itself, or the
            # message contradicts the constant it is reporting: 15 MiB rendered
            # with a /1e6 divisor reads as "limit 16 MB".
            mib = 1024 * 1024
            skipped.append(
                f"{name}: too large ({len(data) / mib:.1f} MB, "
                f"limit {MAX_IMAGE_BYTES // mib} MB)"
            )
            continue

        encoded = base64.b64encode(data).decode()
        images.append({"data_url": f"data:{mime};base64,{encoded}", "name": name})

    return images, skipped
