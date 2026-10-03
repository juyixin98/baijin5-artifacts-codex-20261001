"""miniseed -- minimizer seed indexing and candidate location lookup."""
from .config import Settings
from .errors import ErrorCode, MiniseedError
from .minimizer import Minimizer, scan_minimizers
from .service import MiniseedService
from .store import SeedStore

__version__ = "1.0.0"

__all__ = [
    "Settings",
    "ErrorCode",
    "MiniseedError",
    "Minimizer",
    "scan_minimizers",
    "MiniseedService",
    "SeedStore",
    "__version__",
]
