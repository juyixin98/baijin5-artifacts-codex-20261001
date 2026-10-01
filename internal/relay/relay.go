// Package relay moves bytes between the client and the upstream after a
// successful SOCKS5 CONNECT. Its semantics are deliberately precise:
//
// Half-close — when one side finishes writing (read returns io.EOF), only
// the corresponding write direction on the other side is closed
// (TCP FIN via CloseWrite). The reverse direction keeps flowing until the
// other side also finishes, so a client that half-closes its upload still
// receives the full response. Any bytes already read are fully written and
// flushed before the FIN is sent.
//
// Bounded resources — every copy enforces a maximum number of bytes per
// direction and an idle timeout that resets on any successful read/write.
// Exceeding a bound tears the whole connection down with an explicit
// category; nothing runs unbounded.
package relay

import (
	"errors"
	"io"
	"net"
	"sync"
	"sync/atomic"
	"time"

	"sockswhitelist/internal/proto"
)

// Budgets bounds one relayed session. Zero means unlimited for that field.
type Budgets struct {
	MaxBytesUp   int64         // client -> upstream
	MaxBytesDown int64         // upstream -> client
	IdleTimeout  time.Duration // gap without any read/write activity in a direction
}

// Stats is the accounted result of a relay.
type Stats struct {
	Up       int64
	Down     int64
	UpKind   proto.Kind
	DownKind proto.Kind
}

// writeCloser is implemented by *net.TCPConn (CloseWrite sends a FIN).
type writeCloser interface {
	CloseWrite() error
}

// Relay runs both directions until both finish or a bound/error is hit. It
// always closes both conns before returning (ownership of both is passed
// in). The returned Stats and Kind describe why it ended.
func Relay(client, upstream net.Conn, b Budgets) (Stats, proto.Kind) {
	var up, down atomic.Int64
	var fatalMu sync.Mutex
	var fatalKind proto.Kind
	fatal := false

	closeBoth := func() {
		_ = client.Close()
		_ = upstream.Close()
	}
	// firstFatal records the first fatal category and tears everything
	// down so the sibling goroutine unblocks.
	firstFatal := func(k proto.Kind) {
		fatalMu.Lock()
		if !fatal {
			fatal = true
			fatalKind = k
			closeBoth()
		}
		fatalMu.Unlock()
	}

	var upKind, downKind atomic.Value
	upKind.Store(proto.KindRelayEOF)
	downKind.Store(proto.KindRelayEOF)

	var wg sync.WaitGroup
	wg.Add(2)
	go func() { // client -> upstream ("up")
		defer wg.Done()
		k := copyDirection(client, upstream, b.MaxBytesUp, b, &up)
		upKind.Store(k)
		if k != proto.KindRelayEOF {
			firstFatal(k)
		}
	}()
	go func() { // upstream -> client ("down")
		defer wg.Done()
		k := copyDirection(upstream, client, b.MaxBytesDown, b, &down)
		downKind.Store(k)
		if k != proto.KindRelayEOF {
			firstFatal(k)
		}
	}()
	wg.Wait()

	closeBoth() // no-op when firstFatal already ran; guarantees teardown on clean EOF

	st := Stats{
		Up:       up.Load(),
		Down:     down.Load(),
		UpKind:   upKind.Load().(proto.Kind),
		DownKind: downKind.Load().(proto.Kind),
	}
	fatalMu.Lock()
	gotFatal := fatal
	k := fatalKind
	fatalMu.Unlock()
	if gotFatal {
		return st, k
	}
	return st, proto.KindRelayEOF
}

// copyDirection copies src->dst until EOF (then half-closes dst's write
// side after draining) or until a bound/error is hit. Teardown by the
// sibling (which closes both conns) unblocks a Read parked here.
func copyDirection(src, dst net.Conn, maxBytes int64, b Budgets,
	counter *atomic.Int64) proto.Kind {
	buf := make([]byte, 32*1024)
	halfClosed := false

	for {
		// Hard bound: once exactly maxBytes have been forwarded, stop.
		if maxBytes > 0 && counter.Load() >= maxBytes {
			return proto.KindByteBudgetExceeded
		}

		if b.IdleTimeout > 0 {
			_ = src.SetReadDeadline(time.Now().Add(b.IdleTimeout))
		}
		nr, er := src.Read(buf)

		if nr > 0 {
			p := buf[:nr]
			// Never forward more bytes than the budget; bytes within the
			// current read up to the limit are still fully written (drained)
			// before we report the bound as exceeded.
			if maxBytes > 0 {
				remaining := maxBytes - counter.Load()
				if int64(len(p)) > remaining {
					if werr := writeAll(dst, p[:remaining], counter); werr != nil {
						return classifyIOErr(werr)
					}
					return proto.KindByteBudgetExceeded
				}
			}
			if b.IdleTimeout > 0 {
				_ = dst.SetWriteDeadline(time.Now().Add(b.IdleTimeout))
			}
			if werr := writeAll(dst, p, counter); werr != nil {
				return classifyIOErr(werr)
			}
		}

		if er != nil {
			if errors.Is(er, io.EOF) {
				// Clean half-close: every read byte was written above; now
				// send FIN on exactly this direction's write side.
				if !halfClosed {
					if wc, ok := dst.(writeCloser); ok {
						_ = wc.CloseWrite()
					}
					halfClosed = true
				}
				return proto.KindRelayEOF
			}
			return classifyIOErr(er)
		}
	}
}

// writeAll writes every byte of p, accounting each successful partial write
// so counters stay exact even when the peer accepts fragments.
func writeAll(dst io.Writer, p []byte, counter *atomic.Int64) error {
	for len(p) > 0 {
		nw, err := dst.Write(p)
		if nw > 0 {
			counter.Add(int64(nw))
			p = p[nw:]
		}
		if err != nil {
			return err
		}
		if nw == 0 {
			return io.ErrShortWrite
		}
	}
	return nil
}

// classifyIOErr maps a single read/write error to a relay category.
func classifyIOErr(err error) proto.Kind {
	var nerr net.Error
	if errors.As(err, &nerr) && nerr.Timeout() {
		return proto.KindIdleTimeout
	}
	return proto.KindRelayPeerReset
}
