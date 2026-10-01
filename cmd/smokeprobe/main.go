// Command smokeprobe is a STANDALONE SOCKS5 client, written from RFC 1928/1929
// without importing any package of the proxy. It drives one CONNECT through a
// running proxy and asserts a concrete result: the exact reply code, a
// byte-for-byte echo of the payload, and a clean directional EOF after the
// probe half-closes. It is used by scripts/verify.sh for an end-to-end check
// against the real compiled binary.
//
// Exit status encodes the failure category:
//
//	0  success (observed reply matched -expect-rep and, on success, echo+EOF)
//	2  transport/usage failure
//	3  method negotiation failure
//	4  authentication failure
//	5  request reply mismatch / parse failure
//	6  forwarding (echo or EOF) failure
package main

import (
	"bytes"
	"encoding/binary"
	"errors"
	"flag"
	"fmt"
	"io"
	"net"
	"os"
	"strconv"
	"time"
)

const (
	ver5       = 5
	subnegVer  = 1
	mNone      = 0x00
	mUserPass  = 0x02
	mNoAccept  = 0xFF
	cmdConnect = 1
	atypIPv4   = 1
	atypDomain = 3
	atypIPv6   = 4
	statusOK   = 0
)

func main() {
	var (
		proxy     = flag.String("proxy", "127.0.0.1:1080", "proxy address")
		atyp      = flag.String("type", "ipv4", "target type: ipv4|ipv6|domain")
		host      = flag.String("host", "127.0.0.1", "target host")
		port      = flag.Int("port", 0, "target port")
		method    = flag.String("method", "none", "auth method: none|userpass")
		user      = flag.String("user", "", "username")
		pass      = flag.String("pass", "", "password")
		payload   = flag.String("payload", "smoke-payload", "bytes to send and expect echoed")
		expectRep = flag.Int("expect-rep", 0, "expected SOCKS5 REP byte (decimal)")
		timeout   = flag.Duration("timeout", 8*time.Second, "overall timeout")
	)
	flag.Parse()

	if *port == 0 {
		fail(2, "usage", "-port is required")
	}
	if err := run(*proxy, *atyp, *host, *port, *method, *user, *pass, *payload, *expectRep, *timeout); err != nil {
		var pe probeErr
		if errors.As(err, &pe) {
			fmt.Fprintf(os.Stderr, "FAIL category=%s detail=%s\n", pe.category, pe.Error())
			os.Exit(pe.code)
		}
		fail(2, "transport", err.Error())
	}
	fmt.Println("PASS")
}

type probeErr struct {
	category string
	code     int
	msg      string
}

func (e probeErr) Error() string { return e.msg }

func fail(code int, category, msg string) {
	fmt.Fprintf(os.Stderr, "FAIL category=%s detail=%s\n", category, msg)
	os.Exit(code)
}

func cerr(code int, category, format string, a ...any) error {
	return probeErr{category: category, code: code, msg: fmt.Sprintf(format, a...)}
}

