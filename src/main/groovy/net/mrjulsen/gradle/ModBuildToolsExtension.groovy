package net.mrjulsen.gradle

class ModBuildToolsExtension {

    String modId = null
    String displayName = null

    final PublishConfig publish = new PublishConfig()
    final JavadocConfig javadoc = new JavadocConfig()
    final VersioningConfig versioning = new VersioningConfig()
    final DependencyConfig dependencies = new DependencyConfig()
    final MigrationConfig migration = new MigrationConfig()
    final ScriptsConfig scripts = new ScriptsConfig()

    void setOverwriteWorkflows(boolean value) {
        throw new IllegalArgumentException(
                "'overwriteWorkflows' no longer exists. The workflow files are replaced on every " +
                        "setupModScripts run, everything else is only written when missing. To change " +
                        "that, use:\n\n" +
                        "  modBuildTools {\n" +
                        "      scripts { managed = ['.github/workflows/**', 'crowdin.yml'] }\n" +
                        "  }\n")
    }

    void scripts(Closure c) { delegateTo(scripts, c) }

    void publish(Closure c) { delegateTo(publish, c) }

    void javadoc(Closure c) { delegateTo(javadoc, c) }

    void versioning(Closure c) { delegateTo(versioning, c) }

    void dependencies(Closure c) { delegateTo(dependencies, c) }

    void migration(Closure c) { delegateTo(migration, c) }

    private static void delegateTo(Object target, Closure c) {
        c.delegate = target
        c.resolveStrategy = Closure.DELEGATE_FIRST
        c()
    }

    static class PublishConfig {
        boolean publishCommon = false

        String commonModule = "common"

        String localRepoDir = "${System.getProperty('user.home')}/.m2/mod-build-local"

        String mavenGroup = null

        String artifactIdFormat = '{modid}-{loader}-{mc}'

        boolean allowUnpublishedProjectDependencies = false

        boolean warnOnPlatformProfile = true

        boolean sourcesJar = true

        boolean javadocJar = true
    }

    static class JavadocConfig {
        boolean enabled = true

        String titleFormat = '{name} API {version}'

        String title = null

        String locale = "en_US"

        String encoding = "UTF-8"

        String charset = null

        String docEncoding = null

        String outputDir = "docs/api"

        boolean allowSharedOutputDir = false

        List<String> modules = null

        List<String> excludedModules = []

        List<String> excludedPackages = []

        boolean failOnError = false

        boolean quiet = true

        String doclint = "none"

        List<String> tags = []

        List<String> links = []

        Map<String, String> extraOptions = [:]

        void tag(String name, String header) {
            tag(name, header, "a")
        }

        void tag(String name, String header, String locations) {
            if (!name) throw new IllegalArgumentException("Javadoc tag name must not be empty")
            if (!header) throw new IllegalArgumentException("Javadoc tag '${name}' needs a header")

            def heading = header.endsWith(":") ? header : "${header}:"
            tags.add("${name}:${locations}:${heading}".toString())
        }

        void link(String... urls) {
            urls.each { links.add(it) }
        }

        void option(String name, String value = null) {
            extraOptions.put(name, value)
        }
    }

    static class VersioningConfig {
        boolean enabled = true

        String minecraftVersion = null

        String modVersion = null

        String platformsProperty = "enabled_platforms"

        Integer javaVersion = null

        String minecraftVersionRange = null

        String snapshotEnv = "SNAPSHOT_BUILD"
        String testEnv = "TEST_BUILD"
        String commitEnv = "GITHUB_SHA"

        String artifactNameFormat = '{modid}-{version}+mc{mc}-{loader}{build}'

        String tagFormat = 'v{version}+mc{mc}'

        boolean configureArtifactNames = true

        boolean templateResources = true

        List<String> templatedResources = [
                "**/mods.toml",
                "**/neoforge.mods.toml",
                "**/fabric.mod.json",
                "**/quilt.mod.json",
                "**/pack.mcmeta"
        ]
    }

    static class DependencyConfig {

        final Map<String, Declaration> declared = new LinkedHashMap<>()

        void require(String name, String minimumVersion, Closure c = null) {
            add(name, minimumVersion, true, false, c)
        }

        void requireAtLeast(String name, String minimumVersion, Closure c = null) {
            add(name, minimumVersion, false, false, c)
        }

        void optional(String name, String minimumVersion, Closure c = null) {
            add(name, minimumVersion, true, true, c)
        }

        private void add(String name, String minimumVersion, boolean bounded, boolean optional, Closure c) {
            if (!name) {
                throw new IllegalArgumentException("Dependency name must not be empty")
            }
            if (!(name ==~ /^[a-z][a-z0-9_]*$/)) {
                throw new IllegalArgumentException(
                        "Invalid dependency name '${name}': use lowercase letters, digits and underscores, " +
                                "so it can be used as a template variable")
            }

            ModVersion minimum
            try {
                minimum = ModVersion.parse(minimumVersion)
            } catch (IllegalArgumentException e) {
                throw new IllegalArgumentException(
                        "Dependency '${name}' has an invalid minimum version. ${e.message}", e)
            }

            def declaration = new Declaration(
                    name: name, minimum: minimum, bounded: bounded, optional: optional)

            if (c != null) delegateTo(declaration, c)

            declared.put(name, declaration)
        }

        static class Declaration {
            String name
            ModVersion minimum
            boolean bounded
            boolean optional

            String modrinth = null
            String curseforge = null

            String getForgeRange() {
                return bounded ? minimum.forgeRange : minimum.forgeRangeOpen
            }

            String getFabricRange() {
                return bounded ? minimum.fabricRange : minimum.fabricRangeOpen
            }

            String rangeFor(String loader) {
                return isFabricLike(loader) ? fabricRange : forgeRange
            }

            static boolean isFabricLike(String loader) {
                return loader != null && loader.toLowerCase() in ["fabric", "quilt"]
            }

            boolean hasPlatformIds() {
                return modrinth || curseforge
            }

            String getPublishEntry() {
                if (!hasPlatformIds()) return null

                def entry = new StringBuilder(name)
                entry << (optional ? "(optional)" : "(required)")
                if (modrinth) entry << "{modrinth:${modrinth}}"
                if (curseforge) entry << "{curseforge:${curseforge}}"
                return entry.toString()
            }
        }
    }

    static class MigrationConfig {

        String previousVersion = null
    }

    static class ScriptsConfig {

        String repository = "MisterJulsen/MC-Modding-Build-Tools"

        String ref = "main"

        String directory = "install"

        List<String> managed = [".github/workflows/**"]

        boolean overwriteAll = false

        boolean prune = false
    }
}
