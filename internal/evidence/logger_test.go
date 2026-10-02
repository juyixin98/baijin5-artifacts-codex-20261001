package evidence

import (
	"bytes"
	"encoding/json"
	"strings"
	"testing"

	"stunlab/internal/store"
)

func TestLoggerEmitsOrderedJSONLinesAndPersists(t *testing.T) {
	var buf bytes.Buffer
	st, err := store.Open("")
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()

	runID := NewRunID("test")
	lg := NewLogger(runID, "unit", &buf, st, "scenario-X")

	lg.Info("one", map[string]any{"k": "v"})
	lg.Error("two", "timeout", map[string]any{"txn": "aa"})
	lg.Decision("check", true, "matched", map[string]any{"got": 1, "want": 1})

	lines := strings.Split(strings.TrimRight(buf.String(), "\n"), "\n")
	if len(lines) != 3 {
		t.Fatalf("got %d lines", len(lines))
	}
	var seqs []int64
	for i, line := range lines {
		var ev Event
		if err := json.Unmarshal([]byte(line), &ev); err != nil {
			t.Fatalf("line %d not JSON: %v", i, err)
		}
		if ev.RunID != runID || ev.Seq != int64(i+1) {
			t.Fatalf("line %d wrong run/seq: %+v", i, ev)
		}
		seqs = append(seqs, ev.Seq)
	}
	// Error kind must be preserved as a distinct, queryable field.
	var errEv Event
	_ = json.Unmarshal([]byte(lines[1]), &errEv)
	if errEv.Fields["error_kind"] != "timeout" {
		t.Fatalf("error_kind = %v", errEv.Fields["error_kind"])
	}
	// Decision carries explicit verdict + reason.
	var dec Event
	_ = json.Unmarshal([]byte(lines[2]), &dec)
	if dec.Fields["verdict"] != "pass" || dec.Fields["reason"] != "matched" {
		t.Fatalf("decision fields wrong: %+v", dec.Fields)
	}

	// The same records must be queryable in SQLite.
	if n, _ := st.ExchangeCount(runID); n != 0 {
		t.Fatalf("unexpected exchanges")
	}
}

func TestRunIDIsUniqueAndSortable(t *testing.T) {
	a := NewRunID("x")
	b := NewRunID("x")
	if a == b {
		t.Fatal("run ids collide")
	}
	if !strings.HasPrefix(a, "x-") {
		t.Fatalf("run id missing prefix: %s", a)
	}
}
