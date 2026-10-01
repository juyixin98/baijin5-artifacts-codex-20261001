package integration

import (
	"bytes"
	"context"
	"encoding/hex"
	"io"
	"net"
	"strings"
	"testing"
	"time"

	"socks5d.local/socks5d/internal/auth"
	"socks5d.local/socks5d/internal/config"
	"socks5d.local/socks5d/internal/store"
	"socks5d.local/socks5d/test/fixtures"
	"socks5d.local/socks5d/test/oracle"
)

// env starts an echo upstream and a loopback-whitelist proxy that maps
// echo.local to the echo address.
type env struct {
	h    *fixtures.Harness
	echo *fixtures.EchoUpstream
	host string
	port uint16
}

func startEnv(t *testing.T) env {
	t.Helper()
	echo := fixtures.StartEcho(t)
	host, port := splitHostPort(t, echo.Addr())
	h := fixtures.StartProxy(t, fixtures.LoopbackRules(),
		map[string][]string{"echo.local": {host}}, nil)
	return env{h: h, echo: echo, host: host, port: port}
}

// doConnect performs greeting + CONNECT and returns the client after a
// successful reply.
func doConnect(t *testing.T, h *fixtures.Harness, target string, port uint16) *socksClient {
	t.Helper()
	c := dialProxy(t, h.Addr)
	c.setDeadline(8 * time.Second)
	if sel := c.greet(oracle.MethodNone); !bytes.Equal(sel, oracle.MethodChoice(oracle.MethodNone)) {
		c.close()
		t.Fatalf("method selection = %x want %x", sel, oracle.MethodChoice(oracle.MethodNone))
	}
	if rep := c.connect(target, port); rep.Rep != oracle.RepOK {
		c.close()
		t.Fatalf("CONNECT rep = 0x%02x want 0x%02x", rep.Rep, oracle.RepOK)
	}
	return c
}

// ---------------------------------------------------------------------------
// 1. Segmented handshake + byte-for-byte forwarding
// ---------------------------------------------------------------------------

func TestSegmentedHandshake_ByteForByteForwarding(t *testing.T) {
	e := startEnv(t)
	defer e.h.Server.Shutdown(time.Second)

	c := dialProxy(t, e.h.Addr)
	defer c.close()
	c.setDeadline(8 * time.Second)

	// Emit every handshake byte individually with a small pause.
	fixtures.Segments(t, c.conn, [][]byte{
		oracle.Greeting(oracle.MethodNone),
		oracle.ConnectIPv4([4]byte(net.ParseIP(e.host).To4()), e.port),
	}, 2*time.Millisecond)

	if sel, err := c.readExact(2); err != nil || !bytes.Equal(sel, oracle.MethodChoice(oracle.MethodNone)) {
		t.Fatalf("selection = %x err=%v", sel, err)
	}
	if rep := c.readReply(); rep.Rep != oracle.RepOK {
		t.Fatalf("rep = 0x%02x", rep.Rep)
	}
	if e.echo.AcceptedCount() != 1 {
		t.Fatalf("upstream accepts = %d want 1", e.echo.AcceptedCount())
	}

	payload := bytes.Repeat([]byte("segmented-"), 500)
	if _, err := c.conn.Write(payload); err != nil {
		t.Fatalf("write payload: %v", err)
	}
	got := make([]byte, len(payload))
	if _, err := io.ReadFull(c.conn, got); err != nil {
		t.Fatalf("read echo: %v", err)
	}
	if !bytes.Equal(got, payload) {
		t.Fatal("echoed payload differs byte-for-byte")
	}
}

// ---------------------------------------------------------------------------
// 2. Authentication: method negotiation failure and rejected credentials
// ---------------------------------------------------------------------------

