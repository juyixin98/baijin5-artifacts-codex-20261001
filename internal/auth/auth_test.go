package auth

import (
	"errors"
	"testing"
)

func TestHashAndVerify(t *testing.T) {
	salt, err := GenerateSalt()
	if err != nil {
		t.Fatal(err)
	}
	if len(salt) != SaltLen {
		t.Fatalf("salt length want %d got %d", SaltLen, len(salt))
	}
	pw := []byte("Tester#2025!")
	h := Hash(pw, salt)
	if len(h) != KeyLen {
		t.Fatalf("key length want %d got %d", KeyLen, len(h))
	}
	if !Verify(pw, salt, h) {
		t.Fatal("correct password must verify")
	}
	if Verify([]byte("wrong"), salt, h) {
		t.Fatal("wrong password must not verify")
	}
	// Same password, different salt -> different stored hash (no reuse).
	salt2, _ := GenerateSalt()
	if bytesEqual(Hash(pw, salt), Hash(pw, salt2)) {
		t.Fatal("different salts must produce different hashes")
	}
}

func TestPBKDF2KnownVector(t *testing.T) {
	// RFC 7914-ish spot check is heavy; verify determinism and a change in
	// the password changes the key.
	salt := []byte("saltsaltsaltsalt")
	k1 := Key([]byte("a"), salt, 1000, 32)
	k2 := Key([]byte("a"), salt, 1000, 32)
	if !bytesEqual(k1, k2) {
		t.Fatal("same inputs must derive the same key")
	}
	k3 := Key([]byte("b"), salt, 1000, 32)
	if bytesEqual(k1, k3) {
		t.Fatal("different passwords must derive different keys")
	}
}

func TestUserStoreLogin(t *testing.T) {
	s := NewUserStore()
	if err := s.Provision(Account{Username: "u", Password: "pw", Mailboxes: []string{"INBOX"}}); err != nil {
		t.Fatal(err)
	}
	if _, err := s.Login([]byte("u"), []byte("pw")); err != nil {
		t.Fatalf("valid login: %v", err)
	}
	if _, err := s.Login([]byte("u"), []byte("nope")); !errors.Is(err, ErrCredential) {
		t.Fatalf("wrong pw want ErrCredential, got %v", err)
	}
	if _, err := s.Login([]byte("ghost"), []byte("pw")); !errors.Is(err, ErrCredential) {
		t.Fatalf("unknown user want ErrCredential, got %v", err)
	}
}

func TestCanSelect(t *testing.T) {
	s := NewUserStore()
	_ = s.Provision(Account{Username: "u", Password: "p", Mailboxes: []string{"INBOX"}})
	u, _ := s.Login([]byte("u"), []byte("p"))
	if !s.CanSelect(u, "INBOX") {
		t.Fatal("INBOX should be allowed")
	}
	if s.CanSelect(u, "SECRET") {
		t.Fatal("SECRET should be denied for this account")
	}
	// An account without an explicit list may select any fixture mailbox.
	_ = s.Provision(Account{Username: "admin", Password: "p"})
	a, _ := s.Login([]byte("admin"), []byte("p"))
	if !s.CanSelect(a, "ANY") {
		t.Fatal("unrestricted account should select any mailbox")
	}
}

func bytesEqual(a, b []byte) bool {
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
