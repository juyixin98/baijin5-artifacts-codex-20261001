"""Sample-peak vs true-peak contract probe.

The contract (config.py) fixes peak_mode="sample": the threshold bounds
per-sample peaks only. This test feeds a sine whose TRUE (inter-sample)
peak exceeds the threshold while every SAMPLE peak stays below it, and
asserts the documented behavior: the limiter stays at unity gain, the
output sample peak respects the threshold, and the reconstructed true
peak (16x polyphase resampling, scipy) measurably exceeds it. This is
evidence for the contract boundary, not a bug.
"""

import numpy as np
from scipy.signal import resample_poly

from limiter import fixtures
from limiter.stream import process_offline


def test_sample_peak_mode_leaves_intersample_crest_untouched(cfg, tlog):
    fx = fixtures.intersample_crest()
    pcm = fx.pcm()
    res = process_offline(pcm, cfg)

    up = resample_poly(res.output[:, 0], 16, 1)
    margin = 64  # discard resampler edge transients
    true_peak = float(np.max(np.abs(up[margin:-margin])))

    tlog(
        "sample_peak_vs_true_peak",
        fixture_id=fx.id,
        fixture_sha256=fx.sha256(),
        input_sample_peak=res.input_peak,
        output_sample_peak=res.output_peak,
        reconstructed_true_peak_16x=true_peak,
        threshold=cfg.threshold,
        gain_all_unity=bool(np.all(res.gain == 1.0)),
        conclusion="peak_mode=sample: true peak may exceed threshold by design",
    )

    # Every input sample peak is below threshold -> limiter must be inactive.
    assert res.input_peak < cfg.threshold
    assert np.all(res.gain == 1.0)
    # Contract held on the sample-peak axis.
    assert res.output_peak <= cfg.threshold
    # The reconstructed true peak exceeds the threshold.
    assert true_peak > cfg.threshold
