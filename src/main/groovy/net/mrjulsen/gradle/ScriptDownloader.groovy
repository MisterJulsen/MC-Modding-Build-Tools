package net.mrjulsen.gradle

import org.gradle.api.GradleException
import org.gradle.api.Project

/**
 * Registers the setupModScripts task that installs workflow files and the
 * default .mod-build-config.json from the MC-Modding-Build-Tools repository.
 *
 * All files are fetched directly from the main branch – always up to date.
 *
 * Source structure in MC-Modding-Build-Tools:
 *   scripts/
 *     workflows/
 *       release.yml
 *       publish.yml
 *     .mod-build-config.json
 */
class ScriptDownloader {

    private static final String BASE_URL =
            "https://raw.githubusercontent.com/MisterJulsen/MC-Modding-Build-Tools/main/scripts"

    private static final List<String> WORKFLOW_FILES = [
            "auto-port.yml",
            "build.yml",
            "publish.yml",
            "release.yml",
            "snapshot.yml"
    ]

    private static final String CONFIG_TEMPLATE = ".mod-build-config.json"

    static void registerScriptSetupTask(Project project, ModBuildToolsExtension ext) {
        project.tasks.register("setupModScripts") {
            group       = ModBuildTools.GROUP
            description = "Installs GitHub workflow files and the default .mod-build-config.json " +
                    "from MisterJulsen/MC-Modding-Build-Tools (main branch). " +
                    "Run once to bootstrap a new mod repo."

            doLast {
                def workflowDir = project.file(".github/workflows")
                workflowDir.mkdirs()

                WORKFLOW_FILES.each { filename ->
                    def target = new File(workflowDir, filename)
                    if (target.exists() && !ext.overwriteWorkflows) {
                        project.logger.lifecycle(
                                "[ModBuildTools] Skipping ${filename} (already exists – " +
                                        "set overwriteWorkflows = true in modBuildTools { } to replace)"
                        )
                        return
                    }
                    def url = "${BASE_URL}/workflows/${filename}"
                    project.logger.lifecycle("[ModBuildTools] Downloading ${filename}…")
                    downloadFile(url, target)
                    project.logger.lifecycle("[ModBuildTools] Installed: .github/workflows/${filename}")
                }

                def configTarget = project.file(ModBuildTools.CONFIG_FILE)
                if (configTarget.exists()) {
                    project.logger.lifecycle(
                            "[ModBuildTools] ${ModBuildTools.CONFIG_FILE} already exists – skipping"
                    )
                } else {
                    def url = "${BASE_URL}/${CONFIG_TEMPLATE}"
                    project.logger.lifecycle("[ModBuildTools] Downloading ${CONFIG_TEMPLATE}…")
                    downloadFile(url, configTarget)
                    project.logger.lifecycle("[ModBuildTools] Installed: ${ModBuildTools.CONFIG_FILE}")
                }

                project.logger.lifecycle("[ModBuildTools] setupModScripts complete.")
                project.logger.lifecycle(
                        "[ModBuildTools] Next steps:\n" +
                                "  1. Edit .mod-build-config.json to match your project\n" +
                                "  2. Set up Secrets & Variables in your GitHub repo (see README)\n" +
                                "  3. Commit and push .github/workflows/ and .mod-build-config.json"
                )
            }
        }
    }

    private static void downloadFile(String urlString, File target) {
        try {
            def url  = new URL(urlString)
            def conn = (HttpURLConnection) url.openConnection()
            conn.connectTimeout = 10_000
            conn.readTimeout    = 30_000
            conn.instanceFollowRedirects = true
            conn.connect()

            if (conn.responseCode != 200) {
                throw new GradleException(
                        "[ModBuildTools] Failed to download ${urlString} " +
                                "(HTTP ${conn.responseCode}). " +
                                "Make sure the file exists in the MC-Modding-Build-Tools repository."
                )
            }

            target.withOutputStream { out -> out << conn.inputStream }
        } catch (IOException e) {
            throw new GradleException(
                    "[ModBuildTools] Could not download ${urlString}: ${e.message}", e
            )
        }
    }
}