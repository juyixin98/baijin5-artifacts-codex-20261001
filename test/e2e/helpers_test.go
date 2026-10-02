package e2e

import (
	"io"
	"log/slog"
	"testing"
)

// discardSlog keeps the server quiet while tests run; failure detail is still
// carried by the tagged IMAP responses the tests assert on.
func discardSlog() *slog.Logger {
	return slog.New(slog.NewTextHandler(io.Discard, nil))
}

// loginSelect performs the common LOGIN + SELECT prelude and returns the
// SELECT response records for assertion.
func (c *client) loginSelect(t *testing.T, user, pass, mailbox string) []record {
	t.Helper()
	r1 := c.run("L1", "LOGIN "+user+" "+pass)
	if got := completion(t, r1, "L1"); got.Status != "OK" {
		t.Fatalf("LOGIN failed: %s", got.raw)
	}
	r2 := c.run("S1", "SELECT "+mailbox)
	if got := completion(t, r2, "S1"); got.Status != "OK" {
		t.Fatalf("SELECT failed: %s", got.raw)
	}
	return append(r1, r2...)
}

// completion returns the tagged completion for tag or fails.
func completion(t *testing.T, recs []record, tag string) record {
	t.Helper()
	for i := len(recs) - 1; i >= 0; i-- {
		if recs[i].Tag == tag {
			return recs[i]
		}
	}
	t.Fatalf("no completion for tag %s", tag)
	return record{}
}

// untaggedByKey returns all untagged records with the given key.
func untaggedByKey(recs []record, key string) []record {
	var out []record
	for _, r := range recs {
		if r.IsUntag && r.UntagKey == key {
			out = append(out, r)
		}
	}
	return out
}

// existsCount reads the single "* n EXISTS" value.
func existsCount(t *testing.T, recs []record) int {
	t.Helper()
	g := untaggedByKey(recs, "EXISTS")
	if len(g) != 1 {
		t.Fatalf("want 1 EXISTS record, got %d", len(g))
	}
	return g[0].Seq
}
