"""Minimal-energy seam computation backend with protected-region constraints.

Modules:
    contracts  -- image data contract (validation + result types)
    energy     -- gradient energy and forward energy (kept separate on purpose)
    kernel     -- dynamic-programming seam search (numeric core)
    carving    -- seam removal + original-coordinate mapping
    jobs       -- chunked carve jobs with progress reporting
    api        -- FastAPI validation interface
    config     -- independent configuration
    runlog     -- structured run logging
"""

__version__ = "0.1.0"
