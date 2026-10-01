package integration

import (
	"encoding/binary"
	"errors"
	"fmt"
	"io"
	"net"
	"net/netip"
	"strconv"
	"time"
)

// RawClient is an independent, minimal SOCKS5 client written directly
// against RFC 1928/1929. It shares no code with the proxy under test.
type RawClient struct {
	conn net.Conn
}

// DialRaw opens a TCP connection to the proxy.
func DialRaw(addr string) (*RawClient, error) {
	c, err := net.DialTimeout("tcp", addr, 3*time.Second)
	if err != nil {
		return nil, err
	}
	return &RawClient{conn: c}, nil
}

// Close closes the client socket.
func (c *RawClient) Close() error { return c.conn.Close() }

// TCPConn exposes the underlying conn for half-close tests.
func (c *RawClient) TCPConn() *net.TCPConn { return c.conn.(*net.TCPConn) }

// writeSlow writes the frame with each chunk separated by a delay, and may
// split it down to one byte per write.
func (c *RawClient) writeSlow(frame []byte, delay time.Duration) error {
	for _, b := range frame {
		if _, err := c.conn.Write([]byte{b}); err != nil {
			return err
		}
		if delay > 0 {
			time.Sleep(delay)
		}
	}
	return nil
}

// readExact reads n bytes or returns an error describing a short read.
func (c *RawClient) readExact(n int) ([]byte, error) {
	buf := make([]byte, n)
	if _, err := io.ReadFull(c.conn, buf); err != nil {
		return nil, err
	}
	return buf, nil
}

// Negotiate performs method negotiation. methods are the offered METHOD
// bytes. It returns the server-selected method and 0xFF handling.
func (c *RawClient) Negotiate(methods []byte, segmented bool) (byte, error) {
	frame := append([]byte{0x05, byte(len(methods))}, methods...)
	if segmented {
		if err := c.writeSlow(frame, 3*time.Millisecond); err != nil {
			return 0, err
		}
	} else if _, err := c.conn.Write(frame); err != nil {
		return 0, err
	}
	resp, err := c.readExact(2)
	if err != nil {
		return 0, err
	}
	if resp[0] != 0x05 {
		return 0, fmt.Errorf("bad method version 0x%02x", resp[0])
	}
	return resp[1], nil
}

// Auth performs RFC 1929 username/password, optionally byte-segmented.
func (c *RawClient) Auth(user, pass string, segmented bool) (byte, error) {
	if len(user) > 255 || len(pass) > 255 {
		return 0, errors.New("credentials too long")
	}
	frame := []byte{0x01, byte(len(user))}
	frame = append(frame, []byte(user)...)
	frame = append(frame, byte(len(pass)))
	frame = append(frame, []byte(pass)...)
	if segmented {
		if err := c.writeSlow(frame, 3*time.Millisecond); err != nil {
			return 0, err
		}
	} else if _, err := c.conn.Write(frame); err != nil {
		return 0, err
	}
	resp, err := c.readExact(2)
	if err != nil {
		return 0, err
	}
	if resp[0] != 0x01 {
		return 0, fmt.Errorf("bad auth version 0x%02x", resp[0])
	}
	return resp[1], nil
}

// ConnectResult is a fully decoded CONNECT reply.
type ConnectResult struct {
	Reply byte
	Bound string // host:port
}

// ConnectIP sends a CONNECT for an IP literal (ATYP 1 for v4, 4 for v6).
func (c *RawClient) ConnectIP(addr netip.Addr, port int, segmented bool) (ConnectResult, error) {
	addr = addr.Unmap()
	var atyp byte
	var body []byte
	if addr.Is4() {
		atyp = 0x01
		a4 := addr.As4()
		body = a4[:]
	} else {
		atyp = 0x04
		a16 := addr.As16()
		body = a16[:]
	}
	return c.connect(atyp, body, port, segmented)
}

// ConnectDomain sends a CONNECT with ATYP=3 (domain name).
func (c *RawClient) ConnectDomain(domain string, port int, segmented bool) (ConnectResult, error) {
	if len(domain) > 255 {
		return ConnectResult{}, errors.New("domain too long")
	}
	body := []byte{byte(len(domain))}
	body = append(body, []byte(domain)...)
	return c.connect(0x03, body, port, segmented)
}

func (c *RawClient) connect(atyp byte, addr []byte, port int, segmented bool) (ConnectResult, error) {
	frame := []byte{0x05, 0x01, 0x00, atyp}
	frame = append(frame, addr...)
	pb := make([]byte, 2)
	binary.BigEndian.PutUint16(pb, uint16(port))
	frame = append(frame, pb...)
	if segmented {
		if err := c.writeSlow(frame, 3*time.Millisecond); err != nil {
			return ConnectResult{}, err
		}
	} else if _, err := c.conn.Write(frame); err != nil {
		return ConnectResult{}, err
	}
	return c.readReply()
}

func (c *RawClient) readReply() (ConnectResult, error) {
	head, err := c.readExact(4)
	if err != nil {
		return ConnectResult{}, err
	}
	if head[0] != 0x05 {
		return ConnectResult{}, fmt.Errorf("bad reply version 0x%02x", head[0])
	}
	var host string
	switch head[3] {
	case 0x01:
		b, err := c.readExact(4 + 2)
		if err != nil {
			return ConnectResult{}, err
		}
		host = net.JoinHostPort(
			fmt.Sprintf("%d.%d.%d.%d", b[0], b[1], b[2], b[3]),
			strconv.Itoa(int(binary.BigEndian.Uint16(b[4:]))),
		)
	case 0x04:
		b, err := c.readExact(16 + 2)
		if err != nil {
			return ConnectResult{}, err
		}
		var a16 [16]byte
		copy(a16[:], b[:16])
		host = net.JoinHostPort(
			netip.AddrFrom16(a16).String(),
			strconv.Itoa(int(binary.BigEndian.Uint16(b[16:]))),
		)
	case 0x03:
		l, err := c.readExact(1)
		if err != nil {
			return ConnectResult{}, err
		}
		b, err := c.readExact(int(l[0]) + 2)
		if err != nil {
			return ConnectResult{}, err
		}
		host = net.JoinHostPort(string(b[:l[0]]), strconv.Itoa(int(binary.BigEndian.Uint16(b[l[0]:]))))
	default:
		return ConnectResult{}, fmt.Errorf("unknown reply atyp 0x%02x", head[3])
	}
	return ConnectResult{Reply: head[1], Bound: host}, nil
}

// Read/Write/CloseWrite proxy to the socket for relay-phase tests.
func (c *RawClient) Read(p []byte) (int, error)    { return c.conn.Read(p) }
func (c *RawClient) Write(p []byte) (int, error)   { return c.conn.Write(p) }
func (c *RawClient) CloseWrite() error             { return c.TCPConn().CloseWrite() }
func (c *RawClient) SetDeadline(t time.Time) error { return c.conn.SetDeadline(t) }
