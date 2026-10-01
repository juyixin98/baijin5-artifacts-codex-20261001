package relay_test

import (
	"bytes"
	"context"
	"io"
	"net"
	"testing"
	"time"

	"socks5d.local/socks5d/internal/relay"
)

// peerPair returns two sides of one real TCP connection: the side handed to
// the relay and the side the test drives. Using TCP (not net.Pipe) gives real
// CloseWrite semantics and kernel backpressure.
func peerPair(t *testing.T) (relaySide, testSide net.Conn) {
	t.Helper()
	ln, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen: %v", err)
	}
	t.Cleanup(func() { _ = ln.Close() })

	type res struct {
		c   net.Conn
		err error
	}
	ch := make(chan res, 1)
	go func() {
		c, err := ln.Accept()
		ch <- res{c, err}
	}()
	dialed, err := net.Dial("tcp4", ln.Addr().String())
	if err != nil {
		t.Fatalf("dial: %v", err)
	}
	accepted := <-ch
	if accepted.err != nil {
		t.Fatalf("accept: %v", accepted.err)
	}
	t.Cleanup(func() {
		_ = dialed.Close()
		_ = accepted.c.Close()
	})
	// dialed is the relay side; accepted is the test-driven peer side.
	return dialed, accepted.c
}

// pipe builds the client/upstream connections the relay needs, plus the two
// peer sides the test reads/writes.
func pipe(t *testing.T) (client, upstream net.Conn, clientPeer, upstreamPeer net.Conn) {
	client, clientPeer = peerPair(t)
	upstream, upstreamPeer = peerPair(t)
	return
}

func readAll(t *testing.T, r io.Reader, want int) []byte {
	t.Helper()
	out := make([]byte, 0, want)
	buf := make([]byte, 4096)
	for len(out) < want {
		n, err := r.Read(buf)
		out = append(out, buf[:n]...)
		if err == io.EOF {
			break
		}
		if err != nil {
			t.Fatalf("read: %v", err)
		}
	}
	return out
}

// TestHalfClose_Directional verifies the central half-close contract:
// a FIN from one side closes only that direction (after draining bytes) and
// the opposite direction keeps carrying data until it also half-closes.
func TestHalfClose_Directional(t *testing.T) {
	client, upstream, clientPeer, upstreamPeer := pipe(t)
	_ = upstream
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	upPayload := []byte("UPSTREAM-")
	clientPayload := bytes.Repeat([]byte("C"), 100)

	done := make(chan relay.Stats, 1)
	errCh := make(chan error, 1)
	go func() {
		st, err := relay.Relay(ctx, client, upstream, relay.Options{
			ByteBudget: 1 << 20, BufferSize: 4096,
		})
		done <- st
		errCh <- err
	}()

	// Client -> upstream: send then half-close the client write side.
	if _, err := clientPeer.Write(clientPayload); err != nil {
		t.Fatalf("client write: %v", err)
	}
	if err := clientPeer.(*net.TCPConn).CloseWrite(); err != nil {
		t.Fatalf("client half-close: %v", err)
	}

	// Upstream must receive the exact payload followed by EOF.
	gotUp := readAll(t, upstreamPeer, len(clientPayload))
	if !bytes.Equal(gotUp, clientPayload) {
		t.Fatalf("upstream got %d bytes want %d", len(gotUp), len(clientPayload))
	}
	if _, err := upstreamPeer.Read(make([]byte, 1)); err != io.EOF {
		t.Fatalf("upstream direction should EOF after half-close, got %v", err)
	}

	// Upstream -> client still works after the client half-closed.
	if _, err := upstreamPeer.Write(upPayload); err != nil {
		t.Fatalf("upstream write: %v", err)
	}
	gotDown := readAll(t, clientPeer, len(upPayload))
	if !bytes.Equal(gotDown, upPayload) {
		t.Fatalf("client got %q want %q", gotDown, upPayload)
	}
	// Finish the second direction.
	if err := upstreamPeer.(*net.TCPConn).CloseWrite(); err != nil {
		t.Fatalf("upstream half-close: %v", err)
	}
	if _, err := clientPeer.Read(make([]byte, 1)); err != io.EOF {
		t.Fatalf("client direction should EOF, got %v", err)
	}

	select {
	case st := <-done:
		if err := <-errCh; err != nil {
			t.Fatalf("relay: %v", err)
		}
		if st.EndReason != relay.ReasonClosedBoth {
			t.Fatalf("end reason = %q want %q", st.EndReason, relay.ReasonClosedBoth)
		}
		if st.ClientToUpstream != int64(len(clientPayload)) {
			t.Fatalf("c2s = %d want %d", st.ClientToUpstream, len(clientPayload))
		}
		if st.UpstreamToClient != int64(len(upPayload)) {
			t.Fatalf("s2c = %d want %d", st.UpstreamToClient, len(upPayload))
		}
	case <-time.After(5 * time.Second):
		t.Fatal("relay did not finish after both half-closes")
	}
}

