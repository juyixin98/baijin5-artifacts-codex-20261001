package tests

import (
	"testing"

	"simdc/internal/pipeline"
)

func TestMissingAndMismatchedInputs(t *testing.T) {
	src := "input a[2]int64\noutput o[2]int64\nfor i := 0 .. len(a) { o[i] = a[i] }\n"
	missing := pipeline.Request{RequestID: "req-missing-050", Source: src, Width: 2}
	resp := pipeline.Execute(missing)
	if string(resp.Verdict) != "UNDETERMINED" ||
		string(resp.Issues[0].Code) != "MISSING_INPUT" {
		t.Fatalf("missing input: verdict=%s issues=%+v", resp.Verdict, resp.Issues)
	}

	badLen := pipeline.Request{
		RequestID: "req-badlen-051", Source: src, Width: 2,
		Arrays: map[string][]int64{"a": {1, 2, 3}}, // declared length 2
	}
	resp2 := pipeline.Execute(badLen)
	if string(resp2.Verdict) != "UNDETERMINED" ||
		string(resp2.Issues[0].Code) != "INPUT_LENGTH_MISMATCH" {
		t.Fatalf("length mismatch: verdict=%s issues=%+v", resp2.Verdict, resp2.Issues)
	}
}
