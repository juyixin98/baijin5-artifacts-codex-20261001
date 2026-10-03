"""Index build + candidate query: recall, dedup scope, burst cap, errors."""

import random

import pytest

from app.config import MinimizerConfig
from app.errors import IndexNotFoundError, IndexStateError, SequenceTooShortError
from app.index import MinimizerIndex
from app.sequence import SequenceRecord

from .reference import revcomp

CFG = MinimizerConfig(k=7, window=4)


def random_reference(length: int, seed: int = 20261003) -> str:
    rng = random.Random(seed)
    return "".join(rng.choice("ACGT") for _ in range(length))


def mutate(seq: str, positions: list[int], seed: int = 99) -> str:
    rng = random.Random(seed)
    chars = list(seq)
    for pos in positions:
        chars[pos] = rng.choice([b for b in "ACGT" if b != chars[pos]])
    return "".join(chars)


@pytest.fixture
def built(tmp_path):
    ref = random_reference(400)
    index, run, stats = MinimizerIndex.build(
        tmp_path / "idx.db", [SequenceRecord(name="ref", sequence=ref)], CFG
    )
    yield index, ref, run, stats
    index.close()


def test_build_stats_and_provenance(built):
    index, ref, run, stats = built
    assert stats["sequences_indexed"] == 1
    assert stats["seeds_after_cap"] == stats["seeds_before_cap"] > 0
    assert run.input_fingerprint is not None
    assert index.read_meta("config")["k"] == 7
    assert index.read_meta("run")["run_id"] == run.run_id


def test_forward_read_recalls_planted_position(built, runlog):
    index, ref, _, _ = built
    read_seq = mutate(ref[120:200], positions=[5, 40, 71])
    result = index.query(SequenceRecord(name="read1", sequence=read_seq))
    runlog.step(
        "judgment_basis",
        basis="read planted at ref[120:200] with 3 substitutions; "
        "surviving anchors must cluster at offset 120",
        query_minimizers=result.query_minimizers,
        db_hits=result.db_hits,
    )
    assert result.conclusion == "candidate_only"
    assert result.candidates, "expected at least one candidate"
    top = result.candidates[0]
    assert top.seq_name == "ref"
    assert top.relation == "same"
    assert top.offset == 120
    assert top.estimated_ref_start == 120


def test_reverse_complement_read_recalls_position(built, runlog):
    index, ref, _, _ = built
    read_seq = revcomp(ref[120:200])
    result = index.query(SequenceRecord(name="read_rc", sequence=read_seq))
    runlog.step(
        "judgment_basis",
        basis="reverse-complemented read: anchors cluster with swapped "
        "relation; estimated_ref_start must map back to 120",
    )
    assert result.candidates
    top = result.candidates[0]
    assert top.seq_name == "ref"
    assert top.relation == "swapped"
    assert top.estimated_ref_start == 120


def test_unrelated_read_yields_no_candidates(built):
    index, _, _, _ = built
    other = random_reference(80, seed=1)  # different generator stream
    result = index.query(SequenceRecord(name="noise", sequence=other))
    # No planted relationship: any hit would be a random hash collision.
    assert all(c.hits <= 1 for c in result.candidates)


def test_query_read_too_short_is_an_error(built):
    index, _, _, _ = built
    with pytest.raises(SequenceTooShortError) as excinfo:
        index.query(SequenceRecord(name="tiny", sequence="ACGTAC"))
    assert excinfo.value.category == "SEQUENCE_TOO_SHORT"


def test_low_complexity_burst_cap(tmp_path, runlog):
    cfg = MinimizerConfig(k=7, window=4, max_hash_occurrences=4)
    homo = SequenceRecord(name="homo", sequence="A" * 100)
    index, run, stats = MinimizerIndex.build(tmp_path / "cap.db", [homo], cfg)
    runlog.step(
        "judgment_basis",
        basis="homopolymer minimizer hash occurs 91 times > cap 4; "
        "hash must be filtered entirely, queries then hit nothing",
        stats=stats,
    )
    # 100 - 7 - 4 + 2 = 91 window positions, all the same canonical hash.
    assert stats["seeds_before_cap"] == 91
    assert stats["filtered_hashes"] == 1
    assert stats["filtered_occurrences"] == 91
    assert stats["seeds_after_cap"] == 0
    result = index.query(SequenceRecord(name="q", sequence="A" * 30))
    assert result.db_hits == 0
    assert result.candidates == []
    index.close()


def test_dedup_does_not_span_sequences(tmp_path):
    seq = random_reference(120, seed=5)
    records = [
        SequenceRecord(name="s1", sequence=seq),
        SequenceRecord(name="s2", sequence=seq),
    ]
    index, _, stats = MinimizerIndex.build(tmp_path / "dup.db", records, CFG)
    result = index.query(SequenceRecord(name="q", sequence=seq[:60]))
    names = {c.seq_name for c in result.candidates}
    assert names == {"s1", "s2"}
    index.close()


def test_too_short_sequence_skipped_at_build(tmp_path):
    records = [
        SequenceRecord(name="short", sequence="ACGT"),  # < k + w - 1 = 10
        SequenceRecord(name="ok", sequence=random_reference(50, seed=3)),
    ]
    index, run, stats = MinimizerIndex.build(tmp_path / "skip.db", records, CFG)
    assert stats["sequences_skipped_too_short"] == ["short"]
    assert stats["sequences_indexed"] == 1
    skip_steps = [s for s in run.steps if s.get("step") == "skip_sequence"]
    assert skip_steps and skip_steps[0]["reason"] == "SEQUENCE_TOO_SHORT"
    index.close()


def test_load_roundtrip_and_missing_index(tmp_path):
    records = [SequenceRecord(name="r", sequence=random_reference(60, seed=8))]
    _, _, _ = MinimizerIndex.build(tmp_path / "keep.db", records, CFG)
    loaded = MinimizerIndex.load(tmp_path / "keep.db")
    assert loaded.config == CFG
    loaded.close()
    with pytest.raises(IndexNotFoundError) as excinfo:
        MinimizerIndex.load(tmp_path / "nope.db")
    assert excinfo.value.category == "INDEX_NOT_FOUND"


def test_corrupt_index_file_is_state_error(tmp_path):
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"this is not sqlite")
    with pytest.raises(IndexStateError) as excinfo:
        MinimizerIndex.load(bad)
    assert excinfo.value.category == "INDEX_STATE_ERROR"
