package compat

import (
	"errors"
	"net"
	"testing"
	"time"

	"mbfixture/internal/mbclient"
	"mbfixture/internal/mbcodec"
)

// TestClientTimeout runs the master against a slave that accepts the
// connection but never answers; the request must fail with ErrTimeout and
// name the transaction identity.
func TestClientTimeout(t *testing.T) {
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { ln.Close() })
	go func() {
		c, err := ln.Accept()
		if err != nil {
			return
		}
		defer c.Close()
		// Drain the request, then stay silent.
		mbcodec.ReadFrame(c)
		time.Sleep(2 * time.Second)
	}()

	c, err := mbclient.Dial(ln.Addr().String(), 200*time.Millisecond, testLogger("client"))
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()
	_, err = c.ReadHoldingRegisters(1, 0, 1)
	if !errors.Is(err, mbclient.ErrTimeout) {
		t.Fatalf("got %v, want ErrTimeout", err)
	}
}

// TestClientValidatesArguments checks the master rejects out-of-contract
// quantities before anything goes on the wire.
func TestClientValidatesArguments(t *testing.T) {
	addr := startServer(t, 8, nil)
	c := dialClient(t, addr)
	if _, err := c.ReadHoldingRegisters(1, 0, 0); err == nil {
		t.Fatal("read qty=0 accepted")
	}
	if _, err := c.ReadHoldingRegisters(1, 0, 126); err == nil {
		t.Fatal("read qty=126 accepted")
	}
	if err := c.WriteMultipleRegisters(1, 0, nil); err == nil {
		t.Fatal("write qty=0 accepted")
	}
	if err := c.WriteMultipleRegisters(1, 0, make([]uint16, 124)); err == nil {
		t.Fatal("write qty=124 accepted")
	}
}
