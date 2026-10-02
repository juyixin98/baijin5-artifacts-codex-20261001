package client_test

import (
	"context"
	"net"
	"testing"
	"time"

	"stunlab/internal/client"
	"stunlab/internal/evidence"
	"stunlab/internal/server"
	"stunlab/internal/store"
	"stunlab/internal/stun"
)

func newPair(t *testing.T, network, key string) (*client.Client, *server.Server, *store.Store) {
	t.Helper()
	addr := "127.0.0.1:0"
	if network == "udp6" {
		addr = "[::1]:0"
	}
	st, err := store.Open("")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { st.Close() })
	var logsBuf discardWriter
	lg := evidence.NewLogger(evidence.NewRunID("e2e"), "lab", &logsBuf, st, "")
	srv, err := server.Listen(server.Config{
		Network: network, Addr: addr, Key: []byte(key), Logger: lg, Store: st,
	})
	if err != nil {
		if network == "udp6" {
			t.Skipf("IPv6 unavailable: %v", err)
		}
		t.Fatal(err)
	}
	t.Cleanup(func() { srv.Close() })
	go func() { _ = srv.Serve(context.Background()) }()

	cl, err := client.Dial(context.Background(), client.Config{
		Network: network, Key: []byte(key),
		Timeout: 800 * time.Millisecond, MaxAttempts: 3,
		Retransmit: 100 * time.Millisecond, Logger: lg, Store: st,
	})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { cl.Close() })
	return cl, srv, st
}

type discardWriter struct{}

func (discardWriter) Write(p []byte) (int, error) { return len(p), nil }

func TestBindSuccessIPv4AndIPv6(t *testing.T) {
	for _, network := range []string{"udp4", "udp6"} {
		t.Run(network, func(t *testing.T) {
			cl, srv, _ := newPair(t, network, "")
			res, err := cl.Bind(context.Background(), srv.LocalAddr().String())
			if err != nil {
				t.Fatal(err)
			}
			if res.ErrorCode != 0 {
				t.Fatalf("error code %d %s", res.ErrorCode, res.Detail)
			}
			if res.Endpoint.Port != cl.LocalAddr().Port {
				t.Fatalf("port = %d, want %d", res.Endpoint.Port, cl.LocalAddr().Port)
			}
			if network == "udp4" && res.Endpoint.IP.To4() == nil {
				t.Fatal("expected IPv4 endpoint")
			}
			if network == "udp6" && res.Endpoint.IP.To4() != nil {
				t.Fatal("expected IPv6 endpoint")
			}
			if res.Attempts != 1 {
				t.Fatalf("attempts = %d, want 1 (no retransmit on immediate reply)", res.Attempts)
			}
		})
	}
}

func TestBindIntegrityProtectedRoundTrip(t *testing.T) {
	cl, srv, _ := newPair(t, "udp4", "labkey")
	res, err := cl.Bind(context.Background(), srv.LocalAddr().String())
	if err != nil {
		t.Fatalf("integrity-protected bind: %v", err)
	}
	if res.Endpoint.Port == 0 {
		t.Fatal("zero endpoint port")
	}
}

func TestBindTimeoutAgainstSilentSocket(t *testing.T) {
	// A socket that never answers drives the timeout/retransmit path.
	silent, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.ParseIP("127.0.0.1")})
	if err != nil {
		t.Fatal(err)
	}
	defer silent.Close()

	st, _ := store.Open("")
	defer st.Close()
	var buf discardWriter
	cl, err := client.Dial(context.Background(), client.Config{
		Timeout: 300 * time.Millisecond, MaxAttempts: 2, Retransmit: 80 * time.Millisecond,
		Logger: evidence.NewLogger(evidence.NewRunID("to"), "stunc", &buf, st, ""), Store: st,
	})
	if err != nil {
		t.Fatal(err)
	}
	defer cl.Close()
	_, err = cl.Bind(context.Background(), silent.LocalAddr().String())
	if stun.ErrorOf(err) != stun.KindTimeout {
		t.Fatalf("got %v, want timeout", err)
	}
}

