"""External sort tests: chunked spilling must be observably equivalent
to an in-memory sort, and the pipeline must give identical results
whether or not it spills."""

import random

from coverage_depth.externalsort import external_sort


class TestExternalSort:
    def test_spilled_sort_matches_builtin_sort(self, tmp_path):
        rng = random.Random(42)
        items = [
            {"read": f"r{rng.randrange(500)}", "pos": rng.randrange(10_000)}
            for _ in range(2_000)
        ]
        key = lambda d: (d["read"], d["pos"])
        out = list(external_sort(items, key=key, chunk_size=64,
                                 spill_dir=str(tmp_path)))
        assert out == sorted(items, key=key)

    def test_single_chunk_takes_memory_fast_path(self, tmp_path):
        items = [[3], [1], [2]]
        out = list(external_sort(items, key=lambda x: (x[0],), chunk_size=10,
                                 spill_dir=str(tmp_path)))
        assert out == [[1], [2], [3]]
        assert list(tmp_path.iterdir()) == []  # nothing spilled

    def test_spill_files_cleaned_up(self, tmp_path):
        items = [[i] for i in range(100)]
        list(external_sort(items, key=lambda x: (x[0],), chunk_size=7,
                           spill_dir=str(tmp_path)))
        assert list(tmp_path.iterdir()) == []

    def test_empty_input(self, tmp_path):
        assert list(external_sort([], key=lambda x: (x,), chunk_size=4,
                                  spill_dir=str(tmp_path))) == []

    def test_duplicate_keys_stable_enough_for_sets(self, tmp_path):
        items = [(i % 5, i) for i in range(200)]
        out = list(external_sort(items, key=lambda t: (t[0], t[1]),
                                 chunk_size=16, spill_dir=str(tmp_path),
                                 from_jsonable=tuple))
        assert out == sorted(items)
