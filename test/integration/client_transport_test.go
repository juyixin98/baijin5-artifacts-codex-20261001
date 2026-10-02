package integration_test

import (
	"context"
	"errors"
	"net"
	"sync/atomic"
	"testing"
	"time"

	"coaplab/internal/ids"
	"coaplab/internal/transport"
	"coaplab/internal/wire"
	"coaplab/test/harness"
)

// A server answering a CON with RST must surface ErrRST to the client (the
// request was refused at the message layer), and the client must not retry.
func TestClient_RSTStopsExchange(t *testing.T) {
	srv, err := harness.NewScriptedServer()
	if err != nil {
		t.Fatal(err)
	}
	defer srv.Close()
	var seen int32
	srv.OnMessage(func(msg *wire.Message, _ []byte, _ *net.UDPAddr) *wire.Message {
		atomic.AddInt32(&seen, 1)
		return wire.EmptyRST(msg.MID)
	})
	srv.Start()

	c, err := transport.DialClient(transport.ClientOptions{
		Retransmit: transport.Retransmit{ACKTimeout: 50 * time.Millisecond, ACKRandomFactor: 1.0, MaxRetransmit: 3},
	})
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()

	req := &wire.Message{Code: wire.GET, Token: []byte{1}, Options: []wire.Option{
		{Number: wire.OpURIPath, Value: []byte("x")},
	}}
	_, err = c.Exchange(context.Background(), srv.Addr(), req)
	if !errors.Is(err, transport.ErrRST) {
		t.Fatalf("want ErrRST, got %v", err)
	}
	if atomic.LoadInt32(&seen) != 1 {
		t.Fatalf("client retried after RST: seen=%d", seen)
	}
}

// A piggybacked response with the correct token returns the response; the
// client's LocalAddr is a usable socket address.
func TestClient_PiggybackHappyPath(t *testing.T) {
	srv, err := harness.NewScriptedServer()
	if err != nil {
		t.Fatal(err)
	}
	defer srv.Close()
	srv.OnMessage(func(msg *wire.Message, _ []byte, _ *net.UDPAddr) *wire.Message {
		return &wire.Message{Type: wire.ACK, Code: wire.Content, MID: msg.MID, Token: msg.Token, Payload: []byte("ok")}
	})
	srv.Start()

	c, err := transport.DialClient(transport.ClientOptions{
		Retransmit: transport.Retransmit{ACKTimeout: 50 * time.Millisecond, ACKRandomFactor: 1.0, MaxRetransmit: 1},
	})
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()
	if c.LocalAddr().Port == 0 {
		t.Fatal("LocalAddr must report a bound port")
	}

	req := &wire.Message{Code: wire.GET, Token: []byte{9, 9}, Options: []wire.Option{
		{Number: wire.OpURIPath, Value: []byte("x")},
	}}
	resp, err := c.Exchange(context.Background(), srv.Addr(), req)
	if err != nil {
		t.Fatal(err)
	}
	if resp.Code != wire.Content || string(resp.Payload) != "ok" {
		t.Fatalf("piggyback response wrong: %s %q", resp.Code, resp.Payload)
	}
}

// Dedup Fail releases duplicates waiting on a first response (no replay).
func TestDedup_FailReleasesWaiter(t *testing.T) {
	d := transport.NewDedupCache(time.Second)
	first := d.Observe("h", ids.MID(5), nil)
	if first.Duplicate {
		t.Fatal("first")
	}
	d.Fail("h", ids.MID(5))
	dec := d.Observe("h", ids.MID(5), nil)
	if !dec.Duplicate || dec.Replay != nil || dec.SendEmptyACK {
		t.Fatalf("after Fail, duplicate must be duplicate with no replay, got %+v", dec)
	}
	if d.Len() != 1 {
		t.Fatalf("len = %d", d.Len())
	}
}
