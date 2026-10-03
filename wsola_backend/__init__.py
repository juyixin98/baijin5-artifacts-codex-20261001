"""Audio-only WSOLA time-stretch backend.

Modules
-------
config       Fixed algorithm parameters (window, hops, search radius, ranges).
contracts    Pydantic request/response schemas (the sample contract).
wsola        Offline whole-signal WSOLA core (pure NumPy/SciPy).
stream       Stateful chunked/streaming WSOLA (bit-identical to offline).
diagnostics  Request IDs, decision records, masked logging.
fixtures     Local synthetic signal generators (tone, impulses, silence, noise).
metrics      Independent measurement helpers used by tests and verify script.
service      Validation + orchestration between contracts and the core.
main         FastAPI application.
"""

__version__ = "0.1.0"
