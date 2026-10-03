"""Local multi-resolution image pyramid service.

Modules
-------
* :mod:`contracts` — pixel / tile / level data contracts.
* :mod:`coords` — level geometry and the fixed pixel-center mapping.
* :mod:`kernel` — area and gaussian anti-aliased 2x downsampling.
* :mod:`store` — atomic tile publication and integrity-checked reads.
* :mod:`region` — cross-tile region stitching.
* :mod:`builder` — tiled pyramid build jobs.
* :mod:`api` — FastAPI surface (build, levels, region, validate).
* :mod:`errors` — the distinguishable error taxonomy.
* :mod:`observability` — JSON run-id logging.
"""

__version__ = "1.0.0"
