package wire

import (
	"errors"
	"net/netip"
	"testing"
)

func TestDecodeGreeting(t *testing.T) {
	cases := []struct {
		name    string
		frame   []byte
		wantErr error
		want    []byte
	}{
		{"ok no-auth", []byte{5, 1, 0}, nil, []byte{0}},
		{"ok two methods", []byte{5, 2, 0, 2}, nil, []byte{0, 2}},
		{"empty", []byte{}, ErrShortFrame, nil},
		{"one byte", []byte{5}, ErrShortFrame, nil},
		{"bad version", []byte{4, 1, 0}, ErrBadVersion, nil},
		{"nmethods zero", []byte{5, 0}, ErrEmptyMethodL, nil},
		{"truncated methods", []byte{5, 3, 0, 2}, ErrShortFrame, nil},
		{"extra bytes", []byte{5, 1, 0, 9}, ErrExtraBytes, nil},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			g, err := DecodeGreeting(tc.frame)
			if !errors.Is(err, tc.wantErr) {
				t.Fatalf("err = %v, want %v", err, tc.wantErr)
			}
			if tc.wantErr == nil && string(g.Methods) != string(tc.want) {
				t.Fatalf("methods = %v, want %v", g.Methods, tc.want)
			}
		})
	}
}

func TestGreetingLen(t *testing.T) {
	n, err := GreetingLen([]byte{5, 3})
	if err != nil || n != 5 {
		t.Fatalf("got (%d,%v)", n, err)
	}
	if _, err := GreetingLen([]byte{5}); !errors.Is(err, ErrShortFrame) {
		t.Fatalf("short header: %v", err)
	}
	if _, err := GreetingLen([]byte{5, 0}); !errors.Is(err, ErrEmptyMethodL) {
		t.Fatalf("zero methods: %v", err)
	}
}

func TestDecodeUserPass(t *testing.T) {
	cases := []struct {
		name    string
		frame   []byte
		wantErr error
		user    string
		pass    string
	}{
		{"ok", []byte{1, 3, 'a', 'b', 'c', 2, 'x', 'y'}, nil, "abc", "xy"},
		{"max length", append(append([]byte{1, 255}, make255('u')...), append([]byte{255}, make255('p')...)...), nil, string(make255('u')), string(make255('p'))},
		{"short header", []byte{1}, ErrShortFrame, "", ""},
		{"bad version", []byte{2, 1, 'a', 1, 'b'}, ErrBadMethodVer, "", ""},
		{"ulen zero", []byte{1, 0, 1, 'b'}, ErrBadDomainLen, "", ""},
		{"truncated username", []byte{1, 4, 'a', 1, 'b'}, ErrShortFrame, "", ""},
		{"plen zero", []byte{1, 1, 'a', 0}, ErrBadDomainLen, "", ""},
		{"truncated password", []byte{1, 1, 'a', 3, 'b'}, ErrShortFrame, "", ""},
		{"extra byte", []byte{1, 1, 'a', 1, 'b', 0}, ErrExtraBytes, "", ""},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			up, err := DecodeUserPass(tc.frame)
			if !errors.Is(err, tc.wantErr) {
				t.Fatalf("err = %v, want %v", err, tc.wantErr)
			}
			if tc.wantErr == nil && (up.Username != tc.user || up.Password != tc.pass) {
				t.Fatalf("got (%q,%q), want (%q,%q)", up.Username, up.Password, tc.user, tc.pass)
			}
		})
	}
}

func make255(c byte) []byte {
	b := make([]byte, 255)
	for i := range b {
		b[i] = c
	}
	return b
}

func TestDecodeRequestIPv4(t *testing.T) {
	// VER=5 CMD=1 RSV=0 ATYP=1 1.2.3.4 PORT=0x1f90(8080)
	frame := []byte{5, 1, 0, 1, 1, 2, 3, 4, 0x1f, 0x90}
	req, err := DecodeRequest(frame)
	if err != nil {
		t.Fatal(err)
	}
	if req.Command != CmdConnect || req.Target.ATyp != ATypIPv4 || req.Target.Port != 8080 {
		t.Fatalf("bad request: %+v", req)
	}
	if req.Target.Addr != netip.MustParseAddr("1.2.3.4") {
		t.Fatalf("addr = %v", req.Target.Addr)
	}
	if got := req.Target.String(); got != "1.2.3.4:8080" {
		t.Fatalf("String = %q", got)
	}
}

func TestDecodeRequestIPv6(t *testing.T) {
	frame := []byte{5, 1, 0, 4}
	// 2001:db8::1
	frame = append(frame, 0x20, 0x01, 0x0d, 0xb8, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1)
	frame = append(frame, 0x00, 0x50) // port 80
	req, err := DecodeRequest(frame)
	if err != nil {
		t.Fatal(err)
	}
	if req.Target.ATyp != ATypIPv6 || req.Target.Port != 80 {
		t.Fatalf("bad: %+v", req.Target)
	}
	if req.Target.Addr != netip.MustParseAddr("2001:db8::1") {
		t.Fatalf("addr = %v", req.Target.Addr)
	}
}

