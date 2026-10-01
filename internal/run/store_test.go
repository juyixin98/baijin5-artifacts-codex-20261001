package run_test

import (
	"testing"
	"time"

	"ntpsim/internal/core"
	"ntpsim/internal/run"
)

func TestSQLiteRoundTrip(t *testing.T) {
	st, err := run.OpenSQLite(":memory:")
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	defer st.Close()

	at := time.Date(2024, 6, 1, 12, 0, 0, 0, time.UTC)
	samples := []core.Sample{
		core.Evaluate(core.RawTimestamps{
			SourceID:    "a",
			T1:          at,
			T2:          at.Add(120 * time.Millisecond),
			T3:          at.Add(120 * time.Millisecond),
			T4:          at.Add(200 * time.Millisecond),
			CollectedAt: at.Add(200 * time.Millisecond),
			Stratum:     2,
		}, 0),
	}
	sel := core.Select(samples, at.Add(200*time.Millisecond), core.DefaultPolicy(), core.SourceHistory{})
	if err := st.SaveRound("rid-1", 1, at.Add(200*time.Millisecond), samples, sel); err != nil {
		t.Fatalf("save: %v", err)
	}
	// Same run+round must not silently overwrite (PK guard surfaces error).
	if err := st.SaveRound("rid-1", 1, at, samples, sel); err == nil {
		t.Fatal("duplicate (run,round) insert should fail")
	}

	rows, err := st.QueryRounds("rid-1")
	if err != nil {
		t.Fatalf("query: %v", err)
	}
	if len(rows) != 1 {
		t.Fatalf("rows=%d want 1", len(rows))
	}
	r := rows[0]
	if r.Status != "OK" || r.Selected != "a" {
		t.Fatalf("status=%s selected=%s", r.Status, r.Selected)
	}
	// theta = (120-80)/2 = 20ms.
	if r.Offset != 20*time.Millisecond {
		t.Fatalf("offset=%v want 20ms", r.Offset)
	}
	if r.Accepted != 1 {
		t.Fatalf("accepted=%d", r.Accepted)
	}
}
