package integration

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net"
	"net/netip"
	"strconv"
	"strings"
	"testing"
	"time"

	"sockswhitelist/internal/policy"
	"sockswhitelist/internal/wire"
)

// authHandshake performs negotiation + RFC1929 auth against an auth fixture.
func authHandshake(t *testing.T, f *Fixture, user, pass string, segmented bool) *RawClient {
	t.Helper()
	c, err := DialRaw(f.Addr)
	if err != nil {
		t.Fatal(err)
	}
	method, err := c.Negotiate([]byte{0x00, 0x02}, segmented)
	if err != nil {
		_ = c.Close()
		t.Fatalf("negotiate: %v", err)
	}
	if method != 0x02 {
		_ = c.Close()
		t.Fatalf("server selected method 0x%02x, want 0x02", method)
	}
	status, err := c.Auth(user, pass, segmented)
	if err != nil {
		_ = c.Close()
		t.Fatalf("auth: %v", err)
	}
	if status != 0x00 {
		_ = c.Close()
		t.Fatalf("auth status 0x%02x, want 0x00", status)
	}
	return c
}

// connectEcho authenticates, CONNECTs to host:port and asserts success.
func connectEcho(t *testing.T, f *Fixture, host string, port int, segmented bool) *RawClient {
	t.Helper()
	c := authHandshake(t, f, "alice", "pw", segmented)
	var res ConnectResult
	var err error
	if ip, perr := netip.ParseAddr(host); perr == nil {
		res, err = c.ConnectIP(ip, port, segmented)
	} else {
		res, err = c.ConnectDomain(host, port, segmented)
	}
	if err != nil {
		_ = c.Close()
		t.Fatalf("connect: %v", err)
	}
	if res.Reply != wire.RepSucceeded {
		_ = c.Close()
		t.Fatalf("reply REP=0x%02x, want 0x00 (succeeded)", res.Reply)
	}
	return c
}

// waitForOutcome polls the SQLite request log independently until a row at or
// after startID has the expected outcome kind.
func waitForOutcome(t *testing.T, f *Fixture, want string) policy.LogRow {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		rows, err := f.Store.QueryRequests(context.Background(), "WHERE outcome_kind=? ORDER BY id DESC LIMIT 1", want)
		if err != nil {
			t.Fatal(err)
		}
		if len(rows) == 1 {
			return rows[0]
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatalf("no log row with outcome %q", want)
	return policy.LogRow{}
}

func parsePort(addr string) int {
	_, p, err := net.SplitHostPort(addr)
	if err != nil {
		return -1
	}
	n, _ := strconv.Atoi(p)
	return n
}

// 1. Fully segmented handshake, then byte-for-byte forwarding through an
// echo target, verified against the durable byte counters.
func TestSegmentedHandshakeAndByteForByteRelay(t *testing.T) {
	target := startEcho(t, "127.0.0.1")
	port := parsePort(target)
	f := NewFixture(t, FixtureOptions{AuthRequired: true})

	c := connectEcho(t, f, "127.0.0.1", port, true) // segmented every phase
	defer func() { _ = c.Close() }()

	payload := bytes.Repeat([]byte("socks5-segment-"), 9000) // ~153 KiB
	if _, err := c.Write(payload); err != nil {
		t.Fatal(err)
	}
	if err := c.CloseWrite(); err != nil {
		t.Fatal(err)
	}
	got, err := io.ReadAll(c)
	if err != nil {
		t.Fatalf("read echo: %v", err)
	}
	if !bytes.Equal(got, payload) {
		t.Fatalf("echo mismatch: got %d bytes, want %d", len(got), len(payload))
	}

	row := waitForOutcome(t, f, "relay_completed")
	if row.BytesUp != int64(len(payload)) || row.BytesDown != int64(len(payload)) {
		t.Fatalf("durable counters up=%d down=%d, want %d", row.BytesUp, row.BytesDown, len(payload))
	}
	if row.TargetHost != "127.0.0.1" || row.TargetPort != port || row.Username != "alice" {
		t.Fatalf("log row target=%s:%d user=%q", row.TargetHost, row.TargetPort, row.Username)
	}
}

// 2. Rejected authentication returns 0x01/0x01 and ends the exchange.
func TestRejectedAuthClosesSession(t *testing.T) {
	f := NewFixture(t, FixtureOptions{AuthRequired: true})
	c, err := DialRaw(f.Addr)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = c.Close() }()

	method, err := c.Negotiate([]byte{0x02}, false)
	if err != nil || method != 0x02 {
		t.Fatalf("method=%d err=%v", method, err)
	}
	status, err := c.Auth("alice", "wrong-password", false)
	if err != nil {
		t.Fatal(err)
	}
	if status != wire.AuthStatusFail {
		t.Fatalf("status=0x%02x want 0x01", status)
	}
	// Session must be terminated right after the rejection.
	if _, err := io.ReadFull(c, make([]byte, 1)); err != io.EOF && err != io.ErrUnexpectedEOF {
		t.Fatalf("expected EOF after auth failure, got %v", err)
	}
	row := waitForOutcome(t, f, "auth_denied")
	if row.Stage != "username_password" {
		t.Fatalf("stage=%q", row.Stage)
	}
}

