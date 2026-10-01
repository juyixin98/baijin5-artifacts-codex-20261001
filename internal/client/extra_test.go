package client_test

import (
	"context"
	"net"
	"sync"
	"testing"
	"time"

	"localstun/internal/client"
	"localstun/internal/stun"
	"localstun/internal/stunerror"
)

func TestRoundTrip_BindingErrorResponse(t *testing.T) {
	// Scripted server returns a Binding ERROR (420); RoundTrip must surface a
	// state-class error carrying the code and reason.
	fake, txids, _ := newFakeSTUN(t, "127.0.0.1:0")
	c, err := client.New(client.Config{
		ServerAddr: fake.LocalAddr().(*net.UDPAddr), Timeout: time.Second,
	})
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()

	done := make(chan error, 1)
	go func() {
		_, err := c.RoundTrip(context.Background())
		done <- err
	}()
	txid := <-txids

	// Send a BindingError directly from the fake socket.
	m := stun.NewMessage(stun.BindingError, txid)
	m.AddErrorCode(stun.StatusUnknownAttribute, "Unknown Attribute")
	out, err := stun.Marshal(m, nil, false)
	if err != nil {
		t.Fatal(err)
	}
	fake.mu.Lock()
	dst := fake.client
	fake.mu.Unlock()
	if _, err := fake.conn.WriteToUDP(out, dst); err != nil {
		t.Fatal(err)
	}

	select {
	case err := <-done:
		if stunerror.Of(err) != stunerror.KindState {
			t.Fatalf("binding error response kind=%s want state", stunerror.Of(err))
		}
	case <-time.After(2 * time.Second):
		t.Fatal("no result for error response")
	}
}

func TestRoundTrip_MalformedPacketFromRightSourceIgnoredUntilReply(t *testing.T) {
	fake, txids, replies := newFakeSTUN(t, "127.0.0.1:0")
	var mu sync.Mutex
	var sawDecodeErr bool
	c, err := client.New(client.Config{
		ServerAddr: fake.LocalAddr().(*net.UDPAddr), Timeout: time.Second,
		OnEvent: func(e client.Event) {
			if e.Type == client.EvRecvDecodeError && e.Kind == stunerror.KindInput {
				mu.Lock()
				sawDecodeErr = true
				mu.Unlock()
			}
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()

	done := make(chan *client.Result, 1)
	errs := make(chan error, 1)
	go func() {
		res, err := c.RoundTrip(context.Background())
		if err != nil {
			errs <- err
			return
		}
		done <- res
	}()
	txid := <-txids

	// A malformed (bad-cookie) datagram from the correct source must be
	// classified input and NOT complete the transaction.
	bad := make([]byte, 20)
	copy(bad[8:20], txid[:])
	// type 0x0001, length 0, cookie deliberately left zero
	fake.mu.Lock()
	dst := fake.client
	fake.mu.Unlock()
	if _, err := fake.conn.WriteToUDP(bad, dst); err != nil {
		t.Fatal(err)
	}
	waitFor(100 * time.Millisecond)

	// Real response still completes it.
	replies <- responseCmd{txid: txid, ip: "198.51.100.9", port: c.LocalAddr().Port}
	select {
	case res := <-done:
		if res.IP.String() != "198.51.100.9" {
			t.Fatalf("unexpected ip %s", res.IP)
		}
	case err := <-errs:
		t.Fatalf("request failed: %v", err)
	case <-time.After(2 * time.Second):
		t.Fatal("request never completed")
	}
	mu.Lock()
	ok := sawDecodeErr
	mu.Unlock()
	if !ok {
		t.Fatal("expected an input-class decode_error event")
	}
}

func TestTxIDSequence(t *testing.T) {
	a := client.TxIDSequence(1)
	b := client.TxIDSequence(2)
	if a == b {
		t.Fatal("sequence ids must differ")
	}
	if a[7] != 1 || b[7] != 2 {
		t.Fatalf("low counter bytes a=%x b=%x", a[7], b[7])
	}
}
