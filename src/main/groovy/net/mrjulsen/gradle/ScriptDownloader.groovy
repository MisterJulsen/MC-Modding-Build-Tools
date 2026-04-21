package net.mrjulsen.gradle

import groovy.json.JsonSlurper
import org.gradle.api.GradleException
import org.gradle.api.Project

class ScriptDownloader {

    private static final String REPO_OWNER = "MisterJulsen"
    private static final String REPO_NAME = "MC-Modding-Build-Tools"
    private static final String BRANCH = "main"
    private static final String SCRIPTS_DIR = "install"

    private static final String RAW_BASE = "https://raw.githubusercontent.com/${REPO_OWNER}/${REPO_NAME}/${BRANCH}"
    private static final String TREE_API = "https://api.github.com/repos/${REPO_OWNER}/${REPO_NAME}/git/trees/${BRANCH}?recursive=1"

    static void registerScriptSetupTask(Project project, ModBuildToolsExtension ext) {
        project.tasks.register("setupModScripts") {
            group = ModBuildTools.GROUP
            description = "Automatically installs all files from the install/ folder of the MisterJulsen/MC-Modding-Build-Tools repo into the project."

            doLast {
                project.logger.lifecycle("[ModBuildTools] Loading file list from GitHub ...")
                def files = fetchScriptFiles()
                project.logger.lifecycle("[ModBuildTools] Found ${files.size()} files.")

                files.each { repoPath ->
                    def relativePath = repoPath.substring("${SCRIPTS_DIR}/".length())
                    def targetFile = project.file(relativePath)

                    if (targetFile.exists() && !shouldOverwrite(ext, relativePath)) {
                        project.logger.lifecycle("[ModBuildTools] Skip ${relativePath} (already exists)")
                        return
                    }

                    targetFile.parentFile?.mkdirs()
                    def rawUrl = "${RAW_BASE}/${repoPath}"
                    project.logger.lifecycle("[ModBuildTools] Read ${relativePath} ...")
                    downloadFile(rawUrl, targetFile)
                    project.logger.lifecycle("[ModBuildTools] Installed: ${relativePath}")
                }

                project.logger.lifecycle("[ModBuildTools] setupModScripts done.")
            }
        }
    }

    private static List<String> fetchScriptFiles() {
        def url = new URL(TREE_API)
        def conn = (HttpURLConnection) url.openConnection()
        conn.connectTimeout = 10000
        conn.readTimeout = 30000
        conn.setRequestProperty("Accept", "application/vnd.github+json")
        conn.connect()

        if (conn.responseCode != 200) {
            throw new GradleException("[ModBuildTools] GitHub API error (HTTP ${conn.responseCode}). Please check your internet connection and whether the repo exists or not.")
        }

        def json = new JsonSlurper().parse(conn.inputStream)

        return json.tree
                .findAll { it.type == "blob" && it.path.startsWith("${SCRIPTS_DIR}/") }
                .collect { it.path }
    }

    private static boolean shouldOverwrite(ModBuildToolsExtension ext, String relativePath) {
        return ext.overwriteWorkflows
    }

    private static void downloadFile(String urlString, File target) {
        try {
            def url = new URL(urlString)
            def conn = (HttpURLConnection) url.openConnection()
            conn.connectTimeout = 10000
            conn.readTimeout = 30000
            conn.instanceFollowRedirects = true
            conn.connect()

            if (conn.responseCode != 200) {
                throw new GradleException("[ModBuildTools] Download failed: ${urlString} (HTTP ${conn.responseCode})")
            }

            target.withOutputStream { out -> out << conn.inputStream }
        } catch (IOException e) {
            throw new GradleException("[ModBuildTools] Unable to download ${urlString}: ${e.message}", e)
        }
    }
}