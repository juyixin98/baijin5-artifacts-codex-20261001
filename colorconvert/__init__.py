"""colorconvert: ICC-based image color conversion backend.

Modules
-------
contract     image data contract (layout, channel order, alpha semantics)
profiles     ICC profile registry with hash-pinned validation
kernel       numeric conversion kernel (LittleCMS via Pillow ImageCms)
tiles        tiled job planning/execution
jobs         job records and statuses
service      validate -> plan -> execute orchestration
api          FastAPI validation/conversion interface
diagnostics  request-scoped, redacted logging
config       runtime settings
errors       failure categories shared by all modules
"""

from .contract import ColorMode, ImageData
from .errors import ConversionError, FailureCategory
from .kernel import RenderingIntent

__all__ = [
    "ColorMode",
    "ConversionError",
    "FailureCategory",
    "ImageData",
    "RenderingIntent",
]

__version__ = "0.1.0"
