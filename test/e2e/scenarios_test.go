package e2e

import (
	"bytes"
	"strconv"
	"strings"
	"testing"

	"imaplite/internal/fixture"
)

// TestSequenceShiftButUIDStable is the defining identity test: deleting a
// low-numbered message shifts every later sequence number down by one, while
// the UID stays attached to the same message content throughout.
func TestSequenceShiftButUIDStable(t *testing.T) {
	w := startWorld(t)
	c := dial(t, w, "seq")
	defer c.close()
	c.loginSelect(t, "tester", "Tester#2025!", "INBOX")

	// Baseline: 5 messages; fetch UID and a stable content marker (Subject).
	base := c.run("F0", "FETCH 1:5 (UID BODY[HEADER.FIELDS (SUBJECT)])")
	if n := len(untaggedByKey(base, "FETCH")); n != 5 {
		t.Fatalf("baseline: want 5 FETCH records, got %d", n)
	}
	subjects := map[int]string{} // seq -> subject
	for _, r := range untaggedByKey(base, "FETCH") {
		subjects[r.Seq] = subjectFor(t, r)
	}
	// Explicit expected fixture identities (declared in manifest order).
	if subjects[1] != "Welcome to the IMAPlite fixture mailbox" {
		t.Fatalf("seq1 subject mismatch: %q", subjects[1])
	}
	if subjects[3] != "Multipart sample" {
		t.Fatalf("seq3 subject mismatch: %q", subjects[3])
	}

	// Remember the message currently at sequence 3 ("Multipart sample").
	before := c.run("F1", "FETCH 3 (UID)")
	uid3 := atoiOrFail(t, fetchOne(t, before, 3).Fetch["UID"])

	// Mark sequence 2 \Deleted and EXPUNGE.
	if d := completion(t, c.run("D1", "STORE 2 +FLAGS (\\Deleted)"), "D1"); d.Status != "OK" {
		t.Fatalf("STORE failed: %s", d.raw)
	}
	exp := c.run("X1", "EXPUNGE")
	expunges := untaggedByKey(exp, "EXPUNGE")
	if len(expunges) != 1 || expunges[0].Seq != 2 {
		t.Fatalf("want single '* 2 EXPUNGE', got %+v", expunges)
	}
	if n := existsCount(t, exp); n != 4 {
		t.Fatalf("after expunge EXISTS want 4, got %d", n)
	}

	// The old seq-3 message must now be seq 2 ...
	after := c.run("F2", "FETCH 2 (UID BODY[HEADER.FIELDS (SUBJECT)])")
	r2 := fetchOne(t, after, 2)
	if got := atoiOrFail(t, r2.Fetch["UID"]); got != uid3 {
		t.Fatalf("identity moved with UID: want UID %d at new seq 2, got %d", uid3, got)
	}
	if subj := subjectFor(t, r2); subj != "Multipart sample" {
		t.Fatalf("content identity broken after shift: %q", subj)
	}

	// ... and UID FETCH under the same UID returns that same message even
	// though its sequence number changed.
	uidFetch := c.run("F3", "UID FETCH "+strconv.Itoa(uid3)+" (UID RFC822.SIZE)")
	uf := untaggedByKey(uidFetch, "FETCH")
	if len(uf) != 1 {
		t.Fatalf("UID FETCH want 1 record, got %d", len(uf))
	}
	if got := atoiOrFail(t, uf[0].Fetch["UID"]); got != uid3 {
		t.Fatalf("UID FETCH returned UID %d, want %d", got, uid3)
	}
	if uf[0].Seq != 2 {
		t.Fatalf("UID FETCH reports seq %d, want shifted seq 2", uf[0].Seq)
	}
}