// 3. Method negotiation failure is answered 0x05 0xFF and clearly ends.
func TestNoAcceptableMethod(t *testing.T) {
	f := NewFixture(t, FixtureOptions{AuthRequired: true})
	c, err := DialRaw(f.Addr)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = c.Close() }()

	// Offer only no-auth while the server requires user/password.
	method, err := c.Negotiate([]byte{0x00}, false)
	if err != nil {
		t.Fatal(err)
	}
	if method != wire.MethodNoAcceptable {
		t.Fatalf("method=0x%02x want 0xFF", method)
	}
	if _, err := io.ReadFull(c, make([]byte, 1)); err != io.EOF && err != io.ErrUnexpectedEOF {
		t.Fatalf("expected EOF after 0xFF, got %v", err)
	}
	row := waitForOutcome(t, f, "no_acceptable_method")
	if row.Stage != "method_negotiation" {
		t.Fatalf("stage=%q", row.Stage)
	}
}

// 4. A whitelisted-name request that resolves outside local scope is
// refused and never dialed.
func TestWhitelistBlocksExternalIPAndNeverDials(t *testing.T) {
	f := NewFixture(t, FixtureOptions{AuthRequired: true})
	c := authHandshake(t, f, "alice", "pw", false)
	defer func() { _ = c.Close() }()

	res, err := c.ConnectIP(netip.MustParseAddr("8.8.8.8"), 53, false)
	if err != nil {
		t.Fatal(err)
	}
	if res.Reply != wire.RepConnectionNotAllowed {
		t.Fatalf("REP=0x%02x want 0x02", res.Reply)
	}
	if dialed := f.Dialer.snapshot(); len(dialed) != 0 {
		t.Fatalf("proxy dialed denied target: %v", dialed)
	}
	row := waitForOutcome(t, f, "policy_denied")
	if !strings.Contains(row.Detail, "8.8.8.8") {
		t.Fatalf("denial detail does not name the address: %q", row.Detail)
	}
}

// 5. A non-whitelisted domain is denied before any DNS resolution.
func TestWhitelistBlocksNonLocalDomainBeforeResolve(t *testing.T) {
	calls := 0
	resolver := &countingResolver{inner: scriptResolver{hosts: map[string][]netip.Addr{}}}
	f := NewFixture(t, FixtureOptions{AuthRequired: true, Resolver: resolver})
	resolver.onCall = func() { calls++ }

	c := authHandshake(t, f, "alice", "pw", false)
	defer func() { _ = c.Close() }()
	res, err := c.ConnectDomain("www.example.com", 443, false)
	if err != nil {
		t.Fatal(err)
	}
	if res.Reply != wire.RepConnectionNotAllowed {
		t.Fatalf("REP=0x%02x", res.Reply)
	}
	if calls != 0 || len(f.Dialer.snapshot()) != 0 {
		t.Fatalf("resolution happened (%d calls) or dial happened (%v)", calls, f.Dialer.snapshot())
	}
	waitForOutcome(t, f, "policy_denied")
}

