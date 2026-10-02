//! Synthetic device service-time models.
//!
//! BOUNDARY: these are *controllable synthetic models*, not measurements.
//! `Hdd` is a linear seek model for a mechanical-like device; `SsdLike` is a
//! constant-latency model. Neither is calibrated against real hardware, and
//! `SsdLike` must never be reported as an "SSD measurement". Every run summary
//! carries [`DeviceModel::caveat`] to keep that explicit.

use serde::{Deserialize, Serialize};

/// Cost of servicing one dispatched item, in nanoseconds (integers only, so
/// results are exactly reproducible and hand-checkable).
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct ServiceCost {
    pub service_ns: u64,
    pub seek_ns: u64,
    pub seek_distance_sectors: u64,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum DeviceModel {
    /// Mechanical-like: service = seek_base + seek_rate * |start - head| + transfer * len.
    Hdd {
        seek_base_ns: u64,
        seek_ns_per_sector: u64,
        transfer_ns_per_sector: u64,
    },
    /// Constant-latency synthetic stand-in: service = fixed + transfer * len.
    /// No seek component at all; NOT an SSD benchmark.
    SsdLike {
        fixed_latency_ns: u64,
        transfer_ns_per_sector: u64,
    },
}

impl DeviceModel {
    /// Service cost of an item `[start, start+len)` given the head rests at
    /// `head` (a sector position). The head ends at `start + len`.
    pub fn service(&self, head: u64, start: u64, len: u64) -> ServiceCost {
        match *self {
            DeviceModel::Hdd {
                seek_base_ns,
                seek_ns_per_sector,
                transfer_ns_per_sector,
            } => {
                let dist = head.abs_diff(start);
                let seek_ns = seek_base_ns + seek_ns_per_sector * dist;
                ServiceCost {
                    service_ns: seek_ns + transfer_ns_per_sector * len,
                    seek_ns,
                    seek_distance_sectors: dist,
                }
            }
            DeviceModel::SsdLike {
                fixed_latency_ns,
                transfer_ns_per_sector,
            } => ServiceCost {
                service_ns: fixed_latency_ns + transfer_ns_per_sector * len,
                seek_ns: 0,
                seek_distance_sectors: 0,
            },
        }
    }

    pub fn name(&self) -> &'static str {
        match self {
            DeviceModel::Hdd { .. } => "hdd",
            DeviceModel::SsdLike { .. } => "ssd_like",
        }
    }

    /// Human-readable caveat reproduced in every run summary.
    pub fn caveat(&self) -> &'static str {
        match self {
            DeviceModel::Hdd { .. } => {
                "synthetic linear seek model (base + rate*distance); not measured from real hardware"
            }
            DeviceModel::SsdLike { .. } => {
                "synthetic constant-latency model; this is NOT an SSD measurement"
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hdd_service_is_linear_in_distance_and_length() {
        let dev = DeviceModel::Hdd {
            seek_base_ns: 0,
            seek_ns_per_sector: 1,
            transfer_ns_per_sector: 1,
        };
        // head at 15, request [50, 60): seek 35, transfer 10.
        let c = dev.service(15, 50, 10);
        assert_eq!(
            c,
            ServiceCost {
                service_ns: 45,
                seek_ns: 35,
                seek_distance_sectors: 35
            }
        );
        // seek base is additive.
        let dev2 = DeviceModel::Hdd {
            seek_base_ns: 7,
            seek_ns_per_sector: 1,
            transfer_ns_per_sector: 1,
        };
        assert_eq!(dev2.service(0, 10, 5).service_ns, 7 + 10 + 5);
    }

    #[test]
    fn ssd_like_has_no_seek_component() {
        let dev = DeviceModel::SsdLike {
            fixed_latency_ns: 100,
            transfer_ns_per_sector: 2,
        };
        let c = dev.service(9999, 3, 10);
        assert_eq!(c.service_ns, 120);
        assert_eq!(c.seek_ns, 0);
        assert_eq!(c.seek_distance_sectors, 0);
        assert!(dev.caveat().contains("NOT an SSD measurement"));
    }
}