func startAuthEnv(t *testing.T) (env, interface {
	Authenticate(string, string) bool
}) {
	e := startEnv(t)
	a, err := auth.NewUserPass("alice", "secret")
	if err != nil {
		t.Fatal(err)
	}
	// Replace the no-auth proxy with one requiring credentials.
	e.h.Server.Shutdown(time.Second)
	h := fixtures.StartProxy(t, fixtures.LoopbackRules(),
		map[string][]string{"echo.local": {e.host}}, a)
	e.h = h
	return e, a
}

func TestAuth_RequiredButNotOffered_EndsExplicitly(t *testing.T) {
	echo := fixtures.StartEcho(t)
	host, _ := splitHostPort(t, echo.Addr())
	authn, err := auth.NewUserPass("alice", "secret")
	if err != nil {
		t.Fatal(err)
	}
	h := fixtures.StartProxy(t, fixtures.LoopbackRules(),
		map[string][]string{"echo.local": {host}}, authn)
	defer h.Server.Shutdown(time.Second)

	c := dialProxy(t, h.Addr)
	defer c.close()
	c.setDeadline(5 * time.Second)

	// Client offers only no-auth although the server requires user/pass.
	sel := c.greet(oracle.MethodNone)
	if !bytes.Equal(sel, []byte{0x05, 0xFF}) {
		t.Fatalf("selection = %x want 05ff (no acceptable methods)", sel)
	}
	// The negotiation ends: the server closes the connection.
	if _, err := c.conn.Read(make([]byte, 1)); err == nil {
		t.Fatal("expected connection close after 0xFF")
	}
	if echo.AcceptedCount() != 0 {
		t.Fatalf("upstream must not be dialed after method failure: %d", echo.AcceptedCount())
	}
}

func TestAuth_RejectedCredentials(t *testing.T) {
	echo := fixtures.StartEcho(t)
	host, _ := splitHostPort(t, echo.Addr())
	authn, _ := auth.NewUserPass("alice", "secret")
	h := fixtures.StartProxy(t, fixtures.LoopbackRules(),
		map[string][]string{"echo.local": {host}}, authn)
	defer h.Server.Shutdown(time.Second)

	c := dialProxy(t, h.Addr)
	defer c.close()
	c.setDeadline(5 * time.Second)
	if sel := c.greet(oracle.MethodUserPass); !bytes.Equal(sel, oracle.MethodChoice(oracle.MethodUserPass)) {
		t.Fatalf("selection = %x", sel)
	}
	if status := c.auth("alice", "wrong"); status != oracle.StatusFail {
		t.Fatalf("auth status = 0x%02x want 0x01", status)
	}
	// After rejection the server ends the connection.
	if _, err := c.conn.Read(make([]byte, 1)); err == nil {
		t.Fatal("expected close after auth failure")
	}
	if echo.AcceptedCount() != 0 {
		t.Fatal("upstream must not be dialed after failed auth")
	}
}

func TestAuth_AcceptedCredentialsThenConnect(t *testing.T) {
	e, _ := startAuthEnv(t)
	defer e.h.Server.Shutdown(time.Second)

	c := dialProxy(t, e.h.Addr)
	defer c.close()
	c.setDeadline(5 * time.Second)
	if sel := c.greet(oracle.MethodUserPass); !bytes.Equal(sel, oracle.MethodChoice(oracle.MethodUserPass)) {
		t.Fatalf("selection = %x", sel)
	}
	if status := c.auth("alice", "secret"); status != oracle.StatusOK {
		t.Fatalf("auth status = 0x%02x want 0x00", status)
	}
	if rep := c.connect("echo.local", e.port); rep.Rep != oracle.RepOK {
		t.Fatalf("rep = 0x%02x after valid auth", rep.Rep)
	}
}

// ---------------------------------------------------------------------------
// 3. Domain with multiple resolved addresses; ordered vetted fallback
// ---------------------------------------------------------------------------

