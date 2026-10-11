#!/usr/bin/env python3
"""Discord notifications for Mod Build Tools.

Every message lives in its own json file under notifications/<group>/. This
script is only the engine: it reads the GitHub event, decides which
notification files match it, renders their message template and posts the
result. It knows nothing about what a push or an issue means - that is all in
the json files.

  notify.py run <group>        post every matching notification of a group
  notify.py list [group]       what is configured and what it needs
  notify.py vars [event]       the placeholders available for an event
  notify.py render <file>      render one notification without posting it
  notify.py check              validate the configuration

Only the standard library is used, so there is nothing to install.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))

DISCORD_API = os.environ.get("MBT_DISCORD_API", "https://discord.com/api/v10")
GITHUB_API = os.environ.get("GITHUB_API_URL", "https://api.github.com")
USER_AGENT = "mod-build-tools-discord (+https://github.com/MisterJulsen/MC-Modding-Build-Tools)"

# Discord's own limits. Anything longer is cut automatically, so a template can
# never fail just because an issue body was long.
LIMITS = {
    "content": 2000,
    "thread_name": 100,
    "title": 256,
    "description": 4096,
    "field_name": 256,
    "field_value": 1024,
    "author_name": 256,
    "footer_text": 2048,
    "embeds": 10,
    "fields": 25,
    "buttons": 25,
}

# Hidden marker of the comment that ties an issue to its forum post.
STATE_MARKER = "mbt-discord"

NO_MENTIONS = {"parse": [], "roles": [], "users": []}

CONFIG_DEFAULTS = {
    "targets": {
        "dev": {"webhook": "DISCORD_DEV_WEBHOOK"},
        "announcements": {"webhook": "DISCORD_RELEASE_WEBHOOK"},
        "issue_forum": {"webhook": "DISCORD_ISSUE_WEBHOOK",
                        "forum_channel": "DISCORD_ISSUE_FORUM_ID"},
    },
    "groups": {
        "dev": {"workflow": ".github/workflows/discord-dev.yml"},
        "issues": {"workflow": ".github/workflows/discord-issues.yml"},
        "release": {"workflow": ".github/workflows/discord-release.yml"},
    },
    "colors": {
        "push": "#5865F2",
        "info": "#5865F2",
        "success": "#43B581",
        "opened": "#43B581",
        "merged": "#9B59B6",
        "closed": "#ED4245",
        "failure": "#ED4245",
        "warning": "#FAA61A",
        "neutral": "#747F8D",
    },
    "defaults": {
        "username": "",
        "avatar_url": "",
        "footer": "{REPO}",
    },
    "forum": {
        "labels_as_tags": True,
        "tag_map": {},
        "default_tags": [],
    },
}

RULE_KEYS = {"enabled", "description", "target", "on", "limits", "message",
             "thread", "ping", "crosspost"}
FILTER_KEYS = {"event", "action", "branches", "labels", "workflows", "conclusions",
               "skip_bots", "skip_drafts", "merged", "require"}
THREAD_KEYS = {"action", "name", "tags_from_labels", "archived", "locked"}


# ── output ───────────────────────────────────────────────────────────────────

for _stream in (sys.stdout, sys.stderr):
    # Emoji and box drawing characters are normal here; a Windows console that
    # defaults to cp1252 must not turn them into a crash.
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


def log(message: str) -> None:
    print(message, flush=True)


def warn(message: str) -> None:
    print(f"::warning::{message}", flush=True)


def fail(message: str) -> None:
    print(f"::error::{message}", file=sys.stderr, flush=True)
    sys.exit(1)


def write_summary(markdown: str) -> None:
    path = env("GITHUB_STEP_SUMMARY")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(markdown.rstrip() + "\n")


# ── small helpers ────────────────────────────────────────────────────────────

def env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def merge(base, override):
    """Deep merge, dictionaries only."""
    if not isinstance(base, dict) or not isinstance(override, dict):
        return base if override is None else override
    result = dict(base)
    for key, value in override.items():
        result[key] = merge(base.get(key), value) if key in base else value
    return result


def clip(text, limit: int, suffix: str = "…") -> str:
    text = str(text if text is not None else "")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - len(suffix))].rstrip() + suffix


def tidy(text) -> str:
    """One line, no double spaces - for titles and thread names."""
    return re.sub(r"\s+", " ", str(text or "")).strip()


def fit_text(text: str, budget: int) -> str:
    """Trims text to the budget on line boundaries, so a changelog never ends
    in the middle of an entry."""
    text = str(text or "")
    if len(text) <= budget:
        return text

    kept, used = [], 0
    for line in text.split("\n"):
        cost = len(line) + (1 if kept else 0)
        if used + cost > budget - 2:
            break
        kept.append(line)
        used += cost

    if not kept:
        return clip(text, budget)
    while kept and not kept[-1].strip():
        kept.pop()
    return "\n".join(kept) + "\n…"


def color_of(value, fallback: int = 0x5865F2) -> int:
    if isinstance(value, bool):
        return fallback
    if isinstance(value, int):
        return value
    try:
        return int(str(value or "").strip().lstrip("#"), 16)
    except ValueError:
        return fallback


def read_json(path):
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as error:
        fail(f"{path} is not valid JSON: {error}")


def json_env(name: str):
    raw = env(name)
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        warn(f"{name} does not contain valid JSON, ignoring it.")
        return {}


def body_text(text, limit: int) -> str:
    """An issue or release body, html comments removed and shortened."""
    cleaned = re.sub(r"<!--.*?-->", "", str(text or ""), flags=re.S)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return clip(cleaned, limit)


def load_config() -> dict:
    data = read_json(os.path.join(HERE, "config.json"))
    if data is None:
        warn("config.json not found next to notify.py, using the built in defaults.")
        data = {}
    return merge(CONFIG_DEFAULTS, data)


# ── http ─────────────────────────────────────────────────────────────────────

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
        # Webhooks not owned by an application need this flag, and they may only
        # send link buttons.
        query["with_components"] = "true"
    return request("POST", with_query(webhook, query), payload=payload)


def webhook_send_tolerant(webhook: str, payload: dict, *, thread_id: str = "",
                          links: str = "", wait: bool = True) -> dict:
    """Posts a message and, should the server refuse the link buttons, retries
    once without them and puts the links into the embed instead."""
    try:
        return webhook_send(webhook, payload, thread_id=thread_id, wait=wait)
    except ApiError as error:
        if not payload.get("components") or "component" not in error.body.lower():
            raise
        warn("Discord refused the link buttons for this webhook, posting plain links instead.")
        retry = {key: value for key, value in payload.items() if key != "components"}
        if links and retry.get("embeds"):
            embed = dict(retry["embeds"][0])
            fields = list(embed.get("fields") or [])
            fields.append({"name": "Links", "value": clip(links, LIMITS["field_value"]),
                           "inline": False})
            embed["fields"] = fields
            retry["embeds"] = [embed] + retry["embeds"][1:]
        return webhook_send(webhook, retry, thread_id=thread_id, wait=wait)


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
    if not env("DISCORD_BOT_TOKEN"):
        return None
    try:
        return request(method, f"{DISCORD_API}{path}", payload=payload,
                       authorization=f"Bot {env('DISCORD_BOT_TOKEN')}")
    except ApiError as error:
        warn(f"Discord bot request failed ({method} {path}): {error}")
        return None


def github_request(method: str, path: str, *, payload=None):
    if not env("GITHUB_TOKEN"):
        fail("GITHUB_TOKEN is not available to this step.")
    return request(method, f"{GITHUB_API}{path}", payload=payload,
                   authorization=f"Bearer {env('GITHUB_TOKEN')}",
                   accept="application/vnd.github+json")


# ── template engine ──────────────────────────────────────────────────────────

_PLACEHOLDER = re.compile(r"\{([A-Z][A-Z0-9_]*)\}")
_BLOCK = re.compile(r"\{\{\s*(?:(#each|#if|#unless)\s+([A-Z][A-Z0-9_]*)|(else)|/(each|if|unless))\s*\}\}")


class TemplateError(RuntimeError):
    pass


def parse_template(text: str):
    """Turns a template string into a small node tree.

    {NAME}                      the value of a placeholder, unknown ones are empty
    {{#each LIST}}…{{/each}}    once per entry, the entry's fields become placeholders
    {{#if NAME}}…{{else}}…{{/if}}
    {{#unless NAME}}…{{/unless}}

    Blocks may be nested. Placeholder names are upper case on purpose, so
    normal text in braces is left alone.
    """
    root = []
    stack = [{"kind": "", "name": "", "body": root, "alt": None}]
    position = 0

    def target():
        frame = stack[-1]
        return frame["alt"] if frame["alt"] is not None else frame["body"]

    for match in _BLOCK.finditer(text):
        literal = text[position:match.start()]
        if literal:
            target().append(("text", literal))
        position = match.end()

        opener, name, otherwise, closer = match.groups()
        if opener:
            stack.append({"kind": opener[1:], "name": name, "body": [], "alt": None})
        elif otherwise:
            if len(stack) == 1:
                raise TemplateError("{{else}} outside of a block")
            stack[-1]["alt"] = []
        else:
            if len(stack) == 1 or stack[-1]["kind"] != closer:
                raise TemplateError(f"unexpected {{{{/{closer}}}}}")
            frame = stack.pop()
            if frame["kind"] == "each":
                target().append(("each", frame["name"], frame["body"]))
            else:
                target().append(("cond", frame["name"], frame["body"], frame["alt"] or [],
                                 frame["kind"] == "unless"))

    if len(stack) != 1:
        raise TemplateError(f"{{{{#{stack[-1]['kind']} {stack[-1]['name']}}}}} is never closed")

    rest = text[position:]
    if rest:
        target().append(("text", rest))
    return root


def truthy(value) -> bool:
    if isinstance(value, list):
        return bool(value)
    return str(value or "").strip() not in ("", "false", "0")


def substitute(text: str, scope: dict) -> str:
    def replace(match):
        value = scope.get(match.group(1))
        if value is None or isinstance(value, list):
            return ""
        return str(value)
    return _PLACEHOLDER.sub(replace, text)


def render_nodes(nodes, scope: dict) -> str:
    out = []
    for node in nodes:
        if node[0] == "text":
            out.append(substitute(node[1], scope))
        elif node[0] == "each":
            items = scope.get(node[1])
            items = items if isinstance(items, list) else []
            for index, item in enumerate(items, 1):
                child = dict(scope)
                child.update(item if isinstance(item, dict) else {"VALUE": item})
                child["INDEX"] = str(index)
                child["FIRST"] = "true" if index == 1 else ""
                child["LAST"] = "true" if index == len(items) else ""
                out.append(render_nodes(node[2], child))
        else:
            _, name, body, otherwise, negate = node
            state = truthy(scope.get(name)) != negate
            out.append(render_nodes(body if state else otherwise, scope))
    return "".join(out)


def render(value, scope: dict):
    """Renders every string inside a json structure."""
    if isinstance(value, str):
        return render_nodes(parse_template(value), scope)
    if isinstance(value, list):
        return [render(item, scope) for item in value]
    if isinstance(value, dict):
        return {key: render(item, scope) for key, item in value.items()}
    return value


# ── message assembly ─────────────────────────────────────────────────────────

def emoji_of(raw):
    text = tidy(raw)
    if not text:
        return None
    custom = re.fullmatch(r"<?(a)?:?([A-Za-z0-9_]+):(\d+)>?", text)
    if custom:
        return {"name": custom.group(2), "id": custom.group(3), "animated": bool(custom.group(1))}
    return {"name": text}


def build_buttons(buttons, scope: dict):
    """A button whose url does not resolve is dropped, so an unset variable
    simply hides its button instead of breaking the message."""
    resolved, seen = [], set()

    for entry in buttons or []:
        if not isinstance(entry, dict) or entry.get("enabled") is False:
            continue
        url = tidy(render(entry.get("url") or "", scope))
        if not url.startswith(("http://", "https://")) or url in seen:
            continue
        seen.add(url)

        button = {
            "type": 2,
            "style": 5,
            "label": clip(tidy(render(entry.get("label") or "", scope)) or "Open", 80),
            "url": url,
        }
        emoji = emoji_of(render(entry.get("emoji") or "", scope))
        if emoji:
            button["emoji"] = emoji
        resolved.append(button)

    resolved = resolved[:LIMITS["buttons"]]
    rows = [{"type": 1, "components": resolved[start:start + 5]}
            for start in range(0, len(resolved), 5)]
    links = " • ".join(f"[{button['label']}]({button['url']})" for button in resolved)
    return rows, links


def tidy_embed(embed: dict) -> dict:
    """Drops everything that rendered empty and applies Discord's limits."""
    result = {}

    title = tidy(embed.get("title"))
    if title:
        result["title"] = clip(title, LIMITS["title"])

    description = str(embed.get("description") or "").strip()
    if description:
        result["description"] = clip(description, LIMITS["description"])

    url = tidy(embed.get("url"))
    if url.startswith(("http://", "https://")):
        result["url"] = url

    if embed.get("color") not in (None, ""):
        result["color"] = color_of(embed.get("color"))

    timestamp = tidy(embed.get("timestamp"))
    if timestamp:
        result["timestamp"] = timestamp

    author = embed.get("author") or {}
    name = tidy(author.get("name"))
    if name:
        result["author"] = {"name": clip(name, LIMITS["author_name"])}
        for key in ("url", "icon_url"):
            value = tidy(author.get(key))
            if value.startswith("http"):
                result["author"][key] = value

    footer = embed.get("footer") or {}
    text = tidy(footer.get("text"))
    if text:
        result["footer"] = {"text": clip(text, LIMITS["footer_text"])}
        icon = tidy(footer.get("icon_url"))
        if icon.startswith("http"):
            result["footer"]["icon_url"] = icon

    for key in ("thumbnail", "image"):
        value = tidy((embed.get(key) or {}).get("url"))
        if value.startswith("http"):
            result[key] = {"url": value}

    fields = []
    for field in embed.get("fields") or []:
        if not isinstance(field, dict):
            continue
        name = tidy(field.get("name"))
        value = str(field.get("value") or "").strip()
        if not name or not value:
            continue
        fields.append({"name": clip(name, LIMITS["field_name"]),
                       "value": clip(value, LIMITS["field_value"]),
                       "inline": bool(field.get("inline"))})
    if fields:
        result["fields"] = fields[:LIMITS["fields"]]

    return result


def build_message(rule: dict, scope: dict, settings: dict):
    """Renders the message of a rule. Returns the Discord payload, the plain
    link list of its buttons and the scope that was used."""
    template = rule.get("message") or {}
    defaults = settings.get("defaults") or {}

    # Only what the rule itself puts into "ping" may mention anyone; text that
    # came out of the event can never ping, whatever it contains.
    ping = tidy(render(rule.get("ping") or "", scope))
    if env("MBT_PING").lower() == "false":
        ping = ""
    scope = dict(scope, PING=ping)

    payload = {"allowed_mentions": NO_MENTIONS}
    if ping:
        payload["allowed_mentions"] = {
            "parse": ["everyone"] if ("@everyone" in ping or "@here" in ping) else [],
            "roles": re.findall(r"<@&(\d+)>", ping),
            "users": re.findall(r"<@!?(\d+)>", ping),
        }

    content = str(render(template.get("content") or "", scope)).strip()
    if content:
        payload["content"] = clip(content, LIMITS["content"])

    embeds = []
    for raw in render(template.get("embeds") or [], scope):
        if not isinstance(raw, dict):
            continue
        if not raw.get("footer") and defaults.get("footer"):
            raw["footer"] = {"text": render(str(defaults["footer"]), scope)}
        embed = tidy_embed(raw)
        if embed:
            embeds.append(embed)
    if embeds:
        payload["embeds"] = embeds[:LIMITS["embeds"]]

    rows, links = build_buttons(template.get("buttons"), scope)
    if rows:
        payload["components"] = rows

    for key in ("username", "avatar_url"):
        value = tidy(render(str(template.get(key) or defaults.get(key) or ""), scope))
        if value:
            payload[key] = value

    return payload, links, scope


# ── list entries ─────────────────────────────────────────────────────────────
#
# One builder per list placeholder. Calling them with an empty dictionary
# yields their field names, which is what "notify.py vars" prints.

def user_values(user, prefix: str) -> dict:
    user = user or {}
    return {
        prefix: str(user.get("login") or user.get("username") or ""),
        f"{prefix}_URL": str(user.get("html_url") or ""),
        f"{prefix}_AVATAR": str(user.get("avatar_url") or ""),
    }


def commit_item(commit) -> dict:
    commit = commit or {}
    author = commit.get("author") or {}
    sha = str(commit.get("id") or commit.get("sha") or "")
    message = str(commit.get("message") or "")
    return {
        "SHA": sha,
        "SHORT_SHA": sha[:7],
        "URL": str(commit.get("url") or commit.get("html_url") or ""),
        "SUBJECT": clip(tidy(message.split("\n")[0]), 100),
        "BODY": "\n".join(message.split("\n")[1:]).strip(),
        "AUTHOR": str(author.get("username") or author.get("name") or ""),
    }


def label_item(label) -> dict:
    label = label or {}
    return {
        "NAME": str(label.get("name") or ""),
        "COLOR": f"#{label.get('color')}" if label.get("color") else "",
        "DESCRIPTION": str(label.get("description") or ""),
    }


def release_item(release) -> dict:
    release = release or {}
    return {
        "TAG": str(release.get("tag") or ""),
        "LABEL": str(release.get("label") or ""),
        "MC": str(release.get("mc") or ""),
        "URL": str(release.get("url") or ""),
        "LOADERS": ", ".join(str(item).capitalize() for item in (release.get("loaders") or [])),
        "PUBLISHED": str(release.get("published_at") or "")[:10],
    }


ITEM_BUILDERS = {
    "COMMITS": commit_item,
    "ISSUE_LABELS": label_item,
    "PR_LABELS": label_item,
    "RELEASES": release_item,
}


# ── placeholders ─────────────────────────────────────────────────────────────

def base_values(payload: dict, settings: dict) -> dict:
    """Repository variables come first, so every GitHub variable is usable as
    {MY_VARIABLE} and can override what we derive below."""
    values = {str(key).upper(): str(value) for key, value in json_env("GITHUB_VARS_JSON").items()}

    repository = (env("GITHUB_REPOSITORY")
                  or str((payload.get("repository") or {}).get("full_name") or ""))
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
        "DEFAULT_BRANCH": str((payload.get("repository") or {}).get("default_branch") or ""),
        "ACTION": str(payload.get("action") or ""),
        "EVENT": "",
    })
    values.update(user_values(payload.get("sender"), "ACTOR"))

    run = env("GITHUB_RUN_ID")
    values["WORKFLOW_RUN_URL"] = (f"{values['REPO_URL']}/actions/runs/{run}"
                                  if run and values["REPO_URL"] else "")

    for key, value in (settings.get("colors") or {}).items():
        values[f"COLOR_{str(key).upper()}"] = str(value)

    if not values.get("MOD_NAME"):
        values["MOD_NAME"] = name

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


def vars_push(payload: dict) -> dict:
    ref = str(payload.get("ref") or "")
    branch = ref.split("/", 2)[-1] if ref.startswith("refs/heads/") else ""
    commits = [item for item in (payload.get("commits") or []) if (item or {}).get("message")]
    return {
        "BRANCH": branch,
        "REF": ref,
        "COMPARE_URL": str(payload.get("compare") or ""),
        "BEFORE": str(payload.get("before") or "")[:7],
        "AFTER": str(payload.get("after") or "")[:7],
        "FORCED": "true" if payload.get("forced") else "",
        "DELETED": "true" if payload.get("deleted") else "",
        "HEAD_SUBJECT": commit_item(payload.get("head_commit"))["SUBJECT"],
        "COMMITS": [commit_item(item) for item in commits],
    }


def vars_pull_request(payload: dict) -> dict:
    request_data = payload.get("pull_request") or {}
    action = str(payload.get("action") or "")
    merged = bool(request_data.get("merged"))
    verbs = {
        "opened": "opened",
        "reopened": "reopened",
        "closed": "merged" if merged else "closed",
        "ready_for_review": "ready for review",
        "converted_to_draft": "converted to draft",
        "synchronize": "updated",
        "edited": "edited",
    }
    labels = [label_item(item) for item in (request_data.get("labels") or [])]

    def number(key):
        value = request_data.get(key)
        return "" if value is None else str(value)

    values = {
        "PR_NUMBER": number("number"),
        "PR_TITLE": str(request_data.get("title") or ""),
        "PR_URL": str(request_data.get("html_url") or ""),
        "PR_BODY": body_text(request_data.get("body"), 1200),
        "PR_VERB": verbs.get(action, action.replace("_", " ")),
        "PR_STATE": str(request_data.get("state") or ""),
        "PR_MERGED": "true" if merged else "",
        "PR_DRAFT": "true" if request_data.get("draft") else "",
        "PR_HEAD": str((request_data.get("head") or {}).get("ref") or ""),
        "PR_BASE": str((request_data.get("base") or {}).get("ref") or ""),
        "PR_ADDITIONS": number("additions"),
        "PR_DELETIONS": number("deletions"),
        "PR_CHANGED_FILES": number("changed_files"),
        "PR_COMMITS": number("commits"),
        "PR_LABELS": labels,
        "PR_LABEL_NAMES": ", ".join(item["NAME"] for item in labels),
    }
    values.update(user_values(request_data.get("user"), "PR_AUTHOR"))
    return values


def vars_issues(payload: dict) -> dict:
    issue = payload.get("issue") or {}
    labels = [label_item(item) for item in (issue.get("labels") or [])]
    values = {
        "ISSUE_NUMBER": str(issue.get("number") or ""),
        "ISSUE_TITLE": str(issue.get("title") or ""),
        "ISSUE_URL": str(issue.get("html_url") or ""),
        "ISSUE_BODY": body_text(issue.get("body"), 3500),
        "ISSUE_STATE": str(issue.get("state") or ""),
        "ISSUE_STATE_REASON": str(issue.get("state_reason") or "").replace("_", " "),
        "ISSUE_MILESTONE": str((issue.get("milestone") or {}).get("title") or ""),
        "ISSUE_ASSIGNEES": ", ".join(str((item or {}).get("login") or "")
                                     for item in (issue.get("assignees") or [])),
        "ISSUE_COMMENT_COUNT": str(issue.get("comments") or 0),
        "ISSUE_LABELS": labels,
        "ISSUE_LABEL_NAMES": ", ".join(item["NAME"] for item in labels),
        "LABEL": str((payload.get("label") or {}).get("name") or ""),
    }
    values.update(user_values(issue.get("user"), "ISSUE_AUTHOR"))
    return values


def vars_pull_request_review(payload: dict) -> dict:
    review = payload.get("review") or {}
    state = str(review.get("state") or "").lower()
    verbs = {"approved": "approved", "changes_requested": "requested changes on",
             "commented": "reviewed", "dismissed": "dismissed a review on"}
    values = vars_pull_request(payload)
    values.update({
        "REVIEW_STATE": state,
        "REVIEW_VERB": verbs.get(state, "reviewed"),
        "REVIEW_BODY": body_text(review.get("body"), 1200),
        "REVIEW_URL": str(review.get("html_url") or ""),
    })
    values.update(user_values(review.get("user"), "REVIEW_AUTHOR"))
    return values


def vars_discussion(payload: dict) -> dict:
    discussion = payload.get("discussion") or {}
    comment = payload.get("comment") or {}
    values = {
        "DISCUSSION_NUMBER": str(discussion.get("number") or ""),
        "DISCUSSION_TITLE": str(discussion.get("title") or ""),
        "DISCUSSION_URL": str(discussion.get("html_url") or ""),
        "DISCUSSION_BODY": body_text(discussion.get("body"), 1200),
        "DISCUSSION_CATEGORY": str((discussion.get("category") or {}).get("name") or ""),
        "COMMENT_BODY": body_text(comment.get("body"), 1200),
        "COMMENT_URL": str(comment.get("html_url") or ""),
    }
    values.update(user_values(discussion.get("user"), "DISCUSSION_AUTHOR"))
    return values


def vars_release(payload: dict) -> dict:
    release = payload.get("release") or {}
    return {
        "RELEASE_TAG": str(release.get("tag_name") or ""),
        "RELEASE_NAME": str(release.get("name") or release.get("tag_name") or ""),
        "RELEASE_URL": str(release.get("html_url") or ""),
        "RELEASE_BODY": body_text(release.get("body"), 2000),
        "RELEASE_PRERELEASE": "true" if release.get("prerelease") else "",
    }


def vars_workflow_run(payload: dict) -> dict:
    run = payload.get("workflow_run") or {}
    return {
        "RUN_NAME": str(run.get("name") or ""),
        "RUN_URL": str(run.get("html_url") or ""),
        "RUN_CONCLUSION": str(run.get("conclusion") or ""),
        "RUN_BRANCH": str(run.get("head_branch") or ""),
        "RUN_EVENT": str(run.get("event") or ""),
        "RUN_NUMBER": str(run.get("run_number") or ""),
        "RUN_ATTEMPT": str(run.get("run_attempt") or ""),
        "RUN_COMMIT": str(run.get("head_sha") or "")[:7],
    }


def vars_watch(payload: dict) -> dict:
    repository = payload.get("repository") or {}
    return {"STARS": str(repository.get("stargazers_count") or "")}


def vars_fork(payload: dict) -> dict:
    fork = payload.get("forkee") or {}
    repository = payload.get("repository") or {}
    return {
        "FORK_NAME": str(fork.get("full_name") or ""),
        "FORK_URL": str(fork.get("html_url") or ""),
        "FORK_COUNT": str(repository.get("forks_count") or ""),
    }


def vars_release_announcement(payload: dict) -> dict:
    """The manual announcement: its data comes from the json the workflow
    collected with gh, not from an event payload."""
    releases = load_releases(required=False)
    if not releases:
        return dict(ANNOUNCEMENT_FIELDS)

    base = releases[0]
    metadata = base["metadata"]

    loaders, versions = [], []
    for release in releases:
        for loader in release["loaders"]:
            if loader not in loaders:
                loaders.append(loader)
        if release["mc"] and release["mc"] not in versions:
            versions.append(release["mc"])
    versions.sort(key=version_key)

    links = changelog_links(releases)
    values = {
        "MOD_ID": str(metadata.get("mod_id") or ""),
        "MOD_NAME": str(metadata.get("mod_name") or ""),
        "MOD_VERSION": base["version"],
        "FULL_VERSION": str(metadata.get("full_version") or base["tag"]),
        "JAVA_VERSION": str(metadata.get("java_version") or ""),
        "RELEASE_CHANNEL": str(metadata.get("release_channel") or ""),
        "MC_VERSION": base["mc"],
        "MC_VERSIONS": ", ".join(versions),
        "PLATFORMS": ("[" + "/".join(loader.upper() for loader in loaders) + "]") if loaders else "",
        "LOADERS": ", ".join(loader.capitalize() for loader in loaders),
        "TAG": base["tag"],
        "TAGS": ", ".join(release["tag"] for release in releases),
        "RELEASE_NAME": base["name"],
        "RELEASE_URL": base["url"],
        "PRERELEASE": "true" if base["prerelease"] else "",
        "PUBLISHED": base["published_at"][:10],
        "CHANGELOG": "\n".join(combine_changelogs(releases)).strip(),
        "CHANGELOG_LINKS": links,
        "RELEASES": [release_item(release) for release in releases],
    }

    # Without build data most of these are empty. Handing the empty strings on
    # would overwrite the fallbacks base_values already found, so they are
    # dropped instead - an unknown placeholder renders as "" either way.
    return {key: value for key, value in values.items() if value != ""}


# Every key the announcement provides, so "notify.py vars" can list them
# without any release data at hand.
ANNOUNCEMENT_FIELDS = {key: [] if key == "RELEASES" else ""
                       for key in ("MOD_ID", "MOD_NAME", "MOD_VERSION", "FULL_VERSION",
                                   "JAVA_VERSION", "RELEASE_CHANNEL", "MC_VERSION",
                                   "MC_VERSIONS", "PLATFORMS", "LOADERS", "TAG", "TAGS",
                                   "RELEASE_NAME", "RELEASE_URL", "PRERELEASE", "PUBLISHED",
                                   "CHANGELOG", "CHANGELOG_LINKS", "RELEASES")}

# GitHub event name (or our own pseudo event) -> placeholder builder.
EVENT_VALUES = {
    "push": vars_push,
    "pull_request": vars_pull_request,
    "pull_request_review": vars_pull_request_review,
    "issues": vars_issues,
    "discussion": vars_discussion,
    "discussion_comment": vars_discussion,
    "release": vars_release,
    "workflow_run": vars_workflow_run,
    "watch": vars_watch,
    "fork": vars_fork,
    "release_announcement": vars_release_announcement,
}

# pull_request_target carries exactly the same payload, so rules only ever
# have to name "pull_request".
EVENT_ALIASES = {"pull_request_target": "pull_request"}

DESCRIPTIONS = {
    "ACTION": "the action of the event, for example 'opened'",
    "ACTION_*": "'true' for the current action, e.g. {ACTION_CLOSED} - for {{#if}}",
    "ACTOR": "who triggered the event",
    "BRANCH": "branch of a push, without refs/heads/",
    "CHANGELOG": "the release notes, several releases merged line by line",
    "CHANGELOG_LINKS": "markdown links to the full release notes on GitHub",
    "COLOR_*": "a colour from config.json -> colors",
    "COMMITS": "the commits of a push",
    "COMPARE_URL": "GitHub diff of a push",
    "EVENT": "name of the GitHub event",
    "ISSUE_BODY": "issue text, html comments removed",
    "LABEL": "the label that was added or removed",
    "MC_VERSIONS": "every Minecraft version of the announced releases",
    "MOD_NAME": "the MOD_NAME repository variable, or the repository name",
    "PING": "the rule's own 'ping' value, empty when the run disabled pinging",
    "PLATFORMS": "the mod loaders as [FORGE/FABRIC]",
    "PR_VERB": "opened, reopened, merged, closed, ready for review …",
    "RELEASES": "one entry per announced release",
    "REVIEW_VERB": "approved, requested changes on, reviewed …",
    "ROLE": "mention of the DISCORD_PING_ROLE_ID variable",
    "RUN_CONCLUSION": "success, failure, cancelled …",
    "WORKFLOW_RUN_URL": "the Actions run that is posting this message",
}


def collect_values(event: str, payload: dict, settings: dict) -> dict:
    values = base_values(payload, settings)
    builder = EVENT_VALUES.get(event)
    if builder:
        values.update(builder(payload))
    values["EVENT"] = event

    # {ACTION_CLOSED}, {ACTION_LABELED}, … so a template can branch on the
    # action without needing a comparison operator.
    action = re.sub(r"[^A-Z0-9]+", "_", str(payload.get("action") or "").upper())
    if action:
        values[f"ACTION_{action}"] = "true"

    return values


def finish_values(values: dict, limits: dict) -> dict:
    """Adds the counters of every list placeholder and applies the rule's
    limits. {X_TOTAL} is what the event delivered, {X_COUNT} what the message
    shows and {X_HIDDEN} the difference."""
    values = dict(values)

    for name, value in list(values.items()):
        if isinstance(value, list):
            values[f"{name}_TOTAL"] = str(len(value))
            values[f"{name}_PLURAL"] = "" if len(value) == 1 else "s"
            values[f"{name}_HIDDEN"] = ""

    for name, limit in (limits or {}).items():
        value = values.get(name)
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            warn(f"limits.{name} is not a number, ignoring it.")
            continue
        if isinstance(value, list):
            hidden = max(0, len(value) - limit)
            values[name] = value[:max(0, limit)]
            values[f"{name}_HIDDEN"] = str(hidden) if hidden else ""
        elif isinstance(value, str) and limit > 0:
            values[name] = fit_text(value, limit)

    for name, value in list(values.items()):
        if isinstance(value, list):
            values[f"{name}_COUNT"] = str(len(value))

    return values


# ── matching ─────────────────────────────────────────────────────────────────

def facts_of(event: str, payload: dict, values: dict) -> dict:
    """What the filters in "on" are matched against."""
    sender = payload.get("sender") or {}
    login = str(sender.get("login") or "")

    labels = []
    for key in ("ISSUE_LABELS", "PR_LABELS"):
        labels += [item.get("NAME", "") for item in (values.get(key) or [])]

    return {
        "event": event,
        "action": str(payload.get("action") or ""),
        "bot": login.endswith("[bot]") or str(sender.get("type") or "") == "Bot",
        "branch": (values.get("BRANCH") or values.get("RUN_BRANCH")
                   or values.get("PR_BASE") or ""),
        "labels": [name for name in labels if name],
        "workflow": values.get("RUN_NAME") or "",
        "conclusion": values.get("RUN_CONCLUSION") or "",
        "draft": bool(values.get("PR_DRAFT")),
        "merged": bool(values.get("PR_MERGED")),
    }


def as_list(value) -> list:
    if value is None:
        return []
    return [str(item) for item in value] if isinstance(value, list) else [str(value)]


def matches_any(patterns: list, value: str) -> bool:
    return any(fnmatch.fnmatch(value.lower(), pattern.lower()) for pattern in patterns)


def rule_matches(rule: dict, facts: dict, values: dict):
    """Returns (True, "") or (False, why not) - the reason ends up in the log,
    so a message that does not appear can always be traced."""
    criteria = rule.get("on") or {}

    events = as_list(criteria.get("event"))
    if events and facts["event"] not in events:
        return False, f"event is '{facts['event']}', the rule wants {'/'.join(events)}"

    actions = as_list(criteria.get("action"))
    if actions and "*" not in actions and facts["action"] not in actions:
        return False, f"action is '{facts['action'] or 'none'}', the rule wants {'/'.join(actions)}"

    branches = as_list(criteria.get("branches"))
    if branches and not matches_any(branches, facts["branch"]):
        return False, f"branch '{facts['branch'] or 'none'}' is not in {branches}"

    labels = as_list(criteria.get("labels"))
    if labels and not any(matches_any(labels, name) for name in facts["labels"]):
        return False, f"none of the labels {facts['labels'] or '[]'} is in {labels}"

    workflows = as_list(criteria.get("workflows"))
    if workflows and not matches_any(workflows, facts["workflow"]):
        return False, f"workflow '{facts['workflow'] or 'none'}' is not in {workflows}"

    conclusions = as_list(criteria.get("conclusions"))
    if conclusions and facts["conclusion"] not in conclusions:
        return False, f"conclusion is '{facts['conclusion'] or 'none'}', not {'/'.join(conclusions)}"

    if criteria.get("skip_bots", True) and facts["bot"]:
        return False, "the event came from a bot"

    if criteria.get("skip_drafts", False) and facts["draft"]:
        return False, "the pull request is still a draft"

    if "merged" in criteria and bool(criteria["merged"]) != facts["merged"]:
        return False, f"merged is {facts['merged']}"

    for name in as_list(criteria.get("require")):
        if not truthy(values.get(name)):
            return False, f"{{{name}}} is empty"

    return True, ""


# ── release data ─────────────────────────────────────────────────────────────
#
# Only used by the manual announcement. Its input is the json that
# discord-release.yml collects with gh, not a GitHub event.

# Leading whitespace plus an optional list marker, so a version prefix can go
# behind the bullet instead of in front of it.
BULLET = re.compile(r"^(\s*(?:[-*+]\s+|\d+[.)]\s+)?)(.*)$", re.S)


def version_key(text):
    """Sorts 1.9 before 1.20, which an alphabetical sort would not."""
    parts = [part for part in re.split(r"(\d+)", str(text)) if part != ""]
    return [(1, int(part), "") if part.isdigit() else (0, 0, part) for part in parts]


def load_releases(required: bool = True) -> list:
    """The release data the workflow collected: newest release first."""
    data = read_json(env("MBT_RELEASES", "releases.json"))
    if isinstance(data, dict):
        data = [data]
    if not data:
        if required:
            fail("No release data found. MBT_RELEASES has to point at the json "
                 "that discord-release.yml writes.")
        return []

    releases = []
    for entry in data:
        metadata = entry.get("metadata") or {}
        tag = str(entry.get("tag") or entry.get("tagName") or "")

        # Without build data the Minecraft version still sits in the tag the
        # build tools generated, for example v3.1.0+mc1.20.1.
        mc = str(metadata.get("minecraft_version") or "")
        if not mc:
            found = re.search(r"[+-]mc([0-9]+(?:\.[0-9]+)*)", tag)
            mc = found.group(1) if found else ""

        # Same idea for the mod version: v3.1.0+mc1.20.1 -> 3.1.0. Without this
        # an expired build artifact would leave the announcement title empty.
        version = str(metadata.get("mod_version") or "")
        if not version:
            version = re.sub(r"^[vV]", "", tag.split("+", 1)[0])

        releases.append({
            "version": version,
            "tag": tag,
            "name": str(entry.get("name") or ""),
            "url": str(entry.get("url") or entry.get("html_url") or ""),
            "body": str(entry.get("body") or ""),
            "published_at": str(entry.get("published_at") or entry.get("publishedAt") or ""),
            "prerelease": bool(entry.get("prerelease") or entry.get("isPrerelease")),
            "metadata": metadata,
            "loaders": [str(item) for item in (metadata.get("loaders") or [])],
            "mc": mc,
            "label": mc or tag,
        })

    releases.sort(key=lambda item: item["published_at"], reverse=True)

    blind = [release["tag"] for release in releases if not release["metadata"]]
    if blind:
        warn("No build data for " + ", ".join(blind) + " - mod name, version and "
             "loaders are guessed from the tag. The 'internal-<tag>' artifact of "
             "the release build has expired or was never uploaded.")

    return releases


def clean_changelog(body: str) -> list:
    text = re.sub(r"<!--.*?-->", "", body or "", flags=re.S)
    return text.replace("\r\n", "\n").strip("\n").split("\n")


def prefix_line(line: str, labels: list) -> str:
    head, rest = BULLET.match(line).groups()
    if not rest.strip():
        return line
    return f"{head}[{', '.join(labels)}] {rest}"


def combine_changelogs(releases: list) -> list:
    """Merges the changelogs of several releases line by line. A line every
    release has stays untouched, every other line is tagged with the Minecraft
    versions that have it. The newest release provides the order."""
    sources = [(release["label"], clean_changelog(release["body"])) for release in releases]
    if not sources:
        return []
    if len(sources) == 1:
        return sources[0][1]

    presence = {}
    for label, lines in sources:
        for line in lines:
            key = tidy(line)
            if not key:
                continue
            labels = presence.setdefault(key, [])
            if label not in labels:
                labels.append(label)

    total = len(sources)
    merged, seen = [], set()

    for line in sources[0][1]:
        key = tidy(line)
        if not key:
            merged.append(line)
            continue
        seen.add(key)
        labels = presence[key]
        merged.append(line if len(labels) == total
                      else prefix_line(line, sorted(labels, key=version_key)))

    for _, lines in sources[1:]:
        for line in lines:
            key = tidy(line)
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append(prefix_line(line, sorted(presence[key], key=version_key)))

    while merged and not merged[-1].strip():
        merged.pop()
    return merged


def changelog_links(releases: list) -> str:
    if len(releases) == 1:
        url = releases[0]["url"]
        return f"**[Full changelog on GitHub]({url})**" if url else ""
    parts = [f"[{release['label']}]({release['url']})" for release in releases if release["url"]]
    return ("**Full changelog:** " + " · ".join(parts)) if parts else ""


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


# ── notification files ───────────────────────────────────────────────────────

def repository_root() -> str:
    return os.path.dirname(os.path.dirname(HERE))


def all_groups(settings: dict) -> list:
    root = os.path.join(HERE, "notifications")
    found = sorted(name for name in os.listdir(root)
                   if os.path.isdir(os.path.join(root, name))) if os.path.isdir(root) else []
    known = [name for name in (settings.get("groups") or {}) if name in found]
    return known + [name for name in found if name not in known]


def load_rules(group: str) -> list:
    directory = os.path.join(HERE, "notifications", group)
    if not os.path.isdir(directory):
        fail(f"There is no notification group '{group}' - {directory} does not exist.")

    rules = []
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".json"):
            continue
        rule = read_json(os.path.join(directory, name))
        if not isinstance(rule, dict):
            warn(f"{group}/{name} does not contain a json object, skipping it.")
            continue
        rule["_name"] = name[: -len(".json")]
        rule["_path"] = f"notifications/{group}/{name}"
        rule["_group"] = group
        rules.append(rule)

    # "message": "other-notification" reuses that notification's message, so a
    # structure only has to be maintained once.
    known = {rule["_name"]: rule for rule in rules}
    for rule in rules:
        source = rule.get("message")
        if not isinstance(source, str):
            continue
        parent = known.get(source)
        if not parent or not isinstance(parent.get("message"), dict):
            fail(f"{rule['_path']}: message points at '{source}', which is not a "
                 f"notification in this group with a message of its own.")
        rule["message"] = parent["message"]

    return rules


def find_rule(reference: str):
    """Resolves a path or 'group/name' into the group and the rule inside it,
    so that a reused message can still be resolved."""
    path = ""
    for candidate in (reference,
                      os.path.join(HERE, "notifications", reference),
                      os.path.join(HERE, "notifications", f"{reference}.json")):
        if os.path.isfile(candidate):
            path = os.path.abspath(candidate)
            break
    if not path:
        fail(f"Cannot find the notification '{reference}'. Pass a path or 'group/name'.")

    group = os.path.basename(os.path.dirname(path))
    name = os.path.basename(path)[: -len(".json")]
    for rule in load_rules(group):
        if rule["_name"] == name:
            return rule
    fail(f"{path} is not a notification file.")


def validate_rule(rule: dict) -> list:
    problems = []

    for key in rule:
        if not key.startswith("_") and key not in RULE_KEYS:
            problems.append(f"unknown key '{key}' (known: {', '.join(sorted(RULE_KEYS))})")
    for key in (rule.get("on") or {}):
        if key not in FILTER_KEYS:
            problems.append(f"unknown filter 'on.{key}' (known: {', '.join(sorted(FILTER_KEYS))})")
    for key in (rule.get("thread") or {}):
        if key not in THREAD_KEYS:
            problems.append(f"unknown key 'thread.{key}' (known: {', '.join(sorted(THREAD_KEYS))})")

    if not as_list((rule.get("on") or {}).get("event")):
        problems.append("on.event is missing")
    if not rule.get("target"):
        problems.append("target is missing")

    thread = rule.get("thread")
    if thread is not None and str(thread.get("action") or "reply") not in ("create", "reply", "edit"):
        problems.append("thread.action must be create, reply or edit")

    def walk(value, where):
        if isinstance(value, str):
            try:
                parse_template(value)
            except TemplateError as error:
                problems.append(f"{where}: {error}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{where}[{index}]")
        elif isinstance(value, dict):
            for key, item in value.items():
                walk(item, f"{where}.{key}")

    walk(rule.get("message") or {}, "message")
    walk(rule.get("ping") or "", "ping")
    walk((rule.get("thread") or {}).get("name") or "", "thread.name")
    return problems


def target_of(rule: dict, settings: dict) -> dict:
    name = str(rule.get("target") or "")
    targets = settings.get("targets") or {}
    if name not in targets:
        fail(f"{rule['_path']}: target '{name}' is not defined in config.json "
             f"(known: {', '.join(sorted(targets))}).")
    return dict(targets[name], _name=name)


def secrets_of(rule: dict, settings: dict) -> list:
    """Which environment variables this notification needs."""
    target = (settings.get("targets") or {}).get(str(rule.get("target") or "")) or {}
    needed = [str(target.get("webhook") or "")]

    thread = rule.get("thread") or {}
    if thread:
        needed.append("GITHUB_TOKEN")
        if thread.get("archived") is not None or thread.get("locked") is not None:
            needed.append("DISCORD_BOT_TOKEN")
        if thread.get("action") == "create":
            needed.append("DISCORD_GUILD_ID")
            if thread.get("tags_from_labels", True) and target.get("forum_channel"):
                needed += [str(target["forum_channel"]), "DISCORD_BOT_TOKEN"]
    if rule.get("crosspost"):
        needed.append("DISCORD_BOT_TOKEN")

    return list(dict.fromkeys(item for item in needed if item))


def workflow_triggers(relative: str):
    """The top level keys of a workflow's on: block. A deliberately tiny yaml
    reader - just enough to tell whether a trigger is present."""
    try:
        with open(os.path.join(repository_root(), relative), encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return None

    triggers, inside = [], False
    for line in lines:
        if not inside:
            if re.match(r"""^(on|'on'|"on"):""", line):
                inside = True
                inline = line.split(":", 1)[1].strip()
                if inline.startswith("["):
                    triggers += [item.strip(" []'\"") for item in inline.strip("[]").split(",")]
                    break
                if inline and not inline.startswith("#"):
                    triggers.append(inline)
                    break
            continue
        if line.strip() and not line.startswith((" ", "\t", "#")):
            break
        found = re.match(r"^\s{1,2}([A-Za-z_]+):", line)
        if found:
            triggers.append(found.group(1))
    return [trigger for trigger in triggers if trigger]


# ── forum threads ────────────────────────────────────────────────────────────

def state_comment(repository: str, number: str):
    """The hidden mapping comment of an issue: (comment, state). Both are None
    when the issue has no forum post yet."""
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
    return f"https://discord.com/channels/{guild}/{thread_id}" if guild and thread_id else ""


def save_state(repository: str, number: str, comment, state: dict, thread_url: str) -> None:
    body = (f"Discord thread for this issue: {thread_url or 'see the issue forum on Discord'}\n"
            f"<!--{STATE_MARKER}:{json.dumps(state, separators=(',', ':'))}-->")
    if comment:
        github_request("PATCH", f"/repos/{repository}/issues/comments/{comment['id']}",
                       payload={"body": body})
    else:
        github_request("POST", f"/repos/{repository}/issues/{number}/comments",
                       payload={"body": body})


def forum_tags(settings: dict, target: dict, labels: list) -> list:
    """Maps issue labels onto forum tag ids, either through forum.tag_map or -
    with a bot token and the forum channel id - by matching names."""
    wanted = [str(name).lower() for name in labels if name]
    forum = settings.get("forum") or {}
    mapping = {str(key).lower(): str(value) for key, value in (forum.get("tag_map") or {}).items()}

    channel = env(str(target.get("forum_channel") or ""))
    if forum.get("labels_as_tags", True) and channel and env("DISCORD_BOT_TOKEN"):
        data = bot_request("GET", f"/channels/{channel}") or {}
        for tag in data.get("available_tags") or []:
            mapping.setdefault(str(tag.get("name") or "").lower(), str(tag.get("id")))

    tags = [mapping[name] for name in wanted if name in mapping]
    if not tags:
        tags = [str(tag) for tag in (forum.get("default_tags") or [])]
    return list(dict.fromkeys(tags))[:5]


def set_thread_state(thread_id: str, *, archived=None, locked=None) -> None:
    if archived is None and locked is None:
        return
    if not env("DISCORD_BOT_TOKEN"):
        log("    DISCORD_BOT_TOKEN is not set, the thread was left as it is.")
        return

    # Locking has to happen before archiving: an archived thread can no longer
    # be changed through the api.
    if locked is not None:
        bot_request("PATCH", f"/channels/{thread_id}", payload={"locked": bool(locked)})
    if archived is not None:
        bot_request("PATCH", f"/channels/{thread_id}", payload={"archived": bool(archived)})


def create_forum_post(webhook: str, message: dict, links: str) -> dict:
    try:
        return webhook_send_tolerant(webhook, message, links=links)
    except ApiError as error:
        if message.get("applied_tags") and "applied_tags" in error.body:
            warn("The forum refused the tags, creating the post without them.")
            retry = {key: value for key, value in message.items() if key != "applied_tags"}
            return webhook_send_tolerant(webhook, retry, links=links)
        raise


# ── delivery ─────────────────────────────────────────────────────────────────

def publish(message: dict) -> None:
    if not env("DISCORD_BOT_TOKEN"):
        warn("crosspost is on but DISCORD_BOT_TOKEN is not set, the message was not published.")
        return
    if message.get("id") and message.get("channel_id"):
        path = f"/channels/{message['channel_id']}/messages/{message['id']}/crosspost"
        if bot_request("POST", path) is not None:
            log("    published to the following servers")


def deliver_to_thread(rule: dict, message: dict, links: str, scope: dict,
                      target: dict, settings: dict, webhook: str) -> bool:
    name = rule["_name"]
    thread = rule.get("thread") or {}
    action = str(thread.get("action") or "reply")
    repository = scope.get("REPO") or ""
    number = scope.get("ISSUE_NUMBER") or ""

    if not number:
        log(f"  {name}: skipped - this event carries no issue number")
        return False

    comment, state = state_comment(repository, number)
    state = state or {}
    thread_id = str(state.get("thread") or "")

    if action == "create":
        if thread_id:
            log(f"  {name}: skipped - issue #{number} already has a forum post")
            return False

        title = clip(tidy(render(str(thread.get("name") or "#{ISSUE_NUMBER} {ISSUE_TITLE}"), scope)),
                     LIMITS["thread_name"]) or f"#{number}"
        message = dict(message, thread_name=title)

        if thread.get("tags_from_labels", True):
            tags = forum_tags(settings, target,
                              [item.get("NAME") for item in (scope.get("ISSUE_LABELS") or [])])
            if tags:
                message["applied_tags"] = tags

        created = create_forum_post(webhook, message, links)
        thread_id = str(created.get("channel_id") or created.get("id") or "")
        if not thread_id:
            fail("Discord did not return a thread id. Is the webhook inside a forum channel?")

        url = thread_url_of(thread_id)
        save_state(repository, number, comment,
                   {"thread": thread_id, "message": str(created.get("id") or thread_id)}, url)
        log(f"  {name}: created the forum post of issue #{number}" + (f" -> {url}" if url else ""))
        return True

    if not thread_id:
        log(f"  {name}: skipped - issue #{number} has no forum post")
        return False

    # An archived thread accepts neither messages nor edits, so reopen first.
    reopening = thread.get("archived") is False
    if reopening:
        set_thread_state(thread_id, archived=False, locked=thread.get("locked"))

    if action == "edit":
        editable = {key: value for key, value in message.items()
                    if key in ("content", "embeds", "components", "allowed_mentions")}
        try:
            webhook_edit(webhook, str(state.get("message") or thread_id), editable,
                         thread_id=thread_id)
        except ApiError as error:
            warn(f"{name}: could not update the forum post of issue #{number}: {error}")
            return False
        log(f"  {name}: updated the forum post of issue #{number}")
        return True

    webhook_send_tolerant(webhook, message, thread_id=thread_id, links=links, wait=False)
    log(f"  {name}: posted into the forum post of issue #{number}")

    if not reopening and (thread.get("archived") or thread.get("locked") is not None):
        set_thread_state(thread_id, archived=thread.get("archived"), locked=thread.get("locked"))
    return True


def report(rule: dict, message: dict, scope: dict, dry_run: bool) -> None:
    thread = rule.get("thread") or {}
    lines = [f"### {rule['_group']} / {rule['_name']}"
             + ("  _(dry run, nothing was posted)_" if dry_run else ""), ""]
    lines.append(f"Target `{rule.get('target')}`"
                 + (f", thread action `{thread.get('action') or 'reply'}`" if thread else ""))
    lines += ["", "```json", json.dumps(message, indent=2, ensure_ascii=False), "```", ""]

    if scope.get("RELEASES"):
        for text in release_warnings(load_releases(required=False)):
            lines += ["> [!WARNING]", f"> {text}", ""]

    write_summary("\n".join(lines))
    if dry_run:
        log(json.dumps(message, indent=2, ensure_ascii=False))


def run_rule(rule: dict, context: dict) -> bool:
    settings = context["settings"]
    name = rule["_name"]

    values = finish_values(context["values"], rule.get("limits"))
    allowed, reason = rule_matches(rule, context["facts"], values)
    if not allowed:
        log(f"  {name}: skipped - {reason}")
        return False

    target = target_of(rule, settings)
    message, links, scope = build_message(rule, values, settings)
    thread = rule.get("thread") or {}

    if not message.get("content") and not message.get("embeds"):
        log(f"  {name}: skipped - the message rendered empty")
        return False

    if context["dry_run"]:
        log(f"  {name}: would post to '{target['_name']}'")
        report(rule, message, scope, True)
        return True

    webhook = env(str(target.get("webhook") or ""))
    if not webhook:
        log(f"  {name}: skipped - {target.get('webhook')} is not set")
        return False

    if thread:
        return deliver_to_thread(rule, message, links, scope, target, settings, webhook)

    result = webhook_send_tolerant(webhook, message, links=links, wait=bool(rule.get("crosspost")))
    log(f"  {name}: posted to '{target['_name']}'")
    if rule.get("crosspost"):
        publish(result)
    if context["summary"]:
        report(rule, message, scope, False)
    return True


# ── commands ─────────────────────────────────────────────────────────────────

def print_table(rows: list) -> None:
    widths = [max(len(str(row[index])) for row in rows) for index in range(len(rows[0]))]
    for index, row in enumerate(rows):
        log("  " + "  ".join(str(cell).ljust(widths[column])
                             for column, cell in enumerate(row)).rstrip())
        if index == 0:
            log("  " + "  ".join("-" * width for width in widths))


def command_run(args) -> None:
    settings = load_config()
    event = EVENT_ALIASES.get(args.event or env("GITHUB_EVENT_NAME"),
                              args.event or env("GITHUB_EVENT_NAME"))
    if not event:
        fail("No event name. Set GITHUB_EVENT_NAME or pass --event.")

    payload = read_json(args.payload or env("GITHUB_EVENT_PATH")) or {}
    values = collect_values(event, payload, settings)
    facts = facts_of(event, payload, values)
    dry_run = args.dry_run or env("MBT_DRY_RUN").lower() == "true"

    rules = load_rules(args.group)
    log(f"{args.group}: {len(rules)} notification(s), event '{event}'"
        + (f", action '{facts['action']}'" if facts["action"] else "")
        + (", dry run" if dry_run else ""))

    context = {"settings": settings, "values": values, "facts": facts,
               "dry_run": dry_run, "summary": args.summary}
    sent = 0
    for rule in rules:
        if not rule.get("enabled", True):
            log(f"  {rule['_name']}: skipped - enabled is false")
            continue
        problems = validate_rule(rule)
        if problems:
            fail(f"{rule['_path']}: " + "; ".join(problems))
        if run_rule(rule, context):
            sent += 1

    log(f"{sent} message(s) {'would be sent' if dry_run else 'sent'}.")


def command_list(args) -> None:
    settings = load_config()
    for group in ([args.group] if args.group else all_groups(settings)):
        workflow = ((settings.get("groups") or {}).get(group) or {}).get("workflow", "")
        log(f"\n{group}" + (f"   triggered by {workflow}" if workflow else ""))

        rows = [("", "notification", "event", "action", "target", "needs")]
        for rule in load_rules(group):
            criteria = rule.get("on") or {}
            rows.append((
                "on" if rule.get("enabled", True) else "off",
                rule["_name"],
                ", ".join(as_list(criteria.get("event"))) or "-",
                ", ".join(as_list(criteria.get("action"))) or "any",
                str(rule.get("target") or "-"),
                ", ".join(secrets_of(rule, settings)) or "-",
            ))
        if len(rows) == 1:
            log("  no notifications in this group")
            continue
        print_table(rows)


def command_vars(args) -> None:
    settings = load_config()
    payload = read_json(args.payload or env("GITHUB_EVENT_PATH")) or {}

    events = [args.event] if args.event else list(EVENT_VALUES)
    for name in events:
        event = EVENT_ALIASES.get(name, name)
        if event not in EVENT_VALUES:
            fail(f"Unknown event '{name}'. Known events: {', '.join(EVENT_VALUES)}")

        values = finish_values(collect_values(event, payload, settings), {})
        log(f"\n{event}")
        rows = [("placeholder", "value / entry fields", "what it is")]
        for key in sorted(values):
            value = values[key]
            if isinstance(value, list):
                fields = ", ".join(ITEM_BUILDERS.get(key, dict)({}))
                shown = f"{len(value)} entry/entries " + (f"{{{fields}}}" if fields else "")
            else:
                shown = clip(tidy(value), 44) or "-"
            rows.append((f"{{{key}}}", shown, DESCRIPTIONS.get(key, "")))
        print_table(rows)

    log("\nA list placeholder also provides {NAME_TOTAL}, {NAME_COUNT}, {NAME_HIDDEN} and")
    log("{NAME_PLURAL}; inside {{#each NAME}} there are {INDEX}, {FIRST} and {LAST}.")
    log("The current action is additionally available as {ACTION_<ACTION>}, for example")
    log("{ACTION_CLOSED}, so {{#if}} can branch on it. Every repository variable can be")
    log("used by its own name in upper case.")


def command_render(args) -> None:
    settings = load_config()
    rule = find_rule(args.file)

    problems = validate_rule(rule)
    if problems:
        fail(f"{rule['_path']}: " + "; ".join(problems))

    events = as_list((rule.get("on") or {}).get("event"))
    event = args.event or (events[0] if events else "")
    event = EVENT_ALIASES.get(event, event)

    payload = read_json(args.payload or env("GITHUB_EVENT_PATH")) or {}
    values = finish_values(collect_values(event, payload, settings), rule.get("limits"))
    facts = facts_of(event, payload, values)

    allowed, reason = rule_matches(rule, facts, values)
    message, _, scope = build_message(rule, values, settings)

    log(f"{rule['_group']}/{rule['_name']}  event '{event}'  target '{rule.get('target')}'")
    log(f"matches this event: {'yes' if allowed else 'no - ' + reason}")
    if not payload and event in EVENT_VALUES and event != "release_announcement":
        log("no event payload given, so every placeholder is empty - pass --payload <file>")
    log("")
    log(json.dumps(message, indent=2, ensure_ascii=False))


def command_check(args) -> None:
    settings = load_config()
    targets = settings.get("targets") or {}
    problems, hints = [], []

    for group in all_groups(settings):
        workflow = ((settings.get("groups") or {}).get(group) or {}).get("workflow", "")
        triggers = workflow_triggers(workflow) if workflow else None
        if workflow and triggers is None:
            hints.append(f"{group}: cannot read {workflow}, so its triggers were not checked.")

        for rule in load_rules(group):
            where = f"{group}/{rule['_name']}"
            problems += [f"{where}: {problem}" for problem in validate_rule(rule)]

            if str(rule.get("target") or "") not in targets:
                problems.append(f"{where}: target '{rule.get('target')}' is not defined in config.json")
            if not rule.get("enabled", True) or triggers is None:
                continue

            for event in as_list((rule.get("on") or {}).get("event")):
                if event in EVENT_ALIASES or event == "release_announcement":
                    continue
                if event in triggers:
                    continue
                if event == "pull_request" and "pull_request_target" in triggers:
                    continue
                problems.append(f"{where}: {workflow} has no '{event}:' trigger - "
                                f"add it under 'on:' or the notification will never run")

    for group in all_groups(settings):
        for rule in load_rules(group):
            if not rule.get("enabled", True):
                continue
            missing = [name for name in secrets_of(rule, settings) if not env(name)]
            if missing:
                hints.append(f"{group}/{rule['_name']}: {', '.join(missing)} not set in this shell")

    for hint in hints:
        log(f"note     {hint}")
    for problem in problems:
        log(f"problem  {problem}")

    if problems:
        fail(f"{len(problems)} problem(s) found.")
    log("Everything checks out.")


def main() -> None:
    parser = argparse.ArgumentParser(prog="notify.py",
                                     description="Discord notifications for Mod Build Tools")
    commands = parser.add_subparsers(dest="command", required=True)

    runner = commands.add_parser("run", help="post every matching notification of a group")
    runner.add_argument("group", help="a directory under notifications/, for example dev")
    runner.add_argument("--event", default="", help="override the GitHub event name")
    runner.add_argument("--payload", default="", help="read the event payload from a file")
    runner.add_argument("--dry-run", action="store_true", help="render only, post nothing")
    runner.add_argument("--summary", action="store_true",
                        help="also write every message into the run summary")
    runner.set_defaults(handler=command_run)

    lister = commands.add_parser("list", help="what is configured and what it needs")
    lister.add_argument("group", nargs="?", default="")
    lister.set_defaults(handler=command_list)

    variables = commands.add_parser("vars", help="the placeholders available for an event")
    variables.add_argument("event", nargs="?", default="")
    variables.add_argument("--payload", default="", help="show the values of a real payload")
    variables.set_defaults(handler=command_vars)

    renderer = commands.add_parser("render", help="render one notification without posting it")
    renderer.add_argument("file", help="a path, or 'group/name' such as dev/push")
    renderer.add_argument("--event", default="")
    renderer.add_argument("--payload", default="")
    renderer.set_defaults(handler=command_render)

    checker = commands.add_parser("check", help="validate the configuration")
    checker.set_defaults(handler=command_check)

    args = parser.parse_args()
    try:
        args.handler(args)
    except TemplateError as error:
        fail(str(error))
    except ApiError as error:
        fail(str(error))


if __name__ == "__main__":
    main()
