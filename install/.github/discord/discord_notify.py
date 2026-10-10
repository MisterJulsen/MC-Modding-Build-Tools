#!/usr/bin/env python3
"""Discord notifications for the Mod Build Tools workflows.

Subcommands
  release   announce a published GitHub release in the announcement channel
  dev       mirror a repository event into the development channel
  issue     create and update the forum post that belongs to an issue
  sync      copy new messages from the forum posts back into their issues

Behaviour is configured in build_tools.config under "discord"; webhook urls,
the optional bot token and the repository variables arrive as environment
variables. Only the standard library is used, so there is nothing to install.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

DISCORD_API = os.environ.get("MBT_DISCORD_API", "https://discord.com/api/v10")
GITHUB_API = os.environ.get("GITHUB_API_URL", "https://api.github.com")
USER_AGENT = "mod-build-tools-discord (+https://github.com/MisterJulsen/MC-Modding-Build-Tools)"

TITLE_LIMIT = 256
DESCRIPTION_LIMIT = 4096
FIELD_LIMIT = 1024
CONTENT_LIMIT = 2000
THREAD_NAME_LIMIT = 100

# Hidden markers: the first one carries the issue -> thread mapping, the second
# one keeps mirrored comments out of the mirror that created them.
STATE_MARKER = "mbt-discord"
MIRROR_MARKER = "<!--mbt-discord-mirror-->"

DEFAULTS = {
    "release": {
        "enabled": True,
        "title_format": "{MOD_NAME} {MOD_VERSION} - Minecraft {MC_VERSIONS} {PLATFORMS}",
        "changelog_limit": 0,
        "color": "#43B581",
        "prerelease_color": "#FAA61A",
        "mention": "",
        "thumbnail": "",
        "image": "",
        "footer": "",
        "crosspost": False,
        "buttons": [
            {"label": "CurseForge", "url": "{CURSEFORGE_URL}"},
            {"label": "Modrinth", "url": "{MODRINTH_URL}"},
            {"label": "GitHub", "url": "{RELEASE_URL}"},
        ],
    },
    "dev": {
        "enabled": True,
        "events": {
            "push": True,
            "pull_request": True,
            "pull_request_review": False,
            "issues": True,
            "issue_comment": False,
            "discussion": False,
            "release": True,
            "workflow_failure": True,
            "star": False,
            "fork": False,
        },
        "branches": [],
        "max_commits": 10,
        "skip_bots": True,
        "skip_merge_commits": False,
        "colors": {
            "push": "#5865F2",
            "opened": "#43B581",
            "merged": "#9B59B6",
            "closed": "#ED4245",
            "comment": "#747F8D",
            "release": "#43B581",
            "failure": "#ED4245",
            "misc": "#747F8D",
        },
    },
    "issues": {
        "enabled": True,
        "thread_title_format": "#{NUMBER} {TITLE}",
        "body_limit": 1800,
        "color": "#5865F2",
        "labels_as_tags": True,
        "tag_map": {},
        "default_tags": [],
        "mirror_github_comments": True,
        "mirror_discord_messages": False,
        "mirror_edits": True,
        "close_thread_on_close": True,
        "lock_thread_on_close": False,
        "skip_bots": True,
        "sync_issue_limit": 50,
    },
}


# ── small helpers ─────────────────────────────────────────────────────────────

def log(message: str) -> None:
    print(message, flush=True)


def warn(message: str) -> None:
    print(f"::warning::{message}", flush=True)


def fail(message: str) -> None:
    print(f"::error::{message}", file=sys.stderr, flush=True)
    sys.exit(1)


def merge(base, override):
    """Deep merge of the configuration, dictionaries only."""
    if not isinstance(base, dict) or not isinstance(override, dict):
        return base if override is None else override
    result = dict(base)
    for key, value in override.items():
        result[key] = merge(base.get(key), value) if key in base else value
    return result


def clip(text, limit: int, suffix: str = "…") -> str:
    text = str(text or "")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - len(suffix))].rstrip() + suffix


def squeeze(text) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def color_of(value, fallback: int = 0x5865F2) -> int:
    if isinstance(value, int):
        return value
    try:
        return int(str(value or "").strip().lstrip("#"), 16)
    except ValueError:
        return fallback


def env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def json_env(name: str):
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        warn(f"{name} does not contain valid JSON, ignoring it.")
        return {}


def read_json_file(path: str):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as error:
        fail(f"{path} is not valid JSON: {error}")


def config(section: str) -> dict:
    path = env("MBT_CONFIG", "build_tools.config")
    data = read_json_file(path)
    if data is None:
        warn(f"{path} not found, using the built in defaults.")
        data = {}
    return merge(DEFAULTS[section], (data.get("discord") or {}).get(section) or {})


def event_payload() -> dict:
    path = env("GITHUB_EVENT_PATH")
    data = read_json_file(path) if path else None
    if data is None:
        fail("No event payload available (GITHUB_EVENT_PATH is unset or unreadable).")
    return data


# ── http ──────────────────────────────────────────────────────────────────────

class ApiError(RuntimeError):

    def __init__(self, code: int, body: str, url: str):
        super().__init__(f"HTTP {code} for {url.split('?')[0]}: {clip(body, 400)}")
        self.code = code
        self.body = body or ""


def request(method: str, url: str, *, payload=None, authorization: str = "",
            accept: str = "application/json"):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"User-Agent": USER_AGENT, "Accept": accept}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if authorization:
        headers["Authorization"] = authorization

    attempts = 5
    for attempt in range(attempts):
        message = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(message, timeout=30) as response:
                body = response.read().decode("utf-8", errors="replace").strip()
            return json.loads(body) if body[:1] in ("{", "[") else {}
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            if error.code == 429 and attempt < attempts - 1:
                delay = 5.0
                try:
                    delay = float(json.loads(body).get("retry_after", delay))
                except (AttributeError, json.JSONDecodeError, TypeError, ValueError):
                    pass
                log(f"Rate limited, waiting {delay:.1f}s")
                time.sleep(min(delay + 0.5, 60))
                continue
            if error.code >= 500 and attempt < attempts - 1:
                time.sleep(2 ** attempt)
                continue
            raise ApiError(error.code, body, url) from None
        except urllib.error.URLError as error:
            if attempt < attempts - 1:
                time.sleep(2 ** attempt)
                continue
            raise ApiError(0, str(error.reason), url) from None

    raise ApiError(0, "retries exhausted", url)


def with_query(url: str, query: dict) -> str:
    if not query:
        return url
    return url + ("&" if "?" in url else "?") + urllib.parse.urlencode(query)


def webhook_send(webhook: str, payload: dict, *, thread_id: str = "", wait: bool = True) -> dict:
    query = {}
    if wait:
        query["wait"] = "true"
    if thread_id:
        query["thread_id"] = str(thread_id)
    if payload.get("components"):
        # Webhooks that are not owned by an application need this flag, and they
        # may only send link buttons.
        query["with_components"] = "true"
    return request("POST", with_query(webhook, query), payload=payload)


def webhook_send_tolerant(webhook: str, payload: dict, *, thread_id: str = "",
                          link_text: str = "") -> dict:
    """Posts a message and, should the server refuse the link buttons, retries
    once without them and puts the links into the embed instead."""
    try:
        return webhook_send(webhook, payload, thread_id=thread_id)
    except ApiError as error:
        if not payload.get("components") or "component" not in error.body.lower():
            raise
        warn("Discord refused the link buttons for this webhook, posting plain links instead.")
        retry = {key: value for key, value in payload.items() if key != "components"}
        if link_text and retry.get("embeds"):
            embed = dict(retry["embeds"][0])
            fields = list(embed.get("fields") or [])
            fields.append({"name": "Links", "value": clip(link_text, FIELD_LIMIT), "inline": False})
            embed["fields"] = fields
            retry["embeds"] = [embed] + retry["embeds"][1:]
        return webhook_send(webhook, retry, thread_id=thread_id)


def webhook_edit(webhook: str, message_id: str, payload: dict, *, thread_id: str = "") -> dict:
    query = {}
    if thread_id:
        query["thread_id"] = str(thread_id)
    if payload.get("components"):
        query["with_components"] = "true"
    return request("PATCH", with_query(f"{webhook}/messages/{message_id}", query), payload=payload)


def bot_request(method: str, path: str, *, payload=None):
    """Returns None when there is no bot token or the call failed; every bot
    feature is optional on purpose."""
    token = env("DISCORD_BOT_TOKEN")
    if not token:
        return None
    try:
        return request(method, f"{DISCORD_API}{path}", payload=payload, authorization=f"Bot {token}")
    except ApiError as error:
        warn(f"Discord bot request failed ({method} {path}): {error}")
        return None


def github_request(method: str, path: str, *, payload=None):
    token = env("GITHUB_TOKEN")
    if not token:
        fail("GITHUB_TOKEN is not available to this step.")
    return request(method, f"{GITHUB_API}{path}", payload=payload,
                   authorization=f"Bearer {token}", accept="application/vnd.github+json")


# ── placeholders ──────────────────────────────────────────────────────────────

def base_values() -> dict:
    """Repository variables come first, so every variable can be used as
    {MY_VARIABLE} in the configuration and overrides the derived defaults."""
    values = {str(key).upper(): str(value) for key, value in json_env("GITHUB_VARS_JSON").items()}

    repository = env("GITHUB_REPOSITORY")
    server = env("GITHUB_SERVER_URL", "https://github.com")
    owner, _, name = repository.partition("/")

    values.update({
        "REPO": repository,
        "REPO_NAME": name,
        "REPO_OWNER": owner,
        "OWNER": owner,
        "REPO_URL": f"{server}/{repository}" if repository else "",
        "ISSUES_URL": f"{server}/{repository}/issues" if repository else "",
        "SERVER_URL": server,
    })

    role = values.get("DISCORD_PING_ROLE_ID", "")
    values["ROLE"] = f"<@&{role}>" if role else ""

    if not values.get("CURSEFORGE_URL"):
        slug = values.get("CURSEFORGE_SLUG")
        project = slug or values.get("CURSEFORGE_ID")
        page = "minecraft/mc-mods" if slug else "projects"
        values["CURSEFORGE_URL"] = f"https://www.curseforge.com/{page}/{project}" if project else ""

    if not values.get("MODRINTH_URL"):
        project = values.get("MODRINTH_SLUG") or values.get("MODRINTH_ID")
        values["MODRINTH_URL"] = f"https://modrinth.com/mod/{project}" if project else ""

    return values


def expand(text, values: dict, *, tidy: bool = False) -> str:
    """Replaces {PLACEHOLDER} with its value; unknown names become empty."""
    if not text:
        return ""
    result = re.sub(r"\{([A-Z0-9_]+)\}", lambda match: values.get(match.group(1), ""), str(text))
    return squeeze(result) if tidy else result


NO_MENTIONS = {"parse": [], "roles": [], "users": []}


def allowed_mentions_for(mention: str) -> dict:
    parse = ["everyone"] if ("@everyone" in mention or "@here" in mention) else []
    return {
        "parse": parse,
        "roles": re.findall(r"<@&(\d+)>", mention),
        "users": re.findall(r"<@!?(\d+)>", mention),
    }


# ── buttons ───────────────────────────────────────────────────────────────────

def emoji_of(raw):
    text = str(raw or "").strip()
    if not text:
        return None
    custom = re.fullmatch(r"<?(a)?:?([A-Za-z0-9_]+):(\d+)>?", text)
    if custom:
        return {"name": custom.group(2), "id": custom.group(3), "animated": bool(custom.group(1))}
    return {"name": text}


def build_components(buttons, values: dict):
    """Turns the configured buttons into action rows. A button whose url does
    not resolve is dropped, so an unset variable simply hides its button."""
    resolved = []
    seen = set()
    for entry in buttons or []:
        if not isinstance(entry, dict) or entry.get("enabled") is False:
            continue
        url = expand(entry.get("url"), values, tidy=True)
        if not url.startswith(("http://", "https://")) or url in seen:
            continue
        seen.add(url)
        button = {
            "type": 2,
            "style": 5,
            "label": clip(expand(entry.get("label"), values, tidy=True) or "Open", 80),
            "url": url,
        }
        emoji = emoji_of(entry.get("emoji"))
        if emoji:
            button["emoji"] = emoji
        resolved.append(button)

    resolved = resolved[:25]
    rows = [{"type": 1, "components": resolved[start:start + 5]} for start in range(0, len(resolved), 5)]
    links = " • ".join(f"[{button['label']}]({button['url']})" for button in resolved)
    return rows, links


# ── release announcement ──────────────────────────────────────────────────────

# Leading whitespace plus an optional list marker, so a version prefix can go
# behind the bullet instead of in front of it.
BULLET = re.compile(r"^(\s*(?:[-*+]\s+|\d+[.)]\s+)?)(.*)$", re.S)


def version_key(text):
    """Sorts 1.9 before 1.20, which an alphabetical sort would not."""
    parts = [part for part in re.split(r"(\d+)", str(text)) if part != ""]
    return [(1, int(part), "") if part.isdigit() else (0, 0, part) for part in parts]


def load_releases() -> list:
    """The release data the workflow collected with gh: newest release first."""
    data = read_json_file(env("MBT_RELEASES", "releases.json"))
    if isinstance(data, dict):
        data = [data]
    if not data:
        fail("No release data found. MBT_RELEASES has to point at the json the workflow writes.")

    releases = []
    for entry in data:
        metadata = entry.get("metadata") or {}
        loaders = [str(item) for item in (metadata.get("loaders") or [])]
        tag = str(entry.get("tag") or entry.get("tagName") or "")

        # Without build data the Minecraft version still sits in the tag that
        # the build tools generated, for example v3.1.0+mc1.20.1.
        mc = str(metadata.get("minecraft_version") or "")
        if not mc:
            found = re.search(r"[+-]mc([0-9]+(?:\.[0-9]+)*)", tag)
            mc = found.group(1) if found else ""

        releases.append({
            "tag": tag,
            "name": str(entry.get("name") or ""),
            "url": str(entry.get("url") or entry.get("html_url") or ""),
            "body": str(entry.get("body") or ""),
            "published_at": str(entry.get("published_at") or entry.get("publishedAt") or ""),
            "prerelease": bool(entry.get("prerelease") or entry.get("isPrerelease")),
            "metadata": metadata,
            "loaders": loaders,
            "mc": mc,
            "label": mc or tag,
        })

    releases.sort(key=lambda item: item["published_at"], reverse=True)
    return releases


def clean_changelog(body: str) -> list:
    """The changelog as it was written, only our own marker removed."""
    text = re.sub(r"<!--.*?-->", "", body or "", flags=re.S)
    return text.replace("\r\n", "\n").strip("\n").split("\n")


def prefix_line(line: str, labels: list) -> str:
    head, rest = BULLET.match(line).groups()
    if not rest.strip():
        return line
    return f"{head}[{', '.join(labels)}] {rest}"


def combine_changelogs(releases: list) -> list:
    """Merges the changelogs of several releases line by line. A line that every
    release has stays untouched, every other line is tagged with the Minecraft
    versions that have it. The newest release provides the order; lines only
    found in the others are appended."""
    sources = [(release["label"], clean_changelog(release["body"])) for release in releases]
    if len(sources) == 1:
        return sources[0][1]

    presence = {}
    for label, lines in sources:
        for line in lines:
            key = squeeze(line)
            if not key:
                continue
            labels = presence.setdefault(key, [])
            if label not in labels:
                labels.append(label)

    def order(labels):
        return sorted(labels, key=version_key)

    total = len(sources)
    merged = []
    seen = set()

    for line in sources[0][1]:
        key = squeeze(line)
        if not key:
            merged.append(line)
            continue
        seen.add(key)
        labels = presence[key]
        merged.append(line if len(labels) == total else prefix_line(line, order(labels)))

    for _, lines in sources[1:]:
        for line in lines:
            key = squeeze(line)
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append(prefix_line(line, order(presence[key])))

    while merged and not merged[-1].strip():
        merged.pop()
    return merged


def fit_lines(lines: list, budget: int):
    """Whole lines only: the first line that no longer fits ends the block."""
    kept, used = [], 0
    for line in lines:
        cost = len(line) + (1 if kept else 0)
        if used + cost > budget:
            return kept, True
        kept.append(line)
        used += cost
    return kept, False


def changelog_block(releases: list, limit, links: str) -> str:
    """The changelog for the embed: as many whole lines as fit next to the
    links, a single over long line gets cut with an ellipsis."""
    lines = combine_changelogs(releases)
    if not any(line.strip() for line in lines):
        lines = ["_No changelog for this release._"]

    budget = DESCRIPTION_LIMIT - len(links) - 8
    if limit:
        budget = min(budget, max(50, int(limit)))

    kept, dropped = fit_lines(lines, budget)
    if not kept:
        kept, dropped = [clip(lines[0], budget)], False
    text = "\n".join(kept)
    if dropped:
        text += "\n…"
    return (text + "\n\n" + links).strip()


def changelog_links(releases: list) -> str:
    if len(releases) == 1:
        url = releases[0]["url"]
        return f"**[Full changelog on GitHub]({url})**" if url else ""
    parts = [f"[{release['label']}]({release['url']})" for release in releases if release["url"]]
    return ("**Full changelog:** " + " · ".join(parts)) if parts else ""


def release_values(releases: list) -> dict:
    base = releases[0]
    metadata = base["metadata"]

    loaders = []
    for release in releases:
        for loader in release["loaders"]:
            if loader not in loaders:
                loaders.append(loader)

    versions = []
    for release in releases:
        if release["mc"] and release["mc"] not in versions:
            versions.append(release["mc"])
    versions.sort(key=version_key)

    values = base_values()
    values.update({
        "MOD_ID": str(metadata.get("mod_id") or ""),
        "MOD_NAME": str(metadata.get("mod_name") or values.get("REPO_NAME", "")),
        "MOD_VERSION": str(metadata.get("mod_version") or ""),
        "FULL_VERSION": str(metadata.get("full_version") or ""),
        "MC_VERSION": base["mc"],
        "MINECRAFT_VERSION": base["mc"],
        "MC_VERSIONS": ", ".join(versions),
        "JAVA_VERSION": str(metadata.get("java_version") or ""),
        "RELEASE_CHANNEL": str(metadata.get("release_channel") or ""),
        "PLATFORMS": ("[" + "/".join(loader.upper() for loader in loaders) + "]") if loaders else "",
        "LOADERS": ", ".join(loader.capitalize() for loader in loaders),
        "TAG": base["tag"],
        "TAGS": ", ".join(release["tag"] for release in releases),
        "RELEASE_NAME": base["name"],
        "RELEASE_URL": base["url"],
    })
    return values


def release_warnings(releases: list) -> list:
    warnings = []

    missing = [release["tag"] for release in releases if not release["metadata"]]
    if missing:
        warnings.append("No build data for " + ", ".join(missing)
                        + " - mod name, version and loaders of these releases are unknown.")

    versions = sorted({str(release["metadata"].get("mod_version") or "")
                       for release in releases if release["metadata"]} - {""})
    if len(versions) > 1:
        warnings.append("The releases carry different mod versions (" + ", ".join(versions)
                        + "). The announcement uses " + versions[-1] + " from the newest release.")

    dates = sorted(release["published_at"][:10] for release in releases if release["published_at"])
    if len(dates) > 1 and dates[0] != dates[-1]:
        warnings.append(f"These releases were published on different days ({dates[0]} … {dates[-1]}). "
                        "Did you mean to announce them together?")

    return warnings


def write_summary(markdown: str) -> None:
    path = env("GITHUB_STEP_SUMMARY")
    if not path:
        log(markdown)
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(markdown.rstrip() + "\n")


def release_report(releases: list, payload: dict, buttons: list, dry_run: bool) -> str:
    rows = ["| Tag | Minecraft | Loaders | Published |", "|---|---|---|---|"]
    for release in releases:
        rows.append(f"| `{release['tag']}` | {release['mc'] or '?'} | "
                    f"{', '.join(release['loaders']) or '?'} | {release['published_at'][:10] or '?'} |")

    embed = (payload.get("embeds") or [{}])[0]
    lines = [f"### {'Dry run: nothing was posted' if dry_run else 'Announced on Discord'}", ""]
    lines += rows + [""]
    for warning in release_warnings(releases):
        lines.append(f"> [!WARNING]\n> {warning}")
        lines.append("")
    lines.append(f"**Title:** {embed.get('title', '')}")
    lines.append("")
    lines.append("**Ping:** " + (f"`{payload['content']}`" if payload.get("content") else "none"))
    lines.append("")
    lines.append("**Buttons:** " + (", ".join(f"[{button['label']}]({button['url']})"
                                               for button in buttons) or "none"))
    lines.append("")
    lines.append("**Message:**")
    lines.append("")
    lines.append("```markdown")
    lines.append(embed.get("description", ""))
    lines.append("```")
    return "\n".join(lines)


def command_release() -> None:
    settings = config("release")
    if not settings.get("enabled", True):
        log("Release announcements are switched off in build_tools.config.")
        return

    dry_run = env("MBT_DRY_RUN").lower() == "true"
    webhook = env("DISCORD_RELEASE_WEBHOOK")
    if not webhook and not dry_run:
        log("DISCORD_RELEASE_WEBHOOK is not set, nothing to announce.")
        return

    releases = load_releases()
    values = release_values(releases)
    for warning in release_warnings(releases):
        warn(warning)

    title = clip(expand(settings.get("title_format"), values, tidy=True), TITLE_LIMIT)
    if not title:
        title = clip(values["RELEASE_NAME"] or values["TAG"] or "New release", TITLE_LIMIT)

    links = changelog_links(releases)
    embed = {
        "title": title,
        "description": changelog_block(releases, settings.get("changelog_limit"), links),
        "color": color_of(settings.get("prerelease_color") if releases[0]["prerelease"]
                          else settings.get("color")),
    }
    if values["RELEASE_URL"]:
        embed["url"] = values["RELEASE_URL"]

    thumbnail = expand(settings.get("thumbnail"), values, tidy=True)
    if thumbnail.startswith("http"):
        embed["thumbnail"] = {"url": thumbnail}
    image = expand(settings.get("image"), values, tidy=True)
    if image.startswith("http"):
        embed["image"] = {"url": image}
    footer = clip(expand(settings.get("footer"), values, tidy=True), 2048)
    if footer:
        embed["footer"] = {"text": footer}

    rows, link_text = build_components(settings.get("buttons"), values)
    buttons = [button for row in rows for button in row["components"]]

    mention = "" if env("MBT_PING").lower() == "false" else expand(settings.get("mention"), values, tidy=True)
    payload = {
        "embeds": [embed],
        "allowed_mentions": allowed_mentions_for(mention) if mention else NO_MENTIONS,
    }
    if mention:
        payload["content"] = clip(mention, CONTENT_LIMIT)
    if rows:
        payload["components"] = rows

    if dry_run:
        write_summary(release_report(releases, payload, buttons, True))
        log("Dry run, nothing was posted:")
        log(json.dumps(payload, indent=2, ensure_ascii=False))
        return

    message = webhook_send_tolerant(webhook, payload, link_text=link_text)
    log(f"Announced: {title}")
    write_summary(release_report(releases, payload, buttons, False))

    if settings.get("crosspost"):
        if not env("DISCORD_BOT_TOKEN"):
            warn("crosspost is on but DISCORD_BOT_TOKEN is not set, the message was not published.")
        elif message.get("id") and message.get("channel_id"):
            path = f"/channels/{message['channel_id']}/messages/{message['id']}/crosspost"
            if bot_request("POST", path) is not None:
                log("Published the announcement to the following servers.")


# ── development feed ──────────────────────────────────────────────────────────

def actor_author(payload: dict) -> dict:
    user = payload.get("sender") or {}
    return {
        "name": str(user.get("login") or "unknown"),
        "url": str(user.get("html_url") or ""),
        "icon_url": str(user.get("avatar_url") or ""),
    }


def body_excerpt(text, limit: int = 600) -> str:
    cleaned = re.sub(r"<!--.*?-->", "", str(text or ""), flags=re.S)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return clip(cleaned, limit)


def embed_push(payload: dict, settings: dict, colors: dict):
    if payload.get("deleted") or not str(payload.get("ref") or "").startswith("refs/heads/"):
        return None

    branch = str(payload["ref"]).split("/", 2)[-1]
    patterns = settings.get("branches") or []
    if patterns and not any(fnmatch.fnmatch(branch, str(pattern)) for pattern in patterns):
        log(f"Branch '{branch}' is not covered by discord.dev.branches.")
        return None

    commits = [commit for commit in (payload.get("commits") or []) if commit.get("message")]
    if settings.get("skip_merge_commits"):
        commits = [commit for commit in commits if not str(commit["message"]).startswith("Merge ")]
    if not commits:
        return None

    maximum = max(1, int(settings.get("max_commits") or 10))
    lines = []
    for commit in commits[:maximum]:
        headline = clip(squeeze(str(commit["message"]).splitlines()[0]), 90)
        author = (commit.get("author") or {}).get("username") or (commit.get("author") or {}).get("name") or ""
        short = str(commit.get("id") or "")[:7]
        lines.append(f"[`{short}`]({commit.get('url') or ''}) {headline}" + (f" — {author}" if author else ""))
    if len(commits) > maximum:
        lines.append(f"… and {len(commits) - maximum} more")

    count = f"{len(commits)} new commit{'s' if len(commits) != 1 else ''}"
    return {
        "title": f"{count} in {branch}",
        "url": str(payload.get("compare") or ""),
        "description": clip("\n".join(lines), DESCRIPTION_LIMIT),
        "color": color_of(colors.get("push")),
        "author": actor_author(payload),
        "footer": {"text": str((payload.get("repository") or {}).get("full_name") or "")},
    }


def embed_pull_request(payload: dict, settings: dict, colors: dict):
    action = str(payload.get("action") or "")
    request_data = payload.get("pull_request") or {}

    if action == "closed":
        merged = bool(request_data.get("merged"))
        label, color = ("merged", colors.get("merged")) if merged else ("closed", colors.get("closed"))
    elif action in ("opened", "reopened"):
        label, color = action, colors.get("opened")
    elif action == "ready_for_review":
        label, color = "ready for review", colors.get("opened")
    else:
        return None

    head = (request_data.get("head") or {}).get("ref") or "?"
    base = (request_data.get("base") or {}).get("ref") or "?"
    changes = f"+{request_data.get('additions', 0)} / -{request_data.get('deletions', 0)}"
    repository = str((payload.get("repository") or {}).get("full_name") or "")
    return {
        "title": clip(f"Pull request #{request_data.get('number')} {label}: {request_data.get('title') or ''}",
                      TITLE_LIMIT),
        "url": str(request_data.get("html_url") or ""),
        "description": body_excerpt(request_data.get("body")),
        "color": color_of(color),
        "author": actor_author(payload),
        "footer": {"text": f"{repository} • {head} → {base} • {changes}"},
    }


def embed_issues(payload: dict, settings: dict, colors: dict):
    action = str(payload.get("action") or "")
    if action not in ("opened", "closed", "reopened"):
        return None

    issue = payload.get("issue") or {}
    color = colors.get("opened") if action in ("opened", "reopened") else colors.get("closed")
    labels = ", ".join(str((label or {}).get("name") or "") for label in (issue.get("labels") or []))
    return {
        "title": clip(f"Issue #{issue.get('number')} {action}: {issue.get('title') or ''}", TITLE_LIMIT),
        "url": str(issue.get("html_url") or ""),
        "description": body_excerpt(issue.get("body")),
        "color": color_of(color),
        "author": actor_author(payload),
        "footer": {"text": labels or str((payload.get("repository") or {}).get("full_name") or "")},
    }


def embed_issue_comment(payload: dict, settings: dict, colors: dict):
    if payload.get("action") != "created":
        return None

    issue = payload.get("issue") or {}
    comment = payload.get("comment") or {}
    body = str(comment.get("body") or "")
    if MIRROR_MARKER in body or re.search(rf"<!--{STATE_MARKER}:", body):
        return None

    kind = "pull request" if issue.get("pull_request") else "issue"
    return {
        "title": clip(f"Comment on {kind} #{issue.get('number')}: {issue.get('title') or ''}", TITLE_LIMIT),
        "url": str(comment.get("html_url") or ""),
        "description": body_excerpt(comment.get("body")),
        "color": color_of(colors.get("comment")),
        "author": actor_author(payload),
    }


def embed_review(payload: dict, settings: dict, colors: dict):
    if payload.get("action") != "submitted":
        return None

    review = payload.get("review") or {}
    state = str(review.get("state") or "").lower()
    if state == "commented" and not str(review.get("body") or "").strip():
        return None

    request_data = payload.get("pull_request") or {}
    verbs = {"approved": "approved", "changes_requested": "requested changes on", "commented": "reviewed"}
    return {
        "title": clip(f"{(verbs.get(state) or 'reviewed').capitalize()} pull request "
                      f"#{request_data.get('number')}: {request_data.get('title') or ''}", TITLE_LIMIT),
        "url": str(review.get("html_url") or ""),
        "description": body_excerpt(review.get("body")),
        "color": color_of(colors.get("merged") if state == "approved" else colors.get("misc")),
        "author": actor_author(payload),
    }


def embed_discussion(payload: dict, settings: dict, colors: dict):
    if payload.get("action") not in ("created", "answered"):
        return None

    discussion = payload.get("discussion") or {}
    comment = payload.get("comment") or {}
    if comment:
        return {
            "title": clip(f"Comment on discussion #{discussion.get('number')}: "
                          f"{discussion.get('title') or ''}", TITLE_LIMIT),
            "url": str(comment.get("html_url") or ""),
            "description": body_excerpt(comment.get("body")),
            "color": color_of(colors.get("comment")),
            "author": actor_author(payload),
        }
    return {
        "title": clip(f"New discussion #{discussion.get('number')}: {discussion.get('title') or ''}",
                      TITLE_LIMIT),
        "url": str(discussion.get("html_url") or ""),
        "description": body_excerpt(discussion.get("body")),
        "color": color_of(colors.get("misc")),
        "author": actor_author(payload),
        "footer": {"text": str((discussion.get("category") or {}).get("name") or "")},
    }


def embed_release_event(payload: dict, settings: dict, colors: dict):
    if payload.get("action") not in ("published", "released"):
        return None

    release = payload.get("release") or {}
    return {
        "title": clip(f"Release published: {release.get('name') or release.get('tag_name') or ''}",
                      TITLE_LIMIT),
        "url": str(release.get("html_url") or ""),
        "description": body_excerpt(release.get("body"), 900),
        "color": color_of(colors.get("release")),
        "author": actor_author(payload),
    }


def embed_workflow_failure(payload: dict, settings: dict, colors: dict):
    run = payload.get("workflow_run") or {}
    if run.get("conclusion") != "failure":
        return None

    return {
        "title": clip(f"Workflow failed: {run.get('name') or ''}", TITLE_LIMIT),
        "url": str(run.get("html_url") or ""),
        "description": f"Branch `{run.get('head_branch') or '?'}`, attempt {run.get('run_attempt', 1)}, "
                       f"triggered by `{run.get('event') or '?'}`.",
        "color": color_of(colors.get("failure")),
        "author": actor_author(payload),
    }


def embed_star(payload: dict, settings: dict, colors: dict):
    if payload.get("action") != "created":
        return None

    repository = payload.get("repository") or {}
    return {
        "title": f"New star for {repository.get('full_name') or ''}",
        "url": str(repository.get("html_url") or ""),
        "description": f"{repository.get('stargazers_count', 0)} stars in total.",
        "color": color_of(colors.get("misc")),
        "author": actor_author(payload),
    }


def embed_fork(payload: dict, settings: dict, colors: dict):
    fork = payload.get("forkee") or {}
    return {
        "title": f"New fork: {fork.get('full_name') or ''}",
        "url": str(fork.get("html_url") or ""),
        "color": color_of(colors.get("misc")),
        "author": actor_author(payload),
    }


# GitHub event name -> (toggle in build_tools.config, embed builder)
DEV_EVENTS = {
    "push": ("push", embed_push),
    "pull_request": ("pull_request", embed_pull_request),
    "pull_request_target": ("pull_request", embed_pull_request),
    "pull_request_review": ("pull_request_review", embed_review),
    "issues": ("issues", embed_issues),
    "issue_comment": ("issue_comment", embed_issue_comment),
    "discussion": ("discussion", embed_discussion),
    "discussion_comment": ("discussion", embed_discussion),
    "release": ("release", embed_release_event),
    "workflow_run": ("workflow_failure", embed_workflow_failure),
    "watch": ("star", embed_star),
    "fork": ("fork", embed_fork),
}


def command_dev() -> None:
    settings = config("dev")
    if not settings.get("enabled", True):
        log("The development feed is switched off in build_tools.config.")
        return

    webhook = env("DISCORD_DEV_WEBHOOK")
    if not webhook:
        log("DISCORD_DEV_WEBHOOK is not set, nothing to post.")
        return

    name = env("GITHUB_EVENT_NAME")
    handler = DEV_EVENTS.get(name)
    if not handler:
        log(f"The development feed has no handler for the '{name}' event.")
        return

    toggle, builder = handler
    if not (settings.get("events") or {}).get(toggle, False):
        log(f"'{toggle}' is switched off in discord.dev.events.")
        return

    payload = event_payload()
    sender = payload.get("sender") or {}
    login = str(sender.get("login") or "")
    if settings.get("skip_bots", True) and (login.endswith("[bot]") or sender.get("type") == "Bot"):
        log(f"Ignoring an event triggered by {login or 'a bot'}.")
        return

    colors = merge(DEFAULTS["dev"]["colors"], settings.get("colors") or {})
    embed = builder(payload, settings, colors)
    if not embed:
        log("Nothing worth posting for this event.")
        return

    embed = {key: value for key, value in embed.items() if value not in ("", None, {})}
    webhook_send(webhook, {"embeds": [embed], "allowed_mentions": NO_MENTIONS}, wait=False)
    log(f"Posted the '{name}' event to the development channel.")


# ── issue forum ───────────────────────────────────────────────────────────────

def state_comment(repository: str, number: int):
    """Finds the hidden mapping comment of an issue and returns
    (comment, state); both are None when the issue has no thread yet."""
    comments = github_request("GET", f"/repos/{repository}/issues/{number}/comments?per_page=100")
    for comment in comments or []:
        found = re.search(rf"<!--{STATE_MARKER}:(.*?)-->", str(comment.get("body") or ""), flags=re.S)
        if found:
            try:
                return comment, json.loads(found.group(1))
            except json.JSONDecodeError:
                return comment, {}
    return None, None


def thread_url_of(thread_id: str, comment=None) -> str:
    if comment:
        found = re.search(r"https://discord\.com/channels/\S+", str(comment.get("body") or ""))
        if found:
            return found.group(0)
    guild = env("DISCORD_GUILD_ID")
    return f"https://discord.com/channels/{guild}/{thread_id}" if guild else ""


def save_state(repository: str, number: int, comment, state: dict, thread_url: str) -> None:
    link = thread_url or "see the issue forum on Discord"
    body = (f"Discord thread for this issue: {link}\n"
            f"<!--{STATE_MARKER}:{json.dumps(state, separators=(',', ':'))}-->")
    if comment:
        github_request("PATCH", f"/repos/{repository}/issues/comments/{comment['id']}",
                       payload={"body": body})
    else:
        github_request("POST", f"/repos/{repository}/issues/{number}/comments", payload={"body": body})


def forum_tags(settings: dict, labels) -> list:
    """Maps issue labels onto forum tag ids, either through tag_map or - when a
    bot token and the forum channel id are available - by matching names."""
    wanted = [str(label).lower() for label in labels if label]
    mapping = {str(key).lower(): str(value) for key, value in (settings.get("tag_map") or {}).items()}

    forum = env("DISCORD_ISSUE_FORUM_ID")
    if settings.get("labels_as_tags", True) and forum and env("DISCORD_BOT_TOKEN"):
        channel = bot_request("GET", f"/channels/{forum}") or {}
        for tag in channel.get("available_tags") or []:
            mapping.setdefault(str(tag.get("name") or "").lower(), str(tag.get("id")))

    tags = [mapping[name] for name in wanted if name in mapping]
    if not tags:
        tags = [str(tag) for tag in (settings.get("default_tags") or [])]
    return list(dict.fromkeys(tags))[:5]


def issue_embed(issue: dict, settings: dict, values: dict) -> dict:
    user = issue.get("user") or {}
    labels = ", ".join(str((label or {}).get("name") or "") for label in (issue.get("labels") or []))
    state = "closed" if issue.get("state") == "closed" else "open"
    body = body_excerpt(issue.get("body"), max(200, int(settings.get("body_limit") or 1800)))
    return {
        "title": clip(issue.get("title") or "Issue", TITLE_LIMIT),
        "url": str(issue.get("html_url") or ""),
        "description": body or "_No description._",
        "color": color_of(settings.get("color")),
        "author": {
            "name": str(user.get("login") or "unknown"),
            "url": str(user.get("html_url") or ""),
            "icon_url": str(user.get("avatar_url") or ""),
        },
        "footer": {"text": f"{values.get('REPO', '')} • #{issue.get('number')} • {state}"
                           + (f" • {labels}" if labels else "")},
    }


def create_thread(webhook: str, issue: dict, settings: dict, values: dict) -> dict:
    name = expand(settings.get("thread_title_format") or "#{NUMBER} {TITLE}",
                  dict(values, NUMBER=str(issue.get("number")), TITLE=str(issue.get("title") or "")),
                  tidy=True)
    payload = {
        "thread_name": clip(name or f"#{issue.get('number')}", THREAD_NAME_LIMIT),
        "embeds": [issue_embed(issue, settings, values)],
        "allowed_mentions": NO_MENTIONS,
    }
    rows, links = build_components(
        [{"label": "Open on GitHub", "url": str(issue.get("html_url") or "")}], values)
    if rows:
        payload["components"] = rows

    tags = forum_tags(settings, [(label or {}).get("name") for label in (issue.get("labels") or [])])
    if tags:
        payload["applied_tags"] = tags

    try:
        return webhook_send_tolerant(webhook, payload, link_text=links)
    except ApiError as error:
        if tags and "applied_tags" in error.body:
            warn("The forum refused the tags, creating the post without them.")
            payload.pop("applied_tags")
            return webhook_send_tolerant(webhook, payload, link_text=links)
        raise


def thread_note(webhook: str, thread_id: str, text: str) -> None:
    webhook_send(webhook, {"content": clip(text, CONTENT_LIMIT), "allowed_mentions": NO_MENTIONS},
                 thread_id=thread_id, wait=False)


def mirror_github_comment(webhook: str, settings: dict, payload: dict, thread_id: str, number: int) -> None:
    comment = payload.get("comment") or {}
    user = comment.get("user") or {}
    author = str(user.get("login") or "unknown")

    if MIRROR_MARKER in str(comment.get("body") or ""):
        log("Skipping a comment that this mirror created itself.")
        return
    if re.search(rf"<!--{STATE_MARKER}:", str(comment.get("body") or "")):
        return
    if settings.get("skip_bots", True) and (author.endswith("[bot]") or user.get("type") == "Bot"):
        log(f"Skipping the comment from {author}.")
        return

    text = body_excerpt(comment.get("body"), 1500) or "_empty comment_"
    thread_note(webhook, thread_id, f"**{author}** on GitHub:\n{text}\n<{comment.get('html_url') or ''}>")
    log(f"Mirrored a comment of issue #{number} into its Discord thread.")


def command_issue() -> None:
    settings = config("issues")
    if not settings.get("enabled", True):
        log("The issue forum mirror is switched off in build_tools.config.")
        return

    webhook = env("DISCORD_ISSUE_WEBHOOK")
    if not webhook:
        log("DISCORD_ISSUE_WEBHOOK is not set, nothing to mirror.")
        return

    payload = event_payload()
    issue = payload.get("issue") or {}
    if not issue or issue.get("pull_request"):
        log("Not an issue event, skipping.")
        return

    repository = env("GITHUB_REPOSITORY")
    number = int(issue.get("number"))
    action = str(payload.get("action") or "")
    values = base_values()
    comment, state = state_comment(repository, number)
    state = state or {}
    thread_id = str(state.get("thread") or "")

    if env("GITHUB_EVENT_NAME") == "issue_comment":
        if action != "created" or not settings.get("mirror_github_comments", True):
            return
        if not thread_id:
            log("This issue has no Discord thread to mirror into.")
            return
        mirror_github_comment(webhook, settings, payload, thread_id, number)
        return

    if action == "opened":
        if thread_id:
            log("This issue already has a Discord thread.")
            return
        message = create_thread(webhook, issue, settings, values)
        thread_id = str(message.get("channel_id") or message.get("id") or "")
        if not thread_id:
            fail("Discord did not return a thread id. Is the webhook inside a forum channel?")
        message_id = str(message.get("id") or thread_id)
        url = thread_url_of(thread_id)
        save_state(repository, number, comment,
                   {"thread": thread_id, "message": message_id, "last": message_id}, url)
        log(f"Created the forum post for issue #{number}{f': {url}' if url else ''}")
        return

    if not thread_id:
        log("This issue has no Discord thread, nothing to update.")
        return

    if action == "edited":
        if not settings.get("mirror_edits", True):
            return
        try:
            webhook_edit(webhook, str(state.get("message") or thread_id),
                         {"embeds": [issue_embed(issue, settings, values)],
                          "allowed_mentions": NO_MENTIONS},
                         thread_id=thread_id)
            log(f"Updated the forum post of issue #{number}.")
        except ApiError as error:
            warn(f"Could not update the forum post: {error}")
        return

    if action in ("closed", "reopened"):
        if action == "closed":
            reason = str(issue.get("state_reason") or "")
            thread_note(webhook, thread_id,
                        f"🔒 This issue was closed{f' as {reason}' if reason else ''} on GitHub.")
        else:
            thread_note(webhook, thread_id, "🔓 This issue was reopened on GitHub.")

        if not settings.get("close_thread_on_close", True):
            return
        if not env("DISCORD_BOT_TOKEN"):
            log("DISCORD_BOT_TOKEN is not set, the thread stays as it is.")
            return

        if action == "reopened":
            result = bot_request("PATCH", f"/channels/{thread_id}",
                                 payload={"archived": False, "locked": False})
            log("Reopened the forum post." if result is not None
                else "Could not reopen the forum post, see the warning above.")
            return

        # Locking first: an archived thread can no longer be edited by the api.
        if settings.get("lock_thread_on_close", False):
            bot_request("PATCH", f"/channels/{thread_id}", payload={"locked": True})
        result = bot_request("PATCH", f"/channels/{thread_id}", payload={"archived": True})
        log("Closed the forum post." if result is not None
            else "Could not close the forum post, see the warning above.")
        return

    log(f"No action defined for '{action}'.")


# ── discord back to github ────────────────────────────────────────────────────

def command_sync() -> None:
    settings = config("issues")
    if not settings.get("enabled", True) or not settings.get("mirror_discord_messages", False):
        log("Mirroring Discord messages into issues is switched off in build_tools.config.")
        return
    if not env("DISCORD_BOT_TOKEN"):
        log("DISCORD_BOT_TOKEN is not set. Reading Discord messages needs a bot, skipping.")
        return

    repository = env("GITHUB_REPOSITORY")
    limit = max(1, min(int(settings.get("sync_issue_limit") or 50), 100))
    issues = github_request(
        "GET", f"/repos/{repository}/issues?state=open&sort=updated&direction=desc&per_page={limit}") or []

    for issue in issues:
        if issue.get("pull_request"):
            continue

        number = int(issue.get("number"))
        comment, state = state_comment(repository, number)
        state = state or {}
        thread_id = str(state.get("thread") or "")
        if not thread_id:
            continue

        after = str(state.get("last") or state.get("message") or "")
        query = f"/channels/{thread_id}/messages?limit=100" + (f"&after={after}" if after else "")
        messages = bot_request("GET", query)
        if not messages:
            continue

        newest = after
        posted = 0
        for message in sorted(messages, key=lambda item: int(item.get("id") or 0)):
            newest = str(message.get("id"))
            author = message.get("author") or {}
            if message.get("webhook_id") or author.get("bot"):
                continue
            if int(message.get("type") or 0) not in (0, 19, 21):
                continue

            text = str(message.get("content") or "").strip()
            attachments = [str(item.get("url") or "") for item in (message.get("attachments") or [])]
            if not text and not attachments:
                continue

            name = str(author.get("global_name") or author.get("username") or "someone")
            body = f"**{name}** wrote in the Discord thread:\n\n{clip(text, 4000)}"
            if attachments:
                body += "\n\n" + "\n".join(attachments)
            github_request("POST", f"/repos/{repository}/issues/{number}/comments",
                           payload={"body": f"{body}\n\n{MIRROR_MARKER}"})
            posted += 1

        if newest and newest != after:
            save_state(repository, number, comment, dict(state, last=newest),
                       thread_url_of(thread_id, comment))
        if posted:
            log(f"Copied {posted} Discord message(s) into issue #{number}.")


COMMANDS = {
    "release": command_release,
    "dev": command_dev,
    "issue": command_issue,
    "sync": command_sync,
}


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in COMMANDS:
        fail(f"Usage: discord_notify.py <{'|'.join(COMMANDS)}>")
    try:
        COMMANDS[sys.argv[1]]()
    except ApiError as error:
        fail(str(error))


if __name__ == "__main__":
    main()