func TestDomain_MultiAddress_FallbackWithinWhitelist(t *testing.T) {
	echo := fixtures.StartEcho(t)
	echoHost, port := splitHostPort(t, echo.Addr())

	// echo.local resolves first to an unlistened loopback address, then to
	// the live echo. The proxy must try vetted candidates in order.
	hosts := map[string][]string{"echo.local": {"127.0.0.2", echoHost}}
	h := fixtures.StartProxy(t, fixtures.LoopbackRules(), hosts, nil)
	defer h.Server.Shutdown(time.Second)

	c := doConnect(t, h, "echo.local", port)
	defer c.close()

	if echo.AcceptedCount() != 1 {
		t.Fatalf("echo accepts = %d want 1 (fallback should reach live addr)", echo.AcceptedCount())
	}
	// Audit must record the address actually connected to: the live
	// loopback, never the failed first candidate. The audit row is written
	// immediately after the reply is sent, so poll briefly rather than race.
	var rows []store.AuditRow
	deadline := time.Now().Add(3 * time.Second)
	for {
		var err error
		rows, err = h.Store.AuditTrail(context.Background(), "")
		if err != nil {
			t.Fatalf("audit: %v", err)
		}
		if hasAuditStage(rows, "connect", "ok") {
			break
		}
		if time.Now().After(deadline) {
			t.Fatalf("no successful connect audit row; got %s", auditDigest(rows))
		}
		time.Sleep(10 * time.Millisecond)
	}
	for _, r := range rows {
		if r.Stage == "connect" && r.Result == "ok" {
			if !strings.HasPrefix(r.Target, echoHost+":") {
				t.Fatalf("connected target %q is not the vetted live address %s", r.Target, echoHost)
			}
		}
	}
}

// ---------------------------------------------------------------------------
// 4. Directional half-close through the proxy
// ---------------------------------------------------------------------------

func TestHalfClose_OneDirectionalEOF(t *testing.T) {
	echo := fixtures.StartEcho(t)
	host, port := splitHostPort(t, echo.Addr())
	h := fixtures.StartProxy(t, fixtures.LoopbackRules(),
		map[string][]string{"echo.local": {host}}, nil)
	defer h.Server.Shutdown(time.Second)

	c := doConnect(t, h, "echo.local", port)
	defer c.close()
	c.setDeadline(8 * time.Second)

	down := []byte("downstream-data")
	if _, err := c.conn.Write(down); err != nil {
		t.Fatal(err)
	}
	echoed := make([]byte, len(down))
	if _, err := io.ReadFull(c.conn, echoed); err != nil {
		t.Fatalf("echo: %v", err)
	}
	if !bytes.Equal(echoed, down) {
		t.Fatal("mismatch")
	}

	// Half-close client write: upstream sees EOF and mirrors a half-close
	// back; the client then observes a clean EOF. No data is lost first.
	if err := c.conn.(*net.TCPConn).CloseWrite(); err != nil {
		t.Fatal(err)
	}
	_ = c.conn.SetReadDeadline(time.Now().Add(5 * time.Second))
	if _, err := c.conn.Read(make([]byte, 1)); err != io.EOF {
		t.Fatalf("expected clean EOF after bilateral half-close, got %v", err)
	}
}

// ---------------------------------------------------------------------------
// 5. Slow upstream: backpressure, full drain before the half-close EOF
// ---------------------------------------------------------------------------

func TestSlowUpstream_FullDrain(t *testing.T) {
	slow := fixtures.StartSlow(t)
	host, port := splitHostPort(t, slow.Addr())
	h := fixtures.StartProxy(t, fixtures.LoopbackRules(),
		map[string][]string{"svc.internal": {host}}, nil)
	defer h.Server.Shutdown(time.Second)

	c := doConnect(t, h, "svc.internal", port)
	defer c.close()
	c.setDeadline(15 * time.Second)

	const bodyLen = 200 * 1024
	body := append([]byte("first-line\n"), bytes.Repeat([]byte("Z"), bodyLen)...)
	if _, err := c.conn.Write(body); err != nil {
		t.Fatalf("write: %v", err)
	}
	if err := c.conn.(*net.TCPConn).CloseWrite(); err != nil {
		t.Fatal(err)
	}
	got, err := io.ReadAll(c.conn)
	if err != nil {
		t.Fatalf("read all: %v", err)
	}
	if !bytes.Equal(got, body) {
		t.Fatalf("slow upstream returned %d bytes want %d", len(got), len(body))
	}
}

