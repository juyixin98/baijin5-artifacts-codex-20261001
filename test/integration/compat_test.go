package integration

import (
	"bytes"
	"io"
	"net"
	"strconv"
	"strings"
	"testing"
	"time"

	"golang.org/x/net/proxy"
)

// xnetDialer builds the third-party golang.org/x/net/proxy SOCKS5 client.
func xnetDialer(t *testing.T, proxyAddr string, auth *proxy.Auth) proxy.Dialer {
	t.Helper()
	d, err := proxy.SOCKS5("tcp", proxyAddr, auth, &net.Dialer{Timeout: 3 * time.Second})
	if err != nil {
		t.Fatalf("x/net SOCKS5 dialer: %v", err)
	}
	return d
}

// Compatibility: the independently maintained golang.org/x/net/proxy client
// must complete a no-auth CONNECT and relay bytes verbatim.
func TestXNetClientNoAuth(t *testing.T) {
	target := startEcho(t, "127.0.0.1")
	port := parsePort(target)
	f := NewFixture(t, FixtureOptions{AuthRequired: false})

	d := xnetDialer(t, f.Addr, nil)
	conn, err := d.Dial("tcp", net.JoinHostPort("127.0.0.1", strconv.Itoa(port)))
	if err != nil {
		t.Fatalf("x/net dial: %v", err)
	}
	defer func() { _ = conn.Close() }()

	payload := bytes.Repeat([]byte("xnet-noauth-"), 2000)
	if _, err := conn.Write(payload); err != nil {
		t.Fatal(err)
	}
	_ = conn.(*net.TCPConn).CloseWrite()
	got, err := io.ReadAll(conn)
	if err != nil {
		t.Fatalf("read: %v", err)
	}
	if !bytes.Equal(got, payload) {
		t.Fatalf("x/net payload mismatch: got %d want %d", len(got), len(payload))
	}
	waitForOutcome(t, f, "relay_completed")
}

// Compatibility: x/net client with correct username/password.
func TestXNetClientUserPassOK(t *testing.T) {
	target := startEcho(t, "127.0.0.1")
	port := parsePort(target)
	f := NewFixture(t, FixtureOptions{AuthRequired: true})

	d := xnetDialer(t, f.Addr, &proxy.Auth{User: "alice", Password: "pw"})
	conn, err := d.Dial("tcp", net.JoinHostPort("127.0.0.1", strconv.Itoa(port)))
	if err != nil {
		t.Fatalf("x/net authenticated dial: %v", err)
	}
	defer func() { _ = conn.Close() }()
	if _, err := conn.Write([]byte("hello-xnet-auth")); err != nil {
		t.Fatal(err)
	}
	_ = conn.(*net.TCPConn).CloseWrite()
	got, err := io.ReadAll(conn)
	if err != nil || string(got) != "hello-xnet-auth" {
		t.Fatalf("echo=%q err=%v", got, err)
	}
	row := waitForOutcome(t, f, "relay_completed")
	if row.Username != "alice" {
		t.Fatalf("username recorded=%q", row.Username)
	}
}

// Compatibility: x/net client with wrong password sees the RFC 1929
// rejection surfaced as a dial error and nothing is relayed.
func TestXNetClientBadPasswordRejected(t *testing.T) {
	f := NewFixture(t, FixtureOptions{AuthRequired: true})
	d := xnetDialer(t, f.Addr, &proxy.Auth{User: "alice", Password: "nope"})
	_, err := d.Dial("tcp", "127.0.0.1:9")
	if err == nil {
		t.Fatal("dial with bad password unexpectedly succeeded")
	}
	// x/net reports the 0x01 sub-negotiation status as an auth error.
	if !strings.Contains(strings.ToLower(err.Error()), "auth") &&
		!strings.Contains(err.Error(), "1") {
		t.Fatalf("unexpected rejection error: %v", err)
	}
	waitForOutcome(t, f, "auth_denied")
}

// Compatibility: x/net client connecting to a non-whitelisted IP receives
// REP=0x02 mapped by x/net to "connection not allowed by ruleset".
func TestXNetClientPolicyDenied(t *testing.T) {
	f := NewFixture(t, FixtureOptions{AuthRequired: true})
	d := xnetDialer(t, f.Addr, &proxy.Auth{User: "alice", Password: "pw"})
	_, err := d.Dial("tcp", "8.8.8.8:53")
	if err == nil {
		t.Fatal("dial to denied target succeeded")
	}
	if !strings.Contains(err.Error(), "not allowed") {
		t.Fatalf("error %q must state connection not allowed", err.Error())
	}
	if dialed := f.Dialer.snapshot(); len(dialed) != 0 {
		t.Fatalf("denied target was dialed: %v", dialed)
	}
	waitForOutcome(t, f, "policy_denied")
}
