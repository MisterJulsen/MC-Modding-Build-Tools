# Mod Build Tools

Gradle plugin and GitHub workflows for Minecraft mods built with Architectury Loom.
Handles versioning, multi-loader builds, snapshots, auto porting between Minecraft
version branches, releases and publishing to CurseForge, Modrinth and Maven.

A mod repository needs a small `modBuildTools` block, a few `gradle.properties`
entries and `build_tools.config`. Everything else comes from the plugin.

---

## Setup

```groovy
// build.gradle (root)
plugins {
    id 'net.mrjulsen.mod-build-tools' version '1.0.15'
}

modBuildTools {
    modId       = project.mod_id
    displayName = project.display_name

    publish {
        localRepoDir = "${System.getProperty('user.home')}/.m2/mod-build-local"
        mavenGroup   = project.maven_group
    }

    dependencies {
        require "dragonlib", "3.1.0", {
            modrinth   = "dragonlib"
            curseforge = "dragonlib"
        }
    }
}
```

```bash
./gradlew setupModScripts   # installs the workflows and build_tools.config
```

Then fill in `version_branches` in `build_tools.config` and set the secrets and
variables below.

---

## gradle.properties

| Key | Example | Meaning |
|---|---|---|
| `mod_id` | `dragonlib` | mod id, also the jar name prefix |
| `display_name` | `DragonLib` | human readable name |
| `maven_group` | `de.mrjulsen` | Maven group |
| `minecraft_version` | `1.20.1` | target Minecraft version |
| `mod_version` | `3.1.0-beta.2` | strict SemVer, nothing else |
| `enabled_platforms` | `fabric,forge` | active loaders; missing subprojects are skipped with a warning |

---

## modBuildTools options

All values below are the defaults; set only what you need to change.

```groovy
modBuildTools {
    modId       = null              // else mod_id / archives_name
    displayName = null              // else display_name, else modId

    versioning {
        enabled            = true
        minecraftVersion   = null   // else minecraft_version
        modVersion         = null   // else mod_version
        javaVersion        = null   // else read from the Gradle toolchain
        platformsProperty  = "enabled_platforms"
        minecraftVersionRange = null            // e.g. "[{mc},1.22)"
        artifactNameFormat = '{modid}-{version}+mc{mc}-{loader}{build}'
        tagFormat          = 'v{version}+mc{mc}'
        configureArtifactNames = true
        templateResources  = true   // ${version} etc. in mods.toml, fabric.mod.json, ...
    }

    publish {
        publishCommon     = false   // publish the common module too
        commonModule      = "common"
        localRepoDir      = "${System.getProperty('user.home')}/.m2/mod-build-local"
        mavenGroup        = null    // else maven_group
        artifactIdFormat  = '{modid}-{loader}-{mc}'
        sourcesJar        = true
        javadocJar        = true
        allowUnpublishedProjectDependencies = false
        warnOnPlatformProfile = true
    }

    dependencies {
        require      "name", "1.0.0"   // range bounded at the next MAJOR
        requireAtLeast "name", "1.0.0" // no upper bound
        optional     "name", "1.0.0"   // bounded, optional on the platforms
        // each takes an optional closure: { modrinth = "slug"; curseforge = "slug" }
    }

    javadoc {
        enabled     = true
        titleFormat = '{name} API {version}'
        outputDir   = "docs/api"
        failOnError = false
        // tag "apiNote", "API Note"   link "https://..."   option "-Xmaxwarns", "1"
    }

    migration {
        previousVersion = null      // highest version published under the old scheme
    }

    scripts {
        repository   = "MisterJulsen/MC-Modding-Build-Tools"
        ref          = "main"       // branch, tag or commit
        directory    = "install"
        managed      = [".github/workflows/**"]   // replaced on every run
        overwriteAll = false
        prune        = false        // delete managed files that are gone upstream
    }
}
```

---

## Gradle tasks

| Task | Does |
|---|---|
| `buildRelease` | all jars for every loader, `metadata.json`, `CHANGELOG.md` |
| `buildWithMetadata` | build plus `metadata.json` |
| `publishLocal` | publishes to the local Maven staging directory |
| `generateChangelog` | writes `CHANGELOG.md` from the commits since the last tag |
| `printChangelog` | same, printed only |
| `printVersionInfo` | every derived value: names, tag, ranges, variables |
| `printPublishInfo` | Maven target and which modules get published |
| `verifyPublishing` | fails if the POM would reference unpublished modules |
| `verifyVersionMigration` | checks the version against already published dependents |
| `generateJavadoc` | one javadoc over all modules |
| `setupModScripts` | installs/updates the workflow files (`-PdryRun` to preview) |

`-PbuildProfile=maven` builds slim jars without bundled dependencies,
`-PbuildProfile=platform` (default) the full ones.

---

## build_tools.config

