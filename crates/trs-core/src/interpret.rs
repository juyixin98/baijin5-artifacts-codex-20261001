//! Internal view of a certificate: symbol -> (constant, argument coefficients).

use std::collections::HashMap;
use trs_syntax::Certificate;

pub(crate) struct InterpMap {
    entries: HashMap<String, (u64, Vec<u64>)>,
}

impl InterpMap {
    /// Precondition: the certificate has been structurally validated against
    /// the system signature, so lookups for signature symbols never miss.
    pub(crate) fn from_certificate(cert: &Certificate) -> Self {
        let entries = cert
            .interpretation
            .iter()
            .map(|entry| (entry.symbol.clone(), (entry.constant, entry.coeffs.clone())))
            .collect();
        InterpMap { entries }
    }

    pub(crate) fn get(&self, symbol: &str) -> Option<&(u64, Vec<u64>)> {
        self.entries.get(symbol)
    }
}
