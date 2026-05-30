# claude-cowork-export

> Export, archive, migrate, and resume **Claude Desktop / Cowork** chats.

[English](README.md) · [中文](README.zh-CN.md)

A single-file Python tool that reads every Cowork chat ("task") on disk and
turns it into:

- a **readable bundle** — HTML, Markdown, JSON, CSV, plus the user's
  uploads, the assistant's generated outputs, the audit log, and every
  file the assistant `Write`d or `Edit`ed,
- a **portable archive** — drop the bundle on another machine and the task
  shows up in Cowork's sidebar to keep working on,
- a **continuation seed** — a single Markdown prompt you can paste into a
  brand-new Cowork chat (even under a different account) to keep working
  from where you left off.

Zero third-party dependencies (pure Python 3.9+ stdlib). One file. One CLI.

Sibling project: [claude-code-export](https://github.com/PeriChu/claude-code-export)
targets the Claude Code CLI's `~/.claude/projects/` sessions. This one
targets Claude Desktop's Cowork chats.

---

## Table of contents

- [What "Cowork" means here](#what-cowork-means-here)
- [Install](#install)
- [Quickstart](#quickstart)
- [Commands](#commands)
  - [`list`](#list)
  - [`export`](#export)
  - [`import`](#import)
  - [`seed`](#seed)
- [Bundle layout](#bundle-layout)
- [Where Cowork data lives](#where-cowork-data-lives)
- [Workflows](#workflows)
- [Cross-platform notes](#cross-platform-notes)
- [Security: `--include-auth` caveats](#security)
- [Troubleshooting](#troubleshooting)
- [Branches](#branches)
- [License](#license)

---

## What "Cowork" means here

In the Claude Desktop app, each chat is called a **task** and lives in a
sandboxed working directory under
`~/Library/Application Support/Claude/local-agent-mode-sessions/...` (macOS)
or `%APPDATA%\Claude\local-agent-mode-sessions\...` (Windows). Each task
carries:

- the user's uploaded files (`uploads/`),
- the assistant's generated outputs (`outputs/`, also the chat's working
  directory),
- an audit log (`audit.jsonl`),
- task metadata (`local_<id>.json`: title, model, dates, the initial
  brief, user-selected folders, archive flag, etc.),
- a native JSONL transcript on macOS (`<task>/.claude/projects/<encoded-cwd>/<cli-id>.jsonl`);
  on Windows the native transcript may be absent and `audit.jsonl` is
  treated as the transcript instead.

This tool understands all of that, dedupes spurious record duplication on
the Windows audit path, and produces a self-contained bundle for each task.

---

## Install

Requires Python 3.9 or newer. Recommended via [pipx](https://pipx.pypa.io/):

```bash
# macOS / Linux
pipx install "git+https://github.com/PeriChu/claude-cowork-export.git"

# Windows (PowerShell) — same command works, but install from the Windows branch
pipx install "git+https://github.com/PeriChu/claude-cowork-export.git@windows"
```

The tool registers two equivalent entry points:

```bash
claude-cowork-export ...   # primary name
cowork-export ...          # shorter alias
```

Or run as a single script with no install:

```bash
git clone https://github.com/PeriChu/claude-cowork-export.git
cd claude-cowork-export
python3 cowork_export.py --help
```

---

## Quickstart

```bash
# List every Cowork task on disk
claude-cowork-export list

# Export the most recent task into ./exports/
claude-cowork-export export latest --output ./exports

# Export every task
claude-cowork-export export all --output ./exports

# Move a bundle to another machine, then restore it
claude-cowork-export import ./exports/<task-id>

# Or, on a brand-new account: render a paste-able continuation prompt
claude-cowork-export seed ./exports/<task-id>
# → writes ./exports/<task-id>/seed-prompt.md
```

---

## Commands

The CLI has four subcommands. Run `claude-cowork-export <cmd> --help` for the
exact flag list at any time.

### `list`

```
claude-cowork-export list [--source cowork|code|both] [--cowork-root PATH]
```

Lists every Cowork task discovered on disk. On Windows, the tool walks
**both** the public `%APPDATA%` mirror **and** the package's MSIX
LocalCache and merges by task id, because each location can hold a
different subset of the workspace (this surprised me too).

| Flag | Default | What it does |
|---|---|---|
| `--source cowork\|code\|both` | `cowork` | Which session store to read. `code` falls back to the legacy `~/.claude/projects/` Claude Code CLI sessions. |
| `--cowork-root PATH` | _(auto-detect)_ | Override the auto-detected sessions directory. Useful when auto-detection misses tasks or when pointing at an archived backup. |

**Examples:**

```bash
# Everything
claude-cowork-export list

# Legacy Claude Code CLI sessions too (or only)
claude-cowork-export list --source both
claude-cowork-export list --source code

# Force-read a specific Cowork root (e.g. the MSIX LocalCache directly)
claude-cowork-export list --cowork-root \
  "$LOCALAPPDATA/Packages/Claude_*/LocalCache/Roaming/Claude/local-agent-mode-sessions"
```

### `export`

```
claude-cowork-export export <selector> [-o DIR] [--formats LIST]
                                       [--no-files]
                                       [--include-auth] [--yes-i-know-this-is-risky]
                                       [--source cowork|code|both]
                                       [--cowork-root PATH]
```

Exports one or more tasks into a self-contained bundle directory per task.

| Selector | Picks |
|---|---|
| `latest` | The most recently active task. |
| `all` | Every task. |
| `<prefix>` | The unique task whose UUID starts with `<prefix>`. Errors if ambiguous. |

| Flag | Default | What it does |
|---|---|---|
| `-o`, `--output`, `--out` | `./exports` | Where to write the bundles. One subdir per task. |
| `--formats` | `html,md,json,csv` | Comma-separated subset of `html,md,json,csv`. |
| `--no-files` | _(off)_ | Skip copying uploads, outputs, and touched files. Faster, smaller bundle. |
| `--include-auth` | _(off)_ | **HIGH RISK.** Also copy Cowork's auth artefacts (Cookies, Local State, buddy-tokens, etc.) into the bundle. Only restorable on the same OS family — see [Security](#security). |
| `--purge-source` | _(off)_ | **DESTRUCTIVE.** After each task's bundle is written and verified, delete that task's local sandbox + metadata. The external project folders the chat was attached to (`userSelectedFolders`) are never touched, nor are other tasks. Incompatible with `--no-files`. See [Deleting the local copy](#deleting-the-local-copy-after-export). |
| `--yes-i-know-this-is-risky` | _(off)_ | Skip the interactive confirmation prompts for `--include-auth` and `--purge-source`. CI only. |

**Examples:**

```bash
# Every task to ./exports/
claude-cowork-export export all --output ./exports

# Only one task, HTML + JSON only
claude-cowork-export export <task-id> --output ./exports --formats html,json

# Smallest possible bundle — transcript + manifest only
claude-cowork-export export latest --no-files

# Personal backup with credentials included (read the warning carefully)
claude-cowork-export export latest --output ./private-backup --include-auth
```

### `import`

```
claude-cowork-export import <bundle> [--cowork-root PATH]
                                     [--remap SRC=DST]...
                                     [--skip-auth] [--dry-run] [--force]
```

Restores a bundle into the local Cowork sandbox so that the task shows up
in the Cowork sidebar to continue. Handles sandbox-prefix rewriting and
cross-platform separator translation; cross-platform `--include-auth` is
**refused** because Cowork's auth artefacts are encrypted with the source
OS's keystore (macOS Keychain / Windows DPAPI) and won't decrypt on the
other side.

| Flag | Default | What it does |
|---|---|---|
| `<bundle>` | _(required)_ | Path to a bundle directory produced by `export`. |
| `--cowork-root PATH` | _(auto-detect)_ | Override the target sandbox root. |
| `--remap SRC=DST` | _(none, repeatable)_ | Rewrite a `userSelectedFolders` / `userApprovedFileAccessPaths` entry from `SRC` on the source machine to `DST` on the target. Required for any folder that doesn't exist verbatim on the target. |
| `--skip-auth` | _(off)_ | Ignore `bundle/auth/` even if present. Do not touch local auth state. |
| `--dry-run` | _(off)_ | Print the rewrite plan and exit without writing anything. |
| `--force` | _(off)_ | Overwrite an existing task with the same id. |

**Examples:**

```bash
# Same OS, original sandbox — just rebuild from a bundle
claude-cowork-export import ./exports/<task-id>

# Map user folders from the source machine to the target
claude-cowork-export import ./exports/<task-id> \
  --remap "/Users/<your-username>/Documents/Claude/Projects/<project>=D:\<project>" \
  --remap "/Users/<your-username>/code=C:\code"

# See exactly what would happen, then bail
claude-cowork-export import ./exports/<task-id> --dry-run

# Cross-platform: bundle has auth/ but it's not restorable here — skip it
claude-cowork-export import ./exports/<task-id> --skip-auth
```

### `seed`

```
claude-cowork-export seed <bundle> [--mode brief|standard|full] [-o PATH]
```

Renders a self-contained Markdown prompt from a bundle. Paste it as the
first message of a fresh Cowork chat — under any account, on any machine —
to bootstrap the new conversation with the prior task's context. This does
not touch any auth and does not rely on any server-side state: the new
chat is genuinely new, just primed.

| Flag | Default | What it does |
|---|---|---|
| `<bundle>` | _(required)_ | Path to a bundle. |
| `--mode brief\|standard\|full` | `standard` | How much of the prior conversation to include. See modes table below. |
| `-o`, `--output`, `--out` | `<bundle>/seed-prompt.md` | Where to write the prompt. |

**Modes:**

| Mode | Includes | Typical size for a 30-turn task |
|---|---|---|
| `brief` | Task metadata + initial brief + file inventory + last 3 turns verbatim. | ~50 KB |
| `standard` | All of the above + every user prompt + abridged assistant text (500-char truncation) + tool-call summaries; final turn verbatim. | ~70 KB |
| `full` | Everything verbatim, including reasoning blocks and tool outputs. | ~225 KB |

The Cowork seed includes a couple of fields the CC seed doesn't:
- the task's `initialMessage` rendered as a dedicated "Initial brief" block
  (keeps the long-standing goal in view even when the conversation drifted),
- the task's `Model`, `Space`, `userSelectedFolders`, `isArchived` flag,
  and last-seen error — anything Cowork tracked about the task.

**Examples:**

```bash
# Default — best balance of size vs. fidelity
claude-cowork-export seed ./exports/<task-id>

# Only the tail of the conversation
claude-cowork-export seed ./exports/<task-id> --mode brief

# Reproduce every byte
claude-cowork-export seed ./exports/<task-id> --mode full -o ./seed-full.md
```

---

## Bundle layout

Every `export` produces one directory per task:

```
exports/<task-id>/
├── manifest.json        # version + source platform + sandbox prefix + auth info
├── transcript.jsonl     # raw source (native .jsonl, or audit.jsonl on Windows)
├── task.json            # Cowork's original task metadata
├── audit.jsonl          # Cowork audit log (omitted if it IS the transcript)
├── session.html         # rendered reading view (Marked + highlight.js)
├── session.md           # GitHub-flavoured Markdown
├── session.json         # structured per-block dump (for LLMs)
├── session.csv          # flat per-block table (for spreadsheets)
├── README.md            # auto-generated bundle summary
├── uploads/             # files the user attached to the chat
├── outputs/             # files the assistant generated (Cowork's chat working dir)
├── assets/              # files written/edited outside the outputs/ dir
│   └── <relative paths>
├── auth/                # only when --include-auth was used
│   ├── buddy-tokens.json
│   ├── Local State
│   ├── Cookies
│   └── ...
└── seed-prompt.md       # only after a `seed` invocation
```

### manifest.json schema

| Field | Type | Meaning |
|---|---|---|
| `bundle_version` | int | Bundle format version. Currently `1`. |
| `tool` | string | Always `"claude-cowork-export"`. |
| `tool_version` | string | Tool semver at export time. |
| `exported_at` | ISO-8601 string | UTC timestamp. |
| `source_platform` | string | `"darwin"` / `"win32"` / `"linux"`. |
| `source_path_sep` | string | `"/"` or `"\\"`. |
| `source_home` | string | Source machine's `$HOME` (informational). |
| `source_userdata` | string | Cowork's user-data root on the source (parent of `local-agent-mode-sessions`). |
| `source_sandbox_prefix` | string | The `<userdata>/local-agent-mode-sessions/<acct>/<workspace>/` path the task lived in. `import` rewrites this to the target's prefix. |
| `source_task_id` | string | The Cowork task UUID. |
| `source_cli_session_id` | string | The underlying Claude Code session UUID inside the task sandbox. |
| `source_cwd` | string | The task's `outputs/` directory at the time of export. |
| `source_user_folders` | string[] | The `userSelectedFolders` recorded by Cowork (the external project folders the chat was attached to). |
| `auth` | object | `{"included": false}` or full details when `--include-auth`. |

`import` reads `manifest.json` to drive its rewrites; older bundles
without a manifest are rejected with a clear error pointing at how to
re-export.

---

## Where Cowork data lives

Different operating systems put the Cowork sandbox in different places.
The tool auto-detects all of them and merges any duplicates.

| OS | Path |
|---|---|
| macOS | `~/Library/Application Support/Claude/local-agent-mode-sessions/` |
| Linux | `~/.config/Claude/local-agent-mode-sessions/` |
| Windows (native installer) | `%APPDATA%\Claude\local-agent-mode-sessions\` |
| Windows (Microsoft Store / MSIX) | `%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming\Claude\local-agent-mode-sessions\` |

Per task:

```
<sandbox>/<account-uuid>/<workspace-uuid>/
  spaces.json                                 # space (project) registry
  local_<task-id>.json                        # task metadata
  local_<task-id>/                            # task sandbox dir
    .claude/projects/<encoded-cwd>/<cli-id>.jsonl   # native transcript (macOS)
    audit.jsonl                               # used as transcript on Windows
    uploads/                                  # user-attached files
    outputs/                                  # assistant-generated files (= chat cwd)
```

On Windows with an MSIX install, the public `%APPDATA%\Claude\...` view is
only a partial mirror of the MSIX `LocalCache`. Reading both is essential
to see every task. The tool does this automatically; you can override with
`--cowork-root` if needed.

---

## Workflows

### 1. Personal backup

```bash
# Periodically dump every task to your backup drive
claude-cowork-export export all --output /Volumes/Backup/cowork-$(date +%F)
```

Bundles are self-contained, fully readable in a browser, and the
`session.json` / `session.csv` are stable enough to grep, diff, and feed
back to another LLM.

### 2. Same-OS migration (new machine, same person, same account)

```bash
# Old machine
claude-cowork-export export latest --output ./bundle --include-auth

# Transfer ./bundle to the new machine (treat it like an SSH private key —
# see Security below)

# New machine — same OS family
claude-cowork-export import ./bundle
# Open Cowork; the task appears in the sidebar to keep working on.
```

### 3. Cross-OS migration (macOS ↔ Windows)

```bash
# Source: macOS
claude-cowork-export export latest --output ./bundle

# Transfer the bundle. On the target Windows machine:
claude-cowork-export import .\bundle --skip-auth \
  --remap "/Users/<your-username>/Documents/Claude/Projects/<project>=D:\<project>"

# Then sign in to Cowork normally on the new OS — auth doesn't transfer
# across keystores.
```

### 4. Cross-account / cross-machine continuation (seed mode)

When the destination is a different Anthropic account, server-side state
cannot be transferred. Use seed mode to inline the prior context into a
fresh chat:

```bash
# Source machine
claude-cowork-export export latest --output ./bundle
claude-cowork-export seed ./bundle/<task-id>
# → ./bundle/<task-id>/seed-prompt.md

# Transfer the bundle. On the destination:
# 1. Open a brand-new Cowork chat under the new account.
# 2. Paste the contents of seed-prompt.md as your first message.
# 3. Wait for the assistant to confirm context absorption.
# 4. Optionally also run `import` so the new machine has the files restored.
```

### 5. Archive a task before deleting it from Cowork

```bash
claude-cowork-export export <task-id> --output ./archive
# Now you can delete it from Cowork — the bundle is independent.
```

### 6. Export and free up the local store (`--purge-source`)

```bash
# Archive a task AND remove its local sandbox in one step
claude-cowork-export export <task-id> --output ./archive --purge-source
```

See [Deleting the local copy](#deleting-the-local-copy-after-export) below.

---

## Deleting the local copy after export

`--purge-source` lets you offload a task: it is exported to a bundle and then
removed from the local Cowork sandbox, in a single command.

- **What is deleted**: the task's sandbox directory (`local_<task-id>/`,
  holding the transcript, `uploads/`, `outputs/`, and `audit.jsonl`) and its
  `local_<task-id>.json` metadata — across every discovered root (so the
  Windows MSIX `%APPDATA%` + `%LOCALAPPDATA%` duplicates are both removed).
- **What is never touched**: the external project folders the chat was
  attached to (`userSelectedFolders` — your real working files),
  `spaces.json`, and any other task. Only the selected task(s).
- **Verified-before-delete**: a task is purged only after its bundle is
  written and passes a completeness check (`transcript.jsonl` +
  `manifest.json` present and non-empty). Failed exports leave the source
  intact.
- **Forced confirmation**: the tool prints exactly which paths will be
  removed and waits for you to type `DELETE`. `--yes-i-know-this-is-risky`
  skips the prompt for CI only.
- **Incompatible with `--no-files`**: purging while the bundle omits the
  uploads / outputs would make them unrecoverable, so the combination is
  rejected.
- **Reversible via import**: `claude-cowork-export import <bundle>` restores
  a purged task (see [`import`](#import)).

---

## Cross-platform notes

| Concern | What happens |
|---|---|
| Sandbox path | `import` rewrites `manifest.source_sandbox_prefix` to the target's auto-detected prefix. |
| Path separators | `/` ↔ `\` rewritten based on `manifest.source_path_sep` and the target platform. |
| External folders | `userSelectedFolders` / `userApprovedFileAccessPaths` need explicit `--remap` entries because they live outside the sandbox and don't translate automatically. |
| Case sensitivity | When the source platform is Windows, prefix matches use `os.path.normcase`. |
| `LongPathsEnabled` | Cowork sandbox paths are deeply nested; Windows may need the `LongPathsEnabled` registry flag. |
| Console encoding | The Windows branch reconfigures `sys.stdout` / `sys.stderr` to UTF-8 so non-ASCII task titles print correctly under `cmd.exe`. |
| Windows MSIX vs native | `_cowork_roots()` returns both `%APPDATA%\Claude\...` AND `%LOCALAPPDATA%\Packages\Claude_*\LocalCache\...`, and merges per task id. The active task is usually only fully present in one of the two. |

---

## Security

`--include-auth` makes the bundle carry Cowork's auth artefacts (Cookies,
Local State, OAuth tokens, etc.). **Treat the resulting bundle exactly
like you'd treat an SSH private key.**

- Anyone who obtains the bundle can act as your account until you rotate
  (log out from Cowork on another device, change password, or revoke
  device on your account page).
- The tool prints a multi-line warning and requires you to type
  `I UNDERSTAND` interactively before producing such a bundle.
  `--yes-i-know-this-is-risky` skips the prompt for CI only.
- **Cross-platform auth restore is refused.** Cowork's auth artefacts
  are encrypted with macOS Keychain on macOS and DPAPI on Windows;
  the two are not interoperable. A `--include-auth` bundle exported
  on macOS can only restore auth on another macOS machine, and likewise
  Windows ↔ Windows.
- Bundles without `--include-auth` (the default) are safe to keep in
  unencrypted cloud storage — they contain conversation text, uploads,
  outputs, and metadata only.
- `import` requires `--force` to overwrite an existing task with the
  same id.

---

## Troubleshooting

**`list` shows fewer tasks than the Cowork sidebar**
On Windows MSIX installs, the public `%APPDATA%` mirror is incomplete.
The tool already merges with the MSIX LocalCache by default. If you're
still missing tasks, point `--cowork-root` at the LocalCache path
explicitly and report what shows up there.

**`list` shows duplicate task entries**
Two different roots have records for the same task with the same id;
the tool dedupes by id and picks the more complete record. If you still
see dups, run `--cowork-root` against each root in turn to confirm.

**`User Prompts` table-of-contents in the HTML has duplicate rows**
This was fixed in v0.1.3 (record-level dedup in audit-mode parsing). If
you're still seeing it, your install is older — re-install from the
current branch.

**`error: cross-platform --include-auth is not supported`**
Cowork's auth artefacts are platform-encrypted (macOS Keychain ↔ Windows
DPAPI), and cannot be re-encrypted on a different OS. Use the bundle
without `--include-auth` and sign in normally on the destination.

**`error: --remap is required for "<folder>" — it doesn't exist on this machine`**
`userSelectedFolders` points at a path that isn't valid on the target.
Pass `--remap "/old/path=/new/path"` (repeatable) to translate it.

**`seed-prompt.md` is too big to paste in a single message**
Use `--mode brief` for the smallest version, or split manually.

---

## Branches

This repo intentionally maintains two long-lived branches:

| Branch | For | Notes |
|---|---|---|
| `macos` (default) | macOS / Linux | Clean cross-platform base. |
| `windows` | Windows | Adds UTF-8 stdout reconfiguration, case-insensitive path comparison, and MSIX LocalCache discovery. |

Both stay version-synced. Install the branch matching your target platform.

---

## License

MIT. See [LICENSE](LICENSE).
