package net.mrjulsen.gradle

class ModBuildToolsExtension {

    String modId      = null
    String displayName = null

    boolean overwriteWorkflows = false

    final PublishConfig   publish   = new PublishConfig()
    final JavadocConfig   javadoc   = new JavadocConfig()
    final VersioningConfig versioning = new VersioningConfig()

    void publish(Closure c)    { c.delegate = publish;    c.resolveStrategy = Closure.DELEGATE_FIRST; c() }
    void javadoc(Closure c)    { c.delegate = javadoc;    c.resolveStrategy = Closure.DELEGATE_FIRST; c() }
    void versioning(Closure c) { c.delegate = versioning; c.resolveStrategy = Closure.DELEGATE_FIRST; c() }

    static class PublishConfig {
        boolean publishCommon = false
        String localRepoDir = "${System.getProperty('user.home')}/.m2/mod-build-local"
        String mavenGroup = null
    }

    static class JavadocConfig {
        boolean enabled = true
        String title = null
        List<String> tags = []
        String locale   = "en_US"
        String encoding = "UTF-8"
        String charset  = "UTF-8"
    }

    static class VersioningConfig {
        boolean enabled = true
        String minecraftVersion = null
        String modVersion       = null
        String releaseChannel   = null
        String fullReleaseChannel = "release"
        String snapshotEnv = "SNAPSHOT_BUILD"
        String testEnv     = "TEST_BUILD"

        // Version format strings.
        // Placeholders: {mc}, {mod}, {channel}, {base}, {value}, {timestamp}
        //
        // fullRelease:  mc=1.21.1 mod=2.0.0              → "1.21.1-2.0.0"
        // channel:      mc=1.21.1 mod=2.0.0 channel=beta → "1.21.1-beta-2.0.0"
        // snapshot:     base=1.21.1-2.0.0 value=42       → "1.21.1-2.0.0-SNAPSHOT+build.42"
        // test (PR):    base=1.21.1-2.0.0 value=7        → "1.21.1-2.0.0-TEST+pr.7"
        String releaseVersionFormat  = "{mc}-{mod}"
        String versionFormat         = "{mc}-{channel}-{mod}"
        String snapshotVersionFormat = "{base}-SNAPSHOT+build.{value}"
        String testVersionFormat     = "{base}-TEST+pr.{value}"
    }
}