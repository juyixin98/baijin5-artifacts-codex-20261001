package harness

import (
	"net"
	"sync"
	"time"

	"coaplab/internal/wire"
)

// RawPeer is a scripted CoAP peer that sends fully hand-built messages and
// records every datagram received. It deliberately does NOT implement
// retransmission or token matching — tests script exact wire behaviour
// (duplicate MIDs, swapped tokens, out-of-order blocks) themselves.
type RawPeer struct {
	conn *net.UDPConn

	mu       sync.Mutex
	received []RawDatagram
}

// RawDatagram is one received datagram plus parse result.
type RawDatagram struct {
	From string
	Data []byte
	Msg  *wire.Message
	Err  error
}

// NewRawPeer binds an ephemeral UDP socket targeting serverAddr directly
// (or the proxy front door — the address is supplied by the caller).
func NewRawPeer(target string) (*RawPeer, *net.UDPAddr, error) {
	taddr, err := net.ResolveUDPAddr("udp", target)
	if err != nil {
		return nil, nil, err
	}
	conn, err := net.ListenUDP("udp", &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1)})
	if err != nil {
		return nil, nil, err
	}
	rp := &RawPeer{conn: conn}
	return rp, taddr, nil
}

// LocalAddr reports the peer's bound address.
func (r *RawPeer) LocalAddr() *net.UDPAddr { return r.conn.LocalAddr().(*net.UDPAddr) }

// Send marshals and transmits one message.
func (r *RawPeer) Send(m *wire.Message, to *net.UDPAddr) error {
	raw, err := m.Marshal()
	if err != nil {
		return err
	}
	_, err = r.conn.WriteToUDP(raw, to)
	return err
}

// SendRaw transmits pre-built bytes (malformed-message tests).
func (r *RawPeer) SendRaw(b []byte, to *net.UDPAddr) error {
	_, err := r.conn.WriteToUDP(b, to)
	return err
}

// ReceiveOne waits up to timeout for one datagram.
func (r *RawPeer) ReceiveOne(timeout time.Duration) (RawDatagram, bool) {
	_ = r.conn.SetReadDeadline(time.Now().Add(timeout))
	buf := make([]byte, 65535)
	n, raddr, err := r.conn.ReadFromUDP(buf)
	if err != nil {
		return RawDatagram{}, false
	}
	data := append([]byte(nil), buf[:n]...)
	msg, perr := wire.Parse(data)
	d := RawDatagram{From: raddr.String(), Data: data, Msg: msg, Err: perr}
	r.mu.Lock()
	r.received = append(r.received, d)
	r.mu.Unlock()
	return d, true
}

// ReceiveN collects n datagrams or returns when timeout elapses.
func (r *RawPeer) ReceiveN(n int, total time.Duration) []RawDatagram {
	deadline := time.Now().Add(total)
	var out []RawDatagram
	for len(out) < n && time.Now().Before(deadline) {
		if d, ok := r.ReceiveOne(50 * time.Millisecond); ok {
			out = append(out, d)
		}
	}
	return out
}

// Drain empties the socket for d (used to detect unexpected retransmits).
func (r *RawPeer) Drain(d time.Duration) []RawDatagram {
	return r.ReceiveN(64, d)
}

// Close releases the socket.
func (r *RawPeer) Close() { _ = r.conn.Close() }
