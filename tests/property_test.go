package tests

import (
	"math/rand"
	"testing"

	"simdc/internal/pipeline"
)

// TestRandomizedDifferential runs a fixed-seed family of programs combining
// guarded division and gathers with random indices and predicates across
// several SIMD widths. The oracle is the independent scalar reference inside
// the pipeline; this test asserts the two implementations agree on every
// channel and on the fault category/location.
func TestRandomizedDifferential(t *testing.T) {
	const src = `input a[7]int64, b[5]int64, d[7]int64, idx[7]int64, t[7]int64
output out[7]int64
for i := 0 .. len(a) {
  if (t[i] != 0) {
    out[i] = a[i]/d[i] + b[idx[i]]
  }
}
`
	rng := rand.New(rand.NewSource(20261002))
	for iter := 0; iter < 60; iter++ {
		a := make([]int64, 7)
		d := make([]int64, 7)
		tv := make([]int64, 7)
		idx := make([]int64, 7)
		b := []int64{1, 2, 3, 5, 7}
		for i := range a {
			a[i] = int64(rng.Intn(40) + 1)
			// divisor: sometimes zero, but only dangerous when t[i]!=0
			if rng.Intn(3) == 0 {
				d[i] = 0
			} else {
				d[i] = int64(rng.Intn(4) + 1)
			}
			tv[i] = int64(rng.Intn(2))
			if tv[i] == 0 {
				// masked lane: make index deliberately invalid to prove isolation
				idx[i] = int64(rng.Intn(20) + 50)
			} else {
				idx[i] = int64(rng.Intn(5)) // always in range for b
			}
		}
		width := 1 + rng.Intn(8)
		req := pipeline.Request{
			RequestID: "prop",
			Source:    src,
			Width:     width,
			Arrays: map[string][]int64{
				"a": a, "b": b, "d": d, "idx": idx, "t": tv,
			},
		}
		resp := pipeline.Execute(req)
		if string(resp.Verdict) != "ACCEPT" {
			t.Fatalf("iter=%d width=%d verdict=%s issues=%+v", iter, width, resp.Verdict, resp.Issues)
		}
		if resp.Diff == nil || !resp.Diff.Match {
			t.Fatalf("iter=%d width=%d diff=%+v\narrays=%v d=%v t=%v idx=%v",
				iter, width, resp.Diff, a, d, tv, idx)
		}
	}
}
