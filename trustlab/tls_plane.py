"""mTLS data plane built on Python's `ssl` module (OpenSSL).

Authentication timing is explicit:

- NEW connections authenticate against the trust bundle that is active at
  handshake time. The accept path takes an atomic snapshot of
  (SSLContext, bundle_version) per accepted socket.
- EXISTING connections keep the session they established. Activating a new
  bundle swaps the context used for FUTURE handshakes only; established
  sessions are never re-authenticated and never torn down implicitly.
  Removing a trust root therefore does NOT revoke old connections -
  revoking an established connection is a separate, explicit
  administrative action (revoke_connection).
"""
from __future__ import annotations

import socket
import ssl
import threading
import uuid

from . import frames
from .bundle import Bundle
from .errors import (ErrorCategory, FailureClass, InputError,
                     ResourceExhausted, StateConflict,
                     classify_verify_failure)
from .explainer import explain_untrusted_root
from .identity import CLIENT_AUTH_OID, identity_from_verified_der
from .store import Store


class _LiveConnection:
    __slots__ = ("conn_id", "sock", "identity", "bundle_version", "peer")

    def __init__(self, conn_id, sock, identity, bundle_version, peer):
        self.conn_id = conn_id
        self.sock = sock
        self.identity = identity
        self.bundle_version = bundle_version
        self.peer = peer


def _force_close(sock: socket.socket) -> None:
    """Terminate a socket that another thread may be blocked reading.

    A plain close() does not wake a blocking recv in another thread (the
    makefile reader holds the fd open); shutdown() does.
    """
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        sock.close()
    except OSError:
        pass


