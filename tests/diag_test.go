package tests

import (
	"encoding/json"
	"strings"
	"testing"

	"simdc/internal/diag"
	"simdc/internal/pipeline"
)

func TestDiagnosticsAreCorrelatedAndRedacted(t *testing.T) {
	secret := []int64{9912345, 8812345}
	req := pipeline.Request{
		RequestID: "req-secret-020",
		Source:    "input s[2]int64\noutput out[2]int64\nfor i := 0 .. len(s) { out[i] = s[i] }\n",
		Width:     8, // wider than data: exercises explicit tail mask
		Arrays:    map[string][]int64{"s": secret},
	}
	resp := pipeline.Execute(req)
	if resp.Verdict != "ACCEPT" || !resp.Diff.Match {
		t.Fatalf("verdict=%s diff=%+v", resp.Verdict, resp.Diff)
	}
	b, err := json.Marshal(resp.Events)
	if err != nil {
		t.Fatal(err)
	}
	printed := string(b)
	for _, v := range secret {
		if strings.Contains(printed, strings.TrimSpace(string(rune(0)))) {
			t.Fatal("impossible guard")
		}
		// The raw payload value must never appear in diagnostics.
		if strings.Contains(printed, jsonNum(v)) {
			t.Fatalf("diagnostics leaked payload value %d: %s", v, printed)
		}
	}
	var sawID, sawLen bool
	for _, e := range resp.Events {
		if e.RequestID != "req-secret-020" {
			t.Fatalf("event missing request id: %+v", e)
		}
		if e.KeyState != nil {
			if lens, ok := e.KeyState["array_lens"].(map[string]int); ok {
				if lens["s"] == 2 {
					sawLen = true
				}
			}
			if w, ok := e.KeyState["simd_width"].(int); ok && w == 8 {
				sawID = true
			}
		}
	}
	if !sawID || !sawLen {
		t.Fatalf("missing key state in events: %+v", resp.Events)
	}
	// Sanity: the redaction helper exposes only lengths.
	red := diag.RedactArrays(map[string][]int64{"s": secret})
	if red["s"] != 2 {
		t.Fatalf("redaction = %+v", red)
	}
}

func jsonNum(v int64) string {
	b, _ := json.Marshal(v)
	return string(b)
}
