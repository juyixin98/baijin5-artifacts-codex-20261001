"""Human- and machine-readable explanations for trust failures.

When a handshake fails with UNTRUSTED_ROOT, the TLS stack alone cannot say
*why* the peer's chain is untrusted in terms of bundle history. This module
reconstructs the root-trust timeline from the store and explains, in
particular, whether a common trust period (a bundle version that contained
both the old and the current roots) ever existed.
"""
from __future__ import annotations

from .certs import fingerprint_sha256, load_pem_certificate
from .errors import FailureClass, InputError
from .store import Store


def root_timeline(store: Store) -> dict[str, list[dict]]:
    """Map root fingerprint -> list of bundle versions that contained it."""
    timeline: dict[str, list[dict]] = {}
    for bundle in store.list_bundles():
        for pem in bundle["roots"]:
            fp = fingerprint_sha256(load_pem_certificate(pem))
            timeline.setdefault(fp, []).append({
                "version": bundle["version"],
                "status": bundle["status"],
                "kind": bundle["kind"],
                "activated_at": bundle["activated_at"],
                "retired_at": bundle["retired_at"],
            })
    return timeline


def _bundle_fps(store: Store) -> dict[int, set[str]]:
    result: dict[int, set[str]] = {}
    for bundle in store.list_bundles():
        result[bundle["version"]] = {
            fingerprint_sha256(load_pem_certificate(p))
            for p in bundle["roots"]
        }
    return result


def explain_untrusted_root(store: Store, *,
                           presented_chain_pem: list[str] | None = None
                           ) -> dict:
    """Explain an UNTRUSTED_ROOT failure.

    ``presented_chain_pem`` is optional diagnostic input (e.g. supplied via
    POST /explain). It is NEVER used for authentication; it only makes the
    explanation specific to one client's chain.
    """
    active = store.active_bundle()
    if active is None:
        raise InputError("no active trust bundle; cannot explain failure")
    versions_fps = _bundle_fps(store)
    active_fps = versions_fps[active["version"]]
    timeline = root_timeline(store)

    explanation: dict = {
        "failure_class": FailureClass.UNTRUSTED_ROOT.value,
        "active_bundle_version": active["version"],
        "trusted_roots": sorted(active_fps),
        "retired_roots": {
            fp: [e["version"] for e in entries]
            for fp, entries in timeline.items()
            if fp not in active_fps
        },
        "presented_root": None,
        "common_trust_period": None,
        "reasoning": [],
    }
    reasoning: list[str] = explanation["reasoning"]
    reasoning.append(
        f"active trust bundle is v{active['version']} trusting "
        f"{len(active_fps)} root(s): {sorted(active_fps)}"
    )

    if not presented_chain_pem:
        reasoning.append(
            "the presented chain is not visible to the server after a failed "
            "TLS verification; submit the client chain to POST /explain for "
            "a definitive per-client explanation"
        )
        return explanation

    presented_fps = []
    for pem in presented_chain_pem:
        try:
            presented_fps.append(fingerprint_sha256(load_pem_certificate(pem)))
        except Exception as exc:
            raise InputError(
                f"presented chain contains unparsable PEM: {exc}",
                reasoning="/explain is a diagnostic endpoint; its input is "
                          "validated but never used for authentication",
            ) from exc

    matched = [fp for fp in presented_fps if fp in timeline or fp in active_fps]
    if not matched:
        reasoning.append(
            "no certificate in the presented chain matches any root this "
            "service has ever trusted; the client chains to a completely "
            "unknown root"
        )
        explanation["common_trust_period"] = False
        return explanation

    root_fp = matched[-1]
    explanation["presented_root"] = root_fp
    if root_fp in active_fps:
        reasoning.append(
            "the presented chain root IS in the active bundle; an "
            "UNTRUSTED_ROOT failure for this chain is not explained by "
            "bundle history - check chain completeness and intermediates"
        )
        explanation["common_trust_period"] = True
        return explanation

    entries = timeline[root_fp]
    last_version = max(e["version"] for e in entries)
    retired_at = next(
        (e["retired_at"] for e in entries if e["version"] == last_version),
        None,
    )
    reasoning.append(
        f"presented root {root_fp} was last trusted in bundle "
        f"v{last_version} (retired at {retired_at})"
    )
    overlap_versions = sorted(
        v for v, fps in versions_fps.items()
        if root_fp in fps and fps & active_fps
    )
    if overlap_versions:
        reasoning.append(
            f"bundle version(s) {overlap_versions} contained both the "
            f"presented root and currently trusted roots, so a common trust "
            f"period existed but has ended; the client failed to migrate "
            f"before v{last_version} was retired"
        )
        explanation["common_trust_period"] = True
    else:
        reasoning.append(
            "no bundle version ever contained both the presented root and "
            "the currently trusted roots, so NO common trust period ever "
            "existed; clients on the old root were cut off the moment the "
            "bundle changed (hard cutover without overlap)"
        )
        explanation["common_trust_period"] = False
    return explanation
