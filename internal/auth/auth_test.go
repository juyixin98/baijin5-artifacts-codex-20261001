package auth_test

import (
	"errors"
	"testing"

	"socks5d.local/socks5d/internal/auth"
)

func TestAuthenticate(t *testing.T) {
	a, err := auth.NewUserPass("alice", "correct horse")
	if err != nil {
		t.Fatalf("new: %v", err)
	}
	if !a.Authenticate("alice", "correct horse") {
		t.Fatal("valid credentials rejected")
	}
	if a.Authenticate("alice", "wrong") {
		t.Fatal("wrong password accepted")
	}
	if a.Authenticate("bob", "correct horse") {
		t.Fatal("wrong user accepted")
	}
	// Length-mismatched credentials must fail without panicking.
	if a.Authenticate("alic", "correct horse") {
		t.Fatal("length-different user accepted")
	}
	if a.Authenticate("alice", "short") {
		t.Fatal("length-different password accepted")
	}
}

func TestNewUserPass_Validation(t *testing.T) {
	if _, err := auth.NewUserPass("u", ""); !errors.Is(err, auth.ErrEmptyPassword) {
		t.Fatalf("empty password err = %v", err)
	}
	if _, err := auth.NewUserPass(string(make([]byte, 256)), "p"); !errors.Is(err, auth.ErrUsernameTooLong) {
		t.Fatalf("long username err = %v", err)
	}
	if _, err := auth.NewUserPass("u", string(make([]byte, 256))); !errors.Is(err, auth.ErrPasswordTooLong) {
		t.Fatalf("long password err = %v", err)
	}
}

func TestEmptyUsernameAllowedByRFC(t *testing.T) {
	// RFC 1929 allows a zero-length username octet; only empty password is
	// a configuration error.
	a, err := auth.NewUserPass("", "nonempty")
	if err != nil {
		t.Fatalf("empty username should be allowed: %v", err)
	}
	if !a.Authenticate("", "nonempty") {
		t.Fatal("empty-username credential rejected")
	}
}
