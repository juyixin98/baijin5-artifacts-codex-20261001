package client_test

import (
	"context"
	"net"
	"testing"
	"time"

	"stunlab/internal/client"
	"stunlab/internal/stun"
)

// fakeResponder is a programmable UDP server: it replies to each request with
// the byte string a build function returns (or stays silent).
func fakeResponder(t *testing.T, build func(req *stun.Message) []byte) string {
	t.Helper()
	conn, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.ParseIP("127.0.0.1")})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { conn.Close() })
	go func() {
		buf := make([]byte, 1024)
		for {
			n, src, rerr := conn.ReadFromUDP(buf)
			if rerr != nil {
				return
			}
			m, perr := stun.UnmarshalMessage(buf[:n])
			if perr != nil {
				continue
			}
			if out := build(m); out != nil {
				_, _ = conn.WriteToUDP(out, src)
			}
		}
	}()
	return conn.LocalAddr().String()
}

func newClient(t *testing.T, key []byte) *client.Client {
	t.Helper()
	cl, err := client.Dial(context.Background(), client.Config{
		Key: key, Timeout: 600 * time.Millisecond, MaxAttempts: 1,
	})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { cl.Close() })
	return cl
}

func TestClientHandlesErrorResponse400(t *testing.T) {
	addr := fakeResponder(t, func(req *stun.Message) []byte {
		ec, _ := stun.EncodeErrorCode(stun.ErrorCode{Code: 400, Reason: "Bad Request"})
		out, _ := stun.Marshal(stun.MethodBinding, stun.ClassErrorResponse,
			req.TransactionID, []stun.Attribute{{Type: stun.AttrErrorCode, Value: ec}})
		return out
	})
	res, err := newClient(t, nil).Bind(context.Background(), addr)
	if err != nil {
		t.Fatalf("error response is a Result, not a transport error: %v", err)
	}
	if res.ErrorCode != 400 || res.Detail != "Bad Request" {
		t.Fatalf("result = %+v", res)
	}
}

func TestClientRejectsUnexpectedMessageClass(t *testing.T) {
	addr := fakeResponder(t, func(req *stun.Message) []byte {
		// An Indication where a response was expected.
		out, _ := stun.Marshal(stun.MethodBinding, stun.ClassIndication,
			req.TransactionID, nil)
		return out
	})
	_, err := newClient(t, nil).Bind(context.Background(), addr)
	if stun.ErrorOf(err) != stun.KindInput {
		t.Fatalf("kind = %v, want input_error", err)
	}
}

func TestClientRejectsSuccessWithoutXORAttr(t *testing.T) {
	addr := fakeResponder(t, func(req *stun.Message) []byte {
		out, _ := stun.Marshal(stun.MethodBinding, stun.ClassSuccessResponse,
			req.TransactionID,
			[]stun.Attribute{{Type: stun.AttrSoftware, Value: []byte("no addr")}})
		return out
	})
	_, err := newClient(t, nil).Bind(context.Background(), addr)
	if stun.ErrorOf(err) != stun.KindInput {
		t.Fatalf("kind = %v, want input_error", err)
	}
}

func TestClientRejectsMalformedResponse(t *testing.T) {
	addr := fakeResponder(t, func(req *stun.Message) []byte {
		// Echo txn but corrupt the cookie so the client parser rejects it.
		b, _ := stun.Marshal(stun.MethodBinding, stun.ClassSuccessResponse,
			req.TransactionID, nil)
		b[4] ^= 0xFF
		return b
	})
	_, err := newClient(t, nil).Bind(context.Background(), addr)
	if stun.ErrorOf(err) != stun.KindInput {
		t.Fatalf("kind = %v, want input_error", err)
	}
}

func TestClientErrorResponseWithoutErrorCodeAttr(t *testing.T) {
	addr := fakeResponder(t, func(req *stun.Message) []byte {
		out, _ := stun.Marshal(stun.MethodBinding, stun.ClassErrorResponse,
			req.TransactionID, nil)
		return out
	})
	res, err := newClient(t, nil).Bind(context.Background(), addr)
	if err != nil {
		t.Fatal(err)
	}
	if res.ErrorCode != 0 || res.Detail == "" {
		t.Fatalf("expected placeholder detail, got %+v", res)
	}
}

func TestClientDialRejectsBadNetwork(t *testing.T) {
	_, err := client.Dial(context.Background(), client.Config{Network: "udp99"})
	if err == nil {
		t.Fatal("expected dial failure for bogus network")
	}
}

func TestClientBindRejectsUnresolvableServer(t *testing.T) {
	cl := newClient(t, nil)
	_, err := cl.Bind(context.Background(), "this-host-does-not-exist.invalid:3478")
	if stun.ErrorOf(err) != stun.KindInput {
		t.Fatalf("kind = %v, want input_error", err)
	}
}
