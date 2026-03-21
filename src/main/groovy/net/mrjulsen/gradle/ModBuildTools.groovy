package net.mrjulsen.gradle

import groovy.json.JsonOutput
import groovy.json.JsonSlurper
import org.gradle.api.GradleException
import org.gradle.api.Plugin
import org.gradle.api.Project
import org.gradle.api.Task
import org.gradle.api.tasks.javadoc.Javadoc

class ModBuildTools implements Plugin<Project> {

    static final String GROUP            = "Mod Build Tools"
    static final String CONFIG_FILE      = ".mod-build-config.json"
    static final String PROFILE_PROP     = "buildProfile"
    static final String PROFILE_MAVEN    = "maven"
    static final String PROFILE_PLATFORM = "platform"

    @Override
    void apply(Project project) {
        def ext = project.extensions.create("modBuildTools", ModBuildToolsExtension)
        project.ext.releaseArtifacts = [:]
        project.pluginManager.apply("maven-publish")

        configureVersioning(project, ext)
        configureRepositories(project, ext)
        configureBuildProfile(project)
        configureJavadoc(project, ext)
        configureSubprojectHooks(project)
        registerRootTasks(project, ext)
        registerMetadataTask(project, ext)
        ScriptDownloader.registerScriptSetupTask(project, ext)
    }

    private static void configureBuildProfile(Project project) {
        if (project != project.rootProject) return

        project.allprojects { p ->
            p.ext.isMavenBuild    = { -> resolveProfile(project) == PROFILE_MAVEN }
            p.ext.isPlatformBuild = { -> resolveProfile(project) != PROFILE_MAVEN }
        }
    }

    static String resolveProfile(Project project) {
        return project.rootProject.findProperty(PROFILE_PROP)?.toString()?.toLowerCase()
                ?: PROFILE_PLATFORM
    }

    private static void configureVersioning(Project project, ModBuildToolsExtension ext) {
        if (project != project.rootProject) return

        project.afterEvaluate {
            if (!ext.versioning.enabled) return

            def cfg     = ext.versioning
            def mc      = cfg.minecraftVersion ?: project.findProperty("minecraft_version") ?: "mc"
            def mod     = cfg.modVersion       ?: project.findProperty("mod_version")       ?: "1.0"
            def channel = cfg.releaseChannel   ?: project.findProperty("release_channel")   ?: "release"

            def isFullRelease = (channel == cfg.fullReleaseChannel || channel == "release")

            def base = isFullRelease
                    ? cfg.releaseVersionFormat
                    .replace("{mc}", mc.toString())
                    .replace("{mod}", mod.toString())
                    : cfg.versionFormat
                    .replace("{mc}", mc.toString())
                    .replace("{mod}", mod.toString())
                    .replace("{channel}", channel.toString())

            def snapshotNum = System.getenv(cfg.snapshotEnv)
            def testNum     = System.getenv(cfg.testEnv)
            def timestamp   = new Date().format("yyyyMMdd")

            def finalVersion = snapshotNum
                    ? cfg.snapshotVersionFormat
                    .replace("{base}", base)
                    .replace("{value}", snapshotNum)
                    .replace("{timestamp}", timestamp)
                    : testNum
                    ? cfg.testVersionFormat
                    .replace("{base}", base)
                    .replace("{value}", testNum)
                    .replace("{timestamp}", timestamp)
                    : base

            project.allprojects { it.version = finalVersion }
            project.logger.lifecycle("[ModBuildTools] Version: ${finalVersion} (profile: ${resolveProfile(project)})")
        }
    }

    private static void configureRepositories(Project project, ModBuildToolsExtension ext) {
        project.afterEvaluate {
            project.allprojects { p ->
                p.plugins.withId("maven-publish") {
                    p.publishing {
                        repositories {
                            maven {
                                name = "Local"
                                url  = p.uri(ext.publish.localRepoDir)
                            }
                        }
                    }
                }
            }
        }
    }

    private static void registerRootTasks(Project project, ModBuildToolsExtension ext) {
        if (project != project.rootProject) return

        project.gradle.projectsEvaluated {
            def publishableProjects = project.subprojects.findAll {
                ext.publish.publishCommon || it.name != "common"
            }

            project.tasks.register("publishLocal") {
                group       = GROUP
                description = "Publishes all mod artifacts to the local Maven repository (maven profile)"
                dependsOn "generateReleaseMetadata"
                dependsOn publishableProjects.collect { sub ->
                    sub.tasks.matching { it.name == "remapJar" }
                }
                dependsOn publishableProjects.collect { sub ->
                    sub.tasks.matching { it.name == "publishAllPublicationsToLocalRepository" }
                }
            }
        }

        project.tasks.register("buildWithMetadata") {
            group       = GROUP
            description = "Builds all subprojects (platform profile) and generates metadata.json"
            dependsOn project.subprojects.collect { it.tasks.named("build") }
            dependsOn "generateReleaseMetadata"
        }

        project.tasks.register("printModVersion") {
            group = GROUP
            doLast { println project.version }
        }

        project.tasks.register("exportModVersion") {
            group = GROUP
            doLast {
                def out = project.file("build/mod_version.txt")
                out.parentFile.mkdirs()
                out.text = project.version.toString()
            }
        }

        project.tasks.register("generateJavadoc", Javadoc) {
            group = GROUP
            source project.subprojects.collect { it.sourceSets.main.allJava }
            classpath = project.files(
                    project.subprojects.collect { it.sourceSets.main.compileClasspath }
            )
            destinationDir = project.file("docs")
            options.encoding = "UTF-8"
        }
    }

