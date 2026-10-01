package client_test

import (
	"context"
	"net"
	"testing"
	"time"

	"localstun/internal/client"
	"localstun/internal/stunerror"
)

func TestNew_ConfigValidation(t *testing.T) {
	if _, err := client.New(client.Config{}); err == nil {
		t.Fatal("missing ServerAddr must fail")
	}
	if stunerror.Of(nil) != stunerror.KindUnknown {
		t.Fatal("nil error kind must be unknown")
	}
	addr, _ := net.ResolveUDPAddr("udp", "127.0.0.1:9")
	if _, err := client.New(client.Config{
		ServerAddr: addr, Fingerprint: true,
	}); stunerror.Of(err) != stunerror.KindInput {
		t.Fatalf("fingerprint without key kind=%s", stunerror.Of(err))
	}
}

func TestClose_IsIdempotent(t *testing.T) {
	addr, _ := net.ResolveUDPAddr("udp", "127.0.0.1:9")
	c, err := client.New(client.Config{ServerAddr: addr, Timeout: 50 * time.Millisecond})
	if err != nil {
		t.Fatal(err)
	}
	if err := c.Close(); err != nil {
		t.Fatalf("first close: %v", err)
	}
	if err := c.Close(); err != nil {
		t.Fatalf("second close must be nil: %v", err)
	}
}

func TestRoundTrip_AfterCloseIsStateError(t *testing.T) {
	addr, _ := net.ResolveUDPAddr("udp", "127.0.0.1:9")
	c, err := client.New(client.Config{ServerAddr: addr, Timeout: time.Second})
	if err != nil {
		t.Fatal(err)
	}
	_ = c.Close()
	_, err = c.RoundTrip(context.Background())
	if stunerror.Of(err) != stunerror.KindState {
		t.Fatalf("roundtrip after close kind=%s want state", stunerror.Of(err))
	}
}
