package com.local.mwis.service;

import com.local.mwis.graph.FailureCategory;

import java.util.List;

/**
 * Machine-readable failure description attached to non-OK reports.
 *
 * @param category   primary failure category (from the contract taxonomy) or null
 * @param stage      pipeline stage where processing stopped
 * @param message    human-readable summary
 * @param violations all underlying contract violations, when the request was rejected
 */
public record FailureInfo(FailureCategory category, String stage, String message,
                          List<String> violations) {
}
