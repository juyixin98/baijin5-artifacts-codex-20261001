package store_test

import (
	"path/filepath"
	"reflect"
	"testing"

	"imapd/internal/errs"
	"imapd/internal/store"
	"imapd/internal/testlog"
)

const testUIDValidity = 424242

func openSeeded(t *testing.T, n int) *store.Store {
	t.Helper()
	st, err := store.Open(filepath.Join(t.TempDir(), "test.db"))
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	t.Cleanup(func() { st.Close() })
	msgs := make([]store.SeedMessage, n)
	for i := range msgs {
		msgs[i] = store.SeedMessage{Content: []byte{byte('a' + i)}}
	}
	if err := st.Seed("INBOX", testUIDValidity, msgs); err != nil {
		t.Fatalf("seed: %v", err)
	}
	return st
}

func uidsOf(msgs []store.Message) []uint32 {
	out := make([]uint32, 0, len(msgs))
	for _, m := range msgs {
		out = append(out, m.UID)
	}
	return out
}

func seqsOf(msgs []store.Message) []uint32 {
	out := make([]uint32, 0, len(msgs))
	for _, m := range msgs {
		out = append(out, m.Seq)
	}
	return out
}

func TestSeedAndSelect(t *testing.T) {
	lg := testlog.New(t)
	st := openSeeded(t, 3)
	info, err := st.Select("INBOX")
	if err != nil {
		t.Fatalf("select: %v", err)
	}
	lg.Log("selected", "snapshot must echo seed parameters",
		"exists", info.Exists, "uidvalidity", info.UIDValidity, "uidnext", info.UIDNext)
	if info.Exists != 3 || info.UIDValidity != testUIDValidity || info.UIDNext != 4 {
		t.Fatalf("got %+v, want Exists=3 UIDValidity=%d UIDNext=4", info, testUIDValidity)
	}
	msgs, err := st.List("INBOX")
	if err != nil {
		t.Fatalf("list: %v", err)
	}
	if got := uidsOf(msgs); !reflect.DeepEqual(got, []uint32{1, 2, 3}) {
		t.Fatalf("uids = %v, want [1 2 3]", got)
	}
	if got := seqsOf(msgs); !reflect.DeepEqual(got, []uint32{1, 2, 3}) {
		t.Fatalf("seqs = %v, want [1 2 3]", got)
	}
}

func TestSeedExistingMailboxIsStateConflict(t *testing.T) {
	lg := testlog.New(t)
	st := openSeeded(t, 1)
	err := st.Seed("INBOX", 1, []store.SeedMessage{{Content: []byte("x")}})
	lg.Log("seed-rejected", "re-seeding an existing mailbox must be a state conflict",
		"category", errs.CategoryOf(err).String())
	if errs.CategoryOf(err) != errs.CatState {
		t.Fatalf("category = %v, want state", errs.CategoryOf(err))
	}
}

func TestSelectMissingMailboxIsStateConflict(t *testing.T) {
	lg := testlog.New(t)
	st := openSeeded(t, 1)
	_, err := st.Select("NOPE")
	lg.Log("select-missing", "unknown mailbox must be a state conflict",
		"category", errs.CategoryOf(err).String())
	if errs.CategoryOf(err) != errs.CatState {
		t.Fatalf("category = %v, want state", errs.CategoryOf(err))
	}
}