// TestExpungeRenumberChain exercises several removals and the RFC rule that
// renumbering happens after EACH removal: EXPUNGE reports must be 2 then 2
// again as successive deletions collapse the sequence.
func TestExpungeRenumberChain(t *testing.T) {
	w := startWorld(t)
	c := dial(t, w, "chain")
	defer c.close()
	c.loginSelect(t, "tester", "Tester#2025!", "INBOX")

	// Mark original seq 2 and seq 3 deleted. After removing position 2, the
	// old seq 3 is now at position 2, so the second EXPUNGE report is also 2.
	c.run("D1", "STORE 2,3 +FLAGS (\\Deleted)")
	exp := c.run("X1", "EXPUNGE")
	seqs := []int{}
	uids := []int{}
	for _, r := range untaggedByKey(exp, "EXPUNGE") {
		seqs = append(seqs, r.Seq)
		uids = append(uids, r.Seq)
	}
	if len(seqs) != 2 || seqs[0] != 2 || seqs[1] != 2 {
		t.Fatalf("per-step renumbering want [2 2], got %v", seqs)
	}
	if n := existsCount(t, exp); n != 3 {
		t.Fatalf("EXISTS want 3, got %d", n)
	}
	_ = uids
}

// TestStaleUIDValidity proves that a changed UIDVALIDITY invalidates cached
// UIDs: a message fetchable by UID before rotation is rejected (with the
// UIDVALIDITY state code) once the epoch rotates, and works after re-SELECT
// against the new (empty) epoch.
func TestStaleUIDValidity(t *testing.T) {
	w := startWorld(t)
	c := dial(t, w, "stale")
	defer c.close()
	sel := c.loginSelect(t, "tester", "Tester#2025!", "INBOX")

	// Capture the SELECTed UIDVALIDITY.
	var oldValidity int
	for _, r := range sel {
		if r.IsUntag && r.UntagKey == "OK" && r.Code == "UIDVALIDITY" {
			oldValidity = atoiOrFail(t, r.Text)
		}
	}
	if oldValidity != 1001 {
		t.Fatalf("fixture UIDVALIDITY want 1001, got %d", oldValidity)
	}

	// UID 1 works in the current epoch.
	ok := c.run("U1", "UID FETCH 1 (UID)")
	if len(untaggedByKey(ok, "FETCH")) != 1 {
		t.Fatalf("UID 1 should fetch before rotation")
	}

	// External actor rotates the epoch (as a reconnect/new client would see
	// after the mailbox is rebuilt). Drive it through the store directly to
	// simulate the out-of-band event, mirroring the control-plane ROTATE.
	newV, err := w.store.RotateUIDValidity(testContext(), "INBOX")
	if err != nil {
		t.Fatalf("rotate: %v", err)
	}
	if newV == uint32OrZero(oldValidity) {
		t.Fatalf("UIDVALIDITY did not change")
	}

	// The still-selected connection must now refuse message commands with the
	// UIDVALIDITY state conflict, NOT silently honour the old UID.
	bad := c.run("U2", "UID FETCH 1 (UID)")
	comp := completion(t, bad, "U2")
	if comp.Status != "NO" || comp.Code != "UIDVALIDITY" {
		t.Fatalf("stale epoch want NO [UIDVALIDITY], got %s %s (%s)", comp.Status, comp.Code, comp.Text)
	}
	if len(untaggedByKey(bad, "FETCH")) != 0 {
		t.Fatalf("stale UID must not return message data")
	}

	// Re-SELECT observes the new UIDVALIDITY and an empty epoch.
	re := c.run("S2", "SELECT INBOX")
	var sawNew bool
	for _, r := range re {
		if r.IsUntag && r.UntagKey == "OK" && r.Code == "UIDVALIDITY" {
			if atoiOrFail(t, r.Text) == int(newV) {
				sawNew = true
			}
		}
	}
	if !sawNew {
		t.Fatalf("re-SELECT did not advertise new UIDVALIDITY %d", newV)
	}
	if n := existsCount(t, re); n != 0 {
		t.Fatalf("rotated epoch want 0 messages, got %d", n)
	}
}

