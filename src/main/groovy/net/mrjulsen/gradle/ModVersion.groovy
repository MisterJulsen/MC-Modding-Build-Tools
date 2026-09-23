package net.mrjulsen.gradle

import java.util.regex.Pattern

final class ModVersion implements Comparable<ModVersion>, Serializable {

    private static final long serialVersionUID = 1L

    private static final Pattern PATTERN =
            ~/^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-(alpha|beta|rc)\.([1-9]\d*))?$/

    enum Stage {
        ALPHA('alpha', 'alpha'),
        BETA('beta', 'beta'),
        RC('rc', 'beta'),
        RELEASE(null, 'release')

        final String id

        final String channel

        Stage(String id, String channel) {
            this.id = id
            this.channel = channel
        }

        static Stage fromId(String raw) {
            if (raw == null) return RELEASE
            Stage found = values().find { it.id == raw }
            if (found == null) throw new IllegalArgumentException("Unknown prerelease stage '${raw}'")
            return found
        }
    }

    final int major
    final int minor
    final int patch
    final Stage stage

    final int preNumber

    final String buildMetadata

    private ModVersion(int major, int minor, int patch, Stage stage, int preNumber, String buildMetadata) {
        this.major = major
        this.minor = minor
        this.patch = patch
        this.stage = stage
        this.preNumber = preNumber
        this.buildMetadata = buildMetadata
    }

    static ModVersion parse(String raw) {
        if (raw == null || raw.trim().isEmpty()) {
            throw invalid(raw, "no version given")
        }

        String value = raw.trim()

        if (value.contains('+')) {
            throw invalid(value, "build metadata ('+...') must not appear in mod_version - " +
                    "the plugin attaches it itself for snapshot and PR builds")
        }

        def matcher = PATTERN.matcher(value)
        if (!matcher.matches()) {
            throw invalid(value, diagnose(value))
        }

        return new ModVersion(
                matcher.group(1) as int,
                matcher.group(2) as int,
                matcher.group(3) as int,
                Stage.fromId(matcher.group(4)),
                matcher.group(5) ? matcher.group(5) as int : 0,
                null)
    }

    private static IllegalArgumentException invalid(String value, String reason) {
        return new IllegalArgumentException(
                "Invalid mod version '${value}': ${reason}.\n" +
                        "Expected MAJOR.MINOR.PATCH[-(alpha|beta|rc).N], " +
                        "for example 3.1.0, 3.1.0-beta.2 or 4.0.0-alpha.1.\n" +
                        "See docs/VERSIONING.md for the full specification.")
    }

    private static String diagnose(String value) {
        if (value ==~ /^\d+\.\d+(\.\d+)?-[a-z][a-z-]*-\d+\.\d+.*$/ ||
                value ==~ /^\d+\.\d+(\.\d+)?-\d+\.\d+\.\d+$/) {
            return "this is the old scheme '{mc}-[{channel}-]{version}'. " +
                    "The Minecraft version and the release channel do not belong in the version field - " +
                    "the plugin derives both. Enter only the mod's own version here, e.g. '2.0.0-beta.1'"
        }
        if (value ==~ /^\d+\.\d+\.\d+\.(alpha|beta|rc).*$/) {
            return "the prerelease stage must be separated by a hyphen, not a dot " +
                    "(Fabric cannot parse a dot here and falls back to string comparison, " +
                    "which breaks every version range against this mod)"
        }
        if (value ==~ /^\d+\.\d+\.\d+-(alpha|beta|rc)$/) {
            return "the prerelease stage needs a counter, e.g. '-beta.1' instead of '-beta'"
        }
        if (value ==~ /^\d+\.\d+\.\d+-(alpha|beta|rc)\d+$/) {
            return "the prerelease counter must be separated by a dot, e.g. '-beta.1' instead of '-beta1'"
        }
        if (value ==~ /^\d+(\.\d+){3,}.*$/) {
            return "too many numeric components - the scheme has exactly three (MAJOR.MINOR.PATCH)"
        }
        if (value ==~ /^\d+\.\d+$/) {
            return "too few numeric components - PATCH is mandatory, write '${value}.0'"
        }
        if (value ==~ /^\d+\.\d+\.\d+-(?!alpha|beta|rc).+$/) {
            return "unknown prerelease stage - only 'alpha', 'beta' and 'rc' are allowed. " +
                    "A Minecraft version, a loader name or a release channel must not appear here"
        }
        if (value ==~ /^0\d+.*$/ || value ==~ /^\d+\.0\d+.*$/ || value ==~ /^\d+\.\d+\.0\d+.*$/) {
            return "numeric components must not have leading zeros"
        }
        return "does not match the scheme"
    }

    ModVersion withBuildMetadata(String metadata) {
        if (!metadata) return this
        String cleaned = metadata.trim()
        if (!(cleaned ==~ /^[0-9A-Za-z-]+(\.[0-9A-Za-z-]+)*$/)) {
            throw new IllegalArgumentException(
                    "Invalid build metadata '${metadata}': only dot-separated alphanumerics and hyphens are allowed")
        }
        return new ModVersion(major, minor, patch, stage, preNumber, cleaned)
    }

    ModVersion withoutBuildMetadata() {
        return buildMetadata == null ? this : new ModVersion(major, minor, patch, stage, preNumber, null)
    }

    String getCoreVersion() {
        return "${major}.${minor}.${patch}"
    }

    String getBaseVersion() {
        return stage == Stage.RELEASE ? coreVersion : "${coreVersion}-${stage.id}.${preNumber}"
    }

    boolean isPrerelease() {
        return stage != Stage.RELEASE
    }

    boolean hasBuildMetadata() {
        return buildMetadata != null
    }

    String getChannel() {
        return stage.channel
    }

    String getDependencyLowerBound() {
        return prerelease ? baseVersion : "${coreVersion}-alpha"
    }

    String getBreakingBoundary() {
        return major == 0 ? "0.${minor + 1}.0" : "${major + 1}.0.0"
    }

    String getForgeRange() {
        return "[${dependencyLowerBound},${breakingBoundary})"
    }

    String getFabricRange() {
        return ">=${dependencyLowerBound} <${breakingBoundary}"
    }

    String getForgeRangeOpen() {
        return "[${dependencyLowerBound},)"
    }

    String getFabricRangeOpen() {
        return ">=${dependencyLowerBound}"
    }

    @Override
    int compareTo(ModVersion other) {
        int result = Integer.compare(major, other.major)
        if (result != 0) return result

        result = Integer.compare(minor, other.minor)
        if (result != 0) return result

        result = Integer.compare(patch, other.patch)
        if (result != 0) return result

        result = Integer.compare(stage.ordinal(), other.stage.ordinal())
        if (result != 0) return result

        return Integer.compare(preNumber, other.preNumber)
    }

    @Override
    boolean equals(Object other) {
        if (this.is(other)) return true
        if (!(other instanceof ModVersion)) return false
        return toString() == other.toString()
    }

    @Override
    int hashCode() {
        return toString().hashCode()
    }

    @Override
    String toString() {
        return hasBuildMetadata() ? "${baseVersion}+${buildMetadata}" : baseVersion
    }
}