class TLSDataPlane:
    def __init__(self, *, store: Store, run_id: str, host: str, port: int,
                 server_cert_path: str, server_key_path: str,
                 max_connections: int = 64,
                 max_frame_bytes: int = frames.DEFAULT_MAX_FRAME_BYTES) -> None:
        self._store = store
        self._run_id = run_id
        self._host = host
        self._requested_port = port
        self.port = port
        self._server_cert_path = server_cert_path
        self._server_key_path = server_key_path
        self._max_connections = max_connections
        self._max_frame_bytes = max_frame_bytes

        self._lock = threading.Lock()
        self._context: ssl.SSLContext | None = None
        self._context_version: int | None = None
        self._live: dict[str, _LiveConnection] = {}
        self._server_sock: socket.socket | None = None
        self._accept_thread: threading.Thread | None = None
        self._handler_threads: list[threading.Thread] = []
        self._stop = threading.Event()

    # -- trust management ---------------------------------------------------
    def build_context(self, roots_pem: list[str]) -> ssl.SSLContext:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(certfile=self._server_cert_path,
                            keyfile=self._server_key_path)
        ctx.verify_mode = ssl.CERT_REQUIRED
        ctx.load_verify_locations(cadata="".join(roots_pem))
        return ctx

    def update_trust(self, bundle: Bundle) -> None:
        """Swap the trust context for NEW handshakes only."""
        ctx = self.build_context(bundle.roots_pem)
        with self._lock:
            self._context = ctx
            self._context_version = bundle.version
            live_versions = sorted({c.bundle_version for c in self._live.values()})
            live_count = len(self._live)
        self._store.audit(
            run_id=self._run_id,
            category="STATE_CHANGE",
            event="trust_context_swapped",
            detail={
                "new_bundle_version": bundle.version,
                "live_connections": live_count,
                "live_connection_bundle_versions": live_versions,
            },
            reasoning=(
                f"bundle v{bundle.version} applies to NEW handshakes only; "
                f"{live_count} established connection(s) remain authenticated "
                f"under bundle version(s) {live_versions}; trust-root removal "
                "does not retroactively revoke established sessions"
            ),
        )

    # -- lifecycle ------------------------------------------------------------
    def start(self) -> None:
        self._server_sock = socket.create_server((self._host,
                                                  self._requested_port))
        self._server_sock.settimeout(0.2)
        self.port = self._server_sock.getsockname()[1]
        self._accept_thread = threading.Thread(
            target=self._accept_loop, name="tls-accept", daemon=True
        )
        self._accept_thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._server_sock is not None:
            try:
                self._server_sock.close()
            except OSError:
                pass
        with self._lock:
            live = list(self._live.values())
        for conn in live:
            _force_close(conn.sock)
        if self._accept_thread is not None:
            self._accept_thread.join(timeout=2.0)
        # Handler threads exit once their sockets are closed; join them so
        # no thread touches the store after the service tears it down.
        with self._lock:
            handlers = list(self._handler_threads)
        for thread in handlers:
            thread.join(timeout=2.0)

    # -- accept / handle --------------------------------------------------------
    def _accept_loop(self) -> None:
        assert self._server_sock is not None
        while not self._stop.is_set():
            try:
                conn, addr = self._server_sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with self._lock:
                ctx, version = self._context, self._context_version
            thread = threading.Thread(
                target=self._handle, args=(conn, addr, ctx, version),
                daemon=True,
            )
            with self._lock:
                self._handler_threads = [
                    t for t in self._handler_threads if t.is_alive()
                ]
                self._handler_threads.append(thread)
            thread.start()

    def _handle(self, conn: socket.socket, addr, ctx: ssl.SSLContext | None,
                bundle_version: int | None) -> None:
        peer = f"{addr[0]}:{addr[1]}"
        try:
            with self._lock:
                over_limit = len(self._live) >= self._max_connections
            if over_limit:
                self._store.audit(
                    run_id=self._run_id,
                    category=ErrorCategory.RESOURCE_EXHAUSTED.value,
                    event="connection_rejected",
                    detail={"peer": peer,
                            "max_connections": self._max_connections},
                    reasoning="active connection count reached the configured "
                              "limit; rejected before the handshake to bound "
                              "handshake CPU cost",
                )
                conn.close()
                return
            if ctx is None:
                conn.close()
                return
            try:
                tls = ctx.wrap_socket(conn, server_side=True)
            except ssl.SSLCertVerificationError as exc:
                self._record_verify_failure(exc, bundle_version, peer)
                conn.close()
                return
            except ssl.SSLError as exc:
                self._record_ssl_failure(exc, bundle_version, peer)
                conn.close()
                return
            except (ConnectionResetError, BrokenPipeError, OSError):
                conn.close()
                return
            try:
                self._session_loop(tls, peer, bundle_version)
            finally:
                try:
                    tls.close()
                except OSError:
                    pass
        except Exception as exc:  # a handler must never die silently
            self._store.audit(
                run_id=self._run_id,
                category=ErrorCategory.CRYPTO_FAILURE.value,
                event="handler_error",
                detail={"peer": peer, "error": repr(exc)},
                reasoning="unexpected handler exception; recorded so the run "
                          "can be replayed",
            )

    # -- session ---------------------------------------------------------------
    def _session_loop(self, tls: ssl.SSLSocket, peer: str,
                      bundle_version: int | None) -> None:
        der = tls.getpeercert(binary_form=True)
        identity = identity_from_verified_der(der)

        # Defense in depth: OpenSSL's purpose check is primary; the
        # application re-checks the clientAuth EKU on the verified cert.
        if CLIENT_AUTH_OID not in identity.extended_key_usages:
            self._store.add_handshake_failure(
                run_id=self._run_id,
                failure_class=FailureClass.WRONG_PURPOSE.value,
                layer="application",
                verify_code=None,
                verify_message="certificate lacks clientAuth EKU",
                active_bundle_version=bundle_version,
                peer=peer,
                explanation=None,
            )
            self._store.audit(
                run_id=self._run_id,
                category=ErrorCategory.CRYPTO_FAILURE.value,
                event="handshake_failed",
                detail={"peer": peer,
                        "failure_class": FailureClass.WRONG_PURPOSE.value,
                        "layer": "application"},
                reasoning="verified certificate does not carry the "
                          "clientAuth EKU; rejected at the application layer",
            )
            frames.write_frame(tls, {
                "type": "error",
                "category": ErrorCategory.CRYPTO_FAILURE.value,
                "failure_class": FailureClass.WRONG_PURPOSE.value,
                "message": "client certificate lacks clientAuth EKU",
            })
            return

        conn_id = uuid.uuid4().hex[:16]
        live = _LiveConnection(conn_id, tls, identity, bundle_version, peer)
        with self._lock:
            self._live[conn_id] = live
        self._store.add_connection(
            conn_id=conn_id, run_id=self._run_id,
            identity=identity.to_dict(), bundle_version=bundle_version,
            peer=peer,
        )
        self._store.audit(
            run_id=self._run_id,
            category="AUTH_SUCCESS",
            event="connection_authenticated",
            detail={"connection_id": conn_id,
                    "identity": identity.to_dict(),
                    "bundle_version": bundle_version},
            reasoning="identity extracted from the verified peer certificate "
                      "of the established TLS session",
        )

        reader = tls.makefile("rb")
        frames.write_frame(tls, {
            "type": "hello",
            "connection_id": conn_id,
            "identity": identity.to_dict(),
            "bundle_version": bundle_version,
            "run_id": self._run_id,
        })
        close_reason = "peer_closed"
        try:
            while True:
                msg = frames.read_frame(reader, max_bytes=self._max_frame_bytes)
                self._dispatch(tls, live, msg)
        except frames.ConnectionClosed:
            pass
        except ResourceExhausted as exc:
            close_reason = "resource_exhausted"
            self._store.audit(
                run_id=self._run_id,
                category=ErrorCategory.RESOURCE_EXHAUSTED.value,
                event="frame_rejected",
                detail={"connection_id": conn_id, **exc.to_dict()},
                reasoning=exc.reasoning,
            )
        except InputError as exc:
            close_reason = "input_error"
            self._store.audit(
                run_id=self._run_id,
                category=ErrorCategory.INPUT_ERROR.value,
                event="frame_rejected",
                detail={"connection_id": conn_id, **exc.to_dict()},
                reasoning="malformed application frame from an authenticated "
                          "peer",
            )
            try:
                frames.write_frame(tls, {
                    "type": "error",
                    "category": ErrorCategory.INPUT_ERROR.value,
                    "message": exc.message,
                })
            except OSError:
                pass
        except (ConnectionResetError, BrokenPipeError, OSError):
            pass
        finally:
            with self._lock:
                self._live.pop(conn_id, None)
            self._store.close_connection(conn_id, close_reason)

    def _dispatch(self, tls: ssl.SSLSocket, live: _LiveConnection,
                  msg: dict) -> None:
        mtype = msg["type"]
        if mtype == "ping":
            frames.write_frame(tls, {"type": "pong",
                                     "connection_id": live.conn_id})
        elif mtype == "whoami":
            # Identity is re-read from the TLS session, not from the frame.
            identity = identity_from_verified_der(
                tls.getpeercert(binary_form=True)
            )
            frames.write_frame(tls, {
                "type": "identity",
                "connection_id": live.conn_id,
                "identity": identity.to_dict(),
                "bundle_version": live.bundle_version,
            })
        elif mtype == "echo":
            frames.write_frame(tls, {"type": "echo",
                                     "payload": msg.get("payload")})
        else:
            raise InputError(f"unknown frame type {mtype!r}")

    # -- failure recording -------------------------------------------------------
    def _record_verify_failure(self, exc: ssl.SSLCertVerificationError,
                               bundle_version: int | None, peer: str) -> None:
        verify_code = getattr(exc, "verify_code", None)
        verify_message = getattr(exc, "verify_message", None) or str(exc)
        failure_class = classify_verify_failure(verify_code, verify_message)
        explanation = None
        if failure_class is FailureClass.UNTRUSTED_ROOT:
            explanation = explain_untrusted_root(self._store)
        self._store.add_handshake_failure(
            run_id=self._run_id,
            failure_class=failure_class.value,
            layer="tls",
            verify_code=verify_code,
            verify_message=verify_message,
            active_bundle_version=bundle_version,
            peer=peer,
            explanation=explanation,
        )
        self._store.audit(
            run_id=self._run_id,
            category=ErrorCategory.CRYPTO_FAILURE.value,
            event="handshake_failed",
            detail={
                "peer": peer,
                "failure_class": failure_class.value,
                "layer": "tls",
                "verify_code": verify_code,
                "verify_message": verify_message,
                "active_bundle_version": bundle_version,
            },
            reasoning="OpenSSL rejected the peer certificate during the "
                      "handshake; see handshake_failures for the full record",
        )

    def _record_ssl_failure(self, exc: ssl.SSLError,
                            bundle_version: int | None, peer: str) -> None:
        text = str(exc)
        if "PEER_DID_NOT_RETURN_A_CERTIFICATE" in text:
            failure_class = FailureClass.NO_CLIENT_CERT
        else:
            failure_class = FailureClass.HANDSHAKE_FAILED
        self._store.add_handshake_failure(
            run_id=self._run_id,
            failure_class=failure_class.value,
            layer="tls",
            verify_code=None,
            verify_message=text,
            active_bundle_version=bundle_version,
            peer=peer,
            explanation=None,
        )
        self._store.audit(
            run_id=self._run_id,
            category=ErrorCategory.CRYPTO_FAILURE.value,
            event="handshake_failed",
            detail={"peer": peer, "failure_class": failure_class.value,
                    "layer": "tls", "verify_message": text},
            reasoning="TLS handshake failed before a verified peer "
                      "certificate was available",
        )

    # -- administration ------------------------------------------------------------
    def revoke_connection(self, conn_id: str) -> None:
        row = self._store.get_connection(conn_id)
        if row is None:
            raise InputError(
                f"unknown connection {conn_id!r}",
                reasoning="revocation targets must be recorded connections",
            )
        if row["revoked"]:
            raise StateConflict(
                f"connection {conn_id} is already revoked",
                reasoning="revocation is idempotent-in-effect but a duplicate "
                          "request signals a client-side state mismatch",
            )
        # Record the administrative reason BEFORE tearing the socket down,
        # so the handler thread's finally-block cannot race it.
        self._store.mark_revoked(conn_id)
        self._store.close_connection(conn_id, "revoked")
        with self._lock:
            live = self._live.pop(conn_id, None)
        if live is not None:
            _force_close(live.sock)
        self._store.audit(
            run_id=self._run_id,
            category="ADMIN",
            event="connection_revoked",
            detail={"connection_id": conn_id,
                    "was_live": live is not None,
                    "bundle_version": row["bundle_version"]},
            reasoning="explicit administrative revocation of an established "
                      "session; distinct from trust-root removal, which never "
                      "revokes established sessions implicitly",
        )

    def live_connections(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "connection_id": c.conn_id,
                    "identity": c.identity.to_dict(),
                    "bundle_version": c.bundle_version,
                    "peer": c.peer,
                }
                for c in self._live.values()
            ]

    @property
    def current_bundle_version(self) -> int | None:
        with self._lock:
            return self._context_version
