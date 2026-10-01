// Package proto contains the SOCKS5 session state machine. It drives the
// byte-level decoder of internal/wire through the phases method negotiation,
// RFC 1929 authentication and CONNECT request, and maps every terminal
// condition to an explicit Kind so callers never infer failure class from
// operating-system error strings.
//
// The machine performs no policy decisions itself: authentication is
// delegated to an Authenticator and target validation/dialing to a
// Connecter. The controlled server in internal/server wires both together.
package proto

import (
	"context"
	"errors"
	"io"
	"net"
	"net/netip"
	"os"
	"time"

	"sockswhitelist/internal/wire"
)

// Authenticator validates RFC 1929 credentials. A false result with a nil
// error means the credentials were checked and rejected.
type Authenticator interface {
	Authenticate(ctx context.Context, username, password string) (bool, error)
}

// DialResult is a successful policy check and upstream connection.
type DialResult struct {
	Conn     net.Conn
	Bound    wire.Target   // concrete local address of Conn, for BND.ADDR
	Resolved []netip.Addr  // every address the name resolved to (logging)
	Attempts []DialAttempt // every address actually attempted, in order
}

// DialAttempt records one connect try for explainable logging.
type DialAttempt struct {
	AddrPort netip.AddrPort
	Outcome  string // "connected", "refused", "timeout", "unreachable", "failed"
	Err      string
}

// Connecter validates a target against policy, resolves it and dials the
// first reachable address. Policy denial and resolution/dial failures are
// returned as categorized *Failure values.
type Connecter interface {
	Connect(ctx context.Context, target wire.Target) (*DialResult, *Failure)
}

// Options configures a Machine.
type Options struct {
	// RequireAuth selects the negotiated method: when true the server only
	// offers username/password; otherwise only no-authentication.
	RequireAuth bool
	// HandshakeTimeout bounds each individual blocking read and each
	// protocol-frame write during negotiation. Local work (password
	// hashing, and the independently bounded resolve/dial) is not counted.
	HandshakeTimeout time.Duration
}

// Machine is safe for concurrent use across sessions.
type Machine struct {
	auth    Authenticator
	connect Connecter
	opts    Options
}

// NewMachine builds a state machine. auth must be non-nil when
// opts.RequireAuth is true.
func NewMachine(auth Authenticator, connect Connecter, opts Options) *Machine {
	return &Machine{auth: auth, connect: connect, opts: opts}
}

// SessionOutcome is the terminal result of one client handshake.
type SessionOutcome struct {
	Stage  Stage
	Kind   Kind
	Method byte // method actually negotiated (0x00 / 0x02)
	User   string

	// Set after a well-formed request.
	Target *wire.Target

	// Set on successful connect; ownership of Upstream.Conn passes to the
	// caller, which closes it (relay on success, this function on error).
	Upstream *DialResult

	// Bytes received while negotiating, useful for explainable logs.
	GreetingBytes int
	AuthBytes     int
	RequestBytes  int

	// Detail is the human-readable explanation carried by the terminal
	// failure; empty on success.
	Detail string
	// Failure is the categorized terminal error for advanced callers.
	Failure *Failure
}

// Handshake runs the full negotiation on conn. On a KindRelayReady result
// both conn and outcome.Upstream are owned by the caller. On every other
// result both sockets are closed by Handshake.
//
// Bounding model: each blocking read and each protocol-frame write gets its
// own HandshakeTimeout deadline, reset right after. This bounds a slow or
// zero-window client per operation, while purely local work — PBKDF2
// verification, and the independently time-bounded resolve/dial — never
// consumes the negotiation budget.
func (m *Machine) Handshake(ctx context.Context, conn net.Conn) *SessionOutcome {
	_ = conn.SetDeadline(time.Time{}) // start from a clean slate
	out := &SessionOutcome{Stage: StageMethod}

	method, f := m.negotiateMethod(ctx, conn, out)
	if f != nil {
		m.closeAfter(conn, nil, out, f)
		return out
	}
	out.Method = method

	if method == wire.MethodUserPass {
		out.Stage = StageAuth
		user, f := m.authenticate(ctx, conn, out)
		if f != nil {
			m.closeAfter(conn, nil, out, f)
			return out
		}
		out.User = user
	}

	out.Stage = StageRequest
	req, f := m.readRequest(ctx, conn, out)
	if f != nil {
		m.closeAfter(conn, nil, out, f)
		return out
	}
	target := req.Target
	out.Target = &target

	if req.Command != wire.CmdConnect {
		m.writeFrame(conn, wire.FailureReply(wire.RepCommandNotSupported))
		out.Kind = KindUnsupportedCommand
		m.closeAfter(conn, nil, out, nil)
		return out
	}

	out.Stage = StageConnect
	dial, f := m.connectAndReply(ctx, conn, target)
	if f != nil {
		out.Kind = f.Kind
		m.closeAfter(conn, nil, out, f)
		return out
	}

	out.Stage = StageRelay
	out.Kind = KindRelayReady
	out.Upstream = dial
	return out
}

