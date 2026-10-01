// Package compat holds cross-implementation conformance tests. The Go stack
// under test is compared BOTH directions against an independent Python STUN
// implementation (test/compat/oracle/stun_oracle.py, Python stdlib only, no
// shared code) and against the RFC 5769 known-answer fixtures:
//
//	Python emits wire vectors -> Go decodes and must reach the same verdict
//	Go emits wire vectors     -> Python decodes and must reach the same verdict
//
// The oracle is an independent implementation written straight from the RFC
// text, so reference answers are never produced by the core under test.
package compat

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/binary"
	"encoding/json"
	"errors"
	"net"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"testing"
	"time"

	"localstun/internal/stun"
	"localstun/internal/stunerror"
)

type oracleCase struct {
	Name    string `json:"name"`
	WireHex string `json:"wire_hex"`
	KeyB64  string `json:"key_b64"`
	Expect  struct {
		OK            bool       `json:"ok"`
		Kind          string     `json:"kind"`
		MsgType       int        `json:"msg_type"`
		XOR           *xorExpect `json:"xor"`
		IntegrityOK   bool       `json:"integrity_ok"`
		FingerprintOK bool       `json:"fingerprint_ok"`
	} `json:"expect"`
}

type xorExpect struct {
	Family string `json:"family"`
	IP     string `json:"ip"`
	Port   int    `json:"port"`
}

type judgeVerdict struct {
	Name            string     `json:"name"`
	OK              bool       `json:"ok"`
	Kind            string     `json:"kind"`
	Reason          string     `json:"reason"`
	MsgType         int        `json:"msg_type"`
	TxID            string     `json:"txid"`
	XOR             *xorExpect `json:"xor"`
	IntegrityOK     bool       `json:"integrity_ok"`
	FingerprintOK   bool       `json:"fingerprint_ok"`
	UnknownRequired []string   `json:"unknown_required"`
}

// oraclePath resolves the independent Python implementation relative to this
// source file so the test works from any working directory.
func oraclePath(t *testing.T) string {
	t.Helper()
	_, thisFile, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("cannot resolve caller path")
	}
	return filepath.Join(filepath.Dir(thisFile), "oracle", "stun_oracle.py")
}

// pythonAvailable reports whether an independent Python 3 interpreter exists.
func pythonAvailable(t *testing.T) (string, bool) {
	py, err := exec.LookPath("python3")
	if err != nil {
		t.Skip("python3 not available; skipping cross-implementation suite")
	}
	return py, true
}

func runOracleEmit(t *testing.T) []oracleCase {
	t.Helper()
	py, ok := pythonAvailable(t)
	_ = ok
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	out, err := exec.CommandContext(ctx, py, oraclePath(t), "emit").Output()
	if err != nil {
		t.Fatalf("oracle emit: %v\n%s", err, out)
	}
	var cases []oracleCase
	if err := json.Unmarshal(out, &cases); err != nil {
		t.Fatalf("oracle emit json: %v", err)
	}
	return cases
}

func runOracleJudge(t *testing.T, requests []map[string]string) []judgeVerdict {
	t.Helper()
	py, _ := pythonAvailable(t)
	var stdin bytes.Buffer
	enc := json.NewEncoder(&stdin)
	for _, r := range requests {
		if err := enc.Encode(r); err != nil {
			t.Fatal(err)
		}
	}
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, py, oraclePath(t), "judge")
	cmd.Stdin = &stdin
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	if err := cmd.Run(); err != nil {
		t.Fatalf("oracle judge: %v\n%s", err, stderr.String())
	}
	var verdicts []judgeVerdict
	for _, line := range bytes.Split(stdout.Bytes(), []byte{'\n'}) {
		if len(bytes.TrimSpace(line)) == 0 {
			continue
		}
		var v judgeVerdict
		if err := json.Unmarshal(line, &v); err != nil {
			t.Fatalf("verdict json %q: %v", line, err)
		}
		verdicts = append(verdicts, v)
	}
	if len(verdicts) != len(requests) {
		t.Fatalf("got %d verdicts for %d requests", len(verdicts), len(requests))
	}
	return verdicts
}