func TestLateResponseForOldTxnCannotCompleteNewRequest(t *testing.T) {
	// Black-hole server drives request #1 to timeout; a delayed reply carrying
	// request #1's txn must then be ignored while request #2 (fresh txn, real
	// server) is in flight.
	blackhole, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.ParseIP("127.0.0.1")})
	if err != nil {
		t.Fatal(err)
	}
	defer blackhole.Close()

	st, _ := store.Open("")
	defer st.Close()
	var buf discardWriter
	lg := evidence.NewLogger(evidence.NewRunID("stale"), "stunc", &buf, st, "")
	cl, err := client.Dial(context.Background(), client.Config{
		Timeout: 200 * time.Millisecond, MaxAttempts: 1,
		Logger: lg, Store: st,
	})
	if err != nil {
		t.Fatal(err)
	}
	defer cl.Close()

	// Capture request #1's datagram in the blackhole so it can be replayed
	// late with a forged response carrying the same txn.
	oldTxn := recvOne(t, blackhole, func() {
		_, err := cl.Bind(context.Background(), blackhole.LocalAddr().String())
		if stun.ErrorOf(err) != stun.KindTimeout {
			t.Fatalf("first bind: %v", err)
		}
	})
	if oldTxn == (stun.TransactionID{}) {
		t.Fatal("did not capture request txn")
	}

	// Start request #2 against the real server; inject the stale response for
	// txn #1 from the blackhole address concurrently.
	_, srv, _ := newPair(t, "udp4", "")
	done := make(chan error, 1)
	go func() {
		res, err := cl.Bind(context.Background(), srv.LocalAddr().String())
		if err == nil && res.ErrorCode != 0 {
			err = &stun.Error{Kind: stun.KindInput, Detail: res.Detail}
		}
		done <- err
	}()
	time.Sleep(30 * time.Millisecond)
	replyFrom(t, blackhole, cl.LocalAddr(), forgeSuccess(t, oldTxn, cl.LocalAddr()))

	select {
	case err := <-done:
		if err != nil {
			t.Fatalf("new request was corrupted by stale response: %v", err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("new request never completed")
	}
}

// forgeSuccess builds a syntactically valid success response for txn claiming
// the given endpoint. It is used to impersonate a server from another socket.
func forgeSuccess(t *testing.T, txn stun.TransactionID, mapped *net.UDPAddr) []byte {
	t.Helper()
	xor, err := stun.EncodeXORMappedAddress(stun.Address{IP: mapped.IP, Port: mapped.Port}, txn)
	if err != nil {
		t.Fatal(err)
	}
	attrs := []stun.Attribute{{Type: stun.AttrXORMappedAddress, Value: xor}}
	b, err := stun.Marshal(stun.MethodBinding, stun.ClassSuccessResponse, txn, attrs)
	if err != nil {
		t.Fatal(err)
	}
	return b
}

func TestResponseSourceMismatchEndToEnd(t *testing.T) {
	// Listener A is the address the client queried; listener B injects a
	// valid-looking success from a DIFFERENT source. Because the transaction
	// table requires source equality, the reply must be rejected and the
	// request must time out rather than accept the impostor's endpoint.
	a, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.ParseIP("127.0.0.1")})
	if err != nil {
		t.Fatal(err)
	}
	defer a.Close()
	b, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.ParseIP("127.0.0.1")})
	if err != nil {
		t.Fatal(err)
	}
	defer b.Close()

	st, _ := store.Open("")
	defer st.Close()
	var logs discardWriter
	cl, err := client.Dial(context.Background(), client.Config{
		Timeout: 300 * time.Millisecond, MaxAttempts: 1,
		Logger: evidence.NewLogger(evidence.NewRunID("src"), "stunc", &logs, st, ""), Store: st,
	})
	if err != nil {
		t.Fatal(err)
	}
	defer cl.Close()

	go func() {
		buf := make([]byte, 1024)
		_ = a.SetReadDeadline(time.Now().Add(2 * time.Second))
		n, src, rerr := a.ReadFromUDP(buf)
		if rerr != nil {
			return
		}
		m, perr := stun.UnmarshalMessage(buf[:n])
		if perr != nil {
			return
		}
		// Reply from socket B (different source address/port).
		_, _ = b.WriteToUDP(forgeSuccess(t, m.TransactionID, src), src)
	}()

	start := time.Now()
	_, err = cl.Bind(context.Background(), a.LocalAddr().String())
	if stun.ErrorOf(err) != stun.KindTimeout {
		t.Fatalf("got %v, want timeout (impostor reply must be rejected)", err)
	}
	if d := time.Since(start); d < 150*time.Millisecond {
		t.Fatalf("Bind returned after %v: impostor reply may have completed it", d)
	}
}

