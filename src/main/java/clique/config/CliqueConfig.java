package clique.config;

import clique.error.CliqueException;
import clique.error.ErrorCategory;

import java.io.IOException;
import java.io.InputStream;
import java.io.Reader;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Properties;

/** 独立配置：算法开关与资源预算。非法取值一律 INPUT_ERROR。 */
public record CliqueConfig(long maxSteps, PivotStrategy pivot, Verbosity verbosity, int bruteForceMaxN) {

    public enum PivotStrategy { MAX_INTERSECTION, FIRST }

    public enum Verbosity { SUMMARY, VERBOSE, DEBUG }

    public static CliqueConfig defaults() {
        Properties p = new Properties();
        try (InputStream in = CliqueConfig.class.getResourceAsStream("/clique/default.properties")) {
            if (in != null) {
                p.load(in);
            }
        } catch (IOException e) {
            throw new CliqueException(ErrorCategory.INPUT_ERROR, "cannot read bundled default.properties: " + e.getMessage());
        }
        return fromProperties(p, "bundled defaults");
    }

    /** 在默认值上叠加指定文件。 */
    public static CliqueConfig load(Path path) {
        Properties p = new Properties();
        try (InputStream in = CliqueConfig.class.getResourceAsStream("/clique/default.properties")) {
            if (in != null) {
                p.load(in);
            }
        } catch (IOException e) {
            throw new CliqueException(ErrorCategory.INPUT_ERROR, "cannot read bundled default.properties: " + e.getMessage());
        }
        try (Reader r = Files.newBufferedReader(path)) {
            p.load(r);
        } catch (IOException e) {
            throw new CliqueException(ErrorCategory.INPUT_ERROR, "cannot read config file " + path + ": " + e.getMessage());
        }
        return fromProperties(p, path.toString());
    }

    static CliqueConfig fromProperties(Properties p, String source) {
        long maxSteps = parseLong(p, "clique.maxSteps", 100_000_000L, source);
        int bruteMax = (int) parseLong(p, "clique.bruteForceMaxN", 24L, source);
        if (maxSteps <= 0) {
            throw new CliqueException(ErrorCategory.INPUT_ERROR,
                    source + ": clique.maxSteps must be positive, got " + maxSteps);
        }
        if (bruteMax < 0 || bruteMax > 30) {
            throw new CliqueException(ErrorCategory.INPUT_ERROR,
                    source + ": clique.bruteForceMaxN must be in [0,30], got " + bruteMax);
        }
        PivotStrategy pivot = switch (p.getProperty("clique.pivot", "maxIntersection").trim()) {
            case "maxIntersection" -> PivotStrategy.MAX_INTERSECTION;
            case "first" -> PivotStrategy.FIRST;
            default -> throw new CliqueException(ErrorCategory.INPUT_ERROR,
                    source + ": unknown clique.pivot '" + p.getProperty("clique.pivot") + "'");
        };
        Verbosity verbosity = switch (p.getProperty("clique.verbosity", "summary").trim()) {
            case "summary" -> Verbosity.SUMMARY;
            case "verbose" -> Verbosity.VERBOSE;
            case "debug" -> Verbosity.DEBUG;
            default -> throw new CliqueException(ErrorCategory.INPUT_ERROR,
                    source + ": unknown clique.verbosity '" + p.getProperty("clique.verbosity") + "'");
        };
        return new CliqueConfig(maxSteps, pivot, verbosity, bruteMax);
    }

    private static long parseLong(Properties p, String key, long def, String source) {
        String raw = p.getProperty(key);
        if (raw == null) {
            return def;
        }
        try {
            return Long.parseLong(raw.trim());
        } catch (NumberFormatException e) {
            throw new CliqueException(ErrorCategory.INPUT_ERROR,
                    source + ": " + key + " is not a number: '" + raw.trim() + "'");
        }
    }
}