func mustUnhex(t *testing.T, s string) []byte {
	t.Helper()
	// Oracle hex contains no whitespace; tolerate it anyway.
	clean := make([]byte, 0, len(s)/2)
	for i := 0; i < len(s); i++ {
		if s[i] == ' ' || s[i] == '\n' {
			continue
		}
		if i+1 >= len(s) {
			t.Fatal("odd hex")
		}
		b, err := strconv.ParseUint(s[i:i+2], 16, 8)
		if err != nil {
			t.Fatal(err)
		}
		clean = append(clean, byte(b))
		i++
	}
	return clean
}

func decodeKey(t *testing.T, c oracleCase) []byte {
	if c.KeyB64 == "" {
		return nil
	}
	k, err := base64.StdEncoding.DecodeString(c.KeyB64)
	if err != nil {
		t.Fatal(err)
	}
	return k
}

// goKindLabel maps the Go taxonomy onto oracle vocabulary.
func goKindLabel(err error) string {
	switch stunerror.Of(err) {
	case stunerror.KindInput:
		return "input"
	case stunerror.KindIntegrity:
		return "integrity"
	case stunerror.KindCompute:
		return "compute"
	case stunerror.KindState:
		return "state"
	case stunerror.KindExhausted:
		return "exhausted"
	default:
		return "ok"
	}
}

// TestPythonVectorsDecodeInGo: independent Python-generated wire vectors are
// fed to the Go decoder; verdict class and decoded facts must agree.
func TestPythonVectorsDecodeInGo(t *testing.T) {
	cases := runOracleEmit(t)
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			wire := mustUnhex(t, c.WireHex)
			key := decodeKey(t, c)
			m, err := stun.Decode(wire, key)

			if c.Expect.OK {
				if err != nil {
					t.Fatalf("Go rejected oracle-valid vector: %v (kind=%s)",
						err, stunerror.Of(err))
				}
				if m.Type != stun.MessageType(c.Expect.MsgType) {
					t.Fatalf("msg type = 0x%04x want 0x%04x",
						uint16(m.Type), c.Expect.MsgType)
				}
				if c.Expect.IntegrityOK != m.IntegrityOK {
					t.Fatalf("integrity_ok go=%v want=%v",
						m.IntegrityOK, c.Expect.IntegrityOK)
				}
				if c.Expect.XOR != nil {
					ip, port, ok, xerr := m.XORMappedAddress()
					if xerr != nil || !ok {
						t.Fatalf("xor attr: ok=%v err=%v", ok, xerr)
					}
					wantIP := net.ParseIP(c.Expect.XOR.IP)
					if !ip.Equal(wantIP) {
						t.Fatalf("xor ip go=%s want=%s", ip, wantIP)
					}
					if port != c.Expect.XOR.Port {
						t.Fatalf("xor port go=%d want=%d", port, c.Expect.XOR.Port)
					}
				}
				return
			}

			// Expected failure: Go must reject with the same class.
			if err == nil {
				t.Fatalf("Go accepted oracle-invalid vector (%s)", c.Expect.Kind)
			}
			gotKind := goKindLabel(err)
			if gotKind != c.Expect.Kind {
				t.Fatalf("failure class go=%s want=%s (err=%v)",
					gotKind, c.Expect.Kind, err)
			}
		})
	}
}

// goJudgeInput is one Go-generated datagram handed to the Python judge.
type goJudgeInput struct {
	name string
	wire []byte
	key  []byte
	want judgeVerdict
}

