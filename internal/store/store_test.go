package store

import (
	"context"
	"errors"
	"fmt"
	"testing"
	"time"
)

func testStore(t *testing.T) (*Store, context.Context) {
	t.Helper()
	st, err := Open(context.Background(), t.TempDir()+"/s.db")
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	t.Cleanup(func() { _ = st.Close() })
	return st, context.Background()
}

func seedN(t *testing.T, st *Store, ctx context.Context, n int, uidv uint32) {
	t.Helper()
	msgs := make([]SeedMessage, n)
	for i := range msgs {
		msgs[i] = SeedMessage{
			Raw:          []byte(fmt.Sprintf("subject: m%d\r\n\r\nbody%d", i+1, i+1)),
			InternalDate: time.Unix(1_700_000_000+int64(i), 0).UTC(),
		}
	}
	if err := st.SeedMailbox(ctx, "INBOX", uidv, msgs); err != nil {
		t.Fatalf("seed: %v", err)
	}
}

func TestSequenceDerivedFromUIDOrder(t *testing.T) {
	st, ctx := testStore(t)
	seedN(t, st, ctx, 5, 1001)

	// Sequence positions map 1:1 to ascending UIDs initially.
	got, err := st.FetchBySeq(ctx, "INBOX", []int{1, 3, 5})
	if err != nil {
		t.Fatal(err)
	}
	if uids(got) != "1,3,5" {
		t.Fatalf("initial seq->uid want 1,3,5 got %s", uids(got))
	}
}

func TestExpungeShiftsSequenceNotUID(t *testing.T) {
	st, ctx := testStore(t)
	seedN(t, st, ctx, 5, 1001)

	if _, err := st.SetFlags(ctx, "INBOX", false, []uint32{2}, FlagAdd, []string{`\Deleted`}); err != nil {
		t.Fatal(err)
	}
	removed, exists, err := st.Expunge(ctx, "INBOX")
	if err != nil {
		t.Fatal(err)
	}
	if len(removed) != 1 || removed[0].Seq != 2 || removed[0].UID != 2 {
		t.Fatalf("removal want {seq2 uid2}, got %+v", removed)
	}
	if exists != 4 {
		t.Fatalf("exists want 4 got %d", exists)
	}
	// Old seq 3 (UID 3) is now seq 2; UID 3 content unchanged.
	got, err := st.FetchBySeq(ctx, "INBOX", []int{2})
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 1 || got[0].UID != 3 {
		t.Fatalf("shifted seq2 want UID3, got %+v", got)
	}
	byUID, err := st.FetchByUID(ctx, "INBOX", []uint32{3})
	if err != nil || len(byUID) != 1 {
		t.Fatalf("UID 3 must remain fetchable, got %+v err=%v", byUID, err)
	}
}

func TestExpungePerStepRenumbering(t *testing.T) {
	st, ctx := testStore(t)
	seedN(t, st, ctx, 6, 1001)
	// Flag original seq 2,3,5 (=UID 2,3,5).
	if _, err := st.SetFlags(ctx, "INBOX", false, []uint32{2, 3, 5}, FlagAdd, []string{`\Deleted`}); err != nil {
		t.Fatal(err)
	}
	removed, exists, err := st.Expunge(ctx, "INBOX")
	if err != nil {
		t.Fatal(err)
	}
	// Removing ascending with renumber: seq 2, then old-3 now 2, then old-5
	// now 3. Reported sequence numbers: 2,2,3. UIDs reported: 2,3,5.
	wantSeq := []int{2, 2, 3}
	wantUID := []uint32{2, 3, 5}
	for i, r := range removed {
		if r.Seq != wantSeq[i] || r.UID != wantUID[i] {
			t.Fatalf("removal %d: want seq=%d uid=%d, got seq=%d uid=%d",
				i, wantSeq[i], wantUID[i], r.Seq, r.UID)
		}
	}
	if exists != 3 {
		t.Fatalf("exists want 3 got %d", exists)
	}
	// Survivors are UID 1,4,6 now at seq 1,2,3.
	got, _ := st.FetchBySeq(ctx, "INBOX", []int{1, 2, 3})
	if uids(got) != "1,4,6" {
		t.Fatalf("survivors want 1,4,6 got %s", uids(got))
	}
}

func TestUIDExpungeRestricted(t *testing.T) {
	st, ctx := testStore(t)
	seedN(t, st, ctx, 4, 1001)
	// Flag UIDs 2 and 3 but UID EXPUNGE only the set {3}.
	if _, err := st.SetFlags(ctx, "INBOX", true, []uint32{2, 3}, FlagAdd, []string{`\Deleted`}); err != nil {
		t.Fatal(err)
	}
	removed, exists, err := st.ExpungeUID(ctx, "INBOX", []uint32{3})
	if err != nil {
		t.Fatal(err)
	}
	if len(removed) != 1 || removed[0].UID != 3 || removed[0].Seq != 3 {
		t.Fatalf("UID EXPUNGE want only uid3@seq3, got %+v", removed)
	}
	if exists != 3 {
		t.Fatalf("exists want 3, got %d", exists)
	}
	// UID 2 is still flagged and present.
	got, _ := st.FetchByUID(ctx, "INBOX", []uint32{2})
	if len(got) != 1 || !hasSliceFlag(got[0].Flags, `\Deleted`) {
		t.Fatalf("uid2 should survive still flagged, got %+v", got)
	}
}

