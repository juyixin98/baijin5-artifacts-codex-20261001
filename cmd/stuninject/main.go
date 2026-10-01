// Command stuninject sends one crafted STUN datagram to a test server and
// prints whatever comes back (decoded). It exists to exercise anomaly paths
// end-to-end against the REAL server socket:
//
//	valid            signed Binding request (expect success)
//	unsigned         well-formed request without integrity (expect 401 on a
//	                 signing server)
//	unknown-required request carrying unknown compr-required attr 0x0BAD
//	                 (expect 420 + UNKNOWN-ATTRIBUTES)
//	unknown-optional request carrying unknown compr-optional attr 0x80AD
//	                 (expect success)
//	junk             8 garbage bytes (expect silence/drop)
//	bad-cookie       valid header shape but wrong magic cookie (expect drop)
//	tampered         signed request with one body byte flipped (expect drop)
//
// Exit code is 0 when the observed outcome category matches -expect, which is
// one of: success | error401 | error420 | dropped.
package main

import (
	"encoding/binary"
	"flag"
	"fmt"
	"net"
	"os"
	"time"

	"localstun/internal/stun"
	"localstun/internal/stunerror"
)

func main() {
	server := flag.String("server", "127.0.0.1:3478", "target server")
	mode := flag.String("mode", "valid", "injection mode")
	key := flag.String("key", "localstun-test-key", "integrity key")
	expect := flag.String("expect", "success", "expected outcome: success|error401|error420|dropped")
	wait := flag.Duration("wait", 800*time.Millisecond, "response wait")
	flag.Parse()

	txID := stun.MustTransactionID()
	k := []byte(*key)

	var pkt []byte
	var err error
	switch *mode {
	case "valid":
		pkt, err = stun.Marshal(stun.NewMessage(stun.BindingRequest, txID), k, true)
	case "unsigned":
		pkt, err = stun.Marshal(stun.NewMessage(stun.BindingRequest, txID), nil, false)
	case "unknown-required":
		pkt = frameRaw(stun.BindingRequest, txID, frameAttr(0x0BAD, []byte{0x01, 0x02}))
	case "unknown-optional":
		// Signed so it passes the integrity gate; the unknown OPTIONAL
		// attribute must then be tolerated and produce a success.
		opt := stun.NewMessage(stun.BindingRequest, txID)
		opt.Add(stun.AttrType(0x80AD), []byte{0x01, 0x02, 0x03})
		pkt, err = stun.Marshal(opt, k, true)
	case "junk":
		pkt = []byte{0xDE, 0xAD, 0xBE, 0xEF, 0x00, 0x01, 0x00, 0x00}
	case "bad-cookie":
		pkt = frameRaw(stun.BindingRequest, txID, nil)
		binary.BigEndian.PutUint32(pkt[4:8], 0xDEADBEEF)
	case "tampered":
		pkt, err = stun.Marshal(stun.NewMessage(stun.BindingRequest, txID), k, true)
		// Flip a byte in the FINGERPRINT region tail (last byte) so HMAC and
		// CRC both fail; server discards before responding.
		pkt[len(pkt)-1] ^= 0xFF
	default:
		fmt.Fprintf(os.Stderr, "unknown mode %q\n", *mode)
		os.Exit(2)
	}
	if err != nil {
		fmt.Fprintf(os.Stderr, "build packet: %v\n", err)
		os.Exit(2)
	}

	addr, err := net.ResolveUDPAddr("udp", *server)
	if err != nil {
		fmt.Fprintf(os.Stderr, "resolve: %v\n", err)
		os.Exit(2)
	}
	conn, err := net.DialUDP("udp", nil, addr)
	if err != nil {
		fmt.Fprintf(os.Stderr, "dial: %v\n", err)
		os.Exit(2)
	}
	defer conn.Close()

	fmt.Printf("inject mode=%s tx=%s bytes=%d -> %s\n",
		*mode, hexShort(txID[:]), len(pkt), *server)
	if _, err := conn.Write(pkt); err != nil {
		fmt.Fprintf(os.Stderr, "send: %v\n", err)
		os.Exit(2)
	}

	_ = conn.SetReadDeadline(time.Now().Add(*wait))
	buf := make([]byte, 2048)
	n, err := conn.Read(buf)
	if err != nil {
		// No response within the window: classify as dropped.
		got := "dropped"
		verdict(got, *expect)
		return
	}

	resp := buf[:n]
	m, derr := stun.Decode(resp, k)
	got := "success"
	detail := ""
	if derr != nil {
		got = "dropped"
		detail = "response failed decode: " + derr.Error()
	} else if m.Type == stun.BindingError {
		code, reason, _ := m.ErrorCode()
		switch code {
		case stun.StatusUnauthorized:
			got = "error401"
		case stun.StatusUnknownAttribute:
			got = "error420"
		default:
			got = fmt.Sprintf("error%d", code)
		}
		detail = fmt.Sprintf("code=%d reason=%q integrity=%v", code, reason, m.IntegrityOK)
	} else {
		ip, port, ok, _ := m.XORMappedAddress()
		if ok {
			detail = fmt.Sprintf("xor-mapped=%s:%d integrity=%v", ip, port, m.IntegrityOK)
		}
	}
	fmt.Printf("result bytes=%d outcome=%s %s\n", n, got, detail)
	verdict(got, *expect)
}

func verdict(got, want string) {
	if got != want {
		fmt.Printf("JUDGMENT MISMATCH got=%s want=%s kind=%s\n",
			got, want, stunerror.KindIntegrity)
		os.Exit(1)
	}
	fmt.Printf("JUDGMENT MATCH got=%s want=%s\n", got, want)
}

func frameAttr(at uint16, value []byte) []byte {
	pad := (4 - len(value)%4) % 4
	out := make([]byte, 4+len(value)+pad)
	binary.BigEndian.PutUint16(out[0:2], at)
	binary.BigEndian.PutUint16(out[2:4], uint16(len(value)))
	copy(out[4:], value)
	return out
}

func frameRaw(mt stun.MessageType, txID stun.TransactionID, body []byte) []byte {
	out := make([]byte, 20+len(body))
	binary.BigEndian.PutUint16(out[0:2], uint16(mt))
	binary.BigEndian.PutUint16(out[2:4], uint16(len(body)))
	binary.BigEndian.PutUint32(out[4:8], stun.MagicCookie)
	copy(out[8:20], txID[:])
	copy(out[20:], body)
	return out
}

func hexShort(b []byte) string {
	const hexd = "0123456789abcdef"
	out := make([]byte, len(b)*2)
	for i, c := range b {
		out[2*i] = hexd[c>>4]
		out[2*i+1] = hexd[c&0x0F]
	}
	return string(out)
}
