// Package transport contains the NTP client/server protocol state machine
// running over a generic datagram interface. The same state machine is used
// over a real UDP socket (compatibility tests) and over the simulated fabric
// (deterministic scenarios).
package transport

import (
	"errors"
	"net"
	"time"
)

// Datagram is the minimal packet transport the NTP state machine needs.
// net.UDPConn satisfies it via UDPDatagram; the simulated fabric provides its
// own deterministic implementation.
type Datagram interface {
	// Send transmits b to the named peer.
	Send(b []byte, to string) error
	// Recv waits up to deadline for one datagram, returning payload and sender.
	Recv(deadline time.Time) (b []byte, from string, err error)
	// LocalAddr identifies this endpoint.
	LocalAddr() string
	// Close releases resources.
	Close() error
}

// ErrTimeout is returned by Recv when no datagram arrives before the deadline.
var ErrTimeout = errors.New("transport: receive timed out")

// UDPDatagram adapts *net.UDPConn to Datagram.
type UDPDatagram struct {
	conn *net.UDPConn
}

// NewUDP wraps an existing UDP connection.
func NewUDP(c *net.UDPConn) *UDPDatagram { return &UDPDatagram{conn: c} }

// DialUDP creates an UNCONNECTED client-side UDP socket bound to local
// (e.g. "127.0.0.1:0"); peers are addressed per-Send. An unconnected socket is
// required because a client exchanges with many servers and must observe
// ICMP port-unreachable as a read timeout rather than a dial error.
func DialUDP(local string) (*UDPDatagram, error) {
	addr, err := net.ResolveUDPAddr("udp4", local)
	if err != nil {
		return nil, err
	}
	c, err := net.ListenUDP("udp4", addr)
	if err != nil {
		return nil, err
	}
	return &UDPDatagram{conn: c}, nil
}

// ListenUDP creates a server-side UDP socket bound to addr (e.g. 127.0.0.1:0).
func ListenUDP(addr string) (*UDPDatagram, error) {
	a, err := net.ResolveUDPAddr("udp4", addr)
	if err != nil {
		return nil, err
	}
	c, err := net.ListenUDP("udp4", a)
	if err != nil {
		return nil, err
	}
	return &UDPDatagram{conn: c}, nil
}

// Send writes b to to ("host:port").
func (u *UDPDatagram) Send(b []byte, to string) error {
	a, err := net.ResolveUDPAddr("udp4", to)
	if err != nil {
		return err
	}
	_, err = u.conn.WriteToUDP(b, a)
	return err
}

// Recv waits up to deadline for one datagram.
func (u *UDPDatagram) Recv(deadline time.Time) ([]byte, string, error) {
	if err := u.conn.SetReadDeadline(deadline); err != nil {
		return nil, "", err
	}
	buf := make([]byte, 65535)
	n, a, err := u.conn.ReadFromUDP(buf)
	if err != nil {
		if ne, ok := err.(net.Error); ok && ne.Timeout() {
			return nil, "", ErrTimeout
		}
		return nil, "", err
	}
	return buf[:n], a.String(), nil
}

// LocalAddr returns the socket's local address.
func (u *UDPDatagram) LocalAddr() string { return u.conn.LocalAddr().String() }

// Close closes the socket.
func (u *UDPDatagram) Close() error { return u.conn.Close() }