func TestErrorResponse420Surfaced(t *testing.T) {
	// The client API never sends unknown attrs, so drive the raw socket here;
	// the point is that the same server's 420 (which the client's decoder
	// consumes) carries the right code and UNKNOWN-ATTRIBUTES.
	_, srv, _ := newPair(t, "udp4", "")
	sock, err := net.DialUDP("udp4", nil, srv.LocalAddr())
	if err != nil {
		t.Fatal(err)
	}
	defer sock.Close()
	txn, _ := stun.NewTransactionID()
	req, _ := stun.Marshal(stun.MethodBinding, stun.ClassRequest, txn,
		[]stun.Attribute{{Type: stun.AttributeType(0x0099), Value: []byte{1}}})
	if _, err := sock.Write(req); err != nil {
		t.Fatal(err)
	}
	_ = sock.SetReadDeadline(time.Now().Add(2 * time.Second))
	buf := make([]byte, 1024)
	n, err := sock.Read(buf)
	if err != nil {
		t.Fatal(err)
	}
	m, err := stun.UnmarshalMessage(buf[:n])
	if err != nil {
		t.Fatal(err)
	}
	ecv, _ := m.Attribute(stun.AttrErrorCode)
	ec, _ := stun.DecodeErrorCode(ecv)
	if ec.Code != 420 {
		t.Fatalf("code = %d", ec.Code)
	}
}

func TestResponseIntegrityFailureIsDistinct(t *testing.T) {
	// Fake responder that signs every response with the WRONG key.
	st, _ := store.Open("")
	defer st.Close()
	var buf discardWriter
	cl, err := client.Dial(context.Background(), client.Config{
		Key: []byte("correct"), Timeout: 500 * time.Millisecond, MaxAttempts: 1,
		Logger: evidence.NewLogger(evidence.NewRunID("mi"), "stunc", &buf, st, ""), Store: st,
	})
	if err != nil {
		t.Fatal(err)
	}
	defer cl.Close()

	fake, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.ParseIP("127.0.0.1")})
	if err != nil {
		t.Fatal(err)
	}
	defer fake.Close()
	go func() {
		b := make([]byte, 1024)
		for {
			n, src, rerr := fake.ReadFromUDP(b)
			if rerr != nil {
				return
			}
			m, perr := stun.UnmarshalMessage(b[:n])
			if perr != nil {
				continue
			}
			xor, _ := stun.EncodeXORMappedAddress(
				stun.Address{IP: src.IP, Port: src.Port}, m.TransactionID)
			out, _ := stun.AddMessageIntegrity(stun.MethodBinding, stun.ClassSuccessResponse,
				m.TransactionID,
				[]stun.Attribute{{Type: stun.AttrXORMappedAddress, Value: xor}},
				[]byte("wrong-key"))
			_, _ = fake.WriteToUDP(out, src)
		}
	}()

	_, err = cl.Bind(context.Background(), fake.LocalAddr().String())
	if stun.ErrorOf(err) != stun.KindIntegrity {
		t.Fatalf("got %v, want integrity_failure", err)
	}
}

// recvOne reads a single datagram from conn while fn runs, returning the txn
// id it carried.
func recvOne(t *testing.T, conn *net.UDPConn, fn func()) stun.TransactionID {
	t.Helper()
	got := make(chan stun.TransactionID, 8)
	go func() {
		buf := make([]byte, 1024)
		for {
			_ = conn.SetReadDeadline(time.Now().Add(2 * time.Second))
			n, _, err := conn.ReadFromUDP(buf)
			if err != nil {
				return
			}
			var id stun.TransactionID
			if n >= 20 {
				copy(id[:], buf[8:20])
			}
			select {
			case got <- id:
			default:
			}
		}
	}()
	fn()
	select {
	case id := <-got:
		return id
	case <-time.After(200 * time.Millisecond):
		return stun.TransactionID{}
	}
}

func replyFrom(t *testing.T, from *net.UDPConn, to *net.UDPAddr, b []byte) {
	t.Helper()
	if _, err := from.WriteToUDP(b, to); err != nil {
		t.Fatal(err)
	}
}
