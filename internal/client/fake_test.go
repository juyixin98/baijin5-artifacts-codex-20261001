package client_test

import (
	"net"
	"sync"
	"testing"
	"time"

	"localstun/internal/stun"
)

// responseCmd instructs the fake STUN server to emit one response.
type responseCmd struct {
	txid stun.TransactionID
	ip   string
	port int
}

// fakeOptions controls scripted behavior.
type fakeOptions struct {
	key         []byte
	fingerprint bool
	tamper      bool
}

type fakeOpt func(*fakeOptions)

func withKey(key []byte) fakeOpt {
	return func(o *fakeOptions) { o.key = key; o.fingerprint = true }
}

func tamperNext() fakeOpt {
	return func(o *fakeOptions) { o.tamper = true }
}

// fakeSTUN is a scripted UDP responder. Every received request causes its
// txid to be published on txids; the test later pushes responseCmd values to
// replies to schedule responses sent back to the most recent client address.
type fakeSTUN struct {
	conn    *net.UDPConn
	opts    fakeOptions
	mu      sync.Mutex
	client  *net.UDPAddr
	txids   chan stun.TransactionID
	replies chan responseCmd
}

func newFakeSTUN(t *testing.T, addr string, opts ...fakeOpt) (*fakeSTUN, <-chan stun.TransactionID, chan<- responseCmd) {
	t.Helper()
	o := fakeOptions{}
	for _, fn := range opts {
		fn(&o)
	}
	udpAddr, err := net.ResolveUDPAddr("udp", addr)
	if err != nil {
		t.Fatal(err)
	}
	conn, err := net.ListenUDP("udp", udpAddr)
	if err != nil {
		t.Fatal(err)
	}
	f := &fakeSTUN{
		conn:    conn,
		opts:    o,
		txids:   make(chan stun.TransactionID, 8),
		replies: make(chan responseCmd, 8),
	}
	go f.readLoop()
	go f.replyLoop()
	t.Cleanup(func() { _ = conn.Close() })
	return f, f.txids, f.replies
}

func (f *fakeSTUN) LocalAddr() net.Addr { return f.conn.LocalAddr() }

func (f *fakeSTUN) readLoop() {
	buf := make([]byte, 2048)
	for {
		n, src, err := f.conn.ReadFromUDP(buf)
		if err != nil {
			return
		}
		if n < stun.HeaderLen {
			continue
		}
		var txid stun.TransactionID
		copy(txid[:], buf[8:20])
		f.mu.Lock()
		f.client = src
		f.mu.Unlock()
		f.txids <- txid
	}
}

func (f *fakeSTUN) replyLoop() {
	for cmd := range f.replies {
		f.mu.Lock()
		dst := f.client
		f.mu.Unlock()
		if dst == nil {
			continue
		}
		m := stun.NewMessage(stun.BindingResponse, cmd.txid)
		_ = m.AddXORMappedAddress(net.ParseIP(cmd.ip), cmd.port)
		out, err := stun.Marshal(m, f.opts.key, f.opts.fingerprint)
		if err != nil {
			continue
		}
		if f.opts.tamper {
			// Flip a byte inside the XOR-MAPPED-ADDRESS value region,
			// invalidating MESSAGE-INTEGRITY.
			out[26] ^= 0xFF
		}
		_, _ = f.conn.WriteToUDP(out, dst)
	}
}

// buildResponse builds an unsigned scripted response datagram.
func buildResponse(txid stun.TransactionID, ip string, port int) []byte {
	m := stun.NewMessage(stun.BindingResponse, txid)
	_ = m.AddXORMappedAddress(net.ParseIP(ip), port)
	out, err := stun.Marshal(m, nil, false)
	if err != nil {
		panic(err)
	}
	return out
}

// waitFor is a small helper to make timing assertions deterministic-ish.
func waitFor(d time.Duration) { time.Sleep(d) }
