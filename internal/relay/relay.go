// Package relay performs the post-handshake, full-duplex byte forwarding.
//
// Half-close semantics (RFC 1928 leaves connection teardown to TCP):
//
//   - A FIN from the client (its read returns EOF) closes ONLY the
//     client->upstream direction, via CloseWrite on the upstream, and only
//     after every byte already accepted from the client has been written to
//     the upstream ("the pending send buffer is drained"). The
//     upstream->client direction keeps running.
//   - A FIN from the upstream is handled symmetrically.
//   - The relay ends once BOTH directions have half-closed, or on a fatal
//     condition (byte budget exhausted, idle timeout, reset, cancellation),
//     which tears the whole pipe down.
//
// Every forwarded byte is counted against a single per-connection budget.
package relay

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net"
	"sync"
	"sync/atomic"
	"time"
)

// End reasons recorded in Stats and logs.
const (
	ReasonClosedBoth    = "both_directions_closed"
	ReasonByteBudget    = "byte_budget_exceeded"
	ReasonIdleTimeout   = "idle_timeout"
	ReasonReset         = "connection_reset"
	ReasonCanceled      = "canceled"
	ReasonUnsupportedHC = "half_close_unsupported"
)

// ErrByteBudgetExceeded is returned when the per-connection budget is hit.
var ErrByteBudgetExceeded = errors.New("relay: per-connection byte budget exceeded")

// DefaultBufferSize is the bounded per-direction copy buffer.
const DefaultBufferSize = 32 * 1024

// Stats are the exact, independently testable forwarding counters.
type Stats struct {
	ClientToUpstream int64
	UpstreamToClient int64
	EndReason        string
}

// Options controls relay bounds.
type Options struct {
	// ByteBudget bounds total bytes forwarded in both directions. Required > 0.
	ByteBudget int64
	// BufferSize bounds the in-flight copy buffer; <=0 uses the default.
	BufferSize int
	// IdleTimeout ends the relay after no bytes in either direction.
	// 0 disables it.
	IdleTimeout time.Duration
}

// closeWriter is implemented by *net.TCPConn and *net.UnixConn.
type closeWriter interface {
	CloseWrite() error
}

type result struct {
	reason string
	err    error
}

// Relay forwards bytes between client and upstream until both directions
// half-close or a fatal bound is reached. It never performs a policy check
// (that happened before it is called) and never dials.
func Relay(ctx context.Context, client, upstream net.Conn, opts Options) (Stats, error) {
	if opts.ByteBudget <= 0 {
		return Stats{}, errors.New("relay: byte budget must be > 0")
	}
	bufSize := opts.BufferSize
	if bufSize <= 0 {
		bufSize = DefaultBufferSize
	}

	var stats Stats
	// Each direction has its own bounded counter so that a chatty
	// upstream->client flow can never starve (or be starved by) the reverse
	// direction, while every direction is independently capped at the budget.
	var upCount atomic.Int64   // bytes forwarded client -> upstream
	var downCount atomic.Int64 // bytes forwarded upstream -> client
	var fatalOnce sync.Once
	var firstFatal result
	force := make(chan struct{})
	copyDone := make(chan struct{})

	// reportFatal keeps the initiating error (e.g. byte budget); the peer
	// error caused by the resulting force-close must not overwrite it.
	reportFatal := func(r result) {
		fatalOnce.Do(func() {
			firstFatal = r
			close(force)
		})
	}

	// Fatal conditions force-close both conns, unblocking the other loop.
	go func() {
		select {
		case <-ctx.Done():
			reportFatal(result{reason: ReasonCanceled, err: ctx.Err()})
			forceClose(client, upstream)
		case <-force:
			forceClose(client, upstream)
		case <-copyDone:
		}
	}()

	var wg sync.WaitGroup
	wg.Add(2)
	go func() {
		defer wg.Done()
		// client -> upstream
		n, reason, err := copyDirection(upstream, client, &upCount, opts, bufSize)
		atomic.StoreInt64(&stats.ClientToUpstream, n)
		if err != nil {
			reportFatal(result{reason: reason, err: err})
		}
	}()
	go func() {
		defer wg.Done()
		// upstream -> client
		n, reason, err := copyDirection(client, upstream, &downCount, opts, bufSize)
		atomic.StoreInt64(&stats.UpstreamToClient, n)
		if err != nil {
			reportFatal(result{reason: reason, err: err})
		}
	}()
	wg.Wait()
	close(copyDone)

	// If nothing aborted proactively, context cancellation still counts.
	if ctx.Err() != nil {
		reportFatal(result{reason: ReasonCanceled, err: ctx.Err()})
	}

	stats.ClientToUpstream = atomic.LoadInt64(&stats.ClientToUpstream)
	stats.UpstreamToClient = atomic.LoadInt64(&stats.UpstreamToClient)
	if firstFatal.reason != "" {
		stats.EndReason = firstFatal.reason
		return stats, fmt.Errorf("relay ended: %s: %w", firstFatal.reason, firstFatal.err)
	}
	stats.EndReason = ReasonClosedBoth
	return stats, nil
}

