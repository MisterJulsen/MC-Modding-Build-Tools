package net.mrjulsen.gradle

import groovy.json.JsonSlurper
import org.gradle.api.GradleException
import org.gradle.api.Project

import java.text.SimpleDateFormat
import java.util.regex.Pattern

final class ChangelogGenerator {

    private ChangelogGenerator() {}

    static void register(Project project, ModBuildToolsExtension ext) {
        if (project != project.rootProject) return

        project.tasks.register("generateChangelog") {
            group = ModBuildTools.GROUP
            description = "Writes CHANGELOG.md from the commits since the last tag"
            doLast {
                def file = project.rootProject.file("CHANGELOG.md")
                file.text = build(project, ext)
                project.logger.lifecycle("[ModBuildTools] Generated changelog -> ${file.absolutePath}")
            }
        }

        project.tasks.register("printChangelog") {
            group = ModBuildTools.GROUP
            description = "Prints the changelog for the next release without writing a file"
            doLast { println "\n" + build(project, ext) }
        }
    }

    static String build(Project project, ModBuildToolsExtension ext) {
        def categories = readCategories(project)
        def grouped = new LinkedHashMap<String, List<String>>()
        categories.each { grouped.putIfAbsent(it.output, [] as List<String>) }

        commitSubjects(project).each { subject ->
            for (Category category : categories) {
                def text = match(subject, category.inputs)
                if (text == null) continue
                grouped.get(category.output).add(text)
                break
            }
        }

        grouped = grouped.findAll { !it.value.isEmpty() }

        def version = ModBuildTools.resolveVersion(project, ext)
        def out = new StringBuilder()
        out << "## ${ModBuildTools.resolveDisplayName(project, ext)} ${version.baseVersion}"
        out << " - Minecraft ${ModBuildTools.resolveMinecraftVersion(project, ext)}\n"
        out << "*${new SimpleDateFormat('yyyy-MM-dd').format(new Date())}*\n"

        if (grouped.isEmpty()) {
            out << "\nNo changes recorded.\n"
            return out.toString()
        }

        grouped.each { heading, entries ->
            out << "\n### ${heading}\n"
            entries.each { out << "- ${it}\n" }
        }

        return out.toString()
    }

    private static String match(String subject, List<String> inputs) {
        for (String input : inputs) {
            def marker = input ==~ /^[\p{Alnum}]+$/ ? Pattern.quote("[${input}]") : Pattern.quote(input)
            def matcher = subject =~ /(?i)^\s*${marker}\s*(.+)$/
            if (matcher.find()) return matcher.group(1).trim()
        }
        return null
    }

    private static List<String> commitSubjects(Project project) {
        def lastTag = git(project, ["describe", "--tags", "--abbrev=0"], true)
        def range = lastTag ? "${lastTag}..HEAD".toString() : "HEAD"
        def log = git(project, ["log", range, "--pretty=format:%s"], false)
        return log ? log.readLines().findAll { !it.trim().isEmpty() } : []
    }

    private static String git(Project project, List<String> args, boolean tolerateFailure) {
        def command = ["git"] + args
        def process = new ProcessBuilder(command)
                .directory(project.rootProject.projectDir)
                .redirectErrorStream(false)
                .start()

        def output = process.inputStream.getText("UTF-8")
        def error = process.errorStream.getText("UTF-8")
        process.waitFor()

        if (process.exitValue() != 0) {
            if (tolerateFailure) return null
            throw new GradleException(
                    "[ModBuildTools] '${command.join(' ')}' failed: ${error.trim()}")
        }
        return output.trim()
    }

    private static List<Category> readCategories(Project project) {
        def file = project.rootProject.file(ModBuildTools.CONFIG_FILE)
        if (!file.exists()) {
            throw new GradleException("[ModBuildTools] ${ModBuildTools.CONFIG_FILE} not found in project root")
        }

        def raw = new JsonSlurper().parse(file).changelog_categories
        if (!raw) {
            throw new GradleException(
                    "[ModBuildTools] ${ModBuildTools.CONFIG_FILE} has no 'changelog_categories'.")
        }

        return raw.collect { entry ->
            new Category(
                    output: entry.output.toString(),
                    inputs: entry.inputs.collect { it.toString() })
        }
    }

    private static class Category {
        String output
        List<String> inputs
    }
}
