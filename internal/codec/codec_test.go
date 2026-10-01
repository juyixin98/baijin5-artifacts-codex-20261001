package codec_test

import (
	"bytes"
	"encoding/hex"
	"io"
	"strings"
	"testing"

	"socks5d.local/socks5d/internal/codec"
)

func TestDecodeMethods_Table(t *testing.T) {
	tests := []struct {
		name    string
		hex     string
		wantErr bool
		reason  codec.Reason
		methods []byte
	}{
		{"no_auth", "050100", false, "", []byte{0x00}},
		{"userpass", "050102", false, "", []byte{0x02}},
		{"both_methods", "05020002", false, "", []byte{0x00, 0x02}},
		{"empty_truncated", "05", true, codec.ReasonTruncated, nil},
		{"zero_methods", "0500", true, codec.ReasonNoMethods, nil},
		{"wrong_version", "040100", true, codec.ReasonUnsupportedVersion, nil},
		{"declared_more_than_present", "05030002", true, codec.ReasonTruncated, nil},
		{"trailing_bytes", "050100ff", true, codec.ReasonTruncated, nil},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			raw, _ := hex.DecodeString(tc.hex)
			got, err := codec.DecodeMethods(raw)
			if tc.wantErr {
				de, ok := codec.IsDecodeError(err)
				if !ok {
					t.Fatalf("want DecodeError, got %T %v", err, err)
				}
				if de.Reason != tc.reason {
					t.Fatalf("reason = %q, want %q", de.Reason, tc.reason)
				}
				return
			}
			if err != nil {
				t.Fatalf("unexpected error: %v", err)
			}
			if !bytes.Equal(got.Methods, tc.methods) {
				t.Fatalf("methods = %x, want %x", got.Methods, tc.methods)
			}
		})
	}
}

func TestDecodeRequest_AddressLengths(t *testing.T) {
	tests := []struct {
		name   string
		hex    string
		reason codec.Reason
		ok     bool
		host   string
		port   uint16
	}{
		{"ipv4_ok", "050100017f0000010438", "", true, "127.0.0.1", 1080},
		{"ipv4_short", "050100017f00000104", codec.ReasonIPv4Length, false, "", 0},
		{"ipv4_long", "050100017f000001010438", codec.ReasonIPv4Length, false, "", 0},
		{"ipv6_ok", "05010004000000000000000000000000000000010438", "", true, "::1", 1080},
		{"ipv6_short", "0501000400000000000000000000000000000104", codec.ReasonIPv6Length, false, "", 0},
		{"ipv6_long", "0501000400000000000000000000000000000000010438", codec.ReasonIPv6Length, false, "", 0},
		{"domain_ok", "050100030a6563686f2e6c6f63616c0438", "", true, "echo.local", 1080},
		{"domain_empty", "05010003000438", codec.ReasonDomainEmpty, false, "", 0},
		{"domain_truncated", "05010003096563686f", codec.ReasonTruncated, false, "", 0},
		{"unknown_atyp", "050100057f0000010438", codec.ReasonUnsupportedAtyp, false, "", 0},
		{"wrong_version", "040100017f0000010438", codec.ReasonUnsupportedVersion, false, "", 0},
		{"bad_reserved", "050101017f0000010438", codec.ReasonBadReserved, false, "", 0},
		{"bind_not_connect", "050200017f0000010438", codec.ReasonUnsupportedCommand, false, "", 0},
		{"udp_associate", "050300017f0000010438", codec.ReasonUnsupportedCommand, false, "", 0},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			raw, _ := hex.DecodeString(tc.hex)
			req, err := codec.DecodeRequest(raw)
			if tc.ok {
				if err != nil {
					t.Fatalf("unexpected error: %v", err)
				}
				if req.Dest.String() != hostPort(tc.host, tc.port) {
					t.Fatalf("dest = %q want %s:%d", req.Dest.String(), tc.host, tc.port)
				}
				return
			}
			de, ok := codec.IsDecodeError(err)
			if !ok {
				t.Fatalf("want DecodeError, got %v", err)
			}
			if de.Reason != tc.reason {
				t.Fatalf("reason = %q want %q (%v)", de.Reason, tc.reason, err)
			}
		})
	}
}

func hostPort(host string, port uint16) string {
	if strings.Contains(host, ":") && !strings.HasPrefix(host, "[") {
		return "[" + host + "]:" + uitoa(int(port))
	}
	return host + ":" + uitoa(int(port))
}