// TestBinaryLiteralByteFidelity asserts literals are counted by bytes and
// returned byte-for-byte, including NUL / 0xFF / embedded CRLF.
func TestBinaryLiteralByteFidelity(t *testing.T) {
	w := startWorld(t)
	c := dial(t, w, "bin")
	defer c.close()
	c.loginSelect(t, "tester", "Tester#2025!", "INBOX")

	want := fixture.BinaryMessage(5)

	// UID 5 is the generated binary message. BODY[] must be a literal whose
	// declared length equals the byte length and payload equals want exactly.
	recs := c.run("B1", "UID FETCH 5 (UID BODY[])")
	fetches := untaggedByKey(recs, "FETCH")
	if len(fetches) != 1 {
		t.Fatalf("want 1 FETCH, got %d", len(fetches))
	}
	fr := fetches[0]
	if atoiOrFail(t, fr.Fetch["UID"]) != 5 {
		t.Fatalf("binary message UID want 5")
	}
	got, ok := fr.FetchLit["BODY[]"]
	if !ok {
		t.Fatalf("BODY[] not returned as literal; items=%v", fr.FetchSeen)
	}
	if len(got) != len(want) {
		t.Fatalf("literal byte length: declared/got %d, fixture %d", len(got), len(want))
	}
	if !bytes.Equal(got, want) {
		t.Fatalf("binary literal not byte-identical: first diff at %d", firstDiff(got, want))
	}
	// Explicit sentinel bytes must survive.
	if !bytes.Contains(got, []byte{0x00, 0xFF, 0xFE, 0x01}) {
		t.Fatalf("NUL/0xFF sentinel missing from literal")
	}
}

// TestTagAssociation verifies responses on a pipelined batch are each tagged
// to the command that requested them, in command order.
func TestTagAssociation(t *testing.T) {
	w := startWorld(t)
	c := dial(t, w, "tag")
	defer c.close()
	c.loginSelect(t, "tester", "Tester#2025!", "INBOX")

	// Pipeline three distinct commands in one write; completions must come
	// back tagged t10/t11/t12 in that order.
	c.write("t10 FETCH 1 (UID)\r\nt11 FETCH 2 (UID)\r\nt12 NOOP\r\n")
	var tags []string
	for _, want := range []string{"t10", "t11", "t12"} {
		recs := c.readUntilTag(want)
		last := recs[len(recs)-1]
		if last.Tag != want || last.Status != "OK" {
			t.Fatalf("pipeline completion for %s: %s %s", want, last.Tag, last.Status)
		}
		tags = append(tags, last.Tag)
	}
	if strings.Join(tags, ",") != "t10,t11,t12" {
		t.Fatalf("tag order want t10,t11,t12 got %v", tags)
	}
}

// TestUnknownCommandAndDataItems checks explicit rejection: unknown verbs are
// BAD, undeclared data items are BAD, and framing stays aligned so the next
// command still works.
func TestUnknownCommandAndDataItems(t *testing.T) {
	w := startWorld(t)
	c := dial(t, w, "unk")
	defer c.close()
	c.loginSelect(t, "tester", "Tester#2025!", "INBOX")

	u := c.run("U1", "XYZZY")
	if comp := completion(t, u, "U1"); comp.Status != "BAD" {
		t.Fatalf("unknown verb want BAD, got %s", comp.Status)
	}
	// An out-of-scope but real command (APPEND) is explicitly refused too.
	// The client performs the real literal handshake ({n} marker + bytes).
	a := c.run("A1", "APPEND INBOX {n}", []byte("abc"))
	if comp := completion(t, a, "A1"); comp.Status != "BAD" {
		t.Fatalf("APPEND want explicit BAD, got %s", comp.Status)
	}
	// Undeclared data item.
	f := c.run("F1", "FETCH 1 (RFC822.MSGID)")
	if comp := completion(t, f, "F1"); comp.Status != "BAD" {
		t.Fatalf("unknown data item want BAD, got %s", comp.Status)
	}
	// Stream still aligned: a valid command succeeds afterward.
	ok := c.run("F2", "FETCH 1 (UID)")
	if comp := completion(t, ok, "F2"); comp.Status != "OK" {
		t.Fatalf("stream realignment failed, got %s (%s)", comp.Status, comp.Text)
	}
}
