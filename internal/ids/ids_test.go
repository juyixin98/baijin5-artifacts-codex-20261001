package ids_test

import (
	"testing"

	"coaplab/internal/ids"
)

func TestTokenSource_UniqueAndBounded(t *testing.T) {
	src, err := ids.NewTokenSource(8)
	if err != nil {
		t.Fatal(err)
	}
	seen := map[string]bool{}
	for i := 0; i < 10000; i++ {
		tok := src.New()
		if len(tok) != 8 {
			t.Fatalf("token len %d", len(tok))
		}
		k := tok.Key()
		if seen[k] {
			t.Fatalf("token collision after %d draws", i)
		}
		seen[k] = true
	}
}

func TestTokenSource_RejectsBadLength(t *testing.T) {
	for _, n := range []int{0, 9, -1} {
		if _, err := ids.NewTokenSource(n); err == nil {
			t.Fatalf("length %d must be rejected", n)
		}
	}
}

// Tokens are opaque bytes; Key/hex rendering distinguishes empty token.
func TestTokenRendering(t *testing.T) {
	empty := ids.Token(nil)
	if empty.Hex() != "∅" || empty.Key() != "t:" {
		t.Fatalf("empty token render: %q %q", empty.Hex(), empty.Key())
	}
	tok := ids.Token{0x0a, 0xff}
	if tok.Hex() != "0aff" || tok.Key() != "t:0aff" {
		t.Fatalf("token render: %q %q", tok.Hex(), tok.Key())
	}
}

// MID source wraps and never hands out the same value twice within a wrap
// cycle; EndpointMID keeps endpoint and MID separable (no conflation).
func TestMIDSource_Monotonic(t *testing.T) {
	src := ids.NewMIDSource()
	a := src.Next()
	b := src.Next()
	if b != a+1 {
		t.Fatalf("MIDs not consecutive: %d %d", a, b)
	}
	e1 := ids.EndpointMID{Remote: "h1", MID: a}
	e2 := ids.EndpointMID{Remote: "h2", MID: a}
	if e1 == e2 {
		t.Fatal("EndpointMID must distinguish remotes even with equal MID")
	}
}
