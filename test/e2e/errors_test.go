package e2e

import (
	"strconv"
	"strings"
	"testing"
	"time"

	"imaplite/internal/server"
	"imaplite/internal/store"
)

// TestErrorClassDistinction asserts the required failure families are
// distinguishable on the wire by status + response code:
//   - input error         -> BAD (malformed arguments)
//   - state conflict      -> BAD when wrong state; NO [UIDVALIDITY/READ-ONLY]
//   - auth failure        -> NO [AUTHENTICATIONFAILED]
//   - resource exhaustion -> [TOOBIG] plus connection close
//   - computation failure -> NO [PARSE] on a deliberately malformed message
func TestErrorClassDistinction(t *testing.T) {
	w := startWorld(t)

	// State/input errors.
	c := dial(t, w, "err-in")
	defer c.close()
	if comp := completion(t, c.run("E1", "EXPUNGE"), "E1"); comp.Status != "BAD" {
		t.Fatalf("EXPUNGE before auth want BAD, got %s", comp.Status)
	}
	c.run("L1", "LOGIN tester Tester#2025!")
	c.run("S1", "SELECT INBOX")
	mal := c.run("M1", "FETCH x (UID)")
	if comp := completion(t, mal, "M1"); comp.Status != "BAD" {
		t.Fatalf("non-numeric seqset want BAD input error, got %s [%s]", comp.Status, comp.Code)
	}

	// Authentication failure.
	c2 := dial(t, w, "err-auth")
	defer c2.close()
	af := c2.run("A1", "LOGIN tester wrong-password")
	if comp := completion(t, af, "A1"); comp.Status != "NO" || comp.Code != "AUTHENTICATIONFAILED" {
		t.Fatalf("bad credentials want NO [AUTHENTICATIONFAILED], got %s [%s]", comp.Status, comp.Code)
	}

	// Resource exhaustion: a literal over the per-connection cap.
	wSmall := startWorldOpt(t, func(cfg *server.Config) { cfg.Limits.MaxLiteral = 16 })
	c3 := dial(t, wSmall, "err-res")
	defer c3.close()
	c3.run("L1", "LOGIN tester Tester#2025!")
	recs := c3.runSafe("X1", "LOGIN lit {n}", []byte("01234567890123456789"))
	sawResource := false
	for _, r := range recs {
		if (r.IsUntag && r.UntagKey == "BYE") || strings.Contains(r.raw, "TOOBIG") {
			sawResource = true
		}
	}
	if !sawResource {
		t.Fatalf("oversized literal should surface TOOBIG/BYE, got %v", recs)
	}

	// Computation failure: PARSE on a message with a NUL in its headers.
	uid := seedCorruptMessage(t, w)
	c4 := dial(t, w, "err-parse")
	defer c4.close()
	c4.run("L1", "LOGIN tester Tester#2025!")
	c4.run("S1", "SELECT INBOX")
	pf := c4.run("P1", "UID FETCH "+strconv.Itoa(int(uid))+" (ENVELOPE)")
	comp := completion(t, pf, "P1")
	if comp.Status != "NO" || comp.Code != "PARSE" {
		t.Fatalf("malformed message want NO [PARSE] computation failure, got %s [%s] (%s)",
			comp.Status, comp.Code, comp.Text)
	}
}

// TestExaminerReadOnly enforces the EXAMINE read-only state conflict.
func TestExaminerReadOnly(t *testing.T) {
	w := startWorld(t)
	c := dial(t, w, "examine")
	defer c.close()
	c.run("L1", "LOGIN tester Tester#2025!")
	if comp := completion(t, c.run("S1", "EXAMINE INBOX"), "S1"); comp.Code != "READ-ONLY" {
		t.Fatalf("EXAMINE completion want READ-ONLY, got [%s]", comp.Code)
	}
	if comp := completion(t, c.run("T1", "STORE 1 +FLAGS (\\Deleted)"), "T1"); comp.Status != "NO" || comp.Code != "READ-ONLY" {
		t.Fatalf("STORE under EXAMINE want NO [READ-ONLY], got %s [%s]", comp.Status, comp.Code)
	}
	if comp := completion(t, c.run("E1", "EXPUNGE"), "E1"); comp.Status != "NO" || comp.Code != "READ-ONLY" {
		t.Fatalf("EXPUNGE under EXAMINE want NO [READ-ONLY], got %s [%s]", comp.Status, comp.Code)
	}
}

