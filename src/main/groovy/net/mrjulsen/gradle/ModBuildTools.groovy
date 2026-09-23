package net.mrjulsen.gradle

import groovy.json.JsonOutput
import groovy.json.JsonSlurper
import org.apache.tools.ant.filters.ReplaceTokens
import org.gradle.api.GradleException
import org.gradle.api.JavaVersion
import org.gradle.api.Plugin
import org.gradle.api.Project
import org.gradle.api.Task
import org.gradle.api.artifacts.ProjectDependency
import org.gradle.api.publish.maven.MavenPublication

class ModBuildTools implements Plugin<Project> {

    static final String GROUP            = "Mod Build Tools"
    static final String CONFIG_FILE      = "build_tools.config"
    static final String PROFILE_PROP     = "buildProfile"
    static final String PROFILE_MAVEN    = "maven"
    static final String PROFILE_PLATFORM = "platform"

    private static final List<String> RETIRED_PROPERTIES = ["release_channel"]

    @Override
    void apply(Project project) {
        def ext = project.extensions.create("modBuildTools", ModBuildToolsExtension)
        project.ext.releaseArtifacts = [:]
        project.pluginManager.apply("maven-publish")

        exposeAccessors(project, ext)
        configureVersioning(project, ext)
        configureRepositories(project, ext)
        configurePublications(project, ext)
        configureBuildProfile(project)
        JavadocSupport.configureAll(project, ext)
        configureSubprojectHooks(project, ext)
        configureResourceTemplating(project, ext)
        registerVersionTasks(project, ext)
        registerRootTasks(project, ext)
        registerMetadataTask(project, ext)
        ChangelogGenerator.register(project, ext)
        ScriptDownloader.registerScriptSetupTask(project, ext)
    }

    static ModVersion resolveVersion(Project project, ModBuildToolsExtension ext) {
        def raw = trimmed(ext.versioning.modVersion) ?: trimmed(project.rootProject.findProperty("mod_version"))

        if (!raw) {
            throw new GradleException(
                    "[ModBuildTools] No mod version configured.\n" +
                            "Add 'mod_version=<MAJOR.MINOR.PATCH[-(alpha|beta|rc).N]>' to gradle.properties, " +
                            "for example 'mod_version=3.1.0-beta.2'.\n" +
                            "See docs/VERSIONING.md.")
        }

        ModVersion version
        try {
            version = ModVersion.parse(raw)
        } catch (IllegalArgumentException e) {
            throw new GradleException("[ModBuildTools] ${e.message}", e)
        }

        def metadata = resolveBuildMetadata(ext.versioning)
        return metadata ? version.withBuildMetadata(metadata) : version
    }

    private static String resolveBuildMetadata(ModBuildToolsExtension.VersioningConfig cfg) {
        def snapshot = sanitizeIdentifier(System.getenv(cfg.snapshotEnv))
        def test     = sanitizeIdentifier(System.getenv(cfg.testEnv))

        if (!snapshot && !test) return null

        def parts = snapshot ? ["build", snapshot] : ["pr", test]

        def commit = sanitizeIdentifier(System.getenv(cfg.commitEnv))
        if (commit) parts << commit.take(7)

        return parts.join(".")
    }

    static String resolveMinecraftVersion(Project project, ModBuildToolsExtension ext) {
        def value = trimmed(ext.versioning.minecraftVersion) ?:
                trimmed(project.rootProject.findProperty("minecraft_version"))

        if (!value) {
            throw new GradleException(
                    "[ModBuildTools] No Minecraft version configured. " +
                            "Add 'minecraft_version=1.20.1' to gradle.properties.")
        }
        return value
    }

    static String resolveModId(Project project, ModBuildToolsExtension ext) {
        def value = trimmed(ext.modId) ?:
                trimmed(project.rootProject.findProperty("mod_id")) ?:
                trimmed(project.rootProject.findProperty("archives_name"))

        if (!value) {
            throw new GradleException(
                    "[ModBuildTools] No mod id configured. " +
                            "Add 'mod_id=mymod' to gradle.properties or set modId in the modBuildTools block.")
        }
        return value
    }

