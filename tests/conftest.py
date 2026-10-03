"""Shared fixtures: the hand-computed 40-base reference scenario.

Expected values below were computed by hand from the fixture file, not by
running the pipeline. See tests/fixtures/alignments_small.tsv comments.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from coverage_depth.config import PipelineConfig
from coverage_depth.models import AlignmentRecord
from coverage_depth.parsing import iter_alignment_lines, parse_alignment_line

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "alignments_small.tsv"
REF_NAME = "chrS"
REF_LENGTH = 40

# Hand-computed from the fixture:
#   accepted blocks after per-read merge:
#     r1: [5,10) [15,20)   r2: [8,18)   r3: [20,35)
#   depth segments (0-based half-open; uncovered regions are not segments):
EXPECTED_SEGMENTS = [
    (5, 8, 1),
    (8, 10, 2),
    (10, 15, 1),
    (15, 18, 2),
    (18, 20, 1),
    (20, 35, 1),
]
EXPECTED_HISTOGRAM = {0: 10, 1: 25, 2: 5}
EXPECTED_COVERED = 30
EXPECTED_WEIGHTED = 35  # r1:10 + r2:10 + r3:15 (overlap counted once)


@pytest.fixture()
def fixture_records() -> list[AlignmentRecord]:
    text = FIXTURE_PATH.read_text()
    return [parse_alignment_line(line, i) for i, line in iter_alignment_lines(text)]


@pytest.fixture()
def default_config() -> PipelineConfig:
    return PipelineConfig()