// TestConcurrentObserversSeeSameOrder opens several watcher connections, then
// two actors remove messages; every watcher must record the identical EXPUNGE
// sequence-number stream (one total order for the mailbox).
func TestConcurrentObserversSeeSameOrder(t *testing.T) {
	w := startWorld(t)
	const watchers = 3
	watchersC := make([]*client, watchers)
	for i := range watchersC {
		wc := dial(t, w, "watch"+strconv.Itoa(i))
		wc.run("L1", "LOGIN watcher Watcher#2025!")
		wc.run("S1", "SELECT INBOX")
		watchersC[i] = wc
	}

	actorA := dial(t, w, "actorA")
	defer actorA.close()
	actorA.run("L1", "LOGIN tester Tester#2025!")
	actorA.run("S1", "SELECT INBOX")
	actorB := dial(t, w, "actorB")
	defer actorB.close()
	actorB.run("L1", "LOGIN tester Tester#2025!")
	actorB.run("S1", "SELECT INBOX")

	// Each removal targets the then-current position 2: after the first
	// deletion everything collapses, so the second removal also reports 2.
	actorA.run("D1", "STORE 2 +FLAGS (\\Deleted)")
	actorA.run("X1", "EXPUNGE")
	actorB.run("D2", "STORE 2 +FLAGS (\\Deleted)")
	actorB.run("X2", "EXPUNGE")

	time.Sleep(200 * time.Millisecond)

	for i, wc := range watchersC {
		recs := wc.run("N1", "NOOP")
		var seqs []int
		for _, r := range recs {
			if r.IsUntag && r.UntagKey == "EXPUNGE" {
				seqs = append(seqs, r.Seq)
			}
		}
		if len(seqs) != 2 || seqs[0] != 2 || seqs[1] != 2 {
			t.Fatalf("watcher %d observed expunge stream %v, want [2 2]", i, seqs)
		}
	}
}

// TestLiteralLogin proves credentials can be supplied as byte-counted
// literals, including non-ASCII bytes.
func TestLiteralLogin(t *testing.T) {
	w := startWorld(t)
	c := dial(t, w, "lit")
	defer c.close()
	recs := c.run("L1", "LOGIN lit {n}", []byte("litpäss"))
	if comp := completion(t, recs, "L1"); comp.Status != "OK" {
		t.Fatalf("literal LOGIN want OK, got %s (%s)", comp.Status, comp.Text)
	}
}

// TestStateGating checks commands are rejected in the wrong session state.
func TestStateGating(t *testing.T) {
	w := startWorld(t)
	c := dial(t, w, "gate")
	defer c.close()
	if comp := completion(t, c.run("F1", "FETCH 1 (UID)"), "F1"); comp.Status != "BAD" {
		t.Fatalf("FETCH before auth want BAD, got %s", comp.Status)
	}
	c.run("L1", "LOGIN tester Tester#2025!")
	if comp := completion(t, c.run("F2", "FETCH 1 (UID)"), "F2"); comp.Status != "BAD" {
		t.Fatalf("FETCH before SELECT want BAD state conflict, got %s", comp.Status)
	}
}

// seedCorruptMessage appends a header-NUL message and returns its UID.
func seedCorruptMessage(t *testing.T, w *world) uint32 {
	t.Helper()
	corrupt := []byte("Subject: bad\x00header\r\n\r\nbody")
	uid, err := w.store.InsertMessage(testCtx(), "INBOX", store.SeedMessage{
		Raw: corrupt, InternalDate: time.Date(2025, 9, 1, 0, 0, 0, 0, time.UTC),
	})
	if err != nil {
		t.Fatalf("insert corrupt: %v", err)
	}
	return uid
}
