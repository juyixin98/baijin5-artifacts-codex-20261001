package proto

import (
	"context"
	"errors"
	"io"
	"net"
	"net/netip"
	"syscall"
	"time"

	"socks5d.local/socks5d/internal/codec"
	"socks5d.local/socks5d/internal/policy"
)

// Handshake runs the server state machine on conn. On success it returns an
// Established session carrying the connected upstream; the caller owns both
// connections thereafter. On failure it returns *TerminalError stating the
// outcome and whether (and which) reply was already sent, then closes nothing
// itself beyond the upstream it opened.
func Handshake(ctx context.Context, conn net.Conn, opts Options) (*Established, error) {
	if opts.Policy == nil {
		return nil, &TerminalError{Outcome: OutcomeProtocolError, Reason: "config", Detail: "nil policy"}
	}
	if opts.Resolver == nil {
		return nil, &TerminalError{Outcome: OutcomeProtocolError, Reason: "config", Detail: "nil resolver"}
	}
	if opts.Dialer == nil {
		return nil, &TerminalError{Outcome: OutcomeProtocolError, Reason: "config", Detail: "nil dialer"}
	}

	// Phase 1: method negotiation.
	greet, err := readGreeting(conn, opts)
	if err != nil {
		return nil, err
	}

	// Phase 2 (optional): username/password sub-negotiation.
	if opts.Auth != nil {
		if err := authenticate(conn, greet, opts); err != nil {
			return nil, err
		}
	} else if !greet.Supports(codec.MethodNoAuth) {
		return nil, rejectMethod(conn)
	} else {
		if err := send(conn, codec.EncodeMethodSelection(codec.MethodNoAuth), opts.Timeouts.Method); err != nil {
			return nil, ioErr(err)
		}
	}

	// Phase 3: request frame.
	var req codec.Request
	if err := withPhase(conn, opts.Timeouts.Req, func() error {
		var err error
		req, err = codec.ReadRequest(conn)
		return err
	}); err != nil {
		return handleRequestDecodeError(conn, err, opts)
	}

	// Phase 4: policy evaluation (resolves domains; returns vetted targets).
	decision := opts.Policy.Evaluate(ctx, req.Dest, opts.Resolver)
	if !decision.Allowed {
		rep := policyReply(decision.Reason)
		if werr := send(conn, codec.EncodeReply(rep, codec.ZeroBindAddr()), opts.Timeouts.Req); werr != nil {
			return nil, ioErr(werr)
		}
		return nil, &TerminalError{
			Outcome: OutcomeRejected, Reason: decision.Reason, Detail: decision.Detail,
			ReplySent: true, Rep: rep,
		}
	}

	// Phase 5: dial only the exact, already-vetted addresses.
	upstream, target, dialErr := dialVetted(ctx, opts.Dialer, decision.Targets, opts.Timeouts.Dial)
	if dialErr != nil {
		rep := dialReply(dialErr)
		_ = send(conn, codec.EncodeReply(rep, codec.ZeroBindAddr()), opts.Timeouts.Req)
		return nil, &TerminalError{
			Outcome: OutcomeDialFailed, Reason: "dial_failed",
			Detail: dialErr.Error(), ReplySent: true, Rep: rep,
		}
	}

	// Phase 6: success reply. Only after the upstream is truly connected.
	if err := send(conn, codec.EncodeReply(codec.RepSucceeded, codec.ZeroBindAddr()), opts.Timeouts.Req); err != nil {
		_ = upstream.Close()
		return nil, ioErr(err)
	}
	return &Established{Upstream: upstream, Target: target, Rep: codec.RepSucceeded}, nil
}

func readGreeting(conn net.Conn, opts Options) (codec.MethodRequest, error) {
	var greet codec.MethodRequest
	err := withPhase(conn, opts.Timeouts.Method, func() error {
		var e error
		greet, e = codec.ReadMethods(conn)
		return e
	})
	if err != nil {
		return codec.MethodRequest{}, classifyDecode(err, OutcomeProtocolError)
	}
	return greet, nil
}

func rejectMethod(conn net.Conn) error {
	if err := send(conn, codec.EncodeNoAcceptable(), 0); err != nil {
		return ioErr(err)
	}
	return &TerminalError{
		Outcome: OutcomeNoAcceptableMethod, Reason: "method_not_offered",
		Detail:    "client offered neither no-auth nor username/password",
		ReplySent: true,
	}
}

