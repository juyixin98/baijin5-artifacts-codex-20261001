package com.example.coloring.certificate;

import java.util.List;
import java.util.Optional;

/**
 * Result of independently checking a solver certificate. {@code accepted()} means
 * every claimed bound is backed by a concrete witness; otherwise {@code errors()}
 * lists each concrete failure category found.
 */
public record CertificateVerdict(boolean accepted, List<CertificateError> errors, String detail) {

    public static CertificateVerdict ok(String detail) {
        return new CertificateVerdict(true, List.of(), detail);
    }

    public static CertificateVerdict reject(List<CertificateError> errors, String detail) {
        return new CertificateVerdict(false, List.copyOf(errors), detail);
    }

    public Optional<CertificateError> firstError() {
        return errors.isEmpty() ? Optional.empty() : Optional.of(errors.get(0));
    }
}