// ---------------------------------------------------------------------------
// 6. Non-whitelisted targets must never be dialed
// ---------------------------------------------------------------------------

func TestNonWhitelistedTarget_NeverConnected(t *testing.T) {
	echo := fixtures.StartEcho(t)
	echoHost, port := splitHostPort(t, echo.Addr())

	// Restricted policy: only the exact domain is allowed; NOT 127.0.0.0/8.
	rules := []config.Rule{{Kind: "domain", Value: "echo.local", Mode: "exact"}}
	h := fixtures.StartProxy(t, rules, map[string][]string{"echo.local": {echoHost}}, nil)
	defer h.Server.Shutdown(time.Second)

	connectExpect := func(t *testing.T, h *fixtures.Harness, frame func() []byte, want byte) {
		t.Helper()
		c := dialProxy(t, h.Addr)
		defer c.close()
		c.setDeadline(5 * time.Second)
		c.greet(oracle.MethodNone)
		rep := c.connectRaw(frame())
		if rep.Rep != want {
			t.Fatalf("rep = 0x%02x want 0x%02x", rep.Rep, want)
		}
	}

	t.Run("literal_loopback_ip_rejected", func(t *testing.T) {
		connectExpect(t, h, func() []byte {
			return oracle.ConnectIPv4([4]byte(net.ParseIP(echoHost).To4()), port)
		}, oracle.RepRuleset)
	})

	t.Run("unknown_domain_rejected", func(t *testing.T) {
		connectExpect(t, h, func() []byte { return oracle.ConnectName("evil.example", port) }, oracle.RepRuleset)
	})

	t.Run("allowed_name_resolving_public_rejected", func(t *testing.T) {
		// Same allowed name, but one resolved record is public. The whole
		// request must be rejected.
		hosts2 := map[string][]string{"echo.local": {echoHost, "93.184.216.34"}}
		h2 := fixtures.StartProxy(t, fixtures.LoopbackRules(), hosts2, nil)
		defer h2.Server.Shutdown(time.Second)
		connectExpect(t, h2, func() []byte { return oracle.ConnectName("echo.local", port) }, oracle.RepRuleset)
	})

	// The echo listener must never have accepted any connection.
	if got := echo.AcceptedCount(); got != 0 {
		t.Fatalf("non-whitelisted upstream was dialed %d times; want 0", got)
	}
}

// ---------------------------------------------------------------------------
// 7. IPv6 literal connectivity
// ---------------------------------------------------------------------------

func TestIPv6Literal_Loopback(t *testing.T) {
	echo6 := fixtures.StartEcho6(t)
	host, port := splitHostPort(t, echo6.Addr())
	h := fixtures.StartProxy(t, fixtures.LoopbackRules(), nil, nil)
	defer h.Server.Shutdown(time.Second)

	c := doConnect(t, h, "["+host+"]", port)
	defer c.close()
	if _, err := c.conn.Write([]byte("v6")); err != nil {
		t.Fatal(err)
	}
	got := make([]byte, 2)
	if _, err := io.ReadFull(c.conn, got); err != nil {
		t.Fatalf("v6 echo: %v", err)
	}
}

// ---------------------------------------------------------------------------
// 8. Unsupported command / address type reply codes
// ---------------------------------------------------------------------------