func run(proxyAddr, atypName, targetHost string, targetPort int, method, user, pass, payload string, expectRep int, timeout time.Duration) error {
	conn, err := net.DialTimeout("tcp", proxyAddr, timeout)
	if err != nil {
		return cerr(2, "dial_proxy", "%v", err)
	}
	defer conn.Close()
	_ = conn.SetDeadline(time.Now().Add(timeout))

	// --- Method negotiation ---
	var offered []byte
	switch method {
	case "none":
		offered = []byte{ver5, 1, mNone}
	case "userpass":
		offered = []byte{ver5, 1, mUserPass}
	default:
		return cerr(2, "usage", "unknown method %q", method)
	}
	if _, err := conn.Write(offered); err != nil {
		return cerr(2, "write_greeting", "%v", err)
	}
	sel := make([]byte, 2)
	if _, err := io.ReadFull(conn, sel); err != nil {
		return cerr(3, "read_selection", "%v", err)
	}
	if sel[0] != ver5 {
		return cerr(3, "bad_version", "selection VER=%d", sel[0])
	}
	if sel[1] == mNoAccept {
		if expectRep == -1 { // -1 means "expect 0xFF negotiation failure"
			return nil
		}
		return cerr(3, "no_acceptable_method", "server returned 0xFF")
	}

	// --- Authentication ---
	if method == "userpass" {
		if sel[1] != mUserPass {
			return cerr(4, "unexpected_method", "server selected 0x%02x", sel[1])
		}
		up := buildUserPass(user, pass)
		if _, err := conn.Write(up); err != nil {
			return cerr(2, "write_auth", "%v", err)
		}
		ar := make([]byte, 2)
		if _, err := io.ReadFull(conn, ar); err != nil {
			return cerr(4, "read_auth_status", "%v", err)
		}
		if ar[1] != statusOK {
			if expectRep == -2 { // -2 means "expect auth failure"
				return nil
			}
			return cerr(4, "auth_rejected", "status=0x%02x", ar[1])
		}
	}

	// --- CONNECT request ---
	req, err := buildConnect(atypName, targetHost, targetPort)
	if err != nil {
		return err
	}
	if _, err := conn.Write(req); err != nil {
		return cerr(2, "write_request", "%v", err)
	}
	rep, err := readReply(conn)
	if err != nil {
		return cerr(5, "read_reply", "%v", err)
	}
	fmt.Printf("REP=%d\n", rep)
	if rep != expectRep {
		return cerr(5, "reply_mismatch", "REP=%d expected %d", rep, expectRep)
	}
	if rep != 0 {
		return nil // a deliberately expected failure reply
	}

	// --- Forwarding: byte-exact echo + clean directional EOF ---
	if _, err := conn.Write([]byte(payload)); err != nil {
		return cerr(6, "write_payload", "%v", err)
	}
	if tc, ok := conn.(*net.TCPConn); ok {
		if err := tc.CloseWrite(); err != nil {
			return cerr(6, "half_close", "%v", err)
		}
	}
	got, err := io.ReadAll(conn)
	if err != nil {
		return cerr(6, "read_echo", "%v", err)
	}
	if !bytes.Equal(got, []byte(payload)) {
		return cerr(6, "echo_mismatch", "got %d bytes %q, want %d bytes %q",
			len(got), truncate(got), len(payload), payload)
	}
	return nil
}

func buildUserPass(user, pass string) []byte {
	if len(user) > 255 || len(pass) > 255 {
		fail(2, "usage", "username/password too long")
	}
	b := []byte{subnegVer, byte(len(user))}
	b = append(b, user...)
	b = append(b, byte(len(pass)))
	b = append(b, pass...)
	return b
}

func buildConnect(atypName, host string, port int) ([]byte, error) {
	if port < 1 || port > 65535 {
		return nil, cerr(2, "usage", "port out of range: %d", port)
	}
	b := []byte{ver5, cmdConnect, 0x00}
	switch atypName {
	case "ipv4":
		ip := net.ParseIP(host)
		if ip == nil || ip.To4() == nil {
			return nil, cerr(2, "usage", "not an IPv4 address: %q", host)
		}
		v4 := ip.To4()
		b = append(b, atypIPv4)
		b = append(b, v4...)
	case "ipv6":
		ip := net.ParseIP(host)
		if ip == nil || ip.To4() != nil {
			return nil, cerr(2, "usage", "not an IPv6 address: %q", host)
		}
		b = append(b, atypIPv6)
		b = append(b, ip.To16()...)
	case "domain":
		if len(host) == 0 || len(host) > 255 {
			return nil, cerr(2, "usage", "bad domain length")
		}
		b = append(b, atypDomain, byte(len(host)))
		b = append(b, host...)
	default:
		return nil, cerr(2, "usage", "unknown target type %q", atypName)
	}
	var p [2]byte
	binary.BigEndian.PutUint16(p[:], uint16(port))
	return append(b, p[:]...), nil
}

func readReply(conn net.Conn) (int, error) {
	head := make([]byte, 4)
	if _, err := io.ReadFull(conn, head); err != nil {
		return 0, err
	}
	if head[0] != ver5 {
		return 0, fmt.Errorf("reply VER=%d", head[0])
	}
	var bodyLen int
	switch head[3] {
	case atypIPv4:
		bodyLen = 4 + 2
	case atypIPv6:
		bodyLen = 16 + 2
	case atypDomain:
		l := make([]byte, 1)
		if _, err := io.ReadFull(conn, l); err != nil {
			return 0, err
		}
		bodyLen = int(l[0]) + 2
	default:
		return 0, fmt.Errorf("reply ATYP=%d", head[3])
	}
	if _, err := io.ReadFull(conn, make([]byte, bodyLen)); err != nil {
		return 0, err
	}
	return int(head[1]), nil
}

func truncate(b []byte) string {
	const n = 48
	if len(b) <= n {
		return string(b)
	}
	return string(b[:n]) + "...(" + strconv.Itoa(len(b)) + " bytes)"
}
