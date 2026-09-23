package net.mrjulsen.gradle

import groovy.json.JsonSlurper
import org.gradle.api.GradleException
import org.gradle.api.Project

import java.util.regex.Pattern

final class ScriptDownloader {

    private static final String API = "https://api.github.com"
    private static final String RAW = "https://raw.githubusercontent.com"

    private static final int CONNECT_TIMEOUT = 10000
    private static final int READ_TIMEOUT = 30000

    private ScriptDownloader() {}

    static void registerScriptSetupTask(Project project, ModBuildToolsExtension ext) {
        if (project != project.rootProject) return

        project.tasks.register("setupModScripts") {
            group = ModBuildTools.GROUP
            description = "Installs the workflow and configuration files from the build tools repository"
            doLast { install(project, ext.scripts, project.hasProperty("dryRun")) }
        }
    }

    private static void install(Project project, ModBuildToolsExtension.ScriptsConfig cfg, boolean dryRun) {
        def repository = validateRepository(cfg)
        def directory = cfg.directory.replaceAll('^/+|/+$', "")

        def commit = resolveCommit(repository, cfg.ref)
        project.logger.lifecycle("[ModBuildTools] ${repository}@${cfg.ref} -> ${commit.take(7)}")

        def files = listFiles(repository, commit, directory)
        if (files.isEmpty()) {
            throw new GradleException(
                    "[ModBuildTools] '${directory}/' is empty in ${repository}@${cfg.ref}. " +
                            "Check scripts.directory.")
        }

        def installed = []
        def updated = []
        def skipped = []

        files.sort().each { String path ->
            def relative = path.substring(directory.length() + 1)
            def target = project.rootProject.file(relative)
            def exists = target.exists()

            if (exists && !isManaged(cfg, relative)) {
                skipped << relative
                return
            }

            if (!dryRun) {
                def content = download("${RAW}/${repository}/${commit}/${path}")
                if (exists && content == readBytes(target)) {
                    skipped << relative
                    return
                }
                write(target, content)
            }

            (exists ? updated : installed) << relative
        }

        def stale = findStale(project, cfg, files.collect { it.substring(directory.length() + 1) })
        report(project, dryRun, installed, updated, skipped)
        handleStale(project, cfg, dryRun, stale)
    }

    private static List<String> findStale(Project project, ModBuildToolsExtension.ScriptsConfig cfg,
                                          List<String> expected) {
        if (cfg.managed.isEmpty()) return []

        def root = project.rootProject.projectDir
        def stale = []

        project.rootProject.fileTree(root) { tree ->
            tree.include(cfg.managed)
            tree.exclude("**/build/**", "**/.gradle/**", "**/.git/**")
        }.each { File file ->
            def relative = root.toPath().relativize(file.toPath()).toString().replace('\\', '/')
            if (!expected.contains(relative)) stale << relative
        }

        return stale.sort()
    }

    private static void handleStale(Project project, ModBuildToolsExtension.ScriptsConfig cfg,
                                    boolean dryRun, List<String> stale) {
        if (stale.isEmpty()) return

        if (!cfg.prune) {
            project.logger.warn(
                    "[ModBuildTools] ${stale.size()} managed file(s) no longer exist upstream:\n" +
                            stale.collect { "  ${it}" }.join("\n") +
                            "\nDelete them, or set scripts.prune = true to have this task do it.")
            return
        }

        stale.each { relative ->
            if (!dryRun) project.rootProject.file(relative).delete()
            project.logger.lifecycle("[ModBuildTools] ${dryRun ? 'Would delete' : 'Deleted'} ${relative}")
        }
    }

    private static void report(Project project, boolean dryRun,
                               List<String> installed, List<String> updated, List<String> skipped) {
        def out = new StringBuilder("\n")
        def verb = dryRun ? "would be " : ""

        [("new, ${verb}installed".toString()): installed,
         ("changed, ${verb}replaced".toString()): updated,
         ("unchanged or kept".toString()): skipped].each { label, entries ->
            if (entries.isEmpty()) return
            out << "${entries.size()} ${label}\n"
            entries.each { out << "  ${it}\n" }
        }

        if (installed.isEmpty() && updated.isEmpty()) {
            out << "Everything is up to date.\n"
        } else if (dryRun) {
            out << "\nDry run - nothing was written. Run without -PdryRun to apply.\n"
        }

        project.logger.lifecycle(out.toString())
    }

