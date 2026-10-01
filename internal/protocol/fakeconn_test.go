package protocol_test

import (
	"net"
	"sync"
)

// udpPacket is one datagram delivered to or captured from a fakeUDPConn.
type udpPacket struct {
	data []byte
	addr *net.UDPAddr
}

// fakeUDPConn is an in-memory UDPConn replacement. Writes are captured and
// handed to an optional script; inbound datagrams are pushed into Inbound.
// No real socket is ever opened.
type fakeUDPConn struct {
	addr net.Addr

	mu      sync.Mutex
	sent    [][]byte
	closed  bool
	onWrite func(dst *net.UDPAddr, b []byte)

	inbound chan udpPacket
	closeCh chan struct{}
	once    sync.Once
}

func newFakeUDPConn() *fakeUDPConn {
	return &fakeUDPConn{
		addr:    &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1), Port: 5683},
		inbound: make(chan udpPacket, 64),
		closeCh: make(chan struct{}),
	}
}

func (f *fakeUDPConn) deliver(b []byte, from *net.UDPAddr) {
	f.inbound <- udpPacket{data: append([]byte(nil), b...), addr: from}
}

// setOnWrite installs the capture script under the lock, avoiding races.
func (f *fakeUDPConn) setOnWrite(cb func(dst *net.UDPAddr, b []byte)) {
	f.mu.Lock()
	f.onWrite = cb
	f.mu.Unlock()
}

func (f *fakeUDPConn) ReadFromUDP(b []byte) (int, *net.UDPAddr, error) {
	select {
	case p := <-f.inbound:
		n := copy(b, p.data)
		return n, p.addr, nil
	case <-f.closeCh:
		return 0, nil, net.ErrClosed
	}
}

func (f *fakeUDPConn) WriteToUDP(b []byte, addr *net.UDPAddr) (int, error) {
	f.mu.Lock()
	if f.closed {
		f.mu.Unlock()
		return 0, net.ErrClosed
	}
	cp := append([]byte(nil), b...)
	f.sent = append(f.sent, cp)
	cb := f.onWrite
	f.mu.Unlock()
	// Invoke the script without holding the lock so it may inspect sent data.
	if cb != nil {
		cb(addr, cp)
	}
	return len(b), nil
}

func (f *fakeUDPConn) Close() error {
	f.once.Do(func() {
		f.mu.Lock()
		f.closed = true
		f.mu.Unlock()
		close(f.closeCh)
	})
	return nil
}

func (f *fakeUDPConn) LocalAddr() net.Addr { return f.addr }

// sentCopy returns a snapshot of everything written so far.
func (f *fakeUDPConn) sentCopy() [][]byte {
	f.mu.Lock()
	defer f.mu.Unlock()
	out := make([][]byte, len(f.sent))
	copy(out, f.sent)
	return out
}

// sentCount returns how many datagrams were written.
func (f *fakeUDPConn) sentCount() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return len(f.sent)
}
