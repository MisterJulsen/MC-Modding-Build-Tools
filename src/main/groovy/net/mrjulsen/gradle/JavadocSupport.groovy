package net.mrjulsen.gradle

import org.gradle.api.GradleException
import org.gradle.api.Project
import org.gradle.api.file.FileCollection
import org.gradle.api.tasks.javadoc.Javadoc
import org.gradle.external.javadoc.StandardJavadocDocletOptions

import java.util.regex.Pattern

final class JavadocSupport {

    private static final Pattern TAG_PATTERN = ~/^[A-Za-z][A-Za-z0-9._$-]*:[aoptcmfsX]+:.+$/

    private static final Pattern LOCALE_PATTERN = ~/^[a-z]{2,3}(_[A-Z]{2}(_[A-Za-z0-9]+)?)?$/

    private static final List<String> JAVADOC_MARKERS =
            ["index.html", "element-list", "package-list", "allclasses-index.html"].asImmutable()

    private JavadocSupport() {}

    static void configureAll(Project project, ModBuildToolsExtension ext) {
        project.allprojects { Project owner ->
            owner.tasks.withType(Javadoc).configureEach { Javadoc task ->
                def cfg = ext.javadoc
                if (!cfg.enabled) return

                JavadocSupport.validate(cfg)

                task.failOnError = cfg.failOnError

                def options = task.options
                options.encoding = cfg.encoding

                if (cfg.quiet) options.quiet()

                if (!(options instanceof StandardJavadocDocletOptions)) return
                def std = (StandardJavadocDocletOptions) options

                std.locale = cfg.locale
                std.charSet = cfg.charset ?: cfg.encoding
                std.docEncoding = cfg.docEncoding ?: cfg.encoding

                def title = JavadocSupport.resolveTitle(project, ext, JavadocSupport.loaderOf(project, owner))
                if (title) {
                    std.windowTitle = title
                    std.docTitle = title
                }

                if (cfg.doclint) {
                    std.addStringOption("Xdoclint:${cfg.doclint}".toString(), "-quiet")
                }

                if (!cfg.tags.isEmpty()) std.tags(cfg.tags as String[])
                if (!cfg.links.isEmpty()) std.links(cfg.links as String[])

                cfg.extraOptions.each { key, value ->
                    if (value == null || value.toString().isEmpty()) {
                        std.addBooleanOption(key.toString(), true)
                    } else {
                        std.addStringOption(key.toString(), value.toString())
                    }
                }

                cfg.excludedPackages.each { pkg ->
                    task.exclude("${pkg.replace('.', '/')}/**".toString())
                }
            }
        }
    }

    static String loaderOf(Project root, Project owner) {
        return owner == root ? "" : owner.name
    }

    static String resolveTitle(Project project, ModBuildToolsExtension ext, String loader) {
        def cfg = ext.javadoc

        if (cfg.title) return cfg.title
        if (!cfg.titleFormat) return null

        ModVersion version
        try {
            version = ModBuildTools.resolveVersion(project, ext)
        } catch (GradleException e) {
            throw new GradleException(
                    "[ModBuildTools] Cannot build the javadoc title from '${cfg.titleFormat}': " +
                            "the mod version is not resolvable. Either fix mod_version or set " +
                            "javadoc.title to a literal string.\n${e.message}", e)
        }

        return cfg.titleFormat
                .replace("{name}", ModBuildTools.resolveDisplayName(project, ext))
                .replace("{modid}", ModBuildTools.resolveModId(project, ext))
                .replace("{version}", version.toString())
                .replace("{version_base}", version.baseVersion)
                .replace("{mc}", ModBuildTools.resolveMinecraftVersion(project, ext))
                .replace("{channel}", version.channel)
                .replace("{loader}", loader ?: "")
                .trim()
    }

    static void validate(ModBuildToolsExtension.JavadocConfig cfg) {
        if (!LOCALE_PATTERN.matcher(cfg.locale).matches()) {
            throw new GradleException(
                    "[ModBuildTools] Invalid javadoc locale '${cfg.locale}'. " +
                            "Expected a javadoc locale such as 'en_US', 'de_DE' or 'en'.")
        }

        cfg.tags.each { tag ->
            if (!TAG_PATTERN.matcher(tag.toString()).matches()) {
                throw new GradleException(
                        "[ModBuildTools] Invalid javadoc tag '${tag}'.\n" +
                                "Expected 'name:locations:header', e.g. 'apiNote:a:API Note:'.\n" +
                                "Locations are any of a (all), o (overview), p (packages), t (types), " +
                                "c (constructors), m (methods), f (fields), s (modules).\n" +
                                "Or declare it as: javadoc { tag \"apiNote\", \"API Note\" }")
            }
        }
    }

    static void registerAggregateTask(Project project, ModBuildToolsExtension ext) {
        project.tasks.register("generateJavadoc", Javadoc) { Javadoc task ->
            task.group = ModBuildTools.GROUP
            task.description = "Generates one Javadoc for all modules"
        }

        project.gradle.projectsEvaluated {
            project.tasks.named("generateJavadoc", Javadoc).configure { Javadoc task ->
                def cfg = ext.javadoc
                def modules = JavadocSupport.resolveModules(project, cfg)

                if (modules.isEmpty()) {
                    task.enabled = false
                    return
                }

                modules.each { module ->
                    task.source module.sourceSets.main.allJava
                }

                FileCollection classpath = project.files()
                modules.each { module ->
                    classpath = classpath + module.sourceSets.main.compileClasspath
                }
                task.classpath = classpath

                def output = project.file(cfg.outputDir)

                JavadocSupport.checkOutputDir(cfg, output)
                task.destinationDir = output
            }
        }
    }

    static List<Project> resolveModules(Project project, ModBuildToolsExtension.JavadocConfig cfg) {
        def withJava = project.subprojects.findAll { it.plugins.hasPlugin("java") }.toList()

        if (cfg.modules == null) {
            return withJava.findAll { !(it.name in cfg.excludedModules) }.toList()
        }

        return cfg.modules.collect { name ->
            def module = withJava.find { it.name == name }
            if (!module) {
                throw new GradleException(
                        "[ModBuildTools] javadoc.modules names '${name}', which is not a subproject " +
                                "with the java plugin. Available: ${withJava*.name.join(', ')}")
            }
            return module
        }
    }

    static void checkOutputDir(ModBuildToolsExtension.JavadocConfig cfg, File output) {
        if (cfg.allowSharedOutputDir) return
        if (!output.isDirectory()) return

        def entries = output.list()
        if (entries == null || entries.length == 0) return
        if (JAVADOC_MARKERS.any { entries.contains(it) }) return

        def shown = entries.take(5).join(", ") + (entries.length > 5 ? ", ..." : "")
        throw new GradleException("""
[ModBuildTools] Refusing to generate javadoc into '${output}': it holds files that javadoc
did not write (${shown}).

Gradle treats this directory as a task output and deletes stale content in it, which would
destroy those files on the first run.

Give javadoc a directory of its own:

    modBuildTools { javadoc { outputDir = 'docs/api' } }

If the mix is deliberate and you accept the deletions, set
javadoc.allowSharedOutputDir = true.
""")
    }
}
