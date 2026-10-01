// Package integration contains end-to-end compatibility tests that drive the
// real proxy over TCP. Expected wire bytes are produced exclusively by the
// independent test/oracle package, never by the code under test.
package integration

import (
	"io"
	"net"
	"strconv"
	"testing"
	"time"

	"socks5d.local/socks5d/test/oracle"
)

// socksClient is a minimal, test-only SOCKS5 client whose wire bytes are
// produced by the independent oracle package.
type socksClient struct {
	t    *testing.T
	conn net.Conn
}

func dialProxy(t *testing.T, addr string) *socksClient {
	t.Helper()
	c, err := net.DialTimeout("tcp", addr, 3*time.Second)
	if err != nil {
		t.Fatalf("dial proxy: %v", err)
	}
	return &socksClient{t: t, conn: c}
}

func (c *socksClient) close() { _ = c.conn.Close() }

func (c *socksClient) setDeadline(d time.Duration) {
	_ = c.conn.SetDeadline(time.Now().Add(d))
}

func (c *socksClient) write(b []byte) {
	c.t.Helper()
	if _, err := c.conn.Write(b); err != nil {
		c.t.Fatalf("client write %x: %v", b, err)
	}
}

// readExact reads exactly n bytes, returning the error directly.
func (c *socksClient) readExact(n int) ([]byte, error) {
	buf := make([]byte, n)
	if _, err := io.ReadFull(c.conn, buf); err != nil {
		return nil, err
	}
	return buf, nil
}

// greet sends a method greeting and returns the exact 2-byte selection.
func (c *socksClient) greet(methods ...byte) []byte {
	c.t.Helper()
	c.write(oracle.Greeting(methods...))
	sel, err := c.readExact(2)
	if err != nil {
		c.t.Fatalf("read method selection: %v", err)
	}
	return sel
}

// greetRaw writes arbitrary bytes and returns whatever the server sends.
func (c *socksClient) greetRaw(b []byte) []byte {
	c.write(b)
	sel, _ := c.readExact(2)
	return sel
}

// auth performs the user/password sub-negotiation, returning STATUS (0x00/0x01).
func (c *socksClient) auth(user, pass string) byte {
	c.t.Helper()
	c.write(oracle.UserPass(user, pass))
	rep, err := c.readExact(2)
	if err != nil {
		c.t.Fatalf("read auth status: %v", err)
	}
	if rep[0] != oracle.SubnegVersion {
		c.t.Fatalf("auth reply version = %d want 1", rep[0])
	}
	return rep[1]
}

// connect sends a CONNECT and parses the reply with the independent oracle.
func (c *socksClient) connect(host string, port uint16) oracle.Reply {
	c.t.Helper()
	switch ip := parseV4(host); {
	case ip != nil:
		c.write(oracle.ConnectIPv4([4]byte(ip), port))
	case host != "" && host[0] == '[':
		c.write(oracle.ConnectIPv6(parseV6(c.t, host), port))
	default:
		c.write(oracle.ConnectName(host, port))
	}
	return c.readReply()
}

// connectRaw sends an arbitrary command/atyp frame for negative tests.
func (c *socksClient) connectRaw(frame []byte) oracle.Reply {
	c.t.Helper()
	c.write(frame)
	return c.readReply()
}

func (c *socksClient) readReply() oracle.Reply {
	c.t.Helper()
	head, err := c.readExact(4)
	if err != nil {
		c.t.Fatalf("read reply head: %v", err)
	}
	var body []byte
	switch head[3] {
	case oracle.AtypIPv4:
		body, err = c.readExact(6)
	case oracle.AtypIPv6:
		body, err = c.readExact(18)
	case oracle.AtypName:
		var l []byte
		l, err = c.readExact(1)
		if err == nil {
			body, err = c.readExact(int(l[0]) + 2)
			body = append(l, body...)
		}
	default:
		c.t.Fatalf("unknown atyp in reply %d", head[3])
	}
	if err != nil {
		c.t.Fatalf("read reply body: %v", err)
	}
	rep, perr := oracle.ParseReply(append(head, body...))
	if perr != nil {
		c.t.Fatalf("oracle parse reply: %v", perr)
	}
	return rep
}

func parseV4(host string) net.IP {
	ip := net.ParseIP(host)
	if ip != nil && ip.To4() != nil {
		return ip.To4()
	}
	return nil
}

func parseV6(t *testing.T, host string) [16]byte {
	t.Helper()
	raw := host[1 : len(host)-1] // strip [ ]
	ip := net.ParseIP(raw)
	if ip == nil {
		t.Fatalf("bad v6 host %q", host)
	}
	return [16]byte(ip.To16())
}

// splitHostPort splits an "ip:port" listen address.
func splitHostPort(t *testing.T, addr string) (string, uint16) {
	t.Helper()
	host, ps, err := net.SplitHostPort(addr)
	if err != nil {
		t.Fatalf("split %q: %v", addr, err)
	}
	p, err := strconv.Atoi(ps)
	if err != nil {
		t.Fatalf("port %q: %v", ps, err)
	}
	return host, uint16(p)
}
