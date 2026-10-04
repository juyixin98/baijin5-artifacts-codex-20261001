"""Module-level ASGI app for ``uvicorn commit_reveal.api.app_factory:app``.

Built from environment configuration (CRP_*). For tests, use
``commit_reveal.api.app.create_app`` directly with an injected clock.
"""

from commit_reveal.api.app import create_app
from commit_reveal.config import Settings

app = create_app(Settings.from_env())
