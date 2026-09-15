"""Finding capture files on disk, case-insensitively and including HEIC.

Every extension match in this package goes through here, for one reason: an iPhone writes
`IMG_1582.MOV` and `IMG_1583.HEIC`, in capitals, always. `Path.glob("*.mov")` matches by
`fnmatch`, which compares case-sensitively even on a case-insensitive filesystem, so a
lowercase pattern silently finds nothing in a directory full of iPhone files. The video tier
failed exactly this way on the first real capture: tier detection fell through to the photo
reader, which then reported no images in a folder holding a 3.6 GB walkthrough.

That failure mode is worth naming because of where it would have surfaced. Everything in the
repository's own test data is lowercase, so nothing caught it; the walk-in test is a cold run
on files straight off someone else's iPhone, which is precisely where it would have hit.

HEIC is here for the same reason: it is the iPhone default, so a photo reader that handles
only JPEG and PNG handles the format nobody actually has.
"""

from __future__ import annotations

from pathlib import Path

VIDEO_EXTENSIONS = (".mp4", ".mov", ".m4v")
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".heic", ".heif")


def _matching(directory: Path, extensions: tuple[str, ...]) -> list[Path]:
    if not directory.is_dir():
        return []
    wanted = {e.lower() for e in extensions}
    return sorted(
        p for p in directory.iterdir()
        if p.is_file() and not p.name.startswith(".") and p.suffix.lower() in wanted
    )


def find_videos(directory: Path) -> list[Path]:
    """Video files directly inside `directory`, any capitalisation."""
    return _matching(Path(directory), VIDEO_EXTENSIONS)


def find_images(directory: Path) -> list[Path]:
    """Image files directly inside `directory`, any capitalisation, HEIC included."""
    return _matching(Path(directory), IMAGE_EXTENSIONS)


def ensure_heif_support() -> bool:
    """Register the HEIF opener with Pillow if the plugin is installed.

    Returns whether HEIC can now be read. OpenCV cannot read HEIC at all, so any reader
    that might be handed an iPhone photo has to go through Pillow for those.
    """
    try:
        import pillow_heif

        pillow_heif.register_heif_opener()
        return True
    except Exception:
        return False


def read_image(path: Path):
    """Read any supported still as an RGB uint8 array, HEIC included."""
    import numpy as np

    path = Path(path)
    if path.suffix.lower() in (".heic", ".heif"):
        if not ensure_heif_support():
            raise RuntimeError(
                f"{path.name} is HEIC and pillow-heif is not installed. "
                "Install it, or convert the photos to JPEG before running."
            )
        from PIL import Image, ImageOps

        # An iPhone stores a portrait still in sensor orientation with an EXIF orientation tag. OpenCV applies the
        # tag when it reads a JPEG; Pillow does not, so a HEIC still is turned here or it arrives sideways.
        with Image.open(path) as image:
            return np.asarray(ImageOps.exif_transpose(image).convert("RGB"))

    import cv2

    raw = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if raw is None:
        raise RuntimeError(f"could not read {path}")
    return cv2.cvtColor(raw, cv2.COLOR_BGR2RGB)
