"""Connected-block detection: fragments link sites, blocks stay separate."""

from app.domain import Fragment, Observation
from app.phasing.blocks import find_blocks


def _fragment(read_id, *sites):
    return Fragment(
        read_id=read_id,
        observations=[Observation(site=s, bit=0, weight=30.0) for s in sites],
    )


def test_single_spanning_fragment_links_sites():
    blocks = find_blocks(3, [_fragment("r1", 0, 1, 2)])
    assert blocks == [[0, 1, 2]]


def test_transitive_linkage():
    fragments = [_fragment("r1", 0, 1), _fragment("r2", 1, 2)]
    assert find_blocks(3, fragments) == [[0, 1, 2]]


def test_disconnected_sites_form_separate_blocks():
    fragments = [_fragment("r1", 0, 1), _fragment("r2", 2, 3)]
    assert find_blocks(4, fragments) == [[0, 1], [2, 3]]


def test_uncovered_site_is_its_own_block():
    fragments = [_fragment("r1", 0, 1)]
    assert find_blocks(3, fragments) == [[0, 1], [2]]


def test_single_site_fragments_do_not_link():
    fragments = [_fragment("r1", 0), _fragment("r2", 1)]
    assert find_blocks(2, fragments) == [[0], [1]]