func TestDecodeRequestDomain(t *testing.T) {
	frame := []byte{5, 1, 0, 3, 9, 'l', 'o', 'c', 'a', 'l', 'h', 'o', 's', 't', 0x1f, 0x90}
	req, err := DecodeRequest(frame)
	if err != nil {
		t.Fatal(err)
	}
	if req.Target.Domain != "localhost" || req.Target.Port != 8080 {
		t.Fatalf("bad: %+v", req.Target)
	}
	if req.Target.Host() != "localhost" {
		t.Fatalf("Host = %q", req.Target.Host())
	}

	bad := [][]byte{
		{5, 1, 0, 3, 0, 0, 0},              // zero length
		{5, 1, 0, 3, 4, 'a', 'b', 0, 1, 2}, // declared 4, only 2 chars
		{5, 1, 0, 3, 2, 'a', 'b', 1, 2, 3}, // extra byte
	}
	for i, f := range bad {
		if _, err := DecodeRequest(f); err == nil {
			t.Fatalf("bad domain case %d accepted", i)
		}
	}
}

func TestDecodeRequestBadFields(t *testing.T) {
	bad := [][]byte{
		{4, 1, 0, 1, 1, 2, 3, 4, 0, 80}, // version
		{5, 1, 1, 1, 1, 2, 3, 4, 0, 80}, // reserved
		{5, 1, 0, 9, 1, 2, 3, 4, 0, 80}, // atyp
		{5, 1, 0, 1, 1, 2, 3, 0, 80},    // ipv4 too short (3 addr bytes)
		{5, 1, 0},                       // header short
	}
	want := []error{ErrBadVersion, ErrBadReserved, ErrBadATyp, nil, ErrShortFrame}
	for i, f := range bad {
		_, err := DecodeRequest(f)
		if want[i] == nil {
			if err == nil {
				t.Fatalf("case %d unexpectedly accepted", i)
			}
			continue
		}
		if !errors.Is(err, want[i]) {
			t.Fatalf("case %d: err = %v, want %v", i, err, want[i])
		}
	}
}

func TestRequestBodyLen(t *testing.T) {
	if n, err := RequestBodyLen([]byte{5, 1, 0, 1}); err != nil || n != 10 {
		t.Fatalf("ipv4: %d %v", n, err)
	}
	if n, err := RequestBodyLen([]byte{5, 1, 0, 4}); err != nil || n != 22 {
		t.Fatalf("ipv6: %d %v", n, err)
	}
	if n, err := RequestBodyLen([]byte{5, 1, 0, 3, 9}); err != nil || n != 4+1+9+2 {
		t.Fatalf("domain: %d %v", n, err)
	}
	if _, err := RequestBodyLen([]byte{5, 1, 0, 9}); !errors.Is(err, ErrBadATyp) {
		t.Fatalf("bad atyp: %v", err)
	}
	if _, err := RequestBodyLen([]byte{5, 1, 0, 3}); !errors.Is(err, ErrShortFrame) {
		t.Fatalf("missing dlen: %v", err)
	}
}

func TestEncodeRoundTrip(t *testing.T) {
	if got := EncodeMethodSelection(MethodNoAuth); string(got) != "\x05\x00" {
		t.Fatalf("method selection = %v", got)
	}
	if got := EncodeAuthStatus(AuthStatusFail); string(got) != "\x01\x01" {
		t.Fatalf("auth fail = %v", got)
	}
	ap := netip.MustParseAddrPort("127.0.0.1:8080")
	rep := EncodeReply(RepSucceeded, TargetFromAddrPort(ap))
	want := []byte{5, 0, 0, 1, 127, 0, 0, 1, 0x1f, 0x90}
	if string(rep) != string(want) {
		t.Fatalf("reply = %v, want %v", rep, want)
	}
	fail := FailureReply(RepConnectionNotAllowed)
	if fail[1] != RepConnectionNotAllowed || fail[3] != ATypIPv4 {
		t.Fatalf("failure reply = %v", fail)
	}
	for i := 4; i < len(fail); i++ {
		if fail[i] != 0 {
			t.Fatalf("failure reply BND not zero at %d: %v", i, fail)
		}
	}
}

func TestTargetFromAddrPortIPv6(t *testing.T) {
	// A v4-mapped IPv6 must be unmaped so the reply uses ATYP=1.
	mapped := netip.MustParseAddrPort("[::ffff:1.2.3.4]:443")
	got := TargetFromAddrPort(mapped)
	if got.ATyp != ATypIPv4 || !got.Addr.Is4() {
		t.Fatalf("mapped address not normalized: %+v", got)
	}
	v6 := netip.MustParseAddrPort("[2001:db8::2]:443")
	got = TargetFromAddrPort(v6)
	if got.ATyp != ATypIPv6 || got.Addr.String() != "2001:db8::2" {
		t.Fatalf("v6 = %+v", got)
	}
}