// connectAndReply runs the connector and writes the terminal CONNECT reply.
// Resolve/dial carry their own context timeouts, so the negotiation
// deadline is cleared for their duration and reapplied only to the reply.
// On a failed reply after a successful dial the upstream is closed here.
func (m *Machine) connectAndReply(ctx context.Context, conn net.Conn, target wire.Target) (*DialResult, *Failure) {
	_ = conn.SetDeadline(time.Time{})
	dial, f := m.connect.Connect(ctx, target)
	if f != nil {
		_, _ = m.writeFrame(conn, wire.FailureReply(ReplyCode(f.Kind)))
		return nil, f
	}
	if _, ferr := m.writeFrame(conn, wire.EncodeReply(wire.RepSucceeded, dial.Bound)); ferr != nil {
		_ = dial.Conn.Close()
		k := classifyNet(ferr)
		return nil, NewFailure(k, "write success reply: %v", ferr)
	}
	_ = conn.SetDeadline(time.Time{})
	return dial, nil
}

// KindRelayReady marks a handshake that completed and entered relay phase.
const KindRelayReady Kind = "relay_ready"

// closeAfter finalizes a failed/terminal handshake: sends nothing more,
// records the failure, closes both sockets.
func (m *Machine) closeAfter(client net.Conn, upstream net.Conn, out *SessionOutcome, f *Failure) {
	_ = client.Close()
	if upstream != nil {
		_ = upstream.Close()
	}
	if f != nil {
		out.Failure = f
		out.Detail = f.Err.Error()
		if out.Kind == "" {
			out.Kind = f.Kind
		}
	}
}

// setReadDeadline applies the per-read timeout when configured.
func (m *Machine) setReadDeadline(conn net.Conn) {
	if m.opts.HandshakeTimeout > 0 {
		_ = conn.SetReadDeadline(time.Now().Add(m.opts.HandshakeTimeout))
	}
}

// writeFrame writes a complete protocol frame bounded by the write timeout.
func (m *Machine) writeFrame(conn net.Conn, frame []byte) (int, error) {
	if m.opts.HandshakeTimeout > 0 {
		_ = conn.SetWriteDeadline(time.Now().Add(m.opts.HandshakeTimeout))
	} else {
		_ = conn.SetWriteDeadline(time.Time{})
	}
	return conn.Write(frame)
}

// negotiateMethod performs RFC 1928 §3.
func (m *Machine) negotiateMethod(ctx context.Context, conn net.Conn, out *SessionOutcome) (byte, *Failure) {
	head, f := m.readExact(ctx, conn, 2)
	if f != nil {
		return 0, f
	}
	out.GreetingBytes += 2
	if head[0] != wire.Version5 {
		// Not a SOCKS5 client: no reply is defined at this phase, close.
		return 0, NewFailure(KindProtocolVersion, "client offered VER=0x%02x, want 0x05", head[0])
	}
	n := int(head[1])
	if n == 0 {
		_, _ = m.writeFrame(conn, wire.EncodeMethodSelection(wire.MethodNoAcceptable))
		return 0, NewFailure(KindNoAcceptableMethod, "NMETHODS=0")
	}
	methods, f := m.readExact(ctx, conn, n)
	if f != nil {
		return 0, f
	}
	out.GreetingBytes += n
	greeting, err := wire.DecodeGreeting(append(append([]byte{}, head...), methods...))
	if err != nil {
		return 0, decodeFailure(StageMethod, err)
	}

	want := wire.MethodNoAuth
	if m.opts.RequireAuth {
		want = wire.MethodUserPass
	}
	for _, offered := range greeting.Methods {
		if offered == want {
			if _, err := m.writeFrame(conn, wire.EncodeMethodSelection(want)); err != nil {
				return 0, NewFailure(classifyNet(err), "write method selection: %v", err)
			}
			return want, nil
		}
	}
	// Explicit terminal: tell the client no method is acceptable and stop.
	if _, err := m.writeFrame(conn, wire.EncodeMethodSelection(wire.MethodNoAcceptable)); err != nil {
		return 0, NewFailure(classifyNet(err), "write 0xFF: %v", err)
	}
	return 0, NewFailure(KindNoAcceptableMethod, "client offered %v, server requires 0x%02x", greeting.Methods, want)
}

