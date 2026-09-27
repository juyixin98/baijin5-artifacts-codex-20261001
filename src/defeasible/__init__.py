"""Defeasible reasoning backend with an explicit, restricted semantics.

Modules
-------
- ``language``  : rule language (terms, rules, theories, parser/dumper)
- ``engine``    : the reasoning kernel (grounding + fixpoint evaluation)
- ``storage``   : persistent evidence store (SQLite)
- ``api``       : FastAPI query interface
"""

__version__ = "1.0.0"
