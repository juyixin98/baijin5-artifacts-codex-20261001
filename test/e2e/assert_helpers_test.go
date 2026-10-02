package e2e

import (
	"context"
	"strconv"
	"strings"
	"testing"
)

const subjectItem = "BODY[HEADER.FIELDS (SUBJECT)]"

func atoiOrFail(t *testing.T, s string) int {
	t.Helper()
	n, err := strconv.Atoi(strings.TrimSpace(s))
	if err != nil {
		t.Fatalf("not a number %q: %v", s, err)
	}
	return n
}

func uint32OrZero(n int) uint32 { return uint32(n) }

// fetchOne returns the single FETCH record at sequence seq or fails.
func fetchOne(t *testing.T, recs []record, seq int) record {
	t.Helper()
	fs := untaggedByKey(recs, "FETCH")
	for _, r := range fs {
		if r.Seq == seq {
			return r
		}
	}
	t.Fatalf("no FETCH record for seq %d", seq)
	return record{}
}

// subjectFor extracts the Subject header value from a FETCH record that
// requested BODY[HEADER.FIELDS (SUBJECT)] (returned as a literal).
func subjectFor(t *testing.T, r record) string {
	t.Helper()
	raw, ok := r.FetchLit[subjectItem]
	if !ok {
		t.Fatalf("record missing %s literal (items=%v)", subjectItem, r.FetchSeen)
	}
	for _, line := range strings.Split(string(raw), "\n") {
		line = strings.TrimRight(line, "\r")
		if strings.HasPrefix(strings.ToLower(line), "subject:") {
			return strings.TrimSpace(line[len("subject:"):])
		}
	}
	t.Fatalf("no Subject in %q", raw)
	return ""
}

func firstDiff(a, b []byte) int {
	n := len(a)
	if len(b) < n {
		n = len(b)
	}
	for i := 0; i < n; i++ {
		if a[i] != b[i] {
			return i
		}
	}
	return n
}

func testContext() context.Context { return context.Background() }

// testCtx alias used by error tests.
func testCtx() context.Context { return context.Background() }

// readUntilTag reads records until the tagged completion for tag.
func (c *client) readUntilTag(tag string) []record {
	var recs []record
	for {
		rec := c.readOne()
		c.log.Printf("pipeline << %s", abbreviate(rec.raw))
		recs = append(recs, rec)
		if rec.Tag == tag {
			return recs
		}
	}
}