// authenticate performs RFC 1929 using segmented reads so a frame split
// across TCP segments works byte-for-byte.
func (m *Machine) authenticate(ctx context.Context, conn net.Conn, out *SessionOutcome) (string, *Failure) {
	head, f := m.readExact(ctx, conn, 2)
	if f != nil {
		return "", f
	}
	out.AuthBytes += 2
	if head[0] != wire.UserPassVersion {
		_, _ = m.writeFrame(conn, wire.EncodeAuthStatus(wire.AuthStatusFail))
		return "", NewFailure(KindAuthMalformed, "sub-negotiation VER=0x%02x, want 0x01", head[0])
	}
	ulen := int(head[1])
	if ulen == 0 {
		_, _ = m.writeFrame(conn, wire.EncodeAuthStatus(wire.AuthStatusFail))
		return "", NewFailure(KindAuthMalformed, "ULEN=0")
	}
	user, f := m.readExact(ctx, conn, ulen)
	if f != nil {
		return "", f
	}
	out.AuthBytes += ulen
	plenByte, f := m.readExact(ctx, conn, 1)
	if f != nil {
		return "", f
	}
	out.AuthBytes++
	plen := int(plenByte[0])
	if plen == 0 {
		_, _ = m.writeFrame(conn, wire.EncodeAuthStatus(wire.AuthStatusFail))
		return "", NewFailure(KindAuthMalformed, "PLEN=0")
	}
	pass, f := m.readExact(ctx, conn, plen)
	if f != nil {
		return "", f
	}
	out.AuthBytes += plen

	if _, err := wire.DecodeUserPass(buildAuthFrame(head, user, pass)); err != nil {
		_, _ = m.writeFrame(conn, wire.EncodeAuthStatus(wire.AuthStatusFail))
		return "", decodeFailure(StageAuth, err)
	}

	// No read deadline spans local password verification.
	_ = conn.SetReadDeadline(time.Time{})
	ok, err := m.auth.Authenticate(ctx, string(user), string(pass))
	if err != nil {
		_, _ = m.writeFrame(conn, wire.EncodeAuthStatus(wire.AuthStatusFail))
		return "", NewFailure(KindAuthError, "authenticator: %v", err)
	}
	if !ok {
		_, _ = m.writeFrame(conn, wire.EncodeAuthStatus(wire.AuthStatusFail))
		return "", NewFailure(KindAuthDenied, "credentials rejected for user %q", string(user))
	}
	if _, err := m.writeFrame(conn, wire.EncodeAuthStatus(wire.AuthStatusOK)); err != nil {
		return "", NewFailure(classifyNet(err), "write auth success: %v", err)
	}
	return string(user), nil
}

