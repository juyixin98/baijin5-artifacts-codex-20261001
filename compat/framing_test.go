package compat_test

import (
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"testing"
	"time"

	"modbusfixture/vectors"
)

// TestFramingFailuresCloseConnection verifies that each MBAP-level failure
// is refused and the connection is torn down, because once framing is
// violated the stream boundary can no longer be trusted.
//
// The "trailing byte" case is different on a stream (vs a datagram): the
// extra byte is treated as the START of a next frame, so the legal first
// frame is answered normally and the connection is only closed once that
// second frame is found truncated; it has its own test.
func TestFramingFailuresCloseConnection(t *testing.T) {
	h := startHarness(t, configLatencyNone, 0)
	defer h.shutdown()

	for _, c := range vectors.FramingMalformed {
		if c.Category == vectors.CatTrailingBytes {
			continue
		}
		t.Run(c.Name, func(t *testing.T) {
			conn := h.dial(t)
			defer conn.Close()

			writeFull(t, conn, vectors.MustHex(c.Hex), 0)
			// Deliver EOF promptly for truncated vectors; complete-but-bad
			// frames are unaffected.
			if tc, ok := conn.(*net.TCPConn); ok {
				_ = tc.CloseWrite()
			}
			frame, err := readFrameOrClose(t, conn)
			// A response must never be emitted for a framing violation.
			if err == nil {
				t.Fatalf("want connection close, got frame %x", frame)
			}
		})
	}
}

// TestTrailingByteStartsNextFrame covers stream semantics for a glued extra
// byte: the first, self-consistent frame is answered normally; the lone
// trailing byte then forms a truncated header and the fixture closes the
// connection (audited as header_truncated), never mis-parsing it as part of
// frame one.
func TestTrailingByteStartsNextFrame(t *testing.T) {
	h := startHarness(t, configLatencyNone, 0)
	defer h.shutdown()

	conn := h.dial(t)
	defer conn.Close()

	writeFull(t, conn, vectors.MustHex("123400000006050300000002ff"), 0)

	// First response: the normal golden FC03 answer for txn 0x1234.
	first := readExactFrame(t, conn)
	want := vectors.MustHex(vectors.Golden[0].RespHex)
	if !bytesEqual(first, want) {
		t.Fatalf("first frame:\n got %x\nwant %x", first, want)
	}

	// Signal EOF to the server: the lone 0xFF is a truncated second header.
	if tc, ok := conn.(*net.TCPConn); ok {
		_ = tc.CloseWrite()
	} else {
		_ = conn.Close()
	}

	// Then the lone 0xFF must not produce a response; connection closes.
	if _, err := readFrameOrClose(t, conn); err == nil {
		t.Fatal("want close after trailing truncated header")
	}
	if cat := h.waitForAuditCategory(t, "header_truncated", 2*time.Second); cat == "" {
		t.Fatalf("no header_truncated audit row; rows=%s", h.auditJSON(t))
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

// TestHalfPacketMidBodyIsAudited sends a header that announces a full body
// but the body is cut short, then closes write. The server must classify it
// as body_truncated in the audit trail (not confuse it with a clean close).
func TestHalfPacketMidBodyIsAudited(t *testing.T) {
	h := startHarness(t, configLatencyNone, 0)
	defer h.shutdown()

	conn := h.dial(t)
	// length says 6 (needs 5 PDU bytes), only 2 PDU bytes follow.
	writeFull(t, conn, vectors.MustHex("12340000000605030000"), 0)
	if _, err := readFrameOrClose(t, conn); err == nil {
		t.Fatal("want close on truncated body")
	}
	_ = conn.Close()

	cat := h.waitForAuditCategory(t, "body_truncated", 2*time.Second)
	if cat == "" {
		t.Fatalf("no body_truncated audit row found; rows=%s", h.auditJSON(t))
	}
}

// TestHeaderOnlyHalfPacketIsAudited sends only 6 of 7 header bytes.
func TestHeaderOnlyHalfPacketIsAudited(t *testing.T) {
	h := startHarness(t, configLatencyNone, 0)
	defer h.shutdown()

	conn := h.dial(t)
	writeFull(t, conn, vectors.MustHex("123400000006"), 0)
	if _, err := readFrameOrClose(t, conn); err == nil {
		t.Fatal("want close on truncated header")
	}
	_ = conn.Close()

	if cat := h.waitForAuditCategory(t, "header_truncated", 2*time.Second); cat == "" {
		t.Fatalf("no header_truncated audit row; rows=%s", h.auditJSON(t))
	}
}

// TestWrongProtocolIDAudited pins the exact category for a nonzero MBAP
// protocol id.
func TestWrongProtocolIDAudited(t *testing.T) {
	h := startHarness(t, configLatencyNone, 0)
	defer h.shutdown()

	conn := h.dial(t)
	defer conn.Close()
	writeFull(t, conn, vectors.MustHex("123400010006050300000002"), 0)
	if _, err := readFrameOrClose(t, conn); err == nil {
		t.Fatal("want close on wrong protocol id")
	}
	if cat := h.waitForAuditCategory(t, "wrong_protocol_id", 2*time.Second); cat == "" {
		t.Fatalf("no wrong_protocol_id audit row; rows=%s", h.auditJSON(t))
	}
}

// auditRow is the subset of the audit JSON used by tests.
type auditRow struct {
	ConnID    string `json:"conn_id"`
	TxnID     int    `json:"txn_id"`
	UnitID    int    `json:"unit_id"`
	Function  int    `json:"function"`
	Exception int    `json:"exception"`
	Category  string `json:"category"`
	RawPDUHex string `json:"raw_pdu_hex"`
}

type auditResponse struct {
	Count int        `json:"count"`
	Rows  []auditRow `json:"rows"`
}

func (h *harness) fetchAudit(t *testing.T) auditResponse {
	t.Helper()
	resp, err := http.Get("http://" + h.control + "/audit")
	if err != nil {
		t.Fatalf("get audit: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("audit status %d", resp.StatusCode)
	}
	var ar auditResponse
	if err := json.NewDecoder(resp.Body).Decode(&ar); err != nil {
		t.Fatalf("decode audit: %v", err)
	}
	return ar
}

func (h *harness) auditJSON(t *testing.T) string {
	ar := h.fetchAudit(t)
	s := ""
	for _, r := range ar.Rows {
		s += fmt.Sprintf("[cat=%s txn=%d unit=%d exc=%d fc=%d] ",
			r.Category, r.TxnID, r.UnitID, r.Exception, r.Function)
	}
	if s == "" {
		return "(none)"
	}
	return s
}

// waitForAuditCategory polls the control plane until a row with the given
// category appears, returning the category or "" on timeout.
func (h *harness) waitForAuditCategory(t *testing.T, want string, within time.Duration) string {
	deadline := time.Now().Add(within)
	for time.Now().Before(deadline) {
		ar := h.fetchAudit(t)
		for _, r := range ar.Rows {
			if r.Category == want {
				return r.Category
			}
		}
		time.Sleep(20 * time.Millisecond)
	}
	return ""
}
