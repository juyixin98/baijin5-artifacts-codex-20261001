"""txmap: bidirectional transcript <-> genomic coordinate mapping service.

Coordinate conventions (fixed, do not change without a migration):
- All coordinates are 0-based, half-open intervals [start, end), BED-style.
- Genomic coordinates are offsets into the reference contig, always on the
  forward strand of the contig.
- Transcript coordinates are offsets into the spliced exonic sequence in
  transcript orientation (5' -> 3' of the transcript). For minus-strand
  transcripts, transcript position 0 corresponds to the LARGEST genomic
  coordinate of the last (genomically highest) exon.
"""

__version__ = "0.1.0"