    private static void configureJavadoc(Project project, ModBuildToolsExtension ext) {
        if (!ext.javadoc.enabled) return

        project.allprojects {
            tasks.withType(Javadoc).configureEach { Javadoc javadocTask ->
                javadocTask.options.encoding = ext.javadoc.encoding

                if (!(javadocTask.options instanceof org.gradle.external.javadoc.StandardJavadocDocletOptions)) return

                def stdOpts = (org.gradle.external.javadoc.StandardJavadocDocletOptions) javadocTask.options
                stdOpts.locale  = ext.javadoc.locale
                stdOpts.charSet = ext.javadoc.charset

                if (ext.javadoc.title) {
                    stdOpts.windowTitle(ext.javadoc.title)
                    stdOpts.docTitle(ext.javadoc.title)
                }

                if (!ext.javadoc.tags.isEmpty()) {
                    stdOpts.tags(ext.javadoc.tags as String[])
                }
            }
        }
    }

    private static void configureSubprojectHooks(Project project) {
        if (project != project.rootProject) return

        project.subprojects { sub ->
            sub.plugins.withId("dev.architectury.loom") {
                sub.afterEvaluate {
                    def remap   = sub.tasks.named("remapJar").get()
                    def jarFile = remap.archiveFile.get().asFile

                    project.ext.releaseArtifacts[sub.name] = new ReleaseArtifact(
                            module  : sub.name,
                            filename: jarFile.name,
                            platform: sub.name
                    )

                    sub.logger.lifecycle("[ModBuildTools] Registered artifact: ${sub.name} → ${jarFile.name}")
                }
            }
        }
    }

    static String mapReleaseChannel(Project project, String rawChannel) {
        def cfgFile = project.rootProject.file(CONFIG_FILE)
        def json    = new JsonSlurper().parse(cfgFile)
        def channel = rawChannel.toLowerCase()

        for (entry in json.release_channels.entrySet()) {
            if (entry.value*.toLowerCase().contains(channel)) {
                return entry.key
            }
        }

        project.logger.warn("[ModBuildTools] Unknown release channel '${rawChannel}', defaulting to 'release'")
        return "release"
    }

    private static void registerMetadataTask(Project project, ModBuildToolsExtension ext) {
        if (project != project.rootProject) return

        project.tasks.register("generateReleaseMetadata") { Task t ->
            group       = GROUP
            description = "Generates metadata.json used by the GitHub release workflow"

            project.subprojects { sub ->
                sub.plugins.withId("dev.architectury.loom") {
                    t.dependsOn sub.tasks.named("remapJar")
                }
            }

            doLast { generateMetadata(project, ext) }
        }
    }

    private static void generateMetadata(Project project, ModBuildToolsExtension ext) {
        def cfgFile = project.file(CONFIG_FILE)
        if (!cfgFile.exists()) {
            throw new GradleException("${CONFIG_FILE} not found in project root")
        }

        def jsonConfig  = new JsonSlurper().parse(cfgFile)
        def cfg         = ext.versioning

        def modId       = ext.modId       ?: project.findProperty("mod_id")            ?: ""
        def displayName = ext.displayName ?: project.findProperty("display_name")      ?: ""
        def mcVersion   = cfg.minecraftVersion ?: project.findProperty("minecraft_version") ?: "mc"
        def modVersion  = cfg.modVersion       ?: project.findProperty("mod_version")       ?: "1.0"
        def rawChannel  = cfg.releaseChannel   ?: project.findProperty("release_channel")   ?: "release"
        def mavenGroup  = ext.publish.mavenGroup ?: project.findProperty("maven_group") ?: ""

        def mappedChannel = mapReleaseChannel(project, rawChannel.toString())
        def fullVersion   = project.version.toString()

        def artifacts = [:]
        jsonConfig.modules.each { module ->
            def artifact = project.ext.releaseArtifacts[module]
            if (!artifact) {
                throw new GradleException(
                        "[ModBuildTools] No release artifact for module '${module}'. " +
                                "Make sure the subproject applies 'dev.architectury.loom' and " +
                                "has been evaluated before generateReleaseMetadata runs."
                )
            }
            artifacts[module] = [
                    filename: artifact.filename.replaceFirst(/\.[^.]+$/, ""),
                    platform: artifact.platform
            ]
        }

        def timestamp    = new Date().format("yyyy-MM-dd'T'HH:mm:ss'Z'", TimeZone.getTimeZone("UTC"))
        def localRepoDir = ext.publish.localRepoDir
        def ciRunId = System.getenv("GITHUB_RUN_ID") ?: ""

        def output = [
                generated_at        : timestamp,
                mod_id              : modId,
                display_name        : displayName,
                maven_group         : mavenGroup,
                maven_local_path    : localRepoDir,
                ci_run_id           : ciRunId,
                mod_version         : modVersion,
                minecraft_version   : mcVersion,
                release_channel_raw : rawChannel,
                full_version        : fullVersion,
                release_channel     : mappedChannel,
                artifacts           : artifacts
        ]

        def outFile = project.file("metadata.json")
        outFile.text = JsonOutput.prettyPrint(JsonOutput.toJson(output))
        project.logger.lifecycle("[ModBuildTools] Generated metadata.json → ${outFile.absolutePath}")
    }
}