    static String resolveDisplayName(Project project, ModBuildToolsExtension ext) {
        return trimmed(ext.displayName) ?:
                trimmed(project.rootProject.findProperty("display_name")) ?:
                resolveModId(project, ext)
    }

    static String resolveMavenGroup(Project project, ModBuildToolsExtension ext) {
        return trimmed(ext.publish.mavenGroup) ?:
                trimmed(project.rootProject.findProperty("maven_group")) ?:
                trimmed(project.rootProject.group) ?: ""
    }

    private static void configureVersioning(Project project, ModBuildToolsExtension ext) {
        if (project != project.rootProject) return

        project.afterEvaluate {
            if (!ext.versioning.enabled) return

            RETIRED_PROPERTIES.each { name ->
                if (project.rootProject.hasProperty(name)) {
                    project.logger.warn(
                            "[ModBuildTools] '${name}' in gradle.properties is ignored. " +
                                    "The release channel is derived from the version suffix - remove the property.")
                }
            }

            def version = resolveVersion(project, ext)
            project.allprojects { it.version = version.toString() }

            verifyMigration(project, ext, version, false)

            project.logger.lifecycle(
                    "[ModBuildTools] Version: ${version} " +
                            "(channel: ${version.channel}, profile: ${resolveProfile(project)})")
        }
    }

    static String artifactBaseName(Project project, ModBuildToolsExtension ext, String loader) {
        return expandNameFormat(ext.versioning.artifactNameFormat, project, ext, loader)
    }

    static String gitTag(Project project, ModBuildToolsExtension ext) {
        return expandNameFormat(ext.versioning.tagFormat, project, ext, null)
    }

    static String mavenArtifactId(Project project, ModBuildToolsExtension ext, String loader) {
        return expandNameFormat(ext.publish.artifactIdFormat, project, ext, loader)
    }

    private static String expandNameFormat(String format, Project project,
                                           ModBuildToolsExtension ext, String loader) {
        def version = resolveVersion(project, ext)
        def buildSuffix = version.hasBuildMetadata() ? "-${version.buildMetadata}" : ""

        return format
                .replace("{modid}", resolveModId(project, ext))
                .replace("{version}", version.baseVersion)
                .replace("{fullversion}", version.toString())
                .replace("{mc}", resolveMinecraftVersion(project, ext))
                .replace("{loader}", loader ?: "")
                .replace("{build}", buildSuffix.toString())
    }

    static Map<String, String> templateVariables(Project project, ModBuildToolsExtension ext, String loader) {
        def version = resolveVersion(project, ext)
        def mcVersion = resolveMinecraftVersion(project, ext)

        def vars = new LinkedHashMap<String, String>()

        vars["version"]            = version.toString()
        vars["mod_version"]        = version.toString()
        vars["version_base"]       = version.baseVersion
        vars["version_core"]       = version.coreVersion
        vars["version_major"]      = String.valueOf(version.major)
        vars["version_minor"]      = String.valueOf(version.minor)
        vars["version_patch"]      = String.valueOf(version.patch)
        vars["version_stage"]      = version.stage.id ?: "release"
        vars["version_prerelease"] = version.prerelease ? String.valueOf(version.preNumber) : ""
        vars["is_prerelease"]      = String.valueOf(version.prerelease)
        vars["build_metadata"]     = version.buildMetadata ?: ""
        vars["release_channel"]    = version.channel

        vars["mod_id"]             = resolveModId(project, ext)
        vars["mod_name"]           = resolveDisplayName(project, ext)
        vars["minecraft_version"]  = mcVersion
        vars["maven_group"]        = resolveMavenGroup(project, ext)
        vars["git_tag"]            = gitTag(project, ext)

        if (loader) {
            vars["loader"]      = loader
            vars["loader_name"] = loader.capitalize()
        }

        if (trimmed(ext.versioning.minecraftVersionRange)) {
            vars["minecraft_version_range"] = ext.versioning.minecraftVersionRange.replace("{mc}", mcVersion)
        }

        ext.dependencies.declared.each { String name, ModBuildToolsExtension.DependencyConfig.Declaration dep ->
            vars["${name}_version".toString()]      = dep.minimum.toString()
            vars["${name}_range".toString()]        = dep.rangeFor(loader)
            vars["${name}_range_forge".toString()]  = dep.forgeRange
            vars["${name}_range_fabric".toString()] = dep.fabricRange
        }

        return vars
    }

