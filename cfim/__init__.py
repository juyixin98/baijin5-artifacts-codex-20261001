"""cfim - closed frequent itemset mining backend.

Modules:
    config:        environment-driven configuration
    errors:        transport-independent error codes
    corpus:        transaction corpus normalization / validation
    kernel:        vertical-tidset closed-itemset mining kernel (budgeted, resumable)
    store:         SQLite persistence and vertical index
    models:        pydantic request/response schemas
    observability: request-id context and structured JSON logs
    service:       FastAPI application
"""

__version__ = "1.0.0"
