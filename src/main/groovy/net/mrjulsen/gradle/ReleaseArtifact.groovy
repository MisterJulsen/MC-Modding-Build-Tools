package net.mrjulsen.gradle

/**
 * Represents a single remapped JAR produced by an Architectury Loom subproject.
 * Populated automatically by ModBuildTools.configureSubprojectHooks().
 */
class ReleaseArtifact {
    /** Subproject name, e.g. "fabric", "forge", "neoforge" */
    String module

    /** Full filename including extension, e.g. "MyMod-fabric-1.21.1-2.0.0.jar" */
    String filename

    /** Platform identifier written into metadata.json, same as module name */
    String platform
}