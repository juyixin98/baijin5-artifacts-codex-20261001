"""txmap: bidirectional transcript <-> genomic coordinate mapping.

Fixed coordinate convention (see docs/DESIGN.md):
    genome:     0-based, half-open [start, end), ascending on the reference
    transcript: 0-based, half-open [start, end) over the mature transcript
"""

__version__ = "1.0.0"
