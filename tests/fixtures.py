"""Synthetic fixtures shared across tests. All data is local and synthetic."""

from __future__ import annotations

from er_backend.models import ConstraintSet, Record

# Cross-lingual / alias token map fixture (CJK -> latin, synthetic).
TOKEN_MAP = {
    "北京": "beijing",
    "上海": "shanghai",
    "星辰": "xingchen",
    "科技": "technology",
}


def rec(
    record_id: str,
    name: str,
    aliases: list[str] | None = None,
    attributes: dict[str, str] | None = None,
) -> Record:
    return Record(
        record_id=record_id,
        name=name,
        aliases=aliases or [],
        attributes=attributes or {},
    )


# --- Chain fixture: A-B-C pairwise similar, but A-C is cannot-link. -------
# All three normalize to "acme trading", so naive threshold connected
# components would merge all three; the constraint forbids it.
CHAIN_RECORDS = [
    rec("A", "Acme Trading"),
    rec("B", "Acme Trading Ltd"),
    rec("C", "Acme Trading Company"),
]
CHAIN_CONSTRAINTS = ConstraintSet(must_link=[("A", "B")], cannot_link=[("A", "C")])

# --- Alias fixture: E's name only matches D via D's alias list. ------------
ALIAS_RECORDS = [
    rec("D", "Globex International Corporation", aliases=["Globex Intl"]),
    rec("E", "Globex Intl"),
]

# --- Cross-lingual fixture: CJK and latin names of the same synthetic org. --
CROSSLINGUAL_RECORDS = [
    rec("F", "北京星辰科技有限公司"),
    rec("G", "Beijing Xingchen Technology Ltd"),
    rec("H", "上海星辰科技有限公司"),
]

# --- Same-name-different-entity fixture. ------------------------------------
SAMENAME_RECORDS = [
    rec("I", "Acme Trading Ltd", attributes={"registration_id": "REG-001"}),
    rec("J", "Acme Trading Ltd", attributes={"registration_id": "REG-002"}),
]
SAMENAME_CONSTRAINTS = ConstraintSet(cannot_link=[("I", "J")])

# --- Oracle fixture: 5 records, checked against the exhaustive reference. ---
ORACLE_RECORDS = [
    rec("n1", "Delta Foods Ltd"),
    rec("n2", "Delta Foods Inc"),
    rec("n3", "Delta Foods International"),
    rec("n4", "Sigma Foods Ltd"),
    rec("n5", "Zeta Motors"),
]
ORACLE_CONSTRAINTS = ConstraintSet(
    must_link=[("n3", "n4")], cannot_link=[("n1", "n4")]
)
ORACLE_EXPECTED = frozenset(
    [frozenset({"n1", "n2"}), frozenset({"n3", "n4"}), frozenset({"n5"})]
)
