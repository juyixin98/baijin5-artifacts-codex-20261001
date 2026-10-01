// Package compat_test holds compatibility tests that exercise the SAME
// client and server state machines used by the simulation, but over REAL
// loopback UDP sockets. These tests guard the boundary between the virtual
// fabric and the operating-system network stack.
//
// They bind only to 127.0.0.1 ephemeral ports and never touch the system clock.
package compat_test

import (
	"errors"
	"testing"
	"time"

	"ntpsim/internal/clock"
	"ntpsim/internal/core"
	"ntpsim/internal/harness"
	"ntpsim/internal/transport"
)

// TestRealUDPHealthyExchange runs a full request/reply over a real socket.
// Both peers read the wall clock, so the on-wire offset must be tiny and RTT
// a small non-negative value.
func TestRealUDPHealthyExchange(t *testing.T) {
	srv, err := harness.ServeUDPServer(transport.DefaultServerProfile(), clock.WallClock{})
	if err != nil {
		t.Fatalf("server: %v", err)
	}
	defer srv.Close()

	dg, err := transport.DialUDP("127.0.0.1:0")
	if err != nil {
		t.Fatalf("dial: %v", err)
	}
	defer dg.Close()

	c := transport.NewClient(clock.WallClock{})
	s, err := c.Exchange(dg, srv.Addr(), transport.ExchangeConfig{
		Timeout: time.Second,
		Epsilon: time.Millisecond,
	})
	if err != nil {
		t.Fatalf("exchange: %v", err)
	}
	if !s.OK() {
		t.Fatalf("status=%s reason=%s", s.Status, s.Reason)
	}
	if s.RTT < 0 {
		t.Fatalf("negative RTT on loopback: %v", s.RTT)
	}
	if s.RTT > 500*time.Millisecond {
		t.Fatalf("loopback RTT implausibly large: %v", s.RTT)
	}
	if off := abs(s.Offset); off > 100*time.Millisecond {
		t.Fatalf("loopback offset %v implausibly large", s.Offset)
	}
	// Interval must contain the (near-zero) true offset.
	if s.Lower > 0 || s.Upper < 0 {
		t.Fatalf("zero offset not inside [%v,%v]", s.Lower, s.Upper)
	}
}

// TestRealUDPServerEchoesOrigin is the anti-replay invariant on real bytes:
// the server response's origin field equals exactly what we transmitted.
func TestRealUDPServerEchoesOrigin(t *testing.T) {
	srv, err := harness.ServeUDPServer(transport.DefaultServerProfile(), clock.WallClock{})
	if err != nil {
		t.Fatalf("server: %v", err)
	}
	defer srv.Close()

	dg, err := transport.DialUDP("127.0.0.1:0")
	if err != nil {
		t.Fatalf("dial: %v", err)
	}
	defer dg.Close()

	t1 := clock.WallClock{}.Now()
	req := encodeClient(t1)
	if err := dg.Send(req, srv.Addr()); err != nil {
		t.Fatalf("send: %v", err)
	}
	b, _, err := dg.Recv(time.Now().Add(time.Second))
	if err != nil {
		t.Fatalf("recv: %v", err)
	}
	if len(b) != 48 {
		t.Fatalf("reply len=%d want 48", len(b))
	}
	gotOrigin := b[24:32]
	wantOrigin := req[40:48]
	if string(gotOrigin) != string(wantOrigin) {
		t.Fatalf("origin bytes %x != transmitted %x", gotOrigin, wantOrigin)
	}
}

// TestRealUDPReplayRejected: a server that always answers with a stale origin
// timestamp must be rejected; the client discards the stale packet and fails
// with the replay category rather than accepting bogus math.
func TestRealUDPReplayRejected(t *testing.T) {
	srv, err := harness.ServeReplayUDPServer()
	if err != nil {
		t.Fatalf("replay server: %v", err)
	}
	defer srv.Close()

	dg, err := transport.DialUDP("127.0.0.1:0")
	if err != nil {
		t.Fatalf("dial: %v", err)
	}
	defer dg.Close()

	c := transport.NewClient(clock.WallClock{})
	_, err = c.Exchange(dg, srv.Addr(), transport.ExchangeConfig{
		Timeout: 250 * time.Millisecond,
	})
	if err == nil {
		t.Fatal("stale reply must not be accepted")
	}
	if !errors.Is(err, transport.ErrOnlyStaleReplies) {
		t.Fatalf("err=%v want ErrOnlyStaleReplies", err)
	}
}

// TestRealUDPNoServerTimeout: sending to an unbound loopback port yields the
// categorized timeout failure, never a success or panic.
func TestRealUDPNoServerTimeout(t *testing.T) {
	ln, err := transport.ListenUDP("127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen: %v", err)
	}
	addr := ln.LocalAddr()
	_ = ln.Close() // free the port immediately

	dg, err := transport.DialUDP("127.0.0.1:0")
	if err != nil {
		t.Fatalf("dial: %v", err)
	}
	defer dg.Close()

	c := transport.NewClient(clock.WallClock{})
	start := time.Now()
	_, err = c.Exchange(dg, addr, transport.ExchangeConfig{Timeout: 200 * time.Millisecond})
	if err == nil {
		t.Fatal("expected timeout to unbound port")
	}
	if !errors.Is(err, transport.ErrTimeout) {
		t.Fatalf("err=%v want ErrTimeout", err)
	}
	if time.Since(start) < 150*time.Millisecond {
		t.Fatalf("returned early after %v", time.Since(start))
	}
}

// TestRealUDPKissOfDeath verifies the stratum-0 semantic category end to end
// over real UDP.
func TestRealUDPKissOfDeath(t *testing.T) {
	prof := transport.ServerProfile{Stratum: 0, KissCode: "DENY"}
	srv, err := harness.ServeUDPServer(prof, clock.WallClock{})
	if err != nil {
		t.Fatalf("server: %v", err)
	}
	defer srv.Close()

	dg, err := transport.DialUDP("127.0.0.1:0")
	if err != nil {
		t.Fatalf("dial: %v", err)
	}
	defer dg.Close()

	c := transport.NewClient(clock.WallClock{})
	s, err := c.Exchange(dg, srv.Addr(), transport.ExchangeConfig{Timeout: time.Second})
	if err != nil {
		t.Fatalf("exchange: %v", err)
	}
	if s.Status != core.StatusKissOfDeath {
		t.Fatalf("status=%s want KISS_OF_DEATH", s.Status)
	}
}

func abs(d time.Duration) time.Duration {
	if d < 0 {
		return -d
	}
	return d
}
