"""Application entrypoint: build the app from local fixtures.

Run with:  uvicorn txmap.main:app --port 8000
"""

from __future__ import annotations

from .config import load_settings
from .mapping import Mapper
from .service import create_app
from .store import Store, load_fixture_contigs, load_fixture_transcripts

settings = load_settings()
store = Store(settings.db_path)
_transcripts = load_fixture_transcripts(settings.transcripts_path)
store.load_transcripts(_transcripts)
_contigs = load_fixture_contigs(settings.fasta_path)
mapper = Mapper(store.get_transcripts(), _contigs)
app = create_app(mapper, store)