func authenticate(conn net.Conn, greet codec.MethodRequest, opts Options) error {
	if !greet.Supports(codec.MethodUserPass) {
		if err := send(conn, codec.EncodeNoAcceptable(), opts.Timeouts.Method); err != nil {
			return ioErr(err)
		}
		return &TerminalError{
			Outcome: OutcomeNoAcceptableMethod, Reason: "userpass_not_offered",
			Detail:    "server requires username/password but client did not offer method 0x02",
			ReplySent: true,
		}
	}
	if err := send(conn, codec.EncodeMethodSelection(codec.MethodUserPass), opts.Timeouts.Method); err != nil {
		return ioErr(err)
	}
	var creds codec.UserPassRequest
	if err := withPhase(conn, opts.Timeouts.Auth, func() error {
		var e error
		creds, e = codec.ReadUserPass(conn)
		return e
	}); err != nil {
		return classifyDecode(err, OutcomeProtocolError)
	}
	if !opts.Auth.Authenticate(creds.Username, creds.Password) {
		if err := send(conn, codec.EncodeUserPassReply(codec.UserPassFailure), opts.Timeouts.Auth); err != nil {
			return ioErr(err)
		}
		return &TerminalError{
			Outcome: OutcomeAuthFailed, Reason: "bad_credentials",
			Detail: "username/password rejected", ReplySent: true,
		}
	}
	if err := send(conn, codec.EncodeUserPassReply(codec.UserPassSuccess), opts.Timeouts.Auth); err != nil {
		return ioErr(err)
	}
	return nil
}

func handleRequestDecodeError(conn net.Conn, err error, opts Options) (*Established, error) {
	de, ok := codec.IsDecodeError(err)
	if !ok {
		return nil, ioErr(err)
	}
	// Unsupported command / address type are well-formed frames that receive
	// a specific SOCKS5 reply; truncated or wrong-version frames just close.
	var rep byte
	switch de.Reason {
	case codec.ReasonUnsupportedCommand:
		rep = codec.RepCommandNotSupported
	case codec.ReasonUnsupportedAtyp:
		rep = codec.RepAddressTypeNotSupported
	default:
		return nil, &TerminalError{Outcome: OutcomeProtocolError, Reason: string(de.Reason), Detail: de.Error()}
	}
	_ = send(conn, codec.EncodeReply(rep, codec.ZeroBindAddr()), opts.Timeouts.Req)
	return nil, &TerminalError{
		Outcome: OutcomeRejected, Reason: string(de.Reason), Detail: de.Error(),
		ReplySent: true, Rep: rep,
	}
}

func classifyDecode(err error, outcome Outcome) error {
	if de, ok := codec.IsDecodeError(err); ok {
		return &TerminalError{Outcome: outcome, Reason: string(de.Reason), Detail: de.Error()}
	}
	return ioErr(err)
}

func ioErr(err error) error {
	if errors.Is(err, io.EOF) || errors.Is(err, io.ErrUnexpectedEOF) {
		return &TerminalError{Outcome: OutcomeProtocolError, Reason: "client_eof", Detail: err.Error()}
	}
	var ne net.Error
	if errors.As(err, &ne) && ne.Timeout() {
		return &TerminalError{Outcome: OutcomeProtocolError, Reason: "deadline_exceeded", Detail: err.Error()}
	}
	return &TerminalError{Outcome: OutcomeProtocolError, Reason: "io_error", Detail: err.Error()}
}

// policyReply maps a policy reason to the SOCKS5 REP octet.
func policyReply(reason string) byte {
	switch reason {
	case policy.ReasonDNSFailed, policy.ReasonDNSNoRecords:
		return codec.RepHostUnreachable
	default:
		return codec.RepNotAllowedByRuleset
	}
}

// dialReply maps an upstream dial failure to the SOCKS5 REP octet.
func dialReply(err error) byte {
	var ne net.Error
	if errors.As(err, &ne) && ne.Timeout() {
		return codec.RepTTLExpired
	}
	if errors.Is(err, syscall.ECONNREFUSED) {
		return codec.RepConnectionRefused
	}
	if errors.Is(err, syscall.ENETUNREACH) {
		return codec.RepNetworkUnreachable
	}
	if errors.Is(err, syscall.EHOSTUNREACH) {
		return codec.RepHostUnreachable
	}
	return codec.RepHostUnreachable
}

func dialVetted(ctx context.Context, d UpstreamDialer, targets []netip.AddrPort, timeout time.Duration) (net.Conn, netip.AddrPort, error) {
	var lastErr error
	for _, target := range targets {
		dialCtx := ctx
		var cancel context.CancelFunc
		if timeout > 0 {
			dialCtx, cancel = context.WithTimeout(ctx, timeout)
		}
		conn, err := d.DialContext(dialCtx, target)
		if cancel != nil {
			cancel()
		}
		if err == nil {
			return conn, target, nil
		}
		lastErr = err
	}
	if lastErr == nil {
		lastErr = errors.New("no dial candidates")
	}
	return nil, netip.AddrPort{}, lastErr
}

func send(conn net.Conn, frame []byte, within time.Duration) error {
	if within > 0 {
		_ = conn.SetWriteDeadline(time.Now().Add(within))
		defer func() { _ = conn.SetWriteDeadline(time.Time{}) }()
	}
	_, err := conn.Write(frame)
	return err
}