func TestUnsupportedCommandAndAtyp(t *testing.T) {
	e := startEnv(t)
	defer e.h.Server.Shutdown(time.Second)

	t.Run("bind_command", func(t *testing.T) {
		c := dialProxy(t, e.h.Addr)
		defer c.close()
		c.setDeadline(5 * time.Second)
		c.greet(oracle.MethodNone)
		frame := oracle.ConnectIPv4([4]byte(net.ParseIP(e.host).To4()), e.port)
		frame[1] = 0x02 // CMD=BIND
		if rep := c.connectRaw(frame); rep.Rep != oracle.RepCmdUnsup {
			t.Fatalf("rep = 0x%02x want 0x%02x", rep.Rep, oracle.RepCmdUnsup)
		}
	})

	t.Run("unknown_atyp", func(t *testing.T) {
		c := dialProxy(t, e.h.Addr)
		defer c.close()
		c.setDeadline(5 * time.Second)
		c.greet(oracle.MethodNone)
		raw, _ := hex.DecodeString("050100057f0000010438")
		if rep := c.connectRaw(raw); rep.Rep != oracle.RepAtypUnsup {
			t.Fatalf("rep = 0x%02x want 0x%02x", rep.Rep, oracle.RepAtypUnsup)
		}
	})
}

// ---------------------------------------------------------------------------
// 9. Method negotiation protocol failures end the connection with no reply
// ---------------------------------------------------------------------------

func TestMethodNegotiationFailures(t *testing.T) {
	e := startEnv(t)
	defer e.h.Server.Shutdown(time.Second)

	t.Run("wrong_version_no_reply_and_close", func(t *testing.T) {
		c := dialProxy(t, e.h.Addr)
		defer c.close()
		c.setDeadline(5 * time.Second)
		if sel := c.greetRaw([]byte{0x04, 0x01, 0x00}); sel != nil {
			t.Fatalf("expected no reply for VER=4, got %x", sel)
		}
	})

	t.Run("zero_methods", func(t *testing.T) {
		c := dialProxy(t, e.h.Addr)
		defer c.close()
		c.setDeadline(5 * time.Second)
		if sel := c.greetRaw([]byte{0x05, 0x00}); sel != nil {
			t.Fatalf("expected close for zero methods, got %x", sel)
		}
	})
}

// ---------------------------------------------------------------------------
// 10. Per-connection byte budget enforced end to end
// ---------------------------------------------------------------------------

func TestByteBudget_EnforcedEndToEnd(t *testing.T) {
	echo := fixtures.StartEcho(t)
	host, port := splitHostPort(t, echo.Addr())
	h := fixtures.StartProxyConfig(t, func(c *config.Config) {
		c.Rules = fixtures.LoopbackRules()
		c.Resolver.Hosts = map[string][]string{"echo.local": {host}}
		c.ByteBudget = 4096
		c.IdleTimeout = config.Duration{Duration: 10 * time.Second}
	}, nil)
	defer h.Server.Shutdown(time.Second)

	c := doConnect(t, h, "echo.local", port)
	defer c.close()
	c.setDeadline(15 * time.Second)

	const total = 60000
	const budget = 4096
	go func() { _, _ = c.conn.Write(bytes.Repeat([]byte("Q"), total)) }()

	got, _ := io.ReadAll(c.conn)
	// Security property under test: each direction is capped at the budget
	// before writing, so the client can never receive more than the budget
	// even though 60000 bytes were sent. (The exact lower-bound cutoff is
	// asserted deterministically in the relay unit tests.)
	if int64(len(got)) > budget {
		t.Fatalf("received %d bytes, exceeds per-direction budget %d", len(got), budget)
	}

	// Audit records the bounded failure category, distinct from success.
	// It is written after the relay unwinds, so poll rather than race.
	deadline := time.Now().Add(3 * time.Second)
	var rows []store.AuditRow
	for {
		rows, _ = h.Store.AuditTrail(context.Background(), "")
		if hasAuditReason(rows, "relay", "byte_budget_exceeded") {
			break
		}
		if time.Now().After(deadline) {
			t.Fatalf("expected byte_budget_exceeded audit row; got %s", auditDigest(rows))
		}
		time.Sleep(10 * time.Millisecond)
	}
}

// ---------------------------------------------------------------------------
// 11. Connection count is bounded
// ---------------------------------------------------------------------------