func TestExpungeRenumbersSequences(t *testing.T) {
	lg := testlog.New(t)
	st := openSeeded(t, 4)
	if _, err := st.UpdateFlags("INBOX", []uint32{2}, true, []string{"\\Deleted"}); err != nil {
		t.Fatalf("flag: %v", err)
	}
	events, err := st.Expunge("INBOX")
	if err != nil {
		t.Fatalf("expunge: %v", err)
	}
	lg.Log("expunged", "deleting UID 2 (seq 2) must report seq 2 and shift later messages down",
		"events", events)
	if !reflect.DeepEqual(events, []store.Event{{Seq: 2, UID: 2}}) {
		t.Fatalf("events = %+v, want [{Seq:2 UID:2}]", events)
	}
	msgs, _ := st.List("INBOX")
	lg.Log("renumbered", "UIDs keep identity while sequence numbers close the gap",
		"uids", uidsOf(msgs), "seqs", seqsOf(msgs))
	if got := uidsOf(msgs); !reflect.DeepEqual(got, []uint32{1, 3, 4}) {
		t.Fatalf("uids = %v, want [1 3 4]", got)
	}
	if got := seqsOf(msgs); !reflect.DeepEqual(got, []uint32{1, 2, 3}) {
		t.Fatalf("seqs = %v, want [1 2 3]", got)
	}
}

func TestExpungeMultipleReportsShiftedSeqs(t *testing.T) {
	lg := testlog.New(t)
	st := openSeeded(t, 4)
	if _, err := st.UpdateFlags("INBOX", []uint32{1, 3}, true, []string{"\\Deleted"}); err != nil {
		t.Fatalf("flag: %v", err)
	}
	events, err := st.Expunge("INBOX")
	if err != nil {
		t.Fatalf("expunge: %v", err)
	}
	// UID 1 is seq 1 when deleted; UID 3 was seq 3 but one earlier
	// deletion shifts it to seq 2 at its own deletion moment.
	lg.Log("expunged", "second event must account for the shift caused by the first",
		"events", events)
	want := []store.Event{{Seq: 1, UID: 1}, {Seq: 2, UID: 3}}
	if !reflect.DeepEqual(events, want) {
		t.Fatalf("events = %+v, want %+v", events, want)
	}
}

func TestResetInvalidatesUIDs(t *testing.T) {
	lg := testlog.New(t)
	st := openSeeded(t, 3)
	before, _ := st.Select("INBOX")
	if err := st.Reset("INBOX", 777); err != nil {
		t.Fatalf("reset: %v", err)
	}
	after, _ := st.Select("INBOX")
	lg.Log("reset", "new UIDVALIDITY must differ and all old UIDs must be gone",
		"before", before.UIDValidity, "after", after.UIDValidity, "exists", after.Exists)
	if after.UIDValidity == before.UIDValidity {
		t.Fatalf("UIDVALIDITY unchanged across reset: %d", after.UIDValidity)
	}
	if after.Exists != 0 || after.UIDNext != 1 {
		t.Fatalf("after reset: %+v, want Exists=0 UIDNext=1", after)
	}
	msgs, _ := st.List("INBOX")
	if len(msgs) != 0 {
		t.Fatalf("old UIDs still resolvable after reset: %v", uidsOf(msgs))
	}
}

func TestUpdateFlagsValidation(t *testing.T) {
	lg := testlog.New(t)
	st := openSeeded(t, 2)
	err := func() error {
		_, err := st.UpdateFlags("INBOX", []uint32{1}, true, []string{"\\Flagged"})
		return err
	}()
	lg.Log("bad-flag", "undeclared flag must be an input error",
		"category", errs.CategoryOf(err).String())
	if errs.CategoryOf(err) != errs.CatInput {
		t.Fatalf("category = %v, want input", errs.CategoryOf(err))
	}
	_, err = st.UpdateFlags("GHOST", []uint32{1}, true, []string{"\\Seen"})
	lg.Log("bad-mailbox", "flag update on missing mailbox must be a state conflict",
		"category", errs.CategoryOf(err).String())
	if errs.CategoryOf(err) != errs.CatState {
		t.Fatalf("category = %v, want state", errs.CategoryOf(err))
	}
	// Unknown UIDs are skipped, not errors (IMAP STORE semantics).
	got, err := st.UpdateFlags("INBOX", []uint32{99}, true, []string{"\\Seen"})
	if err != nil || len(got) != 0 {
		t.Fatalf("unknown UID: got=%v err=%v, want empty result without error", got, err)
	}
}
