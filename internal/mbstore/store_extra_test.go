package mbstore

import (
	"errors"
	"path/filepath"
	"testing"
)

func TestOpenRejectsZeroSize(t *testing.T) {
	if _, err := Open(":memory:", 0); err == nil {
		t.Fatal("expected error for zero register space")
	}
}

func TestUnprovisionedUnitFails(t *testing.T) {
	s := openTest(t, 8) // provisions unit 1 only
	if _, err := s.Read(2, 0, 1); !errors.Is(err, ErrAddressRange) {
		t.Fatalf("read unprovisioned unit: got %v", err)
	}
	if err := s.WriteMultiple(2, 0, []uint16{1}); !errors.Is(err, ErrAddressRange) {
		t.Fatalf("write unprovisioned unit: got %v", err)
	}
}

func TestEnsureUnitIdempotent(t *testing.T) {
	s := openTest(t, 8)
	if err := s.WriteMultiple(1, 0, []uint16{7}); err != nil {
		t.Fatal(err)
	}
	if err := s.EnsureUnit(1); err != nil { // must not wipe existing values
		t.Fatal(err)
	}
	got, err := s.Read(1, 0, 1)
	if err != nil || got[0] != 7 {
		t.Fatalf("EnsureUnit wiped data: got=%v err=%v", got, err)
	}
}

// TestPersistenceAcrossReopen writes to a file-backed database, closes it,
// reopens it, and expects the values to survive — the SQLite layer is real,
// not an in-memory fake.
func TestPersistenceAcrossReopen(t *testing.T) {
	path := filepath.Join(t.TempDir(), "regs.db")
	s1, err := Open(path, 8)
	if err != nil {
		t.Fatal(err)
	}
	if err := s1.EnsureUnit(1); err != nil {
		t.Fatal(err)
	}
	if err := s1.WriteMultiple(1, 3, []uint16{0xBEEF}); err != nil {
		t.Fatal(err)
	}
	if err := s1.Close(); err != nil {
		t.Fatal(err)
	}

	s2, err := Open(path, 8)
	if err != nil {
		t.Fatal(err)
	}
	defer s2.Close()
	got, err := s2.Read(1, 3, 1)
	if err != nil {
		t.Fatal(err)
	}
	if got[0] != 0xBEEF {
		t.Fatalf("got 0x%04X after reopen, want 0xBEEF", got[0])
	}
}