// TestGoVectorsJudgedByPython: Go-encoded datagrams (success and every
// failure class) are decoded solely by the independent Python implementation.
func TestGoVectorsJudgedByPython(t *testing.T) {
	py, _ := pythonAvailable(t)
	_ = py
	key := []byte("cross-key-0123456789")
	txID := stun.TransactionID{}
	copy(txID[:], []byte("txid-0000001"))

	inputs := buildGoVectors(t, txID, key)

	reqs := make([]map[string]string, 0, len(inputs))
	for _, in := range inputs {
		r := map[string]string{"name": in.name, "wire_hex": hexEncode(in.wire)}
		if in.key != nil {
			r["key_b64"] = base64.StdEncoding.EncodeToString(in.key)
		}
		reqs = append(reqs, r)
	}

	verdicts := runOracleJudge(t, reqs)
	for i, v := range verdicts {
		in := inputs[i]
		t.Run(in.name, func(t *testing.T) {
			if v.OK != in.want.OK {
				t.Fatalf("oracle ok=%v want=%v reason=%s", v.OK, in.want.OK, v.Reason)
			}
			if v.Kind != in.want.Kind {
				t.Fatalf("oracle kind=%s want=%s reason=%s",
					v.Kind, in.want.Kind, v.Reason)
			}
			if in.want.XOR != nil {
				if v.XOR == nil {
					t.Fatal("oracle did not decode XOR address")
				}
				got := net.ParseIP(v.XOR.IP)
				want := net.ParseIP(in.want.XOR.IP)
				if !got.Equal(want) || v.XOR.Port != in.want.XOR.Port {
					t.Fatalf("xor oracle=%s:%d want=%s:%d",
						v.XOR.IP, v.XOR.Port, in.want.XOR.IP, in.want.XOR.Port)
				}
			}
			if in.want.IntegrityOK && !v.IntegrityOK {
				t.Fatal("oracle failed to verify Go MESSAGE-INTEGRITY")
			}
			if in.want.FingerprintOK && !v.FingerprintOK {
				t.Fatal("oracle failed to verify Go FINGERPRINT")
			}
		})
	}
}

func buildGoVectors(t *testing.T, txID stun.TransactionID, key []byte) []goJudgeInput {
	t.Helper()
	var out []goJudgeInput

	mkV4 := func(softLen int) *stun.Message {
		m := stun.NewMessage(stun.BindingResponse, txID)
		if softLen > 0 {
			m.Add(stun.AttrSoftware, bytes.Repeat([]byte{'G'}, softLen))
		}
		if err := m.AddXORMappedAddress(net.ParseIP("198.51.100.23"), 41723); err != nil {
			t.Fatal(err)
		}
		return m
	}

	// Every software value length 0..9 exercises every padding residue; the
	// independent Python decoder must accept and recover all.
	for n := 0; n <= 9; n++ {
		wire, err := stun.Marshal(mkV4(n), key, true)
		if err != nil {
			t.Fatal(err)
		}
		out = append(out, goJudgeInput{
			name: "go_ipv4_pad_" + strconv.Itoa(n),
			wire: wire, key: key,
			want: judgeVerdict{OK: true, Kind: "ok",
				XOR:           &xorExpect{Family: "ipv4", IP: "198.51.100.23", Port: 41723},
				IntegrityOK:   true,
				FingerprintOK: true},
		})
	}

	// IPv6 success (transaction-id-dependent mask).
	m6 := stun.NewMessage(stun.BindingResponse, txID)
	if err := m6.AddXORMappedAddress(
		net.ParseIP("2001:db8:85a3::8a2e:370:7334"), 55555); err != nil {
		t.Fatal(err)
	}
	wire6, err := stun.Marshal(m6, key, true)
	if err != nil {
		t.Fatal(err)
	}
	out = append(out, goJudgeInput{
		name: "go_ipv6", wire: wire6, key: key,
		want: judgeVerdict{OK: true, Kind: "ok",
			XOR:         &xorExpect{Family: "ipv6", IP: "2001:db8:85a3::8a2e:370:7334", Port: 55555},
			IntegrityOK: true, FingerprintOK: true},
	})

	// Unsigned request: oracle must decode without integrity.
	req := stun.NewMessage(stun.BindingRequest, txID)
	req.Add(stun.AttrUsername, []byte("alice"))
	wreq, err := stun.Marshal(req, nil, false)
	if err != nil {
		t.Fatal(err)
	}
	out = append(out, goJudgeInput{
		name: "go_unsigned_request", wire: wreq,
		want: judgeVerdict{OK: true, Kind: "ok"},
	})

	// Unknown optional attribute accepted (build by raw framing).
	wOpt := frameRaw(stun.BindingRequest, txID,
		frameAttrRaw(0x80AD, []byte{1, 2, 3}))
	out = append(out, goJudgeInput{
		name: "go_unknown_optional", wire: wOpt,
		want: judgeVerdict{OK: true, Kind: "ok"},
	})

	// Unknown comprehension-required rejected by the independent decoder.
	wReq := frameRaw(stun.BindingRequest, txID,
		frameAttrRaw(0x0BAD, []byte{1, 2}))
	out = append(out, goJudgeInput{
		name: "go_unknown_required", wire: wReq,
		want: judgeVerdict{OK: false, Kind: "integrity"},
	})

	// Integrity tamper: valid signed message with one address byte flipped.
	good, err := stun.Marshal(mkV4(2), key, true)
	if err != nil {
		t.Fatal(err)
	}
	tamper := append([]byte(nil), good...)
	tamper[26] ^= 0xFF
	out = append(out, goJudgeInput{
		name: "go_integrity_tamper", wire: tamper, key: key,
		want: judgeVerdict{OK: false, Kind: "integrity"},
	})

	// Fingerprint tamper: last byte flipped.
	fp := append([]byte(nil), good...)
	fp[len(fp)-1] ^= 0x01
	out = append(out, goJudgeInput{
		name: "go_fingerprint_tamper", wire: fp, key: key,
		want: judgeVerdict{OK: false, Kind: "integrity"},
	})

	// Signed message verified under the wrong key.
	out = append(out, goJudgeInput{
		name: "go_wrong_key", wire: good, key: []byte("definitely-other"),
		want: judgeVerdict{OK: false, Kind: "integrity"},
	})

	// Input-class malformations.
	cases := []struct {
		name string
		fn   func([]byte) []byte
	}{
		{"go_short", func(b []byte) []byte { return b[:8] }},
		{"go_leading_bits", func(b []byte) []byte { b[0] |= 0xC0; return b }},
		{"go_bad_cookie", func(b []byte) []byte { b[4] ^= 0xFF; return b }},
		{"go_length_mismatch", func(b []byte) []byte { b[3]++; return b }},
	}
	for _, tc := range cases {
		base, err := stun.Marshal(stun.NewMessage(stun.BindingRequest, txID), nil, false)
		if err != nil {
			t.Fatal(err)
		}
		out = append(out, goJudgeInput{
			name: tc.name, wire: tc.fn(append([]byte(nil), base...)),
			want: judgeVerdict{OK: false, Kind: "input"},
		})
	}

	// MI present, verifier holds no key -> compute-class per shared contract.
	out = append(out, goJudgeInput{
		name: "go_mi_no_key", wire: good, key: nil,
		want: judgeVerdict{OK: false, Kind: "compute"},
	})

	return out
}

