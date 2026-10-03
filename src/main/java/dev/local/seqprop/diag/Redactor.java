package dev.local.seqprop.diag;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;

/**
 * Renders sensitive identifiers/values as keyed hashes so diagnostic output
 * never contains raw sensitive data while remaining stable within one run
 * (equal raw values hash to the same token, enabling correlation).
 */
public final class Redactor {

  private final boolean sensitive;
  private final String salt;

  public Redactor(boolean sensitive, String requestId) {
    this.sensitive = sensitive;
    this.salt = requestId == null ? "" : requestId;
  }

  public String renderValue(int value) {
    if (!sensitive) {
      return Integer.toString(value);
    }
    return "v#" + shortHash("value:" + value);
  }

  public String renderVar(int var) {
    if (!sensitive) {
      return "x" + var;
    }
    return "x#" + shortHash("var:" + var);
  }

  public String renderDomain(java.util.List<Integer> values) {
    StringBuilder sb = new StringBuilder("{");
    boolean first = true;
    for (Integer v : values) {
      if (!first) {
        sb.append(',');
      }
      first = false;
      sb.append(renderValue(v));
    }
    return sb.append('}').toString();
  }

  private String shortHash(String raw) {
    try {
      MessageDigest md = MessageDigest.getInstance("SHA-256");
      byte[] digest = md.digest((salt + '|' + raw).getBytes(StandardCharsets.UTF_8));
      return HexFormat.of().formatHex(digest, 0, 6);
    } catch (NoSuchAlgorithmException e) {
      throw new IllegalStateException(e);
    }
  }
}