    private static boolean isManaged(ModBuildToolsExtension.ScriptsConfig cfg, String relative) {
        if (cfg.overwriteAll) return true
        return cfg.managed.any { pattern -> matches(pattern, relative) }
    }

    static boolean matches(String pattern, String path) {
        def regex = new StringBuilder("^")
        def i = 0

        while (i < pattern.length()) {
            if (pattern.startsWith("**/", i)) {
                regex << "(?:.*/)?"
                i += 3
                continue
            }
            if (pattern.startsWith("**", i)) {
                regex << ".*"
                i += 2
                continue
            }

            def c = pattern.charAt(i)
            if (c == ('*' as char)) {
                regex << "[^/]*"
            } else if (c == ('?' as char)) {
                regex << "[^/]"
            } else {
                regex << Pattern.quote(c.toString())
            }
            i++
        }

        regex << "\$"
        return path ==~ regex.toString()
    }

    private static String validateRepository(ModBuildToolsExtension.ScriptsConfig cfg) {
        def repository = cfg.repository?.trim()
        if (!(repository ==~ /^[\w.-]+\/[\w.-]+$/)) {
            throw new GradleException(
                    "[ModBuildTools] scripts.repository must be 'owner/name', got '${cfg.repository}'.")
        }
        return repository
    }

    private static String resolveCommit(String repository, String ref) {
        def json = api("${API}/repos/${repository}/commits/${ref}",
                "Cannot resolve '${ref}' in ${repository}")
        return json.sha.toString()
    }

    private static List<String> listFiles(String repository, String commit, String directory) {
        def json = api("${API}/repos/${repository}/git/trees/${commit}?recursive=1",
                "Cannot list the files of ${repository}@${commit.take(7)}")

        if (json.truncated) {
            throw new GradleException(
                    "[ModBuildTools] The file listing of ${repository} was truncated by GitHub. " +
                            "The repository is too large to install from.")
        }

        return json.tree
                .findAll { it.type == "blob" && it.path.startsWith("${directory}/") }
                .collect { it.path.toString() }
    }

    private static Object api(String url, String context) {
        def connection = open(url)
        connection.setRequestProperty("Accept", "application/vnd.github+json")

        def code = connection.responseCode
        if (code != 200) {
            throw new GradleException("[ModBuildTools] ${context} (HTTP ${code}).${hint(code)}")
        }
        return new JsonSlurper().parse(connection.inputStream)
    }

    private static byte[] download(String url) {
        def connection = open(url)

        def code = connection.responseCode
        if (code != 200) {
            throw new GradleException("[ModBuildTools] Download failed: ${url} (HTTP ${code}).${hint(code)}")
        }
        return connection.inputStream.bytes
    }

    private static HttpURLConnection open(String url) {
        HttpURLConnection connection
        try {
            connection = (HttpURLConnection) new URL(url).openConnection()
        } catch (IOException e) {
            throw new GradleException("[ModBuildTools] Cannot reach ${url}: ${e.message}", e)
        }

        connection.connectTimeout = CONNECT_TIMEOUT
        connection.readTimeout = READ_TIMEOUT
        connection.instanceFollowRedirects = true

        def token = System.getenv("GITHUB_TOKEN") ?: System.getenv("GH_TOKEN")
        if (token) connection.setRequestProperty("Authorization", "Bearer ${token}")

        return connection
    }

    private static String hint(int code) {
        if (code == 403 || code == 429) {
            return " GitHub is rate limiting anonymous requests - set GITHUB_TOKEN to raise the limit."
        }
        if (code == 404 || code == 422) {
            return " Check scripts.repository, scripts.ref and scripts.directory, and whether the repository is private."
        }
        if (code == 401) {
            return " GITHUB_TOKEN is set but not accepted - it may be expired or lack repository access."
        }
        return ""
    }

    private static byte[] readBytes(File file) {
        try {
            return file.bytes
        } catch (IOException ignored) {
            return null
        }
    }

    private static void write(File target, byte[] content) {
        target.parentFile?.mkdirs()

        def temp = new File(target.parentFile, "${target.name}.mbt-tmp")
        try {
            temp.bytes = content
            if (!temp.renameTo(target)) {
                target.delete()
                if (!temp.renameTo(target)) {
                    throw new GradleException("[ModBuildTools] Cannot write ${target}")
                }
            }
        } finally {
            temp.delete()
        }
    }
}
