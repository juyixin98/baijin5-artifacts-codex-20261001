package fixture

import (
	"context"
	"testing"

	"imaplite/internal/store"
)

func TestLoadBundledFixtures(t *testing.T) {
	loaded, err := Load(DefaultDataDir())
	if err != nil {
		t.Fatalf("load: %v", err)
	}
	if len(loaded.Accounts) == 0 {
		t.Fatal("no accounts provisioned")
	}
	if len(loaded.Mailboxes) != 2 {
		t.Fatalf("want 2 mailboxes, got %d", len(loaded.Mailboxes))
	}
	var inbox *ProvisionedMailbox
	for i := range loaded.Mailboxes {
		if loaded.Mailboxes[i].Name == "INBOX" {
			inbox = &loaded.Mailboxes[i]
		}
	}
	if inbox == nil {
		t.Fatal("INBOX missing")
	}
	if inbox.UIDValidity != 1001 || len(inbox.Messages) != 5 {
		t.Fatalf("INBOX seed wrong: uidv=%d n=%d", inbox.UIDValidity, len(inbox.Messages))
	}
	// The last message is the code-generated binary; it must contain NUL/0xFF.
	last := inbox.Messages[4].Raw
	if !containsByte(last, 0x00) || !containsByte(last, 0xFF) {
		t.Fatal("binary fixture missing NUL/0xFF sentinels")
	}

	// Provisioning into a real store succeeds and the data is queryable.
	st, err := store.Open(context.Background(), t.TempDir()+"/f.db")
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()
	if err := loaded.Provision(st); err != nil {
		t.Fatalf("provision: %v", err)
	}
	info, err := st.MailboxInfo(context.Background(), "INBOX")
	if err != nil {
		t.Fatal(err)
	}
	if info.Exists != 5 || info.UIDValidity != 1001 {
		t.Fatalf("provisioned counters wrong: %+v", info)
	}
}

func TestBinaryMessageDeterministic(t *testing.T) {
	a := BinaryMessage(1)
	b := BinaryMessage(1)
	if len(a) == 0 || !equalBytes(a, b) {
		t.Fatal("BinaryMessage must be deterministic and non-empty")
	}
	c := BinaryMessage(2)
	if equalBytes(a, c) {
		t.Fatal("different seeds must produce different messages")
	}
}

func containsByte(b []byte, want byte) bool {
	for _, x := range b {
		if x == want {
			return true
		}
	}
	return false
}

func equalBytes(a, b []byte) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}
