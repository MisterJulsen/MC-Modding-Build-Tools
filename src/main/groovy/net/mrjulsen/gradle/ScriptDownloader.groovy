package net.mrjulsen.gradle

import org.gradle.api.GradleException
import org.gradle.api.Project

/**
 * Registers the setupModScripts task that installs workflow files and the
 * default .mod-build-config.json from the mod-resources repository.
 *
 * Files are fetched from:
 *   https://raw.githubusercontent.com/MisterJulsen/mod-resources/main/data/
 *
 * Using /main/ is intentional – the mod-resources repo is the single source
 * of truth for shared tooling, and updates there should propagate immediately.
 */
class ScriptDownloader {

    private static final String BASE_URL =
            "https://raw.githubusercontent.com/MisterJulsen/mod-resources/main/data/build-tools"

    // Files to install into .github/workflows/
    private static final List<String> WORKFLOW_FILES = [
            "release.yml",
            "publish.yml"
    ]

    // Default config file to install into the project root (only if missing)
    private static final String CONFIG_TEMPLATE = ".mod-build-config.json"

    static void registerScriptSetupTask(Project project, ModBuildToolsExtension ext) {
        project.tasks.register("setupModScripts") {
            group       = ModBuildTools.GROUP
            description = "Installs GitHub workflow files and the default .mod-build-config.json " +
                    "from MisterJulsen/mod-resources. Run once to bootstrap a new mod repo."

            doLast {
                def workflowDir = project.file(".github/workflows")
                workflowDir.mkdirs()

                // --- Workflow files -----------------------------------------
                WORKFLOW_FILES.each { filename ->
                    def target = new File(workflowDir, filename)
                    if (target.exists() && !ext.overwriteWorkflows) {
                        project.logger.lifecycle("[ModBuildTools] Skipping ${filename} (already exists, set overwriteWorkflows=true to replace)")
                        return
                    }
                    def url = "${BASE_URL}/workflows/${filename}"
                    project.logger.lifecycle("[ModBuildTools] Downloading ${filename}…")
                    downloadFile(url, target)
                }

                // --- Config template ----------------------------------------
                def configTarget = project.file(ModBuildTools.CONFIG_FILE)
                if (configTarget.exists()) {
                    project.logger.lifecycle("[ModBuildTools] ${ModBuildTools.CONFIG_FILE} already exists – skipping")
                } else {
                    def url = "${BASE_URL}/${CONFIG_TEMPLATE}"
                    project.logger.lifecycle("[ModBuildTools] Downloading ${CONFIG_TEMPLATE}…")
                    downloadFile(url, configTarget)
                }

                project.logger.lifecycle("[ModBuildTools] setupModScripts complete.")
            }
        }
    }

    private static void downloadFile(String urlString, File target) {
        try {
            def url = new URL(urlString)
            def conn = (HttpURLConnection) url.openConnection()
            conn.connectTimeout = 10_000
            conn.readTimeout    = 30_000
            conn.connect()

            if (conn.responseCode != 200) {
                throw new GradleException(
                        "[ModBuildTools] Failed to download ${urlString} (HTTP ${conn.responseCode})"
                )
            }

            target.withOutputStream { out -> out << conn.inputStream }
        } catch (IOException e) {
            throw new GradleException("[ModBuildTools] Could not download ${urlString}: ${e.message}", e)
        }
    }
}