func TestMaxConnections_Bounded(t *testing.T) {
	echo := fixtures.StartEcho(t)
	host, port := splitHostPort(t, echo.Addr())
	h := fixtures.StartProxyConfig(t, func(c *config.Config) {
		c.Rules = fixtures.LoopbackRules()
		c.Resolver.Hosts = map[string][]string{"echo.local": {host}}
		c.MaxConnections = 1
		c.IdleTimeout = config.Duration{Duration: 0}
	}, nil)
	defer h.Server.Shutdown(time.Second)

	// Occupy the single slot with a fully established, idle session.
	holder := doConnect(t, h, "echo.local", port)
	defer holder.close()

	// A second TCP client must be refused at capacity and closed, never
	// reach the method negotiation reply.
	c := dialProxy(t, h.Addr)
	defer c.close()
	c.setDeadline(5 * time.Second)
	_, _ = c.conn.Write(oracle.Greeting(oracle.MethodNone))
	if _, err := io.ReadFull(c.conn, make([]byte, 2)); err == nil {
		t.Fatal("capacity limit not enforced: received a method selection")
	}
}

// ---------------------------------------------------------------------------
// 12. Logs are correlated and explainable
// ---------------------------------------------------------------------------

func TestLogs_CorrelatedAndStructured(t *testing.T) {
	e := startEnv(t)
	defer e.h.Server.Shutdown(time.Second)

	c := doConnect(t, e.h, "echo.local", e.port)
	if _, err := c.conn.Write([]byte("hi")); err != nil {
		t.Fatal(err)
	}
	_ = c.conn.(*net.TCPConn).CloseWrite()
	_ = c.conn.SetReadDeadline(time.Now().Add(5 * time.Second))
	_, _ = io.Copy(io.Discard, c.conn)
	c.close()

	// relay_end is logged when the relay goroutine unwinds; wait for it
	// rather than reading the captured lines immediately.
	var lines []string
	deadline := time.Now().Add(3 * time.Second)
	for {
		lines = e.h.LogLines()
		if joined := strings.Join(lines, ""); strings.Contains(joined, `"event":"relay_end"`) {
			break
		}
		if time.Now().After(deadline) {
			break
		}
		time.Sleep(10 * time.Millisecond)
	}
	if len(lines) == 0 {
		t.Fatal("no structured log lines captured")
	}
	joined := strings.Join(lines, "")
	for _, want := range []string{`"version":"socks5d/1.0.0"`, `"req_id":"r-`, `"at":"`, `"event":"relay_end"`} {
		if !strings.Contains(joined, want) {
			t.Fatalf("logs missing %s\n%s", want, joined)
		}
	}
	// The same req_id must recur across the lifecycle (correlation).
	if !sameReqIDRepeats(lines) {
		t.Fatalf("req_id is not correlated across log lines\n%s", joined)
	}
}

func hasAuditReason(rows []store.AuditRow, stage, reason string) bool {
	for _, r := range rows {
		if r.Stage == stage && r.Reason == reason {
			return true
		}
	}
	return false
}

func hasAuditStage(rows []store.AuditRow, stage, result string) bool {
	for _, r := range rows {
		if r.Stage == stage && r.Result == result {
			return true
		}
	}
	return false
}

func auditDigest(rows []store.AuditRow) string {
	var b strings.Builder
	for _, r := range rows {
		b.WriteString(r.Stage)
		b.WriteString("/")
		b.WriteString(r.Result)
		b.WriteString("/")
		b.WriteString(r.Reason)
		b.WriteString("; ")
	}
	return b.String()
}

func sameReqIDRepeats(lines []string) bool {
	seen := map[string]int{}
	for _, ln := range lines {
		const key = `"req_id":"`
		i := strings.Index(ln, key)
		if i < 0 {
			continue
		}
		rest := ln[i+len(key):]
		j := strings.IndexByte(rest, '"')
		if j <= 0 {
			continue
		}
		seen[rest[:j]]++
	}
	for _, n := range seen {
		if n >= 2 {
			return true
		}
	}
	return false
}
