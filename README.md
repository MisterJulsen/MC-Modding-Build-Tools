# Mod Build & Release System

Automated build, release, and publishing pipeline for Minecraft mods using Architectury Loom. Supports multiple mod loaders (Fabric, Forge, NeoForge, etc.) and multiple Minecraft version branches simultaneously.

## How it works

```
workflow_dispatch (manual trigger)
        ↓
[release.yml]  Build → Create Draft Release
        ↓
You test JARs, edit the changelog, click "Publish release"
        ↓
[publish.yml]  → CurseForge & Modrinth  (platform JARs – full, for players)
               → Maven                  (slim JARs – no bundled deps, for devs)
```

Two separate JAR variants are built in a single CI run:
- **Platform JARs** – full build with bundled dependencies, attached to the GitHub Release, uploaded to CurseForge and Modrinth
- **Maven JARs** – slim build without bundled dependencies, published to the configured Maven target

---

## Table of Contents

1. [Secrets & Variables](#1-secrets--variables)
2. [Deploy Key Setup (GitHub Pages)](#2-deploy-key-setup-github-pages-only)
3. [Maven Target Configuration](#3-maven-target-configuration)
4. [build.gradle Setup](#4-buildgradle-setup)
5. [gradle.properties](#5-gradleproperties)
6. [.mod-build-config.json](#6-mod-build-configjson)
7. [Release Workflow](#7-release-workflow)
8. [Using the Maven Repository](#8-using-the-maven-repository)
9. [Commit Format for Changelog](#9-commit-format-for-changelog)

---

## 1. Secrets & Variables

Go to **Settings → Secrets and variables → Actions** in each mod repository.

> **Secrets** are encrypted and masked as `***` in workflow logs. Use for tokens, passwords, and private keys.  
> **Variables** are stored in plaintext and visible in logs. Use for non-sensitive configuration values.

### Secrets tab

| Name | Description | Required when |
|------|-------------|---------------|
| `MODRINTH_TOKEN` | Modrinth personal access token (modrinth.com/settings/pats) | Always |
| `CF_TOKEN` | CurseForge API token | Always |
| `MOD_RESOURCES_DEPLOY_KEY` | SSH private key with write access to the Maven repo | `MAVEN_TARGET=github-pages` |
| `MAVEN_HTTP_URL` | e.g. `https://nexus.example.com/repository/releases` | `MAVEN_TARGET=http` |
| `MAVEN_HTTP_USERNAME` | HTTP username | `MAVEN_TARGET=http` |
| `MAVEN_HTTP_PASSWORD` | HTTP password | `MAVEN_TARGET=http` |
| `MAVEN_S3_ENDPOINT` | e.g. `https://s3.us-east-1.amazonaws.com` | `MAVEN_TARGET=s3` |
| `MAVEN_S3_BUCKET` | Bucket name | `MAVEN_TARGET=s3` |
| `MAVEN_S3_ACCESS_KEY` | S3 access key | `MAVEN_TARGET=s3` |
| `MAVEN_S3_SECRET_KEY` | S3 secret key | `MAVEN_TARGET=s3` |
| `MAVEN_S3_PATH_PREFIX` | Prefix inside the bucket, e.g. `maven` (optional) | `MAVEN_TARGET=s3` |
| `MAVEN_SSH_KEY` | SSH private key for the remote server | `MAVEN_TARGET=ssh` |
| `MAVEN_SSH_HOST` | e.g. `maven.example.com` | `MAVEN_TARGET=ssh` |
| `MAVEN_SSH_USER` | SSH username, e.g. `deploy` | `MAVEN_TARGET=ssh` |
| `MAVEN_SSH_PATH` | Remote path, e.g. `/var/www/maven` | `MAVEN_TARGET=ssh` |

### Variables tab

| Name | Description | Example | Required when |
|------|-------------|---------|---------------|
| `MODRINTH_ID` | Modrinth project ID or slug | `AAbbCCdd` | Always |
| `CF_ID` | CurseForge project ID (number) | `123456` | Always |
| `MAVEN_TARGET` | Maven publish strategy | `github-pages` | When Maven is enabled |
| `MAVEN_GITHUB_REPO` | GitHub repo to publish Maven artifacts to | `MisterJulsen/mod-resources` | `MAVEN_TARGET=github-pages` |
| `MAVEN_GITHUB_PATH` | Subfolder inside the repo | `maven` | `MAVEN_TARGET=github-pages` |
| `CHANGELOG_SCRIPT_COMMIT` | Commit hash of the changelog script in mod-resources | `a3f8c21...` | Always |

---

## 2. Deploy Key Setup (GitHub Pages only)

Run this locally — **do not commit the key files**.

```bash
ssh-keygen -t ed25519 -C "mod-release-deploy" -f mod_deploy_key -N ""
```

This produces two files:

**`mod_deploy_key.pub`** → Public key  
→ Open your Maven repo (e.g. `mod-resources`) on GitHub  
→ Settings → Deploy keys → Add deploy key  
→ Paste the contents, enable **"Allow write access"**, save

**`mod_deploy_key`** → Private key  
→ Open your mod repo on GitHub  
→ Settings → Secrets and variables → Actions → New repository secret  
→ Name: `MOD_RESOURCES_DEPLOY_KEY`, paste the full contents including the header/footer lines

```bash
# Delete the local key files after adding them
rm mod_deploy_key mod_deploy_key.pub
```

Repeat the private key step for every mod repo that uses this pipeline.

---

## 3. Maven Target Configuration

Set the `MAVEN_TARGET` variable to one of the following values:

| Value | Description | Required secrets/variables |
|-------|-------------|---------------------------|
| `github-pages` | Commits artifacts to a GitHub repo served via GitHub Pages | `MOD_RESOURCES_DEPLOY_KEY`, `MAVEN_GITHUB_REPO`, `MAVEN_GITHUB_PATH` |
| `http` | HTTP PUT to Nexus, Artifactory, Gitea, or any WebDAV server | `MAVEN_HTTP_URL`, `MAVEN_HTTP_USERNAME`, `MAVEN_HTTP_PASSWORD` |
| `s3` | S3-compatible object storage (AWS, Backblaze B2, MinIO, Cloudflare R2) | `MAVEN_S3_ENDPOINT`, `MAVEN_S3_BUCKET`, `MAVEN_S3_ACCESS_KEY`, `MAVEN_S3_SECRET_KEY` |
| `ssh` | rsync over SSH to a self-hosted server | `MAVEN_SSH_KEY`, `MAVEN_SSH_HOST`, `MAVEN_SSH_USER`, `MAVEN_SSH_PATH` |

Only the secrets for the chosen target need to be set. Unset secrets are validated at runtime with a clear error message.

---

## 4. build.gradle Setup

```groovy
plugins {
    id "net.mrjulsen.mod-build-tools" version "x.x.x"
    // ... other plugins
}

// Do NOT set version here – the plugin manages it based on gradle.properties
allprojects {
    group = rootProject.maven_group
}

modBuildTools {
    modId       = project.mod_id        // read from gradle.properties
    displayName = project.display_name  // read from gradle.properties

    publish {
        // Local Maven path used during CI staging – must be consistent across builds
        localRepoDir  = "${System.getProperty('user.home')}/.m2/github/modsrepo/maven"
        publishCommon = false
        mavenGroup    = project.maven_group  // read from gradle.properties
    }

    javadoc {
        title = "${project.display_name} API ${project.release_channel}-${project.mod_version}"
        tags  = [
            "apiNote:a:API Note:",
            "implNote:a:Impl Note:",
            "implSpec:a:Impl Spec:",
            "related:a:Related:",
            "example:a:Examples:",
            "side:a:Valid on side:"
        ]
    }
}
```

### Using build profiles in subprojects

The plugin exposes `isMavenBuild()` and `isPlatformBuild()` as helper closures in every subproject. Use them to control what gets included in each JAR variant:

```groovy
// fabric/build.gradle, forge/build.gradle, neoforge/build.gradle, etc.
dependencies {
    // Always included in both variants:
    modImplementation "de.mrjulsen:dragonlib-fabric:${rootProject.dragonlib_version}"

    // Only included in the platform build (large JAR for players):
    if (isPlatformBuild()) {
        modImplementation(include("ws.schild:jave-core:${rootProject.jave_version}"))
        modImplementation(include("ws.schild:jave-nativebin-linux64:${rootProject.jave_version}"))
        // ...
    }

    // Only included in the Maven build (slim JAR for developers):
    if (isMavenBuild()) {
        // e.g. compile-only stubs, annotation processors
    }
}
```

| Helper | `./gradlew build` | `-PbuildProfile=platform` | `-PbuildProfile=maven` |
|--------|:-----------------:|:-------------------------:|:----------------------:|
| `isPlatformBuild()` | `true` | `true` | `false` |
| `isMavenBuild()` | `false` | `false` | `true` |

Without `-PbuildProfile`, Gradle always behaves as `platform` so local dev builds always produce full JARs.

---

## 5. gradle.properties

The following properties are read automatically by the plugin:

```properties
mod_id=mymod
display_name=My Mod
mod_version=1.2.3
minecraft_version=1.21.1
release_channel=beta       # see channel mapping below
maven_group=de.mrjulsen
archives_name=MyMod
```

### Release channel mapping

The `release_channel` value is mapped to a canonical channel using `.mod-build-config.json`:

| gradle.properties value | Canonical channel | CurseForge / Modrinth |
|-------------------------|-------------------|-----------------------|
| `alpha`, `a`, `snapshot`, `experimental`, `test` | `alpha` | Alpha |
| `beta`, `b`, `rc`, `release-candidate`, `preview` | `beta` | Beta |
| `release`, `r`, `stable`, `final` | `release` | Release |

The full version string is built from the channel and version:

| Channel | Format | Example |
|---------|--------|---------|
| `release` | `{mc}-{mod}` | `1.21.1-2.0.0` |
| other | `{mc}-{channel}-{mod}` | `1.21.1-beta-2.0.0` |

---

## 6. .mod-build-config.json

Place this file in the root of each mod repository. It is read by both the Gradle plugin and the changelog script.

```json
{
  "github_release_title_format": "{MOD_NAME} - v{MOD_VERSION} {PLATFORMS}",
  "mod_release_title_format": "{MOD_ID}-{LOADER}-{FULL_VERSION}",
  "main_branch_pattern": "^mc[/-].+",
  "modules": ["fabric", "forge"],
  "changelog_categories": [
    { "inputs": ["added",   "✨"], "output": "✨" },
    { "inputs": ["fixed",   "🐛"], "output": "🐛" },
    { "inputs": ["changed", "♻️"], "output": "♻️" },
    { "inputs": ["removed", "🔥"], "output": "🔥" },
    { "inputs": ["updated", "⬆️"], "output": "⬆️" }
  ],
  "release_channels": {
    "alpha":   ["a", "alpha", "snapshot", "experimental", "test"],
    "beta":    ["b", "beta", "rc", "release-candidate", "preview"],
    "release": ["r", "release", "stable", "final"]
  }
}
```

### Title format placeholders

| Placeholder | Value |
|-------------|-------|
| `{MOD_ID}` | `mod_id` from gradle.properties |
| `{MOD_NAME}` | `display_name` from gradle.properties |
| `{MOD_VERSION}` | `mod_version` from gradle.properties |
| `{FULL_VERSION}` | Full version string, e.g. `1.21.1-beta-2.0.0` |
| `{MC_VERSION}` | `minecraft_version` from gradle.properties |
| `{RELEASE_CHANNEL}` | Canonical channel (`alpha`, `beta`, `release`) |
| `{PLATFORMS}` | e.g. `[FABRIC/FORGE]` |
| `{LOADER}` | Current loader, e.g. `fabric` (mod title only) |

---

## 7. Release Workflow

### Triggering a release

1. Go to **Actions → Create Release → Run workflow**
2. Select options:
    - **Publish to CurseForge & Modrinth** (default: enabled)
    - **Publish to Maven** (default: enabled)
3. The workflow runs:
    - Platform build → full JARs for players
    - Maven build → slim JARs for developers
    - Changelog is generated from commits since the last tag
    - A **Draft Release** is created with the platform JARs attached

### Approval

1. Open the Draft Release under **Releases**
2. Download the platform JARs and test them in Minecraft
3. Edit the changelog directly in the GitHub release editor (full Markdown support)
4. Click **"Publish release"** when ready

### Automatic publishing

Once published, `publish.yml` triggers automatically:

```
prepare
  ├── publish-platforms  → CurseForge & Modrinth (parallel per loader)
  └── publish-maven      → configured MAVEN_TARGET
```

`publish-platforms` uses the JARs from the GitHub Release assets — exactly the files you tested.  
`publish-maven` uses the pre-built slim JARs from the internal Actions artifact — no second Gradle build.

### Skipping individual publish targets

Add these flags anywhere in the release body (they are invisible in rendered Markdown):

| Flag | Effect |
|------|--------|
| `[skip-platforms]` | Skip CurseForge & Modrinth upload |
| `[skip-maven]` | Skip Maven publish |

Both flags can be combined. They can also be pre-set when triggering the workflow by unchecking the corresponding input.

---

## 8. Using the Maven Repository

```groovy
// build.gradle (in a project that depends on your mod API)
repositories {
    // GitHub Pages:
    maven { url 'https://MisterJulsen.github.io/mod-resources/maven' }

    // Self-hosted:
    maven { url 'https://maven.example.com/releases' }
}

dependencies {
    // Maven JARs contain no bundled dependencies – no version conflicts
    modImplementation 'de.mrjulsen:mymod-fabric:1.21.1-2.0.0'
    modImplementation 'de.mrjulsen:mymod-forge:1.21.1-2.0.0'
}
```

---

## 9. Commit Format for Changelog

The changelog is generated automatically from commits since the last Git tag. Only commits with a recognized prefix are included. All others are silently ignored.

| Commit message | Changelog entry |
|----------------|-----------------|
| `[added] New feature` | `✨: New feature` |
| `✨ New feature` | `✨: New feature` |
| `[fixed] Bug #123` | `🐛: Bug #123` |
| `🐛 Bug #123` | `🐛: Bug #123` |
| `[changed] Refactoring` | `♻️: Refactoring` |
| `♻️ Refactoring` | `♻️: Refactoring` |
| `[removed] Old API` | `🔥: Old API` |
| `[updated] Dependency X` | `⬆️: Dependency X` |
| `fix typo in readme` | *(ignored)* |