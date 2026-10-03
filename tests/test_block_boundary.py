"""Block-boundary probes: a burst straddling block edges must be handled
identically regardless of how the input is chunked, and the ceiling
promise must hold across the boundary."""

import numpy as np

from limiter import fixtures
from limiter.stream import StreamingLimiter, process_offline

from .reference import ref_limit


def run_chunked(cfg, pcm, sizes):
    lim = StreamingLimiter(cfg)
    outs, gains = [], []
    i = 0
    k = 0
    while i < len(pcm):
        s = sizes[k % len(sizes)]
        block = pcm[i : i + s]
        res = lim.process(block)
        outs.append(res.pcm)
        gains.append(res.gain)
        i += len(block)
        k += 1
    tail = lim.flush()
    outs.append(tail.pcm)
    gains.append(tail.gain)
    return np.vstack(outs), np.concatenate(gains)


def test_chunking_is_sample_exact(cfg, tlog):
    fx = fixtures.boundary_burst()
    pcm = fx.pcm()
    bs = fx.meta["block_size"]

    one_shot = process_offline(pcm, cfg)
    out_a, gain_a = run_chunked(cfg, pcm, [bs])
    out_b, gain_b = run_chunked(cfg, pcm, [1, 7, 333, 1024, 2048])

    tlog(
        "block_boundary_chunking",
        fixture_id=fx.id,
        fixture_sha256=fx.sha256(),
        block_size=bs,
        burst_positions=list(fx.meta["positions"]),
        chunkings=[[bs], [1, 7, 333, 1024, 2048]],
        max_abs_diff_blockwise=float(np.max(np.abs(one_shot.output - out_a))),
        max_abs_diff_irregular=float(np.max(np.abs(one_shot.output - out_b))),
        output_peak=one_shot.output_peak,
        promised_ceiling=one_shot.promised_ceiling,
    )

    assert np.array_equal(one_shot.output, out_a)
    assert np.array_equal(one_shot.gain, gain_a)
    assert np.array_equal(one_shot.output, out_b)
    assert np.array_equal(one_shot.gain, gain_b)
    # Burst sits at frames bs-2, bs-1, bs: all three must be attenuated
    # under the ceiling promise even though they straddle the boundary.
    assert one_shot.output_peak <= one_shot.promised_ceiling + 1e-12


def test_boundary_burst_matches_independent_reference(cfg, tlog):
    fx = fixtures.boundary_burst()
    pcm = fx.pcm()
    res = process_offline(pcm, cfg)
    ref_out, ref_gain = ref_limit(
        pcm,
        threshold=cfg.threshold,
        sample_rate=cfg.sample_rate,
        attack_ms=cfg.attack_ms,
        release_ms=cfg.release_ms,
        lookahead_ms=cfg.lookahead_ms,
    )
    tlog(
        "boundary_reference_crosscheck",
        fixture_sha256=fx.sha256(),
        frames=int(len(pcm)),
        max_abs_diff=float(np.max(np.abs(res.output - ref_out))),
    )
    assert np.array_equal(res.output, ref_out)
    assert np.array_equal(res.gain, ref_gain)