func TestRotateUIDValidityInvalidates(t *testing.T) {
	st, ctx := testStore(t)
	seedN(t, st, ctx, 3, 1001)
	info, _ := st.MailboxInfo(ctx, "INBOX")
	if info.UIDValidity != 1001 || info.UIDNext != 4 {
		t.Fatalf("initial counters wrong: %+v", info)
	}
	newV, err := st.RotateUIDValidity(ctx, "INBOX")
	if err != nil || newV != 1002 {
		t.Fatalf("rotate want 1002, got %d err=%v", newV, err)
	}
	after, _ := st.MailboxInfo(ctx, "INBOX")
	if after.UIDValidity != 1002 || after.Exists != 0 || after.UIDNext != 1 {
		t.Fatalf("post-rotate epoch wrong: %+v", after)
	}
	// Old UIDs no longer match anything.
	got, err := st.FetchByUID(ctx, "INBOX", []uint32{1, 2, 3})
	if err != nil || len(got) != 0 {
		t.Fatalf("old UIDs must be invalid after rotation, got %+v", got)
	}
}

func TestFlagModesAndSequenceOfUID(t *testing.T) {
	st, ctx := testStore(t)
	seedN(t, st, ctx, 4, 1001)

	// Replace wipes prior flags; Remove subtracts; Add unions.
	if _, err := st.SetFlags(ctx, "INBOX", true, []uint32{1}, FlagAdd, []string{`\Seen`, `\Draft`}); err != nil {
		t.Fatal(err)
	}
	got, _ := st.FetchByUID(ctx, "INBOX", []uint32{1})
	if !hasSliceFlag(got[0].Flags, `\Seen`) || !hasSliceFlag(got[0].Flags, `\Draft`) {
		t.Fatalf("add flags failed: %v", got[0].Flags)
	}
	if _, err := st.SetFlags(ctx, "INBOX", true, []uint32{1}, FlagRemove, []string{`\Draft`}); err != nil {
		t.Fatal(err)
	}
	got, _ = st.FetchByUID(ctx, "INBOX", []uint32{1})
	if hasSliceFlag(got[0].Flags, `\Draft`) || !hasSliceFlag(got[0].Flags, `\Seen`) {
		t.Fatalf("remove flags failed: %v", got[0].Flags)
	}
	if _, err := st.SetFlags(ctx, "INBOX", true, []uint32{1}, FlagReplace, []string{`\Flagged`}); err != nil {
		t.Fatal(err)
	}
	got, _ = st.FetchByUID(ctx, "INBOX", []uint32{1})
	if len(got[0].Flags) != 1 || got[0].Flags[0] != `\Flagged` {
		t.Fatalf("replace flags failed: %v", got[0].Flags)
	}

	// SetFlags returns post-commit sequence positions.
	changes, err := st.SetFlags(ctx, "INBOX", false, []uint32{4}, FlagAdd, []string{`\Seen`})
	if err != nil {
		t.Fatal(err)
	}
	if len(changes) != 1 || changes[0].Seq != 4 || changes[0].UID != 4 {
		t.Fatalf("change position wrong: %+v", changes)
	}

	// SequenceOfUID present and missing.
	pos, err := st.SequenceOfUID(ctx, "INBOX", 3)
	if err != nil || pos != 3 {
		t.Fatalf("SequenceOfUID(3) want 3, got %d err=%v", pos, err)
	}
	pos, err = st.SequenceOfUID(ctx, "INBOX", 999)
	if err != nil || pos != 0 {
		t.Fatalf("missing UID want 0,nil, got %d %v", pos, err)
	}
}

func TestInsertAndRotateErrors(t *testing.T) {
	st, ctx := testStore(t)
	seedN(t, st, ctx, 1, 1001)

	// Insert appends at UIDNEXT.
	uid, err := st.InsertMessage(ctx, "INBOX", SeedMessage{
		Raw: []byte("s: x\r\n\r\ny"), InternalDate: time.Now().UTC(),
	})
	if err != nil || uid != 2 {
		t.Fatalf("insert want uid2, got %d err=%v", uid, err)
	}
	if _, err := st.InsertMessage(ctx, "GHOST", SeedMessage{}); !errors.Is(err, ErrNotFound) {
		t.Fatalf("insert into missing mailbox want ErrNotFound, got %v", err)
	}
	if _, err := st.RotateUIDValidity(ctx, "GHOST"); !errors.Is(err, ErrNotFound) {
		t.Fatalf("rotate missing mailbox want ErrNotFound, got %v", err)
	}
	if err := st.SetUIDValidityForTest(ctx, "INBOX", ^uint32(0)); err != nil {
		t.Fatal(err)
	}
	if _, err := st.RotateUIDValidity(ctx, "INBOX"); err == nil {
		t.Fatal("UIDVALIDITY overflow must be a computation error")
	}
	if v := TimedUIDValidity(time.Unix(1_700_000_000, 0)); v != 1_700_000_000 {
		t.Fatalf("TimedUIDValidity wrong: %d", v)
	}
}

func TestUnknownMailboxIsNotFound(t *testing.T) {
	st, ctx := testStore(t)
	if _, err := st.MailboxInfo(ctx, "NOPE"); !errors.Is(err, ErrNotFound) {
		t.Fatalf("want ErrNotFound, got %v", err)
	}
}

func uids(msgs []Message) string {
	out := ""
	for i, m := range msgs {
		if i > 0 {
			out += ","
		}
		out += fmt.Sprintf("%d", m.UID)
	}
	return out
}

func hasSliceFlag(flags []string, want string) bool {
	for _, f := range flags {
		if f == want {
			return true
		}
	}
	return false
}
