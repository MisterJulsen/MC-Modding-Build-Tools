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
        managed      = [".github/workflows/**", ".github/discord/notify.py"]   // replaced every run
        overwriteAll = false        // true: replace everything, not just the managed files
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

Discord is **not** configured here any more; it lives entirely in
`.github/discord/`, see [Discord](#discord).

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
| `DISCORD_PING_ROLE_ID` | variable | optional, the role behind `{ROLE}`, used by the `ping` of the announcement |
| `CURSEFORGE_SLUG` | variable | optional, makes the CurseForge button link to the pretty url instead of `curseforge.com/projects/<id>` |
| `MODRINTH_SLUG` | variable | optional, same for Modrinth; `MODRINTH_ID` already works on its own |

Everything works with webhooks alone. `DISCORD_BOT_TOKEN` is only needed for
three extras:

| Extra | Why a bot |
|---|---|
| archiving and reopening the forum post with the issue | webhooks cannot edit a thread |
| forum tags from issue labels by name | reading the available tags needs the api |
| `crosspost` in an announcement channel | publishing is a bot action |

Which secret a single notification needs is in
`python .github/discord/notify.py list`.

Bot setup: [Discord Developer Portal](https://discord.com/developers/applications)
→ New Application → *Bot* → *Reset Token* → copy it into the secret. Invite the
bot to the server (*OAuth2* → URL Generator → scope `bot`) and give it
**View Channel** and **Manage Threads** on the forum channel, plus
**Manage Messages** in the announcement channel if you use `crosspost`. No
privileged intents, no gateway, the workflows only use the REST api.

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
| commits, pull requests, failed builds in a dev channel | `DISCORD_DEV_WEBHOOK` | – |
| issues as forum posts, archived together with the issue | `DISCORD_ISSUE_WEBHOOK`, `DISCORD_BOT_TOKEN` | `DISCORD_GUILD_ID`, `DISCORD_ISSUE_FORUM_ID` |

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
| `discord-dev.yml` | commits, pull requests, workflow failures, … | posts repository activity in a dev channel |
| `discord-issues.yml` | issue events | an issue becomes a post in a Discord forum channel |

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

Three optional workflows, each dead until its webhook secret exists. Everything
they need lives in one directory, installed by `setupModScripts`:

```
.github/discord/
  notify.py            the engine - replaced on every update, not meant to be edited
  config.json          webhooks, colours, forum tags
  notifications/
    dev/       commits.json, pull-request.json, pull-request-review.json,
               workflow-failure.json, release-published.json, star.json, fork.json
    issues/    forum-post.json, forum-post-update.json, status-closed.json,
               status-reopened.json, status-labeled.json
    release/   announcement.json
```

**One file is one message.** The directory it sits in says which workflow
carries its trigger: `dev/` → `discord-dev.yml`, `issues/` →
`discord-issues.yml`, `release/` → `discord-release.yml`.

`notify.py` is generic — it knows nothing about pushes or issues. It reads the
event, finds the notification files whose `on` block matches, renders their
`message` template and posts the result. `setupModScripts` replaces `notify.py`
on every update but installs everything under `notifications/` only once, so a
message you changed is never overwritten.

### The four commands

From the repository root, nothing but Python 3 needed:

| Command | |
|---|---|
| `python .github/discord/notify.py list` | every notification: on/off, event, action, target, required secrets |
| `notify.py check` | validates the files and reports a notification whose event is missing from its workflow's `on:` block |
| `notify.py vars issues` | every placeholder of an event with a short description; `--payload event.json` fills in real values |
| `notify.py render issues/forum-post` | the finished message as json, without posting it |

`render` and `vars` work without a payload and then show the structure with
empty placeholders, which is enough while writing a template. For real values,
save an event payload from a workflow run (`${{ toJSON(github.event) }}`) or
grab one from GitHub's
[webhook payload examples](https://docs.github.com/en/webhooks/webhook-events-and-payloads)
and pass it with `--payload`.

### Recipes

**I want notifications for commits.** Already there:
`notifications/dev/commits.json`. `on.branches` picks the branches,
`limits.COMMITS` how many commits one message lists.

**I want to switch something off.** `"enabled": false` in its file, or delete
the file. `enabled: false` keeps it around as documentation.

**I want a new notification.** Copy the file that comes closest, change `on`
and `message`, run `notify.py check`. Names it an event its workflow does not
trigger yet, `check` says which line to add under `on:`.

**I want to change the release message.** `notifications/release/announcement.json`,
the `message` block. `notify.py render release/announcement` shows the result.

**I want different colours everywhere.** `config.json` → `colors`. Every
shipped template uses `{COLOR_SUCCESS}` and friends, so one edit covers all
messages.

**I want an extra button.** Add `{ "label": "Wiki", "url": "{WIKI_URL}" }` to
`message.buttons`. A button whose url does not resolve is dropped, so it
appears the moment a `WIKI_URL` repository variable exists.

### Anatomy of a notification file

```json
{
  "description": "Every push to a branch that matters, one line per commit.",
  "enabled": true,
  "target": "dev",

  "on": {
    "event": "push",
    "branches": ["main", "mc/*"],
    "skip_bots": true,
    "require": ["COMMITS"]
  },

  "limits": { "COMMITS": 10 },

  "message": {
    "embeds": [{
      "title": "{COMMITS_TOTAL} new commit{COMMITS_PLURAL} in {BRANCH}",
      "url": "{COMPARE_URL}",
      "color": "{COLOR_PUSH}",
      "description": "{{#each COMMITS}}[`{SHORT_SHA}`]({URL}) {SUBJECT}\n{{/each}}"
    }]
  }
}
```

| Key | Meaning |
|---|---|
| `description` | for you, never sent anywhere |
| `enabled` | `false` switches the message off without deleting it |
| `target` | a key of `config.json` → `targets`, which maps it onto a webhook secret |
| `on` | the filters; all of them have to match |
| `limits` | per placeholder: entries for a list, characters for text. Text is cut on line boundaries |
| `message` | a Discord message, see below |
| `ping` | the only thing in the whole message that may mention anyone |
| `crosspost` | press *Publish* in an announcement channel; needs `DISCORD_BOT_TOKEN` |
| `thread` | forum posts only, see [Issue forum](#issue-forum--discord-issuesyml) |

`"message": "other-file"` instead of a block reuses that notification's
message. `forum-post-update.json` does exactly that, so the look of an issue
post is defined in one place only.

### `on` — when a message is sent

| Filter | Meaning |
|---|---|
| `event` | GitHub event name, or a list of them. `pull_request` also covers `pull_request_target` |
| `action` | `["opened", "closed"]`, or `"*"` for any. Omitted means any |
| `branches` | globs, matched against the push branch, the `workflow_run` branch or a pull request's target branch |
| `labels` | globs; at least one label of the issue or pull request has to match |
| `workflows` | globs on the workflow name, for `workflow_run` |
| `conclusions` | `["failure", "timed_out"]`, for `workflow_run` |
| `skip_bots` | default `true`: ignore anything a bot account triggered |
| `skip_drafts` | ignore draft pull requests |
| `merged` | `true` only for merged pull requests, `false` only for closed ones |
| `require` | placeholders that must not be empty, for example `["COMMITS"]` |

Whatever stopped a message is in the log of the run, one line per notification:

```
dev: 7 notification(s), event 'push'
  commits: skipped - branch 'l10n/crowdin' is not in ['main', 'mc/*']
  fork: skipped - enabled is false
  pull-request: skipped - event is 'push', the rule wants pull_request
```

### `message` — what is sent

The block is a Discord message as the api wants it, so everything Discord can do
is available: `content`, `embeds` with `title`, `description`, `url`, `color`,
`author`, `footer`, `fields`, `thumbnail`, `image`, `timestamp`, and `buttons`
as a shortcut for link components (max 25, five per row, `label`, `url`,
optional `emoji` as `🔥` or `name:id`). Three things happen on their own:

* Everything that renders empty is removed — an empty field, an embed without a
  title, an image without a url.
* Discord's length limits are applied, so a 40 kB issue body cannot break a
  message.
* `color` takes `"#43B581"` as well as `{COLOR_SUCCESS}` from `config.json`.

Template syntax, usable in every string of the block:

| | |
|---|---|
| `{NAME}` | a placeholder; an unknown name is simply empty |
| `{{#each LIST}}…{{/each}}` | once per entry, the entry's own fields become placeholders inside |
| `{{#if NAME}}…{{else}}…{{/if}}` | empty, `false` and `0` count as false |
| `{{#unless NAME}}…{{/unless}}` | the other way round |

Blocks nest, which is how `announcement.json` picks its colour:

```json
"color": "{{#if PRERELEASE}}{COLOR_WARNING}{{else}}{COLOR_SUCCESS}{{/if}}"
```

A list placeholder such as `COMMITS` also provides `{COMMITS_TOTAL}` (what the
event delivered), `{COMMITS_COUNT}` (what the message shows),
`{COMMITS_HIDDEN}` (the difference, empty when nothing was cut) and
`{COMMITS_PLURAL}` (`s` or nothing). Inside `{{#each}}` there are `{INDEX}`,
`{FIRST}` and `{LAST}`. The current action is a placeholder too —
`{ACTION_CLOSED}`, `{ACTION_LABELED}`, … — so `{{#if}}` can branch on it
without needing a comparison operator.

`notify.py vars <event>` prints every placeholder an event has; the table below
only names the ones worth remembering.

| | |
|---|---|
| repository | `{REPO}` `{REPO_NAME}` `{REPO_OWNER}` `{REPO_URL}` `{ISSUES_URL}` `{DEFAULT_BRANCH}` |
| who | `{ACTOR}` `{ACTOR_URL}` `{ACTOR_AVATAR}` |
| push | `{BRANCH}` `{COMPARE_URL}` `{COMMITS}` with `{SHORT_SHA}` `{URL}` `{SUBJECT}` `{AUTHOR}` |
| pull request | `{PR_NUMBER}` `{PR_TITLE}` `{PR_URL}` `{PR_BODY}` `{PR_VERB}` `{PR_HEAD}` `{PR_BASE}` `{PR_ADDITIONS}` `{PR_MERGED}` `{PR_LABEL_NAMES}` |
| issue | `{ISSUE_NUMBER}` `{ISSUE_TITLE}` `{ISSUE_URL}` `{ISSUE_BODY}` `{ISSUE_AUTHOR}` `{ISSUE_STATE_REASON}` `{ISSUE_LABEL_NAMES}` `{ISSUE_MILESTONE}` `{LABEL}` |
| workflow run | `{RUN_NAME}` `{RUN_URL}` `{RUN_CONCLUSION}` `{RUN_BRANCH}` `{RUN_ATTEMPT}` |
| announcement | `{MOD_NAME}` `{MOD_VERSION}` `{MC_VERSION}` `{MC_VERSIONS}` `{PLATFORMS}` `{LOADERS}` `{TAG}` `{TAGS}` `{RELEASE_URL}` `{CHANGELOG}` `{CHANGELOG_LINKS}` `{PRERELEASE}` `{RELEASES}` |
| store pages | `{CURSEFORGE_URL}` `{MODRINTH_URL}`, built from the ids or slugs you already set |
| anything else | every repository **variable** by its own name in upper case, plus `{ROLE}`, `{PING}` and `{COLOR_*}` |

### config.json

The shared half: which secret belongs to which channel, the colour palette, the
forum settings. Nothing in here describes a message.

```json
{
  "targets": {
    "dev": { "webhook": "DISCORD_DEV_WEBHOOK" },
    "announcements": { "webhook": "DISCORD_RELEASE_WEBHOOK" },
    "issue_forum": {
      "webhook": "DISCORD_ISSUE_WEBHOOK",
      "forum_channel": "DISCORD_ISSUE_FORUM_ID"
    }
  },
  "groups": { "dev": { "workflow": ".github/workflows/discord-dev.yml" } },
  "colors": { "success": "#43B581", "failure": "#ED4245" },
  "defaults": { "username": "", "avatar_url": "", "footer": "{REPO}" },
  "forum": { "labels_as_tags": true, "tag_map": {}, "default_tags": [] }
}
```

| Key | Meaning |
|---|---|
| `targets` | name → the **name of the environment variable** that holds the webhook. The url itself stays in *Secrets* |
| `groups` | which workflow carries a directory's triggers; only `notify.py check` uses this |
| `colors` | the palette behind `{COLOR_<NAME>}`, add your own names freely |
| `defaults.footer` | footer for every embed that does not bring its own |
| `defaults.username` | overrides the webhook's name, for example `{MOD_NAME}` |
| `forum` | see [Issue forum](#issue-forum--discord-issuesyml) |

### Release announcement — `discord-release.yml`

Manual, and that is the point: publish your drafts first — one per Minecraft
version — then start this workflow **once** and get a single message, whether it
covers one version or four. The message is
`notifications/release/announcement.json`.

| Input | |
|---|---|
| `tags` | empty = the newest published release. Otherwise a list of tags, space or comma separated, globs allowed: `v3.1.0+mc*` catches every Minecraft version of 3.1.0, `v3.1.0+mc1.20.1 v3.1.0+mc1.21.1` names them explicitly. Drafts are never matched |
| `ping` | on by default and means "use the `ping` value of the notification". Switch it off for a hotfix that should not wake anyone. It can only suppress a ping, never create one |
| `dry_run` | posts nothing and writes the whole message into the run summary |

Nothing is ever grouped on its own: an empty `tags` announces exactly one
release. If you ship 3.1.0 for 1.20.1 today and 3.1.0 for 1.21.1 in two weeks,
the second run with the exact tag leaves the first release alone.

Mod name, version and loaders come from the `metadata.json` of each build. Has
its artifact expired, the workflow says so and falls back to the Minecraft
version in the tag.

The changelog arrives as `{CHANGELOG}`, trimmed to `limits.CHANGELOG`
characters on line boundaries; `{CHANGELOG_LINKS}` is *Full changelog on
GitHub*, or one link per Minecraft version when several releases are announced.

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

### Development feed — `discord-dev.yml`

One embed per event in a development channel. Seven files in
`notifications/dev/`, three of them off by default:

| File | | |
|---|---|---|
| `commits.json` | on | one line per commit, linked to the compare view |
| `pull-request.json` | on | opened, reopened, ready for review, merged, closed |
| `release-published.json` | on | a GitHub release went public |
| `workflow-failure.json` | on | a failed run of the workflows named in `on.workflows` |
| `pull-request-review.json` | off | approvals and change requests |
| `star.json` | off | someone starred the repository |
| `fork.json` | off | someone forked it |

Issues are missing from this list on purpose: they go into the forum instead.
Do you want them here as well, copy `pull-request.json`, set `on.event` to
`issues` and add an `issues:` trigger to `discord-dev.yml` — `notify.py check`
reminds you of the second half.

`workflow-failure.json` filters on workflow names in `on.workflows`, and GitHub
needs the same names in the `workflow_run.workflows` list of
`discord-dev.yml`. Two places, both commented; a name only present in one of
them means no message.

### Issue forum — `discord-issues.yml`

A new issue becomes a post in a Discord **forum** channel, and the issue gets a
single comment carrying the link to it. That comment also stores the thread id,
so no database is needed anywhere.

What this deliberately does **not** do is mirror comments, in either direction.
A bot that syncs every few minutes suggests a cross platform chat that does not
exist, and GitHub is not a chat in the first place. The forum post is the
documentation of the issue; what happens below it on Discord and what happens
on GitHub are two separate conversations.

Five files, one per thing that can happen to an issue:

| File | |
|---|---|
| `forum-post.json` | the issue was opened → create the thread. Defines the look of an issue post |
| `forum-post-update.json` | the issue text was edited → rewrite the first post, nothing new is sent |
| `status-closed.json` | *closed on GitHub* into the thread, then archive it |
| `status-reopened.json` | unarchive the thread and say why it is back |
| `status-labeled.json` | a label was added or removed |

The `thread` block of such a file:

| Key | Meaning |
|---|---|
| `action` | `create`, `reply` (the default) or `edit` for the first post |
| `name` | thread title, `create` only, 100 characters max |
| `tags_from_labels` | `create` only: labels pick the forum tags of the same name |
| `archived` | `true` archives after the message, `false` reopens the thread before it |
| `locked` | `true` so nobody can answer in a closed report |

`archived` and `locked` need `DISCORD_BOT_TOKEN`; without it the message is
posted and the thread is left as it is. The matching settings in `config.json`:

| `forum` key | Meaning |
|---|---|
| `labels_as_tags` | match labels against the forum's tag names; needs `DISCORD_ISSUE_FORUM_ID` and `DISCORD_BOT_TOKEN` |
| `tag_map` | label → tag id, for when you do not want a bot (tag id: forum channel → Edit Channel → Tags, or ask the api) |
| `default_tags` | tag ids for issues whose labels match nothing, for forums that require a tag |

Pull requests get no forum post; they appear in the dev feed.

### Good to know

* Only the `ping` value of a notification may mention anyone. Everything that
  came out of the event — commit messages, issue bodies, labels — is posted with
  mentions switched off, whatever it contains.
* Link buttons work through plain webhooks. Should Discord refuse them anyway,
  the engine posts the same links as a *Links* field instead and says so in the
  log.
* Pull requests from forks arrive through `pull_request_target`, which only ever
  reads the event and checks out the base branch, never the fork's code.
* A notification file belongs to your project. `setupModScripts` installs it
  once and afterwards only updates `notify.py`. Want the shipped version back,
  delete your file and run the task again.

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
