package stunerror_test

import (
	"errors"
	"strings"
	"testing"

	"localstun/internal/stunerror"
)

func TestKindsAndOf(t *testing.T) {
	if stunerror.Of(nil) != stunerror.KindUnknown {
		t.Fatal("nil must be unknown")
	}
	for _, tc := range []struct {
		kind stunerror.Kind
		want string
	}{
		{stunerror.KindInput, "input"},
		{stunerror.KindState, "state"},
		{stunerror.KindIntegrity, "integrity"},
		{stunerror.KindExhausted, "exhausted"},
		{stunerror.KindCompute, "compute"},
		{stunerror.Kind(99), "unknown"},
	} {
		if tc.kind.String() != tc.want {
			t.Fatalf("%d.String()=%s want %s", tc.kind, tc.kind.String(), tc.want)
		}
	}

	err := stunerror.New(stunerror.KindState, "op", "boom")
	if stunerror.Of(err) != stunerror.KindState {
		t.Fatal("Of on *Error")
	}
	if !strings.Contains(err.Error(), "state") || !strings.Contains(err.Error(), "boom") {
		t.Fatalf("error text = %q", err.Error())
	}

	wrapped := errors.New("io failure")
	werr := stunerror.Wrap(stunerror.KindCompute, "x", "ctx", wrapped)
	if !errors.Is(werr, wrapped) {
		t.Fatal("Wrap must preserve the cause")
	}

	// Kinded errors (implementing Kind()) are recognized without *Error.
	if stunerror.Of(kindedErr{}) != stunerror.KindExhausted {
		t.Fatal("Of must honor the Kinded interface")
	}
}

type kindedErr struct{}

func (kindedErr) Error() string        { return "kinded" }
func (kindedErr) Kind() stunerror.Kind { return stunerror.KindExhausted }
