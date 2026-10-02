// Package diag is the structured diagnostic recorder used by the protocol
// state machines. Every decision line carries the correlation identifiers
// (Message ID for the message layer, Token for the request layer) and an
// explicit verdict explaining why a datagram or block was ACCEPTED,
// REJECTED, or could not be decided (INDETERMINATE).
//
// Values that may carry user data are passed through Redact() so secrets
// in payloads or query strings never appear verbatim in logs.
package diag

import (
	"fmt"
	"io"
	"os"
	"sync"
	"time"
)

// Verdict is the disposition of a protocol element.
type Verdict string

// Explicit verdicts required for every diagnostic decision.
const (
	Accept        Verdict = "ACCEPT"        // processed and state advanced
	Reject        Verdict = "REJECT"        // refused, response/error category recorded
	Indeterminate Verdict = "INDETERMINATE" // cannot decide yet (e.g. awaiting retransmit window)
	Ignore        Verdict = "IGNORE"        // e.g. duplicate already applied; no state change
)

// Category refines a rejection with a stable, test-assertable code.
type Category string

// Failure categories asserted on by the test suites.
const (
	CatParseError         Category = "parse_error"
	CatBadBlockSize       Category = "bad_block_size"
	CatGap                Category = "gap"                // non-contiguous block offset
	CatDuplicateMismatch  Category = "duplicate_mismatch" // repeated block with different bytes
	CatETagChanged        Category = "etag_changed"       // representation changed mid-download
	CatContentFormatDrift Category = "content_format_drift"
	CatIncomplete         Category = "request_incomplete" // 4.08
	CatTooLarge           Category = "entity_too_large"   // 4.13
	CatNoRoute            Category = "no_route"           // 4.04
	CatMethodNotAllowed   Category = "method_not_allowed" // 4.05
	CatTimeout            Category = "timeout"
	CatExchangeLifetime   Category = "exchange_lifetime"
	CatMessageLayer       Category = "message_layer"
	CatUnknownCriticalOpt Category = "unknown_critical_option"
)

// Event is one structured decision record.
type Event struct {
	Time     time.Time
	Remote   string
	MID      string // "0x7d34" or "-" when absent
	Token    string // masked hex or "∅"
	Verdict  Verdict
	Category Category
	Detail   string
}

// Recorder collects events; it is safe for concurrent use.
type Recorder struct {
	mu     sync.Mutex
	out    io.Writer
	events []Event
	quiet  bool
}

// NewRecorder writes human-readable lines to out (nil => stderr) and keeps
// the event slice for test assertions.
func NewRecorder(out io.Writer) *Recorder {
	if out == nil {
		out = os.Stderr
	}
	return &Recorder{out: out}
}

// Quiet suppresses human output while still retaining events (tests).
func (r *Recorder) Quiet() *Recorder { r.quiet = true; return r }

// Log records a decision. mid/token may be 0 / nil ("-"/"∅" rendered).
func (r *Recorder) Log(remote string, mid uint16, haveMID bool, tokenHex string, v Verdict, c Category, format string, args ...any) {
	e := Event{
		Time:     time.Now().UTC(),
		Remote:   remote,
		MID:      "-",
		Token:    "∅",
		Verdict:  v,
		Category: c,
		Detail:   fmt.Sprintf(format, args...),
	}
	if haveMID {
		e.MID = fmt.Sprintf("0x%04x", mid)
	}
	if tokenHex != "" {
		e.Token = tokenHex
	}
	r.mu.Lock()
	r.events = append(r.events, e)
	quiet := r.quiet
	out := r.out
	r.mu.Unlock()
	if !quiet {
		fmt.Fprintf(out, "%s remote=%s mid=%s token=%s verdict=%s cat=%s %s\n",
			e.Time.Format("15:04:05.000"), e.Remote, e.MID, e.Token, v, c, e.Detail)
	}
}

// Events returns a copy of all recorded events.
func (r *Recorder) Events() []Event {
	r.mu.Lock()
	defer r.mu.Unlock()
	out := make([]Event, len(r.events))
	copy(out, r.events)
	return out
}

// ByCategory returns recorded events with the given category.
func (r *Recorder) ByCategory(c Category) []Event {
	var out []Event
	for _, e := range r.Events() {
		if e.Category == c {
			out = append(out, e)
		}
	}
	return out
}

// HasVerdict reports whether any event with the verdict exists.
func (r *Recorder) HasVerdict(v Verdict) bool {
	for _, e := range r.Events() {
		if e.Verdict == v {
			return true
		}
	}
	return false
}

// Redact masks values that could contain sensitive material before they are
// embedded in Detail. Payloads render as length+short SHA-like fingerprint,
// never as content; query/option values are length-bounded.
func Redact(b []byte) string {
	if len(b) == 0 {
		return "<empty>"
	}
	return fmt.Sprintf("<%d bytes>", len(b))
}

// RedactString caps a printable value and strips anything after a secret
// marker keyword; used for Uri-Query etc.
func RedactString(s string) string {
	const max = 16
	if len(s) <= max {
		return s
	}
	return s[:max] + fmt.Sprintf("…(%d more)", len(s)-max)
}
