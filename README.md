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
    id 'net.mrjulsen.mod-build-tools' version '2.1.0'
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
  ],
  "discord": { "release": {}, "dev": {}, "issues": {} }
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
| `discord` | the three Discord workflows, see [Discord](#discord) |

---

## GitHub secrets and variables

**Settings → Secrets and variables → Actions**, tab *Secrets* or *Variables*,
always *Repository* level.

* **Secrets** are encrypted, masked in the logs and cannot be read back later —
  if you forget one, create a new token instead of looking it up.
* **Variables** are plain text and visible in the logs — ids and urls only.
* `GITHUB_TOKEN` is supplied by Actions itself. Never add it.

Nothing is required to start: `build.yml`, `snapshot.yml` and `release.yml` work
with an empty configuration. Add a block below when you first want that target.

### Modrinth — needed by `publish.yml` when the release was built with *Modrinth*

| Name | Type | What goes in |
|---|---|---|
| `MODRINTH_ID` | variable | the project id, **not** the slug: Modrinth project page → right column → `Project ID`. Example: `AaBbCcDd` |
| `MODRINTH_TOKEN` | secret | a personal access token: modrinth.com → avatar → Settings → *PATs* → scopes **Create versions** and **Read projects**. Example: `mrp_a1b2c3…` |

The project has to exist on Modrinth already; the workflow only adds versions to it.

### CurseForge — needed by `publish.yml` when the release was built with *CurseForge*

| Name | Type | What goes in |
|---|---|---|
| `CURSEFORGE_ID` | variable | the numeric project id from the project page sidebar. Example: `123456` |
| `CURSEFORGE_TOKEN` | secret | an author API token: legacy.curseforge.com → Account → *API Tokens* → generate. Example: `$2a$10$…` |

### Crowdin — needed by `crowdin.yml` (manual runs only)

| Name | Type | What goes in |
|---|---|---|
| `CROWDIN_PROJECT_ID` | variable | numeric project id: Crowdin project → Settings → *API*. Example: `654321` |
| `CROWDIN_TOKEN` | secret | Crowdin account settings → *API* → personal access token, with access to projects, source files and translations |

The workflow stops with a clear error message if one of the two is missing.

### Auto port — optional for `auto-port.yml`

| Name | Type | What goes in |
|---|---|---|
| `AUTO_PORT_TOKEN` | secret | a personal access token with **Contents: read/write** and **Pull requests: read/write** on this repository |

Only reason to set it: pull requests opened with the built in `GITHUB_TOKEN` do
not start other workflows, so the port pull requests arrive without a build.
Without the secret the workflow falls back to `GITHUB_TOKEN` and still works.

### Maven — needed by `publish.yml` when the release was built with *Maven*

`MAVEN_TARGET` (variable) picks the transport: `github-pages`, `http`, `s3` or
`ssh`. If it is unset or misspelled the job fails with
*"MAVEN_TARGET is not set or invalid"*. Then fill in **only** the block of the
chosen target.

**`github-pages`** — commits the artifacts into another repository

| Name | Type | What goes in |
|---|---|---|
| `MAVEN_GITHUB_REPO` | variable | `owner/repo` of the repository that hosts the Maven files. Example: `MisterJulsen/mod-resources` |
| `MAVEN_GITHUB_PATH` | variable | folder inside that repository; empty means `maven`. Example: `maven` |
| `MAVEN_GITHUB_DEPLOY_KEY` | secret | the **private** key, complete file including the `-----BEGIN …-----` lines |

`ssh-keygen -t ed25519 -f key -N ""` → put `key.pub` as a deploy key **with
write access** on the Maven repository (its Settings → Deploy keys) and the
contents of `key` into this secret in the mod repository.

**`http`** — one HTTP `PUT` per file (Nexus, Reposilite, Artifactory, …)

| Name | Type | What goes in |
|---|---|---|
| `MAVEN_HTTP_URL` | variable | base url of the repository, without trailing slash. Example: `https://nexus.example.com/repository/releases` |
| `MAVEN_HTTP_USERNAME` | secret | user with deploy rights |
| `MAVEN_HTTP_PASSWORD` | secret | its password or deploy token |

**`s3`** — `aws s3 sync` into a bucket (AWS, R2, MinIO, …)

| Name | Type | What goes in |
|---|---|---|
| `MAVEN_S3_ENDPOINT` | variable | endpoint url. Example: `https://s3.eu-central-1.amazonaws.com` |
| `MAVEN_S3_BUCKET` | variable | bucket name. Example: `mrjulsen-maven` |
| `MAVEN_S3_PATH_PREFIX` | variable | optional folder inside the bucket; empty means the bucket root. Example: `maven` |
| `MAVEN_S3_ACCESS_KEY` | secret | access key id with write access to the bucket |
| `MAVEN_S3_SECRET_KEY` | secret | the matching secret access key |

**`ssh`** — `rsync` to a server

| Name | Type | What goes in |
|---|---|---|
| `MAVEN_SSH_HOST` | variable | host name. Example: `maven.example.com` |
| `MAVEN_SSH_USER` | variable | ssh user. Example: `deploy` |
| `MAVEN_SSH_PATH` | variable | absolute target directory. Example: `/var/www/maven` |
| `MAVEN_SSH_KEY` | secret | private ssh key whose public half sits in that user's `~/.ssh/authorized_keys` |

### Discord — needed by `discord-release.yml`, `discord-dev.yml` and `discord-issues.yml`

Every one of the three is optional and does nothing at all while its webhook is
missing. A webhook url contains a token, so it belongs into *Secrets*.

| Name | Type | What goes in |
|---|---|---|
| `DISCORD_RELEASE_WEBHOOK` | secret | webhook of the announcement channel: Discord → channel → Edit Channel → *Integrations* → Webhooks → New Webhook → *Copy Webhook URL* |
| `DISCORD_DEV_WEBHOOK` | secret | webhook of the development channel, created the same way |
| `DISCORD_ISSUE_WEBHOOK` | secret | webhook of the **forum** channel that holds the issue posts |
| `DISCORD_BOT_TOKEN` | secret | optional, see the table below |
| `DISCORD_GUILD_ID` | variable | optional, only so the link in the issue comment points at the thread. Server icon → right click → *Copy Server ID* (needs Developer Mode) |
| `DISCORD_ISSUE_FORUM_ID` | variable | optional, forum channel → right click → *Copy Channel ID*; lets labels pick the forum tags of the same name |
| `DISCORD_PING_ROLE_ID` | variable | optional, the role for `{ROLE}` in `discord.release.mention` |
| `CURSEFORGE_SLUG` | variable | optional, makes the CurseForge button link to the pretty url instead of `curseforge.com/projects/<id>` |
| `MODRINTH_SLUG` | variable | optional, same for Modrinth; `MODRINTH_ID` already works on its own |

Everything works with webhooks alone. `DISCORD_BOT_TOKEN` is only needed for the
four extras:

| Extra | Why a bot |
|---|---|
| closing and reopening the forum post with the issue | webhooks cannot edit a thread |
| forum tags from issue labels by name | reading the available tags needs the api |
| `crosspost` in an announcement channel | publishing is a bot action |
| mirroring Discord messages back into the issue | reading messages needs a bot |

Bot setup: [Discord Developer Portal](https://discord.com/developers/applications)
→ New Application → *Bot* → *Reset Token* → copy it into the secret. Invite the
bot to the server (*OAuth2* → URL Generator → scope `bot`) and give it
**View Channel**, **Read Message History** and **Manage Threads** on the forum
channel, plus **Manage Messages** in the announcement channel if you use
`crosspost`. No privileged intents, no gateway, the workflows only use the REST
api.

Any repository *variable* can be used as a placeholder, so a button
`{ "label": "Wiki", "url": "{WIKI_URL}" }` works as soon as a `WIKI_URL`
variable exists — and the button simply disappears while it does not.

### What to fill in for which goal

| Goal | Secrets | Variables |
|---|---|---|
| pull request and snapshot builds | – | – |
| GitHub releases only | – | – |
| releases on Modrinth and CurseForge | `MODRINTH_TOKEN`, `CURSEFORGE_TOKEN` | `MODRINTH_ID`, `CURSEFORGE_ID` |
| library, Maven on GitHub as well | `MAVEN_GITHUB_DEPLOY_KEY` | `MAVEN_TARGET=github-pages`, `MAVEN_GITHUB_REPO`, `MAVEN_GITHUB_PATH` |
| translations as well | `CROWDIN_TOKEN` | `CROWDIN_PROJECT_ID` |
| port pull requests that build | `AUTO_PORT_TOKEN` | – |
| release announcements on Discord | `DISCORD_RELEASE_WEBHOOK` | – |
| commits, pull requests, issues in a dev channel | `DISCORD_DEV_WEBHOOK` | – |
| issues as forum posts, closed together with the issue | `DISCORD_ISSUE_WEBHOOK`, `DISCORD_BOT_TOKEN` | `DISCORD_GUILD_ID` |

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
| `discord-release.yml` | manual | announces one or several releases in a Discord channel |
| `discord-dev.yml` | commits, pull requests, issues, … | posts repository activity in a dev channel |
| `discord-issues.yml` | issue events, every 15 minutes | mirrors issues into a Discord forum channel |

Release flow: run `release.yml` on every Minecraft version branch you want to
ship → test the drafts, edit the changelogs → publish them → `publish.yml` uploads
each one → run `discord-release.yml` once for a single announcement covering all
of them. Keep the `<!--mbt-run:…-->` comment in the release body, it links the
release to its build.

One upload per loader. The version number is the jar name without `.jar`
(`dragonlib-3.1.0+mc1.20.1-fabric`), the display name comes from
`mod_release_title_format`, the release type from the version suffix.

---

## Discord

Three workflows, each optional and each off until its webhook secret exists.
They share one script (`.github/discord/discord_notify.py`, installed by
`setupModScripts`) and one configuration block:

```json
"discord": {
  "release": {
    "enabled": true,
    "title_format": "{MOD_NAME} {MOD_VERSION} - Minecraft {MC_VERSIONS} {PLATFORMS}",
    "changelog_limit": 0,
    "color": "#43B581",
    "prerelease_color": "#FAA61A",
    "mention": "",
    "thumbnail": "",
    "footer": "",
    "crosspost": false,
    "buttons": [
      { "label": "CurseForge", "url": "{CURSEFORGE_URL}" },
      { "label": "Modrinth", "url": "{MODRINTH_URL}" },
      { "label": "GitHub", "url": "{RELEASE_URL}" }
    ]
  },
  "dev": { "enabled": true, "events": { "push": true, "pull_request": true } },
  "issues": { "enabled": true, "mirror_github_comments": true }
}
```

### Release announcement — `discord-release.yml`

Manual, and that is the point: publish your drafts first — one per Minecraft
version — then start this workflow **once** and get a single message, whether it
covers one version or four.

| Input | |
|---|---|
| `tags` | empty = the newest published release. Otherwise a list of tags, space or comma separated, globs allowed: `v3.1.0+mc*` catches every Minecraft version of 3.1.0, `v3.1.0+mc1.20.1 v3.1.0+mc1.21.1` names them explicitly. Drafts are never matched |
| `ping` | on by default and means "use `discord.release.mention`". Switch it off for a hotfix that should not wake anyone. It can only suppress a ping, never create one |
| `dry_run` | posts nothing, writes the whole message, the matched releases and all warnings into the run summary |

Nothing is ever grouped on its own: an empty `tags` announces exactly one
release. If you ship 3.1.0 for 1.20.1 today and 3.1.0 for 1.21.1 in two weeks,
the second run with the exact tag leaves the first release alone.

| Key | Meaning |
|---|---|
| `title_format` | placeholders below; default `DragonLib 3.1.0 - Minecraft 1.20.1, 1.21.1 [FABRIC/FORGE]` |
| `changelog_limit` | `0` means "as much as fits into the embed". A positive number makes it shorter |
| `color`, `prerelease_color` | hex color of the embed stripe |
| `mention` | text above the embed, for example `@everyone`, `<@&123>` or `{ROLE}` for the `DISCORD_PING_ROLE_ID` variable. Only what is written here may be pinged |
| `thumbnail`, `image` | image urls, placeholders allowed |
| `footer` | small line under the body, for example `Minecraft {MC_VERSIONS} • {LOADERS}` |
| `crosspost` | press *Publish* in an announcement channel; needs `DISCORD_BOT_TOKEN` |
| `buttons` | `label`, `url`, optional `emoji` (`🔥` or `name:id`) and `enabled: false`. Max 25, five per row, a button with an unresolved url is dropped. The GitHub button points at the newest of the announced releases |

Placeholders in `title_format`, `mention`, `footer`, image urls and button urls:

| | |
|---|---|
| mod | `{MOD_ID}` `{MOD_NAME}` `{MOD_VERSION}` `{FULL_VERSION}` `{RELEASE_CHANNEL}` `{JAVA_VERSION}` |
| Minecraft | `{MC_VERSIONS}` → `1.20.1, 1.21.1` (version sorted), `{MC_VERSION}` → only the newest one |
| loaders | `{PLATFORMS}` → `[FABRIC/FORGE]`, `{LOADERS}` → `Fabric, Forge`; both are the union over all announced releases |
| release | `{TAG}` `{RELEASE_NAME}` `{RELEASE_URL}` of the newest, `{TAGS}` for all of them |
| repository | `{REPO}` `{REPO_NAME}` `{REPO_OWNER}` `{REPO_URL}` `{ISSUES_URL}` |
| platforms | `{CURSEFORGE_URL}` `{MODRINTH_URL}` (from the ids or slugs you already set) |
| anything else | every repository **variable** by its own name, plus `{ROLE}` |

#### How several changelogs become one

The changelog goes into the message as you wrote it — no format rules, no
parsing of labels, Unicode art included. With more than one release the only
operation is a line by line comparison:

* a line every release has stays exactly as it is,
* a line only some releases have is tagged with their Minecraft versions,
* the newest release sets the order, lines that exist only in the others are
  appended at the end.

```
- Added: Pipe network rendering
- [1.21.1] Added: Data component support
- Fixed: Crash when loading a world with Sodium
- [1.20.1] Fixed: Optifine shader glitch
```

Are the changelogs identical — the normal case after an auto port — the result
is the plain changelog without a single prefix. The prefix goes behind the list
marker, so indentation and nesting survive. Two lines that differ by one word
count as two different lines and both get a prefix; the dry run shows that
immediately.

Whole lines only: the first line that no longer fits into the embed ends the
block with `…`, so nothing is silently dropped out of the middle. A single line
that is too long on its own gets cut. Under the changelog sits
*Full changelog on GitHub*, or one link per Minecraft version when several
releases are announced.

Mod name, version and loaders come from the `metadata.json` of each build. If
its artifact has expired, the workflow says so and falls back to the Minecraft
version in the tag.

### Development feed — `discord-dev.yml`

One embed per event in a second channel, each event type switchable on its own:

```json
"dev": {
  "enabled": true,
  "events": {
    "push": true, "pull_request": true, "pull_request_review": false,
    "issues": true, "issue_comment": false, "discussion": false,
    "release": true, "workflow_failure": true, "star": false, "fork": false
  },
  "branches": ["main", "mc/*"],
  "max_commits": 10,
  "skip_bots": true,
  "skip_merge_commits": false,
  "colors": { "push": "#5865F2" }
}
```

| Key | Meaning |
|---|---|
| `events.push` | new commits as a list of `sha` – message – author, linked to the compare view |
| `events.pull_request` | opened, reopened, ready for review, merged, closed |
| `events.issues` / `issue_comment` | issues opened, closed, reopened / new comments |
| `events.workflow_failure` | a failed run of the workflows listed in `discord-dev.yml` |
| `branches` | glob patterns; empty means every branch. Only applies to `push` |
| `max_commits` | commits per message, the rest becomes *and N more* |
| `skip_bots` | ignores everything triggered by a bot account |
| `colors` | per kind: `push`, `opened`, `merged`, `closed`, `comment`, `release`, `failure`, `misc` |

### Issue forum — `discord-issues.yml`

A new issue becomes a post in a Discord **forum** channel, and the issue gets one
comment with the link to it. That comment also stores the thread id, so no
database is needed anywhere.

```json
"issues": {
  "enabled": true,
  "thread_title_format": "#{NUMBER} {TITLE}",
  "body_limit": 1800,
  "labels_as_tags": true,
  "tag_map": { "bug": "1234567890" },
  "default_tags": [],
  "mirror_github_comments": true,
  "mirror_discord_messages": false,
  "mirror_edits": true,
  "close_thread_on_close": true,
  "lock_thread_on_close": false
}
```

| Key | Meaning |
|---|---|
| `thread_title_format` | `{NUMBER}`, `{TITLE}` and the repository placeholders; 100 characters max |
| `labels_as_tags` | labels pick the forum tags with the same name; needs `DISCORD_ISSUE_FORUM_ID` and `DISCORD_BOT_TOKEN` |
| `tag_map` | label → forum tag id, for when you do not want to use a bot (tag id: forum channel → Edit Channel → Tags, or ask the api) |
| `default_tags` | tag ids for issues whose labels match nothing, for forums that require a tag |
| `mirror_github_comments` | comments on the issue are posted into the thread |
| `mirror_discord_messages` | and the other way round, every 15 minutes, **needs** `DISCORD_BOT_TOKEN` |
| `mirror_edits` | an edited issue text updates the first post |
| `close_thread_on_close` | closing the issue archives the thread; needs `DISCORD_BOT_TOKEN` |
| `lock_thread_on_close` | and locks it, so nobody writes into a closed report |

Without a bot token the thread only receives a *this issue was closed* message
and stays open. Mirrored messages never trigger each other: the script skips
webhook and bot authors on the Discord side and its own hidden marker on the
GitHub side.

The `*/15` schedule in `discord-issues.yml` only exists for
`mirror_discord_messages`. If you leave that off, delete the `schedule:` block —
the run does nothing but still shows up in the Actions tab.

### Good to know

* Link buttons work through plain webhooks. Should Discord refuse them anyway,
  the script posts the same links as a *Links* field instead and says so in the
  log.
* Pull requests from forks are reported through `pull_request_target`, which
  only ever reads the event, never checks out or runs the fork's code.
* Nothing in a mirrored text can ping anyone; only `discord.release.mention`
  may.

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
[add] New pipe texture      ->  - Added: New pipe texture
[fix] NPE on world change   ->  - Fixed: NPE on world change
refactor some stuff         ->  ignored
```

`CHANGELOG.md` holds nothing but these lines: no title and no date, because the
release title, CurseForge, Modrinth and the Discord announcement all show name,
version and date by themselves.
