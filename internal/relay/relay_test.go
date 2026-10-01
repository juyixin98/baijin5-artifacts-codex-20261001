package relay

import (
	"bytes"
	"io"
	"net"
	"testing"
	"time"

	"sockswhitelist/internal/proto"
)

// harness builds the real two-leg topology Relay operates in:
//
//	client --(front leg)--> Relay --(target leg)--> target server
//
// The two legs are separate TCP connections, so a CloseWrite (FIN) on one
// leg is forwarded by Relay to the other leg instead of looping back.
type harness struct {
	targetLn net.Listener
	frontLn  net.Listener
	relayCh  chan relayResult
}

type relayResult struct {
	stats Stats
	kind  proto.Kind
}

func newHarness(t *testing.T, b Budgets) *harness {
	t.Helper()
	targetLn, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	frontLn, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	h := &harness{
		targetLn: targetLn,
		frontLn:  frontLn,
		relayCh:  make(chan relayResult, 1),
	}
	go func() {
		front, err := frontLn.Accept()
		if err != nil {
			return
		}
		upstream, err := net.Dial("tcp", targetLn.Addr().String())
		if err != nil {
			_ = front.Close()
			return
		}
		st, kind := Relay(front, upstream, b)
		h.relayCh <- relayResult{st, kind}
	}()
	t.Cleanup(func() {
		_ = targetLn.Close()
		_ = frontLn.Close()
	})
	return h
}

// acceptTarget accepts the connection Relay opens to the target server.
func (h *harness) acceptTarget(t *testing.T) net.Conn {
	t.Helper()
	c, err := h.targetLn.Accept()
	if err != nil {
		t.Fatal(err)
	}
	return c
}

// dialClient connects a client through the front leg.
func (h *harness) dialClient(t *testing.T) net.Conn {
	t.Helper()
	c, err := net.Dial("tcp", h.frontLn.Addr().String())
	if err != nil {
		t.Fatal(err)
	}
	return c
}

func halfClose(t *testing.T, c net.Conn) {
	t.Helper()
	tc, ok := c.(*net.TCPConn)
	if !ok {
		t.Fatalf("%T is not *net.TCPConn", c)
	}
	if err := tc.CloseWrite(); err != nil {
		t.Fatalf("close write: %v", err)
	}
}

func TestRelayForwardsByteForByte(t *testing.T) {
	upPayload := bytes.Repeat([]byte{0xAB}, 200_000) // > several 32KiB buffers
	downPayload := bytes.Repeat([]byte("down-xyz"), 70_000)

	h := newHarness(t, Budgets{IdleTimeout: 5 * time.Second})
	client := h.dialClient(t)
	target := h.acceptTarget(t)

	// Target server: read the whole upload, verify, answer, half-close.
	targetDone := make(chan error, 1)
	go func() {
		got, err := io.ReadAll(target)
		if err != nil {
			targetDone <- err
			return
		}
		if !bytes.Equal(got, upPayload) {
			targetDone <- &mismatchErr{len(got), len(upPayload)}
			return
		}
		if _, err := target.Write(downPayload); err != nil {
			targetDone <- err
			return
		}
		halfClose(t, target)
		targetDone <- nil
	}()

	// Client: upload, half-close write side, read the full answer.
	if _, err := client.Write(upPayload); err != nil {
		t.Fatal(err)
	}
	halfClose(t, client)
	gotDown, err := io.ReadAll(client)
	if err != nil {
		t.Fatalf("client read: %v", err)
	}
	if !bytes.Equal(gotDown, downPayload) {
		t.Fatalf("down payload mismatch: got %d want %d", len(gotDown), len(downPayload))
	}
	if err := <-targetDone; err != nil {
		t.Fatalf("target verification: %v", err)
	}
	res := <-h.relayCh
	if res.kind != proto.KindRelayEOF {
		t.Fatalf("relay kind=%s, want completed", res.kind)
	}
	if res.stats.Up != int64(len(upPayload)) || res.stats.Down != int64(len(downPayload)) {
		t.Fatalf("stats up=%d down=%d", res.stats.Up, res.stats.Down)
	}
}

type mismatchErr struct{ got, want int }

func (e *mismatchErr) Error() string { return "payload length mismatch" }

func TestRelayByteBudgetUp(t *testing.T) {
	h := newHarness(t, Budgets{MaxBytesUp: 1000, IdleTimeout: 3 * time.Second})
	client := h.dialClient(t)
	target := h.acceptTarget(t)

	_, _ = client.Write(bytes.Repeat([]byte{1}, 5000))
	got, err := io.ReadAll(target)
	if err != nil {
		// A hard close may surface; the byte count is what matters.
		_ = err
	}
	res := <-h.relayCh
	if len(got) != 1000 {
		t.Fatalf("target got %d bytes, want exactly budget 1000", len(got))
	}
	if res.stats.Up != 1000 {
		t.Fatalf("stats up=%d want 1000", res.stats.Up)
	}
	if res.kind != proto.KindByteBudgetExceeded {
		t.Fatalf("kind=%s want byte_budget_exceeded", res.kind)
	}
}

func TestRelayByteBudgetDown(t *testing.T) {
	h := newHarness(t, Budgets{MaxBytesDown: 500, IdleTimeout: 3 * time.Second})
	client := h.dialClient(t)
	target := h.acceptTarget(t)

	_, _ = target.Write(bytes.Repeat([]byte{2}, 4000))
	got, _ := io.ReadAll(client)
	res := <-h.relayCh
	if len(got) != 500 {
		t.Fatalf("client got %d, want 500", len(got))
	}
	if res.stats.Down != 500 {
		t.Fatalf("stats down=%d", res.stats.Down)
	}
	if res.kind != proto.KindByteBudgetExceeded {
		t.Fatalf("kind=%s", res.kind)
	}
}

func TestRelayIdleTimeout(t *testing.T) {
	h := newHarness(t, Budgets{IdleTimeout: 150 * time.Millisecond})
	// Keep both socket ends referenced for the whole test so the GC
	// finalizer cannot close an fd and produce a spurious EOF.
	client := h.dialClient(t)
	target := h.acceptTarget(t)
	t.Cleanup(func() {
		_ = client.Close()
		_ = target.Close()
	})
	// Neither side sends anything: the silent peer must trip the idle bound.
	select {
	case res := <-h.relayCh:
		if res.kind != proto.KindIdleTimeout {
			t.Fatalf("kind=%s want idle_timeout", res.kind)
		}
	case <-time.After(3 * time.Second):
		t.Fatal("relay did not time out on silent peers")
	}
}

func TestRelayHalfCloseKeepsOtherDirection(t *testing.T) {
	h := newHarness(t, Budgets{IdleTimeout: 5 * time.Second})
	client := h.dialClient(t)
	target := h.acceptTarget(t)

	// Client FINs its upload immediately, keeping the read side open.
	halfClose(t, client)

	buf := make([]byte, 16)
	if n, err := target.Read(buf); err != io.EOF || n != 0 {
		t.Fatalf("target expected EOF, got n=%d err=%v", n, err)
	}
	// Downstream must still flow after the upstream-side client FIN.
	time.Sleep(50 * time.Millisecond)
	msg := []byte("late response after client FIN")
	if _, err := target.Write(msg); err != nil {
		t.Fatalf("write after client half-close: %v", err)
	}
	halfClose(t, target)
	got, err := io.ReadAll(client)
	if err != nil {
		t.Fatalf("client read late response: %v", err)
	}
	if string(got) != string(msg) {
		t.Fatalf("got %q want %q", got, msg)
	}
}