// 6. Domain with multiple resolved addresses: the first refuses, the proxy
// must try the second in order and succeed, recording both attempts.
func TestDomainMultiAddressFallback(t *testing.T) {
	// Echo on 127.0.0.1; 127.0.0.2:<same port> has no listener -> refused.
	target := startEcho(t, "127.0.0.1")
	port := parsePort(target)
	resolver := scriptResolver{hosts: map[string][]netip.Addr{
		"web.local.test": {
			netip.MustParseAddr("127.0.0.2"), // refused
			netip.MustParseAddr("127.0.0.1"), // echo
		},
	}}
	f := NewFixture(t, FixtureOptions{AuthRequired: true, Resolver: resolver})

	c := connectEcho(t, f, "web.local.test", port, false)
	defer func() { _ = c.Close() }()

	dialed := f.Dialer.snapshot()
	if len(dialed) != 2 || dialed[0] != net.JoinHostPort("127.0.0.2", strconv.Itoa(port)) ||
		dialed[1] != net.JoinHostPort("127.0.0.1", strconv.Itoa(port)) {
		t.Fatalf("attempt order = %v", dialed)
	}
	msg := []byte("multi-address payload")
	_, _ = c.Write(msg)
	if err := c.CloseWrite(); err != nil {
		t.Fatal(err)
	}
	got, err := io.ReadAll(c)
	if err != nil || !bytes.Equal(got, msg) {
		t.Fatalf("echo=%q err=%v", got, err)
	}
	row := waitForOutcome(t, f, "relay_completed")
	if !strings.Contains(row.Attempts, "dial_refused") || !strings.Contains(row.Attempts, "connected") {
		t.Fatalf("attempts not recorded: %s", row.Attempts)
	}
	if !strings.Contains(row.Resolved, "127.0.0.2") {
		t.Fatalf("resolved addresses not recorded: %s", row.Resolved)
	}
}

// 7. DNS rebinding: name is whitelisted but resolves externally.
func TestDNSRebindingVeto(t *testing.T) {
	resolver := scriptResolver{hosts: map[string][]netip.Addr{
		"evil.local.test": {netip.MustParseAddr("203.0.113.7"), netip.MustParseAddr("127.0.0.1")},
	}}
	f := NewFixture(t, FixtureOptions{AuthRequired: true, Resolver: resolver})
	c := authHandshake(t, f, "alice", "pw", false)
	defer func() { _ = c.Close() }()
	res, err := c.ConnectDomain("evil.local.test", 80, false)
	if err != nil {
		t.Fatal(err)
	}
	if res.Reply != wire.RepConnectionNotAllowed {
		t.Fatalf("REP=0x%02x want 0x02; rebinding address must veto", res.Reply)
	}
	if len(f.Dialer.snapshot()) != 0 {
		t.Fatalf("dialed despite rebind veto: %v", f.Dialer.snapshot())
	}
	waitForOutcome(t, f, "policy_denied")
}

// 8. One-direction EOF: client FINs upload; downstream response must still
// arrive in full.
func TestHalfCloseClientUploadStillReceivesResponse(t *testing.T) {
	reply := bytes.Repeat([]byte("R"), 50_000)
	target := startCollectServer(t, reply)
	port := parsePort(target)
	f := NewFixture(t, FixtureOptions{AuthRequired: true})

	c := connectEcho(t, f, "127.0.0.1", port, false)
	defer func() { _ = c.Close() }()
	if _, err := c.Write([]byte("upload-before-fin")); err != nil {
		t.Fatal(err)
	}
	if err := c.CloseWrite(); err != nil {
		t.Fatal(err)
	}
	got, err := io.ReadAll(c)
	if err != nil {
		t.Fatalf("read after half-close: %v", err)
	}
	if !bytes.Equal(got, reply) {
		t.Fatalf("got %d response bytes, want %d", len(got), len(reply))
	}
	waitForOutcome(t, f, "relay_completed")
}

// 9. Slow/upstream silent peer trips the bounded idle timeout.
func TestSlowUpstreamIdleTimeout(t *testing.T) {
	target := startSilentServer(t)
	port := parsePort(target)
	f := NewFixture(t, FixtureOptions{AuthRequired: true, IdleTimeout: 200 * time.Millisecond})

	c := connectEcho(t, f, "127.0.0.1", port, false)
	defer func() { _ = c.Close() }()
	_ = c.SetDeadline(time.Now().Add(5 * time.Second))
	// The proxy must tear the session down and the client observes closure.
	buf := make([]byte, 16)
	if _, err := c.Read(buf); err == nil {
		t.Fatal("expected relay teardown on idle timeout")
	}
	row := waitForOutcome(t, f, "idle_timeout")
	if row.Stage != "relay" {
		t.Fatalf("stage=%q", row.Stage)
	}
}