    private static void configureResourceTemplating(Project project, ModBuildToolsExtension ext) {
        if (project != project.rootProject) return

        project.subprojects { sub ->
            sub.plugins.withId("java") {
                sub.tasks.matching { it.name == "processResources" }.configureEach { task ->
                    if (!ext.versioning.enabled || !ext.versioning.templateResources) return

                    task.inputs.property("modBuildToolsVariables",
                            project.provider { templateVariables(project, ext, sub.name).toString() })

                    task.filesMatching(ext.versioning.templatedResources) { details ->
                        details.filter(
                                [beginToken: '${',
                                 endToken  : '}',
                                 tokens    : templateVariables(project, ext, sub.name)],
                                ReplaceTokens)
                    }
                }
            }
        }
    }

    private static void verifyMigration(Project project, ModBuildToolsExtension ext,
                                       ModVersion version, boolean required) {
        def previous = trimmed(ext.migration.previousVersion)

        if (!previous) {
            if (required) {
                throw new GradleException(
                        "[ModBuildTools] Nothing to verify: no previous version configured.\n" +
                                "Set the highest version ever published under the old scheme:\n\n" +
                                "  modBuildTools {\n" +
                                "      migration { previousVersion = \"1.20.1-3.0.28\" }\n" +
                                "  }\n")
            }
            return
        }

        def candidate = version.withoutBuildMetadata().toString()
        def problems = VersionOrdering.findOrderingProblems(previous, candidate)

        if (problems.isEmpty()) {
            project.logger.info(
                    "[ModBuildTools] Version ordering verified: '${candidate}' ranks above '${previous}' " +
                            "on Forge/NeoForge and Fabric.")
            return
        }

        throw new GradleException(
                "[ModBuildTools] Version '${candidate}' would break already published dependents.\n\n" +
                        "  last published under the old scheme: ${previous}\n" +
                        "  current:                             ${candidate}\n\n" +
                        problems.collect { "  - ${it}" }.join("\n\n") + "\n\n" +
                        "Old versions began with the Minecraft version, so their first component was '1'. " +
                        "Dropping that prefix moves the real MAJOR to the front, which only ranks higher " +
                        "when MAJOR is at least 2.\n" +
                        "Either raise MAJOR, or clear migration.previousVersion once every dependent has " +
                        "been rebuilt against the new scheme.\n" +
                        "See docs/VERSIONING.md section 12.")
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

    private static void exposeAccessors(Project project, ModBuildToolsExtension ext) {
        project.allprojects { p ->
            p.ext.modVersionInfo = { -> resolveVersion(project, ext) }
            p.ext.modTemplateVariables = { String loader = null -> templateVariables(project, ext, loader) }
        }
    }

    static File resolveLocalRepoDir(Project project, ModBuildToolsExtension ext) {
        def configured = trimmed(ext.publish.localRepoDir)

        if (!configured) {
            throw new GradleException(
                    "[ModBuildTools] No local Maven directory configured. " +
                            "Set publish.localRepoDir in the modBuildTools block.")
        }

        return project.rootProject.file(configured)
    }

    static boolean isPublishable(Project sub, ModBuildToolsExtension ext) {
        if (ext.publish.publishCommon) return true
        return sub.name != trimmed(ext.publish.commonModule)
    }

    static List<Project> publishableProjects(Project project, ModBuildToolsExtension ext) {
        return project.subprojects.findAll { isPublishable(it, ext) }.toList()
    }

    private static void configureRepositories(Project project, ModBuildToolsExtension ext) {
        if (project != project.rootProject) return

        project.subprojects { sub ->
            sub.plugins.withId("maven-publish") {
                sub.afterEvaluate {
                    if (!ModBuildTools.isPublishable(sub, ext)) return

                    def target = ModBuildTools.resolveLocalRepoDir(project, ext)
                    sub.publishing.repositories.maven { repo ->
                        repo.name = "Local"
                        repo.url = target.toURI()
                    }
                }
            }
        }
    }

    private static void configurePublications(Project project, ModBuildToolsExtension ext) {
        if (project != project.rootProject) return

        project.subprojects { sub ->
            sub.plugins.withId("maven-publish") {
                sub.afterEvaluate {
                    if (!ext.versioning.enabled) return
                    if (!ModBuildTools.isPublishable(sub, ext)) return

                    def artifactId = mavenArtifactId(project, ext, sub.name)
                    sub.publishing.publications.withType(MavenPublication).configureEach { pub ->
                        pub.artifactId = artifactId
                    }
                }
            }
        }
    }

    static Map<String, Set<String>> findUnpublishedProjectDependencies(
            Project project, ModBuildToolsExtension ext) {

        def publishable = publishableProjects(project, ext)
        def publishablePaths = publishable*.path as Set

        def problems = new LinkedHashMap<String, Set<String>>()

        publishable.each { sub ->
            def leaked = new LinkedHashSet<String>()

            ["apiElements", "runtimeElements"].each { name ->
                def conf = sub.configurations.findByName(name)
                if (conf == null) return

                conf.allDependencies.withType(ProjectDependency).each { dep ->
                    def path = dep.dependencyProject.path
                    if (!publishablePaths.contains(path)) leaked.add(path)
                }
            }

            if (!leaked.isEmpty()) problems.put(sub.path, leaked)
        }

        return problems
    }

    private static void verifyPublishing(Project project, ModBuildToolsExtension ext) {
        def problems = findUnpublishedProjectDependencies(project, ext)

        if (problems.isEmpty()) return

        def detail = problems.collect { path, leaked ->
            "  ${path} depends on ${leaked.join(', ')}"
        }.join("\n")

        if (ext.publish.allowUnpublishedProjectDependencies) {
            project.logger.warn(
                    "[ModBuildTools] Published metadata references modules that are not published:\n" +
                            detail + "\nConsumers will fail to resolve them.")
            return
        }

        throw new GradleException(buildUnpublishedDependencyFailure(ext, detail))
    }

    private static String buildUnpublishedDependencyFailure(ModBuildToolsExtension ext, String detail) {
        def common = trimmed(ext.publish.commonModule) ?: "common"

        return """
[ModBuildTools] Refusing to publish: the generated POM and Gradle module metadata would
reference modules that are not being published.

${detail}

A consumer of the published artifact would fail with 'could not resolve' on those
coordinates.

Either publish the module:

    modBuildTools { publish { publishCommon = true } }

or, if its classes are bundled into the platform jars, declare the dependency in a
configuration that is not published. The Architectury layout uses a custom
configuration instead of 'implementation':

    configurations {
        common
        compileClasspath.extendsFrom common
        runtimeClasspath.extendsFrom common
    }
    dependencies {
        common project(path: ':${common}', configuration: 'namedElements')
    }

To publish anyway and accept the broken metadata, set
publish.allowUnpublishedProjectDependencies = true.
"""
    }

    private static void configureSubprojectHooks(Project project, ModBuildToolsExtension ext) {
        if (project != project.rootProject) return

        project.subprojects { sub ->
            sub.plugins.withId("dev.architectury.loom") {
                configureExtraJars(ext, sub)
                sub.afterEvaluate {
                    if (ext.versioning.enabled && ext.versioning.configureArtifactNames) {
                        applyArtifactNames(project, ext, sub)
                    }
                    registerReleaseArtifact(project, ext, sub)
                }
            }
        }
    }

    private static void configureExtraJars(ModBuildToolsExtension ext, Project sub) {
        sub.plugins.withId("java") {
            if (ext.publish.sourcesJar) sub.extensions.getByName("java").withSourcesJar()
            if (ext.publish.javadocJar) sub.extensions.getByName("java").withJavadocJar()
        }
    }

    private static void applyArtifactNames(Project project, ModBuildToolsExtension ext, Project sub) {
        def base = artifactBaseName(project, ext, sub.name)

        setArchiveFileName(sub, "remapJar", "${base}.jar")
        setArchiveFileName(sub, "remapSourcesJar", "${base}-sources.jar")
        setArchiveFileName(sub, "javadocJar", "${base}-javadoc.jar")
    }

    private static void setArchiveFileName(Project sub, String taskName, String fileName) {
        if (!sub.tasks.names.contains(taskName)) return
        sub.tasks.named(taskName).configure { t -> t.archiveFileName.set(fileName.toString()) }
    }

    private static void registerReleaseArtifact(Project project, ModBuildToolsExtension ext, Project sub) {
        def main = archiveFileName(sub, "remapJar")
        if (!main) return

        project.ext.releaseArtifacts[sub.name] = new ReleaseArtifact(
                filename  : main,
                sourcesJar: archiveFileName(sub, "remapSourcesJar"),
                javadocJar: archiveFileName(sub, "javadocJar")
        )

        sub.logger.lifecycle("[ModBuildTools] Registered artifact: ${sub.name} -> ${main}")
    }

    private static String archiveFileName(Project sub, String taskName) {
        if (!sub.tasks.names.contains(taskName)) return null
        return sub.tasks.named(taskName).get().archiveFile.get().asFile.name
    }

    private static void registerVersionTasks(Project project, ModBuildToolsExtension ext) {
        if (project != project.rootProject) return

        project.tasks.register("printVersionInfo") {
            group       = GROUP
            description = "Prints everything derived from mod_version and minecraft_version"
            doLast { printVersionInfo(project, ext) }
        }

        project.tasks.register("verifyVersionMigration") {
            group       = GROUP
            description = "Verifies the current version still satisfies dependency ranges published " +
                    "under the old versioning scheme"
            doLast { verifyMigration(project, ext, resolveVersion(project, ext), true) }
        }
    }

    private static void printVersionInfo(Project project, ModBuildToolsExtension ext) {
        def version = resolveVersion(project, ext)
        def mcVersion = resolveMinecraftVersion(project, ext)
        def modules = resolveLoaders(project, ext)

        def out = new StringBuilder("\n")
        out << "Mod              ${resolveModId(project, ext)} (${resolveDisplayName(project, ext)})\n"
        out << "Minecraft        ${mcVersion} (Java ${resolveJavaVersion(project, ext)})\n"
        out << "Loaders          ${modules.join(', ')}\n"
        out << "\n"
        out << "Version field    ${version}\n"
        out << "  base           ${version.baseVersion}\n"
        out << "  core           ${version.coreVersion}\n"
        out << "  stage          ${version.stage.id ? "${version.stage.id}.${version.preNumber}" : 'release'}\n"
        out << "  channel        ${version.channel}\n"
        out << "  build metadata ${version.buildMetadata ?: '-'}\n"
        out << "Git tag          ${gitTag(project, ext)}\n"

        out << "\nArtifacts\n"
        modules.each { module ->
            out << "  ${module.padRight(14)} ${artifactBaseName(project, ext, module)}.jar\n"
        }

        out << "\nMaven\n"
        def group = resolveMavenGroup(project, ext)
        modules.each { module ->
            out << "  ${module.padRight(14)} ${group}:${mavenArtifactId(project, ext, module)}:${version}\n"
        }

        if (!ext.dependencies.declared.isEmpty()) {
            out << "\nDependencies\n"
            ext.dependencies.declared.each { name, dep ->
                out << "  ${name.padRight(14)} minimum ${dep.minimum}${dep.bounded ? '' : '  (no upper bound)'}\n"
                out << "  ${''.padRight(14)}   Forge/NeoForge  ${dep.forgeRange}\n"
                out << "  ${''.padRight(14)}   Fabric          ${dep.fabricRange}\n"
            }
        }

        out << "\nTemplate variables (module '${modules ? modules.first() : 'n/a'}')\n"
        templateVariables(project, ext, modules ? modules.first() : null).each { key, value ->
            out << "  \${${key}}".padRight(34) << " = ${value}\n"
        }

        def previous = trimmed(ext.migration.previousVersion)
        out << "\nMigration guard  " << (previous ? "active, previous version ${previous}" : "inactive") << "\n"

        println out.toString()
    }

    private static void printPublishInfo(Project project, ModBuildToolsExtension ext) {
        def version = resolveVersion(project, ext)
        def group = resolveMavenGroup(project, ext)
        def target = resolveLocalRepoDir(project, ext)
        def publishableNames = publishableProjects(project, ext)*.name as Set

        def out = new StringBuilder("\n")
        out << "Local repository ${target.absolutePath}\n"
        out << "  configured as  ${ext.publish.localRepoDir}\n"
        out << "  exists         ${target.isDirectory() ? 'yes' : 'no, created on first publish'}\n"
        out << "Build profile    ${resolveProfile(project)}\n"
        out << "Maven group      ${group}\n"

        out << "\nModules\n"
        project.subprojects.sort { it.name }.each { sub ->
            if (publishableNames.contains(sub.name)) {
                out << "  ${sub.name.padRight(14)} ${group}:${mavenArtifactId(project, ext, sub.name)}:${version}\n"
            } else {
                out << "  ${sub.name.padRight(14)} not published (publish.publishCommon = false)\n"
            }
        }

        def problems = findUnpublishedProjectDependencies(project, ext)
        out << "\nMetadata check   "
        if (problems.isEmpty()) {
            out << "ok, no unpublished modules referenced\n"
        } else {
            out << "FAILING\n"
            problems.each { path, leaked ->
                out << "  ${path} depends on ${leaked.join(', ')}\n"
            }
        }

        println out.toString()
    }

    private static void registerRootTasks(Project project, ModBuildToolsExtension ext) {
        if (project != project.rootProject) return

        project.tasks.register("verifyPublishing") {
            group       = GROUP
            description = "Checks that the published metadata does not reference unpublished modules"
            doLast { verifyPublishing(project, ext) }
        }

        project.tasks.register("printPublishInfo") {
            group       = GROUP
            description = "Prints the local Maven target and which modules get published"
            doLast { printPublishInfo(project, ext) }
        }

        project.gradle.projectsEvaluated {
            def publishable = publishableProjects(project, ext)

            project.tasks.register("publishLocal") {
                group       = GROUP
                description = "Publishes all mod artifacts to the local Maven repository (maven profile)"
                dependsOn "verifyPublishing"
                dependsOn "generateReleaseMetadata"
                dependsOn publishable.collect { sub ->
                    sub.tasks.matching { it.name == "remapJar" }
                }
                dependsOn publishable.collect { sub ->
                    sub.tasks.matching { it.name == "publishAllPublicationsToLocalRepository" }
                }

                doFirst {
                    if (ext.publish.warnOnPlatformProfile && resolveProfile(project) != PROFILE_MAVEN) {
                        project.logger.warn(
                                "[ModBuildTools] Publishing with the '${resolveProfile(project)}' profile. " +
                                        "Maven consumers get the bundled jar and its shaded dependencies. " +
                                        "Use -PbuildProfile=maven for a slim artifact.")
                    }
                }
            }
        }

        project.tasks.register("buildWithMetadata") {
            group       = GROUP
            description = "Builds all subprojects (platform profile) and generates metadata.json"
            dependsOn project.subprojects.collect { it.tasks.named("build") }
            dependsOn "generateReleaseMetadata"
        }

        project.tasks.register("buildRelease") {
            group       = GROUP
            description = "Builds main, sources and javadoc jars for every loader, plus metadata and changelog"
            dependsOn "buildWithMetadata"
            dependsOn "generateChangelog"

            project.subprojects { sub ->
                sub.plugins.withId("dev.architectury.loom") {
                    ["remapJar", "remapSourcesJar", "javadocJar"].each { name ->
                        dependsOn sub.tasks.matching { it.name == name }
                    }
                }
            }
        }

        JavadocSupport.registerAggregateTask(project, ext)
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

    static Map readConfig(Project project) {
        def cfgFile = project.rootProject.file(CONFIG_FILE)
        if (!cfgFile.exists()) {
            throw new GradleException("[ModBuildTools] ${CONFIG_FILE} not found in project root")
        }
        return new JsonSlurper().parse(cfgFile) as Map
    }

    static List<String> declaredLoaders(Project project, ModBuildToolsExtension ext) {
        def raw = trimmed(project.rootProject.findProperty(ext.versioning.platformsProperty))
        if (!raw) return []
        return raw.split(/[,\s]+/).collect { it.trim() }.findAll { !it.isEmpty() }
    }

    static List<String> loomModules(Project project) {
        return project.subprojects
                .findAll { it.plugins.hasPlugin("dev.architectury.loom") }
                .collect { it.name }
                .sort()
    }

    static List<String> resolveLoaders(Project project, ModBuildToolsExtension ext) {
        def available = loomModules(project)
        def declared = declaredLoaders(project, ext)

        if (declared.isEmpty()) {
            if (available.isEmpty()) {
                throw new GradleException(
                        "[ModBuildTools] No mod loaders found. Add " +
                                "'${ext.versioning.platformsProperty}=fabric,forge' to gradle.properties " +
                                "or apply 'dev.architectury.loom' in the loader subprojects.")
            }
            return available
        }

        def missing = declared.findAll { !available.contains(it) }
        if (!missing.isEmpty()) {
            project.logger.warn(
                    "[ModBuildTools] ${ext.versioning.platformsProperty} lists " +
                            "${missing.join(', ')}, which ${missing.size() == 1 ? 'is' : 'are'} not a built " +
                            "subproject - skipped. Built: ${available.join(', ') ?: 'none'}")
        }

        def active = declared.findAll { available.contains(it) }
        if (active.isEmpty()) {
            throw new GradleException(
                    "[ModBuildTools] None of the loaders in ${ext.versioning.platformsProperty} " +
                            "(${declared.join(', ')}) is a subproject applying 'dev.architectury.loom'.")
        }
        return active
    }

    static int resolveJavaVersion(Project project, ModBuildToolsExtension ext) {
        if (ext.versioning.javaVersion) return ext.versioning.javaVersion

        def loaders = loomModules(project)
        for (String name : loaders) {
            def sub = project.subprojects.find { it.name == name }
            def java = sub?.extensions?.findByName("java")
            if (java == null) continue

            def toolchain = java.toolchain?.languageVersion?.getOrNull()
            if (toolchain != null) return toolchain.asInt()

            def target = java.targetCompatibility?.toString()
            if (target) return Integer.parseInt(target.replaceFirst(/^1\./, ""))
        }

        return Integer.parseInt(JavaVersion.current().majorVersion)
    }

    static String expandTitle(String format, Project project, ModBuildToolsExtension ext, String loader) {
        def version = resolveVersion(project, ext)
        def loaders = resolveLoaders(project, ext)

        return format
                .replace("{MOD_ID}", resolveModId(project, ext))
                .replace("{MOD_NAME}", resolveDisplayName(project, ext))
                .replace("{MOD_VERSION}", version.baseVersion)
                .replace("{FULL_VERSION}", version.toString())
                .replace("{MC_VERSION}", resolveMinecraftVersion(project, ext))
                .replace("{RELEASE_CHANNEL}", version.channel)
                .replace("{PLATFORMS}", "[" + loaders.collect { it.toUpperCase() }.join("/") + "]")
                .replace("{LOADER}", loader ?: "")
                .replace("{LOADER_NAME}", loader ? loader.capitalize() : "")
                .replaceAll(/\s+/, " ")
                .trim()
    }

    private static final int MODRINTH_VERSION_LIMIT = 32

    private static final int MODRINTH_NAME_LIMIT = 64

    private static String platformVersion(Project project, ModBuildToolsExtension ext, String loader) {
        def version = resolveVersion(project, ext)
        def value = "${version}+${resolveMinecraftVersion(project, ext)}-${loader}".toString()

        if (value.length() > MODRINTH_VERSION_LIMIT) {
            throw new GradleException(
                    "[ModBuildTools] Version number '${value}' is ${value.length()} characters long, " +
                            "Modrinth allows at most ${MODRINTH_VERSION_LIMIT}. " +
                            "Shorten mod_version in gradle.properties.")
        }
        return value
    }

    private static String platformTitle(String format, Project project, ModBuildToolsExtension ext, String loader) {
        def value = expandTitle(format, project, ext, loader)

        if (value.length() > MODRINTH_NAME_LIMIT) {
            throw new GradleException(
                    "[ModBuildTools] Release title '${value}' is ${value.length()} characters long, " +
                            "Modrinth allows at most ${MODRINTH_NAME_LIMIT}. " +
                            "Shorten mod_release_title_format in build_tools.config.")
        }
        return value
    }

    private static void generateMetadata(Project project, ModBuildToolsExtension ext) {
        def version = resolveVersion(project, ext)
        def loaders = resolveLoaders(project, ext)
        def config = readConfig(project)

        def githubTitleFormat = config.github_release_title_format?.toString() ?:
                '{MOD_NAME} {MOD_VERSION} - Minecraft {MC_VERSION}'
        def platformTitleFormat = config.mod_release_title_format?.toString() ?:
                '{MOD_NAME} {MOD_VERSION} - Minecraft {MC_VERSION} ({LOADER_NAME})'

        def artifacts = [:]
        loaders.each { loader ->
            def artifact = project.ext.releaseArtifacts[loader]
            if (!artifact) {
                throw new GradleException(
                        "[ModBuildTools] No release artifact for loader '${loader}'. " +
                                "Make sure the subproject applies 'dev.architectury.loom' and " +
                                "has been evaluated before generateReleaseMetadata runs."
                )
            }
            artifacts[loader] = [
                    main_jar        : artifact.filename,
                    sources_jar     : artifact.sourcesJar ?: "",
                    javadoc_jar     : artifact.javadocJar ?: "",
                    platform_version: platformVersion(project, ext, loader),
                    platform_title  : platformTitle(platformTitleFormat, project, ext, loader)
            ]
        }

        def output = [
                minecraft_version : resolveMinecraftVersion(project, ext),
                java_version      : resolveJavaVersion(project, ext),
                full_version      : version.toString(),
                is_prerelease     : version.prerelease,
                release_channel   : version.channel,
                git_tag           : gitTag(project, ext),
                release_title     : expandTitle(githubTitleFormat, project, ext, null),
                maven_local_path  : resolveLocalRepoDir(project, ext).absolutePath,

                loaders           : loaders,
                dependency_entries: ext.dependencies.declared.values()
                        .collect { it.publishEntry }.findAll { it } as List<String>,
                artifacts         : artifacts
        ]

        def outFile = project.file("metadata.json")
        outFile.text = JsonOutput.prettyPrint(JsonOutput.toJson(output))
        project.logger.lifecycle("[ModBuildTools] Generated metadata.json -> ${outFile.absolutePath}")
    }

    private static String trimmed(Object value) {
        if (value == null) return null
        def text = value.toString().trim()
        return text.isEmpty() ? null : text
    }

    private static String sanitizeIdentifier(String value) {
        def text = trimmed(value)
        if (!text) return null
        def cleaned = text.replaceAll(/[^0-9A-Za-z-]/, "")
        return cleaned.isEmpty() ? null : cleaned
    }
}