func uitoa(n int) string {
	if n == 0 {
		return "0"
	}
	var b [12]byte
	i := len(b)
	for n > 0 {
		i--
		b[i] = byte('0' + n%10)
		n /= 10
	}
	return string(b[i:])
}

func TestUserPass(t *testing.T) {
	frame := codec.EncodeUserPass("alice", "s3cret")
	got, err := codec.DecodeUserPass(frame)
	if err != nil {
		t.Fatalf("decode: %v", err)
	}
	if got.Username != "alice" || got.Password != "s3cret" {
		t.Fatalf("creds = %q/%q", got.Username, got.Password)
	}
	// Wrong sub-negotiation version is a distinct failure.
	bad := append([]byte{0x02}, frame[1:]...)
	if _, err := codec.DecodeUserPass(bad); err == nil {
		t.Fatal("expected error for subneg version 2")
	} else if de, ok := codec.IsDecodeError(err); !ok || de.Reason != codec.ReasonBadUserPassVersion {
		t.Fatalf("reason = %v", err)
	}
}

// TestStreamReaders_ByteAtATime proves segmentation tolerance: a reader that
// yields exactly one byte per Read must still parse every frame correctly.
type oneByteReader struct{ data []byte }

func (r *oneByteReader) Read(p []byte) (int, error) {
	if len(r.data) == 0 {
		return 0, io.EOF
	}
	p[0] = r.data[0]
	r.data = r.data[1:]
	return 1, nil
}
func TestStreamReaders_ByteAtATime(t *testing.T) {
	greet := codec.EncodeMethods(0x00, 0x02)
	mr, err := codec.ReadMethods(&oneByteReader{data: greet})
	if err != nil {
		t.Fatalf("methods: %v", err)
	}
	if len(mr.Methods) != 2 {
		t.Fatalf("methods = %x", mr.Methods)
	}

	up := codec.EncodeUserPass("u", "p")
	if _, err := codec.ReadUserPass(&oneByteReader{data: up}); err != nil {
		t.Fatalf("userpass: %v", err)
	}

	req := codec.EncodeRequest(codec.CmdConnect, codec.DomainAddr("echo.local", 1080))
	rq, err := codec.ReadRequest(&oneByteReader{data: req})
	if err != nil {
		t.Fatalf("request: %v", err)
	}
	if rq.Dest.Domain != "echo.local" || rq.Dest.Port != 1080 {
		t.Fatalf("dest = %q", rq.Dest.String())
	}

	v6 := codec.EncodeRequest(codec.CmdConnect, codec.IPv6Addr("::1", 1080))
	r6, err := codec.ReadRequest(&oneByteReader{data: v6})
	if err != nil {
		t.Fatalf("v6 request: %v", err)
	}
	if r6.Dest.IP.String() != "::1" {
		t.Fatalf("v6 = %s", r6.Dest.IP)
	}
}

// TestStreamReaders_Truncated asserts a mid-frame EOF maps to truncated.
func TestStreamReaders_Truncated(t *testing.T) {
	if _, err := codec.ReadRequest(bytes.NewReader(hexMust("050100030965"))); err == nil {
		t.Fatal("expected truncated")
	} else if de, ok := codec.IsDecodeError(err); !ok || de.Reason != codec.ReasonTruncated {
		t.Fatalf("reason = %v", err)
	}
}

// TestEncodeReplyRoundTrip checks the exact success reply length and bytes.
func TestEncodeReply_SuccessExactBytes(t *testing.T) {
	reply := codec.EncodeReply(codec.RepSucceeded, codec.ZeroBindAddr())
	if len(reply) != 10 {
		t.Fatalf("success reply len = %d want 10", len(reply))
	}
	want := "05000001000000000000"
	if hex.EncodeToString(reply) != want {
		t.Fatalf("reply = %x want %s", reply, want)
	}
}

// TestNoAcceptableExactBytes locks the method-failure frame.
func TestNoAcceptableExactBytes(t *testing.T) {
	if got := hex.EncodeToString(codec.EncodeNoAcceptable()); got != "05ff" {
		t.Fatalf("no-acceptable = %s want 05ff", got)
	}
}

func hexMust(s string) []byte {
	b, err := hex.DecodeString(s)
	if err != nil {
		panic(err)
	}
	return b
}