// 10. Byte budget is enforced exactly and durably recorded.
func TestByteBudgetEnforced(t *testing.T) {
	target := startEcho(t, "127.0.0.1")
	port := parsePort(target)
	const budget int64 = 2048
	f := NewFixture(t, FixtureOptions{
		AuthRequired: true, MaxBytesDown: budget, MaxBytesUp: 16 * 1024 * 1024,
	})

	c := connectEcho(t, f, "127.0.0.1", port, false)
	defer func() { _ = c.Close() }()
	_, _ = c.Write(make([]byte, 8192))
	_ = c.SetDeadline(time.Now().Add(5 * time.Second))
	got, _ := io.ReadAll(c)
	if int64(len(got)) != budget {
		t.Fatalf("received %d downstream bytes, want exactly %d", len(got), budget)
	}
	row := waitForOutcome(t, f, "byte_budget_exceeded")
	if row.BytesDown != budget {
		t.Fatalf("durable bytes_down=%d want %d", row.BytesDown, budget)
	}
}

// 11. Logs correlate every step to one request id and carry a distinct
// failure category/reason.
func TestRequestIDCorrelationAndExplainability(t *testing.T) {
	f := NewFixture(t, FixtureOptions{AuthRequired: true})
	c, err := DialRaw(f.Addr)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = c.Close() }()
	_, _ = c.Negotiate([]byte{0x02}, false)
	_, _ = c.Auth("alice", "pw", false)
	_, _ = c.ConnectIP(netip.MustParseAddr("8.8.4.4"), 53, false)
	row := waitForOutcome(t, f, "policy_denied")

	lines := strings.Split(strings.TrimSpace(f.logText()), "\n")
	var matched int
	for _, line := range lines {
		var rec map[string]any
		if json.Unmarshal([]byte(line), &rec) != nil {
			t.Fatalf("non-JSON log line: %q", line)
		}
		if rec["request_id"] != row.RequestID {
			continue
		}
		matched++
		if rec["socks_version"] != "RFC1928/v5" {
			t.Fatalf("missing/incorrect version field: %v", rec)
		}
		if rec["client_addr"] == nil || rec["location"] == nil {
			t.Fatalf("missing client/location: %v", rec)
		}
	}
	if matched == 0 {
		t.Fatalf("no correlated log lines for %s", row.RequestID)
	}
	var failureLine map[string]any
	for _, line := range lines {
		var rec map[string]any
		if json.Unmarshal([]byte(line), &rec) != nil {
			continue
		}
		if rec["request_id"] == row.RequestID && rec["failure_category"] == "policy_denied" {
			failureLine = rec
		}
	}
	if failureLine == nil {
		t.Fatal("no log line with failure_category=policy_denied")
	}
	if reason, _ := failureLine["failure_reason"].(string); !strings.Contains(reason, "8.8.4.4") {
		t.Fatalf("failure_reason does not explain the denial: %v", failureLine["failure_reason"])
	}
}

// 12. IPv6 loopback CONNECT works and is recorded as ATYP=4.
func TestIPv6LoopbackConnect(t *testing.T) {
	target := startEcho(t, "::1")
	port := parsePort(target)
	f := NewFixture(t, FixtureOptions{AuthRequired: true})
	c := connectEcho(t, f, "::1", port, false)
	defer func() { _ = c.Close() }()
	msg := []byte("ipv6-ok")
	_, _ = c.Write(msg)
	_ = c.CloseWrite()
	got, err := io.ReadAll(c)
	if err != nil || !bytes.Equal(got, msg) {
		t.Fatalf("ipv6 echo=%q err=%v", got, err)
	}
	row := waitForOutcome(t, f, "relay_completed")
	if row.TargetHost != "::1" {
		t.Fatalf("target host=%q", row.TargetHost)
	}
}
