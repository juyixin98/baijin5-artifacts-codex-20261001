package server_test

import (
	"encoding/binary"
	"errors"
	"net"
	"testing"

	"localstun/internal/server"
	"localstun/internal/stun"
	"localstun/internal/stunerror"
)

func mustTx() stun.TransactionID { return stun.MustTransactionID() }

func newTestServer(t *testing.T, key []byte) *server.Server {
	t.Helper()
	s, err := server.New(server.Config{
		ListenAddr:  "127.0.0.1:0",
		SharedKey:   key,
		Fingerprint: len(key) > 0,
	})
	if err != nil {
		t.Fatalf("server.New: %v", err)
	}
	return s
}

func TestHandlePacket_SuccessXORMapped(t *testing.T) {
	key := []byte("k")
	s := newTestServer(t, key)
	src := &net.UDPAddr{IP: net.ParseIP("198.51.100.9"), Port: 54321}

	req := stun.NewMessage(stun.BindingRequest, mustTx())
	raw, err := stun.Marshal(req, key, true)
	if err != nil {
		t.Fatal(err)
	}
	out, kind, event, detail := s.HandlePacket(raw, src)
	if out == nil {
		t.Fatalf("expected response, event=%s kind=%s detail=%s", event, kind, detail)
	}
	if event != "binding_success" || kind != stunerror.KindUnknown {
		t.Fatalf("event=%s kind=%s", event, kind)
	}
	m, err := stun.Decode(out, key)
	if err != nil {
		t.Fatalf("response decode: %v", err)
	}
	if !m.IntegrityOK {
		t.Fatal("response integrity must verify")
	}
	ip, port, ok, err := m.XORMappedAddress()
	if err != nil || !ok {
		t.Fatalf("xor addr: %v %v", ok, err)
	}
	if ip.String() != "198.51.100.9" || port != 54321 {
		t.Fatalf("mapped = %s:%d, want source 198.51.100.9:54321", ip, port)
	}
	if m.TxID != req.TxID {
		t.Fatal("response transaction id must echo request")
	}
}

func TestHandlePacket_UnknownRequired420(t *testing.T) {
	s := newTestServer(t, nil)
	tx := mustTx()
	body := frameAttr(0x0BAD, []byte{1, 2})
	pkt := frameMsg(stun.BindingRequest, tx, body)

	out, kind, event, _ := s.HandlePacket(pkt, &net.UDPAddr{IP: net.ParseIP("127.0.0.1"), Port: 1})
	if out == nil {
		t.Fatalf("420 response expected, event=%s kind=%s", event, kind)
	}
	if event != "unknown_required_attribute" || kind != stunerror.KindIntegrity {
		t.Fatalf("event=%s kind=%s", event, kind)
	}
	m, err := stun.Decode(out, nil)
	if err != nil {
		t.Fatal(err)
	}
	code, _, ok := m.ErrorCode()
	if !ok || code != 420 {
		t.Fatalf("error code = %d ok=%v, want 420", code, ok)
	}
	ua, ok := m.Get(stun.AttrUnknownAttrs)
	if !ok || len(ua.Value) != 2 || binary.BigEndian.Uint16(ua.Value) != 0x0BAD {
		t.Fatalf("UNKNOWN-ATTRIBUTES = %x ok=%v", ua.Value, ok)
	}
}

func TestHandlePacket_UnsignedRequestToSignedServer(t *testing.T) {
	key := []byte("secret")
	s := newTestServer(t, key)
	pkt, _ := stun.Marshal(stun.NewMessage(stun.BindingRequest, mustTx()), nil, false)
	out, kind, event, _ := s.HandlePacket(pkt, &net.UDPAddr{IP: net.ParseIP("127.0.0.1"), Port: 1})
	if out == nil {
		t.Fatalf("401 response expected, event=%s", event)
	}
	if kind != stunerror.KindIntegrity {
		t.Fatalf("kind=%s want integrity", kind)
	}
	m, err := stun.Decode(out, key)
	if err != nil {
		t.Fatalf("401 must itself be signed: %v", err)
	}
	code, _, _ := m.ErrorCode()
	if code != 401 {
		t.Fatalf("code=%d want 401", code)
	}
}

func TestHandlePacket_JunkDropped(t *testing.T) {
	s := newTestServer(t, nil)
	cases := [][]byte{
		{0x00, 0x01}, // short
		{0xC0, 0x01, 0, 0, 0x21, 0x12, 0xa4, 0x42}, // leading bits
		make([]byte, 20), // length-zero, bad cookie -> drop
	}
	for i, pkt := range cases {
		out, kind, event, _ := s.HandlePacket(pkt, &net.UDPAddr{IP: net.ParseIP("127.0.0.1"), Port: 1})
		if out != nil {
			t.Fatalf("case %d: junk must be dropped, got %d bytes", i, len(out))
		}
		if kind != stunerror.KindInput {
			t.Fatalf("case %d kind=%s event=%s want input", i, kind, event)
		}
	}
}

func TestHandlePacket_OversizedIsResourceExhaustion(t *testing.T) {
	s := newTestServer(t, nil)
	big := make([]byte, 2049)
	out, kind, event, _ := s.HandlePacket(big, &net.UDPAddr{IP: net.ParseIP("127.0.0.1"), Port: 1})
	if out != nil {
		t.Fatal("oversized datagram must be dropped")
	}
	if kind != stunerror.KindExhausted || event != "datagram_too_large" {
		t.Fatalf("kind=%s event=%s want exhausted/datagram_too_large", kind, event)
	}
}

func TestHandlePacket_NotBindingMethod(t *testing.T) {
	s := newTestServer(t, nil)
	tx := mustTx()
	// A well-formed Binding *Response* sent to the request port must decode
	// but be rejected as a non-request (decode accepts all Binding classes).
	pkt, _ := stun.Marshal(stun.NewMessage(stun.BindingResponse, tx), nil, false)
	out, kind, event, _ := s.HandlePacket(pkt, &net.UDPAddr{IP: net.ParseIP("127.0.0.1"), Port: 1})
	if out != nil || kind != stunerror.KindInput || event != "not_binding_request" {
		t.Fatalf("out=%d kind=%s event=%s", len(out), kind, event)
	}
}

// frameAttr frames one attribute with correct four-byte padding.
func frameAttr(at stun.AttrType, value []byte) []byte {
	pad := (4 - len(value)%4) % 4
	out := make([]byte, 4+len(value)+pad)
	binary.BigEndian.PutUint16(out[0:2], uint16(at))
	binary.BigEndian.PutUint16(out[2:4], uint16(len(value)))
	copy(out[4:], value)
	return out
}

func frameMsg(t stun.MessageType, tx stun.TransactionID, body []byte) []byte {
	out := make([]byte, 20+len(body))
	binary.BigEndian.PutUint16(out[0:2], uint16(t))
	binary.BigEndian.PutUint16(out[2:4], uint16(len(body)))
	binary.BigEndian.PutUint32(out[4:8], stun.MagicCookie)
	copy(out[8:20], tx[:])
	copy(out[20:], body)
	return out
}

var _ = errors.Is
