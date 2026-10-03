"""MFCC + delta/delta-delta feature backend.

Layered structure:
- ``config``     : immutable, fully-specified feature configuration
- ``errors``     : explicit error taxonomy (no silent success on bad input)
- ``dsp``        : signal-processing stages (pre-emphasis, framing, spectrum,
                   mel filterbank, log/DCT, temporal deltas)
- ``pipeline``   : batch computation returning every intermediate matrix
- ``streaming``  : chunked computation with causal context handling
- ``contracts``  : request/response schemas (sample contract)
- ``service``    : FastAPI entry point
"""

__version__ = "1.0.0"
