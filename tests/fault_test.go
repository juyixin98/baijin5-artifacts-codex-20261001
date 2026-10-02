package tests

import (
	"testing"

	"simdc/internal/pipeline"
)

// Active lane writes to a computed index that goes out of bounds on the
// tail lane; masked lanes must not attempt the write.
const writeOobSrc = `input idx[5]int64, t[5]int64
output o[4]int64
for i := 0 .. len(idx) {
  if (t[i] != 0) {
    o[idx[i]] = i
  }
}
`

func TestActiveWriteOutOfBounds(t *testing.T) {
	req := pipeline.Request{
		RequestID: "req-writeoob-040",
		Source:    writeOobSrc,
		Width:     3,
		Arrays: map[string][]int64{
			"idx": {0, 9, 1, 3, 4},
			"t":   {1, 0, 1, 1, 1}, // lane4 writes idx=4 into len-4 array -> OOB
		},
	}
	resp := pipeline.Execute(req)
	if string(resp.Verdict) != "ACCEPT" {
		t.Fatalf("verdict=%s issues=%+v", resp.Verdict, resp.Issues)
	}
	if !resp.Diff.FaultsEqual {
		t.Fatalf("fault disagreement: %+v", resp.Diff.FaultSummary)
	}
	if !resp.Simd.Halted || resp.Simd.Fault.Code != "OUTPUT_INDEX_OUT_OF_BOUNDS" {
		t.Fatalf("want active write OOB, got %+v", resp.Simd.Fault)
	}
	if resp.Simd.Fault.GlobalIdx != 4 || resp.Simd.Fault.Stage != "BODY" {
		t.Fatalf("fault location=%+v want idx 4 BODY", resp.Simd.Fault)
	}
	// Prefix semantics: lanes 0 and 2 committed; lane 3 also committed (idx3).
	want := []int64{0, 2, 0, 3}
	assertSlice(t, "o", resp.Simd.Arrays[0], want)
}

// Width sweep: same request across widths 1..7 must always match reference,
// proving tail masks are correct for every remainder class.
const copySrc = `input a[5]int64
output o[5]int64
for i := 0 .. len(a) { o[i] = a[i]*2 + 1 }
`

func TestWidthSweepTailMasks(t *testing.T) {
	for w := 1; w <= 7; w++ {
		resp := pipeline.Execute(pipeline.Request{
			RequestID: "req-widthsweep", Source: copySrc, Width: w,
			Arrays: map[string][]int64{"a": {1, 2, 3, 4, 5}},
		})
		if string(resp.Verdict) != "ACCEPT" || !resp.Diff.Match {
			t.Fatalf("width=%d verdict=%s diff=%+v", w, resp.Verdict, resp.Diff)
		}
		assertSlice(t, "o", resp.Simd.Arrays[0], []int64{3, 5, 7, 9, 11})
	}
}

func TestActiveReadOOBFaultCategory(t *testing.T) {
	src := `input b[3]int64, idx[4]int64
output o[4]int64
for i := 0 .. len(idx) { o[i] = b[idx[i]] }
`
	resp := pipeline.Execute(pipeline.Request{
		RequestID: "req-readoob-041", Source: src, Width: 2,
		Arrays: map[string][]int64{
			"b":   {10, 20, 30},
			"idx": {0, 2, 5, 1}, // lane 2 OOB (first), lane 3 never runs
		},
	})
	if !resp.Simd.Halted || resp.Simd.Fault.Code != "INDEX_OUT_OF_BOUNDS" {
		t.Fatalf("want INDEX_OUT_OF_BOUNDS got %+v", resp.Simd.Fault)
	}
	if resp.Simd.Fault.GlobalIdx != 2 {
		t.Fatalf("fault gi=%d want 2", resp.Simd.Fault.GlobalIdx)
	}
	if !resp.Diff.FaultsEqual {
		t.Fatalf("fault category mismatch: %+v", resp.Diff.FaultSummary)
	}
}