// TestByteBudget_Bounded asserts forwarding stops at the bound, with exact
// accounting and the budget failure category.
func TestByteBudget_Bounded(t *testing.T) {
	client, upstream, clientPeer, upstreamPeer := pipe(t)
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	const budget = 4096
	const total = budget + 5000

	errCh := make(chan error, 1)
	go func() {
		_, err := relay.Relay(ctx, client, upstream, relay.Options{
			ByteBudget: budget, BufferSize: 2048,
		})
		errCh <- err
	}()

	go func() {
		_, _ = clientPeer.Write(bytes.Repeat([]byte("X"), total))
	}()

	// Drain upstream until it is force-closed by the budget violation.
	received := 0
	buf := make([]byte, 4096)
	for {
		n, err := upstreamPeer.Read(buf)
		received += n
		if err != nil {
			break
		}
	}
	// With pre-write capping, exactly the budget is forwarded, no more.
	if int64(received) != budget {
		t.Fatalf("forwarded %d, want exactly the budget %d", received, budget)
	}

	select {
	case err := <-errCh:
		if err == nil {
			t.Fatal("expected budget error")
		}
		if !errorIs(err, relay.ErrByteBudgetExceeded) {
			t.Fatalf("err = %v, want byte budget exceeded", err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("relay did not stop on budget")
	}
}

// TestSlowUpstream_PendingBufferDrained: the upstream reads slowly while the
// client sends a large body and half-closes. Every byte must still reach the
// upstream before its read EOF (the half-close is sent only after the relay's
// pending write buffer has drained).
func TestSlowUpstream_PendingBufferDrained(t *testing.T) {
	client, upstream, clientPeer, upstreamPeer := pipe(t)
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	const payloadLen = 400 * 1024
	go func() {
		_, _ = relay.Relay(ctx, client, upstream, relay.Options{
			ByteBudget: 4 * 1024 * 1024, BufferSize: 32 * 1024,
		})
	}()

	// Slow upstream: read a few KB at a time with pacing.
	drainDone := make(chan []byte, 1)
	go func() {
		var got bytes.Buffer
		buf := make([]byte, 8192)
		for {
			_ = upstreamPeer.SetReadDeadline(time.Now().Add(8 * time.Second))
			n, err := upstreamPeer.Read(buf)
			got.Write(buf[:n])
			if err == io.EOF {
				drainDone <- got.Bytes()
				return
			}
			if err != nil {
				t.Errorf("slow upstream read: %v", err)
				drainDone <- got.Bytes()
				return
			}
			time.Sleep(2 * time.Millisecond) // apply backpressure
		}
	}()

	body := make([]byte, payloadLen)
	for i := range body {
		body[i] = byte('A' + i%26)
	}
	if _, err := clientPeer.Write(body); err != nil {
		t.Fatalf("write body: %v", err)
	}
	if err := clientPeer.(*net.TCPConn).CloseWrite(); err != nil {
		t.Fatalf("half-close: %v", err)
	}

	select {
	case got := <-drainDone:
		if len(got) != payloadLen {
			t.Fatalf("upstream drained %d bytes want %d", len(got), payloadLen)
		}
		if !bytes.Equal(got, body) {
			t.Fatal("drained payload differs byte-for-byte")
		}
	case <-time.After(12 * time.Second):
		t.Fatal("upstream never received EOF / full body")
	}
}

func errorIs(err, target error) bool {
	for err != nil {
		if err == target {
			return true
		}
		type wrapper interface{ Unwrap() error }
		w, ok := err.(wrapper)
		if !ok {
			return false
		}
		err = w.Unwrap()
	}
	return false
}