// copyDirection copies src->dst until EOF (then half-closes dst), a bound
// fires, or an error occurs. The per-direction budget is enforced BEFORE
// writing and each write is capped to the remaining allowance, so the total
// bytes this direction forwards can never exceed ByteBudget. Any bytes read
// within the allowance are fully written before the bound is reported, so a
// slow peer still receives the complete allowed prefix.
func copyDirection(dst, src net.Conn, count *atomic.Int64, opts Options, bufSize int) (int64, string, error) {
	buf := make([]byte, bufSize)
	var forwarded int64
	for {
		if opts.IdleTimeout > 0 {
			_ = src.SetReadDeadline(time.Now().Add(opts.IdleTimeout))
		}
		nr, rerr := src.Read(buf)
		if nr > 0 {
			data := buf[:nr]
			used := count.Load()
			if used >= opts.ByteBudget {
				return forwarded, ReasonByteBudget, ErrByteBudgetExceeded
			}
			if remaining := opts.ByteBudget - used; int64(len(data)) > remaining {
				data = data[:remaining]
			}
			for len(data) > 0 {
				if opts.IdleTimeout > 0 {
					_ = dst.SetWriteDeadline(time.Now().Add(opts.IdleTimeout))
				}
				nw, werr := dst.Write(data)
				if nw > 0 {
					data = data[nw:]
					forwarded += int64(nw)
					count.Add(int64(nw))
				}
				if werr != nil {
					return forwarded, classify(werr), werr
				}
			}
			if count.Load() >= opts.ByteBudget {
				return forwarded, ReasonByteBudget, ErrByteBudgetExceeded
			}
		}
		if rerr != nil {
			if errors.Is(rerr, io.EOF) {
				// Allowed read bytes have been written above; now half-close.
				if err := halfClose(dst); err != nil {
					return forwarded, ReasonUnsupportedHC, err
				}
				return forwarded, ReasonClosedBoth, nil
			}
			return forwarded, classify(rerr), rerr
		}
	}
}

func halfClose(conn net.Conn) error {
	if cw, ok := conn.(closeWriter); ok {
		return cw.CloseWrite()
	}
	// No directional close available: close the whole connection so the peer
	// observes termination rather than a hang. This is documented behavior for
	// non-TCP transports.
	return conn.Close()
}

func classify(err error) string {
	var ne net.Error
	if errors.As(err, &ne) && ne.Timeout() {
		return ReasonIdleTimeout
	}
	if errors.Is(err, ErrByteBudgetExceeded) {
		return ReasonByteBudget
	}
	if errors.Is(err, context.Canceled) {
		return ReasonCanceled
	}
	return ReasonReset
}

func forceClose(conns ...net.Conn) {
	for _, c := range conns {
		_ = c.Close()
	}
}
