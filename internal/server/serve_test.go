package server_test

import (
	"net"
	"sync"
	"testing"
	"time"

	"localstun/internal/audit"
	"localstun/internal/server"
	"localstun/internal/stun"
)

// memSink captures audit records in memory.
type memSink struct {
	mu      sync.Mutex
	records []audit.Record
}

func (s *memSink) Write(r audit.Record) error {
	s.mu.Lock()
	s.records = append(s.records, r)
	s.mu.Unlock()
	return nil
}
func (s *memSink) Close() error { return nil }
func (s *memSink) snapshot() []audit.Record {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]audit.Record, len(s.records))
	copy(out, s.records)
	return out
}

func startLive(t *testing.T, addr string, key []byte, fp bool, sink audit.Sink) *server.Server {
	t.Helper()
	s, err := server.New(server.Config{
		ListenAddr: addr, SharedKey: key, Fingerprint: fp, Sink: sink,
	})
	if err != nil {
		t.Fatalf("server.New: %v", err)
	}
	errc := s.Serve()
	t.Cleanup(func() {
		_ = s.Close()
		<-errc
	})
	return s
}

func TestServe_LiveUDP_SuccessAndAudit(t *testing.T) {
	key := []byte("live")
	sink := &memSink{}
	s := startLive(t, "127.0.0.1:0", key, true, sink)
	if s.RunID() == "" {
		t.Fatal("run id must be populated")
	}

	req, _ := stun.Marshal(stun.NewMessage(stun.BindingRequest, stun.MustTransactionID()), key, true)
	conn, err := net.DialUDP("udp", nil, s.Addr())
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if _, err := conn.Write(req); err != nil {
		t.Fatal(err)
	}
	_ = conn.SetReadDeadline(time.Now().Add(2 * time.Second))
	buf := make([]byte, 2048)
	n, err := conn.Read(buf)
	if err != nil {
		t.Fatalf("read response: %v", err)
	}
	m, err := stun.Decode(buf[:n], key)
	if err != nil || !m.IntegrityOK {
		t.Fatalf("live response: %v", err)
	}

	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		if len(sink.snapshot()) > 0 {
			break
		}
		time.Sleep(5 * time.Millisecond)
	}
	recs := sink.snapshot()
	if len(recs) == 0 {
		t.Fatal("expected an audit record")
	}
	r := recs[0]
	if r.RunID != s.RunID() || r.Seq != 1 || r.Component != "server" {
		t.Fatalf("record metadata = %+v", r)
	}
	if r.TxID == "" || len(r.WireHex) < 40 {
		t.Fatalf("record must carry txid and wire bytes: %+v", r)
	}
}

func TestNew_ConfigValidation(t *testing.T) {
	if _, err := server.New(server.Config{ListenAddr: ""}); err == nil {
		t.Fatal("empty listen addr must fail")
	}
	if _, err := server.New(server.Config{
		ListenAddr: "127.0.0.1:0", Fingerprint: true,
	}); err == nil {
		t.Fatal("fingerprint without key must be rejected")
	}
}

func TestServe_LiveUDP_JunkDropped(t *testing.T) {
	s := startLive(t, "127.0.0.1:0", nil, false, nil)
	conn, err := net.DialUDP("udp", nil, s.Addr())
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if _, err := conn.Write([]byte{0x00, 0x01}); err != nil {
		t.Fatal(err)
	}
	_ = conn.SetReadDeadline(time.Now().Add(300 * time.Millisecond))
	buf := make([]byte, 64)
	if _, err := conn.Read(buf); err == nil {
		t.Fatal("junk datagram must not produce a response")
	}
}