// readRequest reads a CONNECT request, requiring the domain length byte
// before committing to a body length.
func (m *Machine) readRequest(ctx context.Context, conn net.Conn, out *SessionOutcome) (wire.Request, *Failure) {
	head4, f := m.readExact(ctx, conn, 4)
	if f != nil {
		return wire.Request{}, f
	}
	out.RequestBytes += 4
	if head4[0] != wire.Version5 {
		return wire.Request{}, NewFailure(KindProtocolVersion, "request VER=0x%02x", head4[0])
	}
	if head4[2] != wire.Reserved {
		_, _ = m.writeFrame(conn, wire.FailureReply(wire.RepGeneralFailure))
		return wire.Request{}, NewFailure(KindMalformedFrame, "RSV=0x%02x", head4[2])
	}

	frame := make([]byte, 0, wire.MaxRequestLen)
	frame = append(frame, head4...)
	if head4[3] == wire.ATypDomain {
		dlenByte, f := m.readExact(ctx, conn, 1)
		if f != nil {
			return wire.Request{}, f
		}
		out.RequestBytes++
		frame = append(frame, dlenByte[0])
	}
	bodyLen, err := wire.RequestBodyLen(frame)
	if err != nil {
		if errors.Is(err, wire.ErrBadATyp) {
			_, _ = m.writeFrame(conn, wire.FailureReply(wire.RepAddressNotSupported))
			return wire.Request{}, NewFailure(KindAddressNotSupported, "ATYP=0x%02x", head4[3])
		}
		_, _ = m.writeFrame(conn, wire.FailureReply(wire.RepGeneralFailure))
		return wire.Request{}, decodeFailure(StageRequest, err)
	}
	rest := bodyLen - len(frame)
	if rest > 0 {
		buf, f := m.readExact(ctx, conn, rest)
		if f != nil {
			return wire.Request{}, f
		}
		out.RequestBytes += rest
		frame = append(frame, buf...)
	}
	req, derr := wire.DecodeRequest(frame)
	if derr != nil {
		_, _ = m.writeFrame(conn, wire.FailureReply(wire.RepGeneralFailure))
		return wire.Request{}, decodeFailure(StageRequest, derr)
	}
	return req, nil
}

// ReplyCode maps an internal failure Kind to the SOCKS5 REP byte sent back.
func ReplyCode(k Kind) byte {
	switch k {
	case KindPolicyDenied:
		return wire.RepConnectionNotAllowed
	case KindResolveFailed:
		return wire.RepHostUnreachable
	case KindDialRefused:
		return wire.RepConnectionRefused
	case KindDialUnreachable:
		return wire.RepNetworkUnreachable
	case KindDialTimeout:
		return wire.RepTTLExpired
	default:
		return wire.RepGeneralFailure
	}
}

// readExact reads exactly n bytes with a per-read timeout, turning I/O
// behavior into categories: clean EOF before any byte -> client closed;
// timeout -> handshake deadline; partial frame -> malformed/truncated.
func (m *Machine) readExact(_ context.Context, conn net.Conn, n int) ([]byte, *Failure) {
	m.setReadDeadline(conn)
	buf := make([]byte, n)
	got, err := io.ReadFull(conn, buf)
	_ = conn.SetReadDeadline(time.Time{})
	if err == nil {
		return buf, nil
	}
	if errors.Is(err, io.EOF) {
		return nil, NewFailure(KindClientClosed, "peer closed before sending %d bytes", n)
	}
	if errors.Is(err, os.ErrDeadlineExceeded) {
		return nil, NewFailure(KindHandshakeDeadline, "timeout after %d/%d bytes", got, n)
	}
	var nerr net.Error
	if errors.As(err, &nerr) && nerr.Timeout() {
		return nil, NewFailure(KindHandshakeDeadline, "timeout after %d/%d bytes: %v", got, n, err)
	}
	if errors.Is(err, io.ErrUnexpectedEOF) {
		return nil, NewFailure(KindMalformedFrame, "truncated frame: got %d/%d bytes", got, n)
	}
	return nil, NewFailure(classifyNet(err), "read: %v", err)
}

// classifyNet maps low-level network errors to relay/connect categories.
func classifyNet(err error) Kind {
	if err == nil {
		return KindRelayEOF
	}
	var nerr net.Error
	if errors.As(err, &nerr) && nerr.Timeout() {
		return KindIdleTimeout
	}
	return KindRelayPeerReset
}

func buildAuthFrame(head, user, pass []byte) []byte {
	frame := make([]byte, 0, 2+len(user)+1+len(pass))
	frame = append(frame, head...)
	frame = append(frame, user...)
	frame = append(frame, byte(len(pass)))
	frame = append(frame, pass...)
	return frame
}

// decodeFailure maps wire decoder errors to handshake categories.
func decodeFailure(stage Stage, err error) *Failure {
	switch {
	case errors.Is(err, wire.ErrBadVersion):
		return NewFailure(KindProtocolVersion, "%s: %v", stage, err)
	case errors.Is(err, wire.ErrBadMethodVer):
		return NewFailure(KindAuthMalformed, "%s: %v", stage, err)
	case errors.Is(err, wire.ErrBadATyp):
		return NewFailure(KindAddressNotSupported, "%s: %v", stage, err)
	default:
		return NewFailure(KindMalformedFrame, "%s: %v", stage, err)
	}
}
