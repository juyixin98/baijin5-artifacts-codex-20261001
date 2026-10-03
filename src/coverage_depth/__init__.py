"""Coverage depth backend for synthetic alignment intervals.

Coordinate basis is FIXED: 0-based, half-open [start, end) intervals on the
reference. Gap operations (CIGAR D/N) never contribute coverage. Blocks of the
same read (including both mates of a pair sharing a read_id) are union-merged
before counting, so paired overlaps are never double counted.
"""

__version__ = "0.1.0"