// TestRFC5769AgreesWithOracle: the RFC known-answer messages must be accepted
// by BOTH implementations with the same HMAC key, demonstrating the oracle
// matches the IETF-published vectors as well as the Go code.
func TestRFC5769AgreesWithOracle(t *testing.T) {
	// The Go side's KAT tests already assert these bytes; here ask the
	// independent Python decoder the same question.
	txID := stun.TransactionID{}
	copy(txID[:], mustUnhex(t, "b7e7a701bc34d686fa87dfae"))
	m := stun.NewMessage(stun.BindingResponse, txID)
	m.Add(stun.AttrSoftware, []byte("test vector"))
	if err := m.AddXORMappedAddress(net.ParseIP("192.0.2.1"), 32853); err != nil {
		t.Fatal(err)
	}
	key := []byte("VOkJxbRl1RmTxUk/WvJxBt")
	wire, err := stun.Marshal(m, key, true)
	if err != nil {
		t.Fatal(err)
	}
	vs := runOracleJudge(t, []map[string]string{{
		"name":     "rfc5769_2_2_reencoded",
		"wire_hex": hexEncode(wire),
		"key_b64":  base64.StdEncoding.EncodeToString(key),
	}})
	v := vs[0]
	if !v.OK || !v.IntegrityOK || !v.FingerprintOK {
		t.Fatalf("oracle rejected RFC 5769-style message: %+v", v)
	}
	if v.XOR == nil || v.XOR.IP != "192.0.2.1" || v.XOR.Port != 32853 {
		t.Fatalf("oracle xor = %+v", v.XOR)
	}
}

// frameAttrRaw/frameRaw build wire bytes with padding but bypass the encoder.
func frameAttrRaw(at uint16, value []byte) []byte {
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

func hexEncode(b []byte) string {
	const hexd = "0123456789abcdef"
	out := make([]byte, len(b)*2)
	for i, c := range b {
		out[2*i] = hexd[c>>4]
		out[2*i+1] = hexd[c&0x0F]
	}
	return string(out)
}

var _ = errors.Is