```json
{
  "version_branches": ["mc/1.20.1", "mc/1.21.1"],
  "java_versions": [17, 21],
  "environment": "both",
  "snapshot":  { "enabled": true, "retention_days": 30 },
  "auto_port": { "enabled": true, "draft_on_conflict": true },
  "github_release_title_format": "{MOD_NAME} {MOD_VERSION} - Minecraft {MC_VERSION}",
  "mod_release_title_format": "{MOD_NAME} {MOD_VERSION} - Minecraft {MC_VERSION} ({LOADER_NAME})",
  "changelog_categories": [
    { "inputs": ["added", "add"], "output": "Added" },
    { "inputs": ["fixed", "fix"], "output": "Fixed" }
  ]
}
```

| Key | Meaning |
|---|---|
| `version_branches` | the Minecraft version branches; snapshots and auto port only act on these |
| `java_versions` | JDKs the workflows install; the Gradle toolchain picks the right one |
| `environment` | sides the mod supports: `both`, `client`, `server`, `singleplayer`, `dedicated-server`; combine with `\|`, suffix `?` optional, `*` preferred |
| `snapshot.retention_days` | how long the snapshot workflow artifacts are kept |
| `auto_port.draft_on_conflict` | ports with conflicts become draft pull requests |
| `*_title_format` | placeholders: `{MOD_ID} {MOD_NAME} {MOD_VERSION} {FULL_VERSION} {MC_VERSION} {RELEASE_CHANNEL} {PLATFORMS} {LOADER} {LOADER_NAME}` |
| `changelog_categories` | commit prefixes and their headings |

---

## GitHub secrets and variables

**Settings → Secrets and variables → Actions**

| Secret | Needed for |
|---|---|
| `MODRINTH_TOKEN` | Modrinth upload |
| `CURSEFORGE_TOKEN` | CurseForge upload |
| `CROWDIN_TOKEN` | Crowdin sync |
| `AUTO_PORT_TOKEN` | optional PAT so port pull requests trigger builds |
| `MAVEN_GITHUB_DEPLOY_KEY` | `MAVEN_TARGET=github-pages` |
| `MAVEN_HTTP_USERNAME`, `MAVEN_HTTP_PASSWORD` | `MAVEN_TARGET=http` |
| `MAVEN_S3_ACCESS_KEY`, `MAVEN_S3_SECRET_KEY` | `MAVEN_TARGET=s3` |
| `MAVEN_SSH_KEY` | `MAVEN_TARGET=ssh` |

| Variable | Example | Needed for |
|---|---|---|
| `MODRINTH_ID` | `AAbbCCdd` | Modrinth upload |
| `CURSEFORGE_ID` | `123456` | CurseForge upload |
| `CROWDIN_PROJECT_ID` | `123456` | Crowdin sync |
| `MAVEN_TARGET` | `github-pages` | `github-pages`, `http`, `s3` or `ssh` |
| `MAVEN_GITHUB_REPO`, `MAVEN_GITHUB_PATH` | `MisterJulsen/mod-resources`, `maven` | `github-pages` |
| `MAVEN_HTTP_URL` | `https://nexus.example.com/releases` | `http` |
| `MAVEN_S3_ENDPOINT`, `MAVEN_S3_BUCKET`, `MAVEN_S3_PATH_PREFIX` | | `s3` |
| `MAVEN_SSH_HOST`, `MAVEN_SSH_USER`, `MAVEN_SSH_PATH` | | `ssh` |

Deploy key for `github-pages`: `ssh-keygen -t ed25519 -f key -N ""`, public part as a
deploy key with write access on the Maven repository, private part as
`MAVEN_GITHUB_DEPLOY_KEY` in the mod repository.

---

## Workflows

| File | Runs on | Does |
|---|---|---|
| `build.yml` | pull request | builds every loader, version `…+pr.N.<sha>` |
| `snapshot.yml` | push to a version branch | builds every loader, jars only as a workflow artifact, version `…+build.N.<sha>` |
| `auto-port.yml` | merged pull request | applies the same change to the other version branches as pull requests |
| `release.yml` | manual, with CurseForge / Modrinth / Maven checkboxes | builds all jars and opens a **draft release** |
| `publish.yml` | release published | uploads to the selected targets |
| `crowdin.yml` | manual | upload sources, download translations, optionally overwrite the translations on Crowdin |

Release flow: run `release.yml` → test the draft, edit the changelog → publish →
`publish.yml` uploads. Keep the `<!--mbt-run:…-->` comment in the release body, it
links the release to its build.

One upload per loader. The version number is the jar name without `.jar`
(`dragonlib-3.1.0+mc1.20.1-fabric`), the display name comes from
`mod_release_title_format`, the release type from the version suffix.

---

## Versioning

The version field is plain SemVer: `MAJOR.MINOR.PATCH[-(alpha|beta|rc).N]`.
Minecraft version and loader live in the file name and the Maven `artifactId`,
never in the version. Build metadata (`+build.57.a1b2c3d`) only exists in
snapshots and pull request builds.

Full specification: [docs/VERSIONING.md](docs/VERSIONING.md).

---

## Commit format for the changelog

Only commits with a known prefix appear in the changelog, case insensitive:

```
[add] New pipe texture      ->  ### Added
[fix] NPE on world change   ->  ### Fixed
refactor some stuff         ->  ignored
```
