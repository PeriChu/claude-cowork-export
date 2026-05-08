# claude-cowork-export (Windows branch)

Export Claude **Cowork** chats — and legacy Claude Code sessions — to clean
HTML / Markdown / JSON / CSV bundles, with all uploads, generated outputs, and
the original transcript packaged alongside.

Zero dependencies (pure Python stdlib, 3.9+). One file, one command.

> This is the **Windows branch**. It auto-detects platform paths
> (`%APPDATA%\Claude\...` on Windows, `~/Library/Application Support/Claude/...`
> on macOS, `~/.config/Claude/...` on Linux), reconfigures the console to
> UTF-8 on Windows so Chinese / emoji titles print correctly, and falls back
> to case-insensitive path comparison where needed. The macOS-only build
> lives on the [`macos`](../../tree/macos) branch.

## Why

Cowork stores each chat as a sandboxed task on disk. The transcript is a JSONL
file inside `~/Library/Application Support/Claude/local-agent-mode-sessions/...`,
the user's uploads sit in `uploads/`, the assistant's generated files in
`outputs/`, and there's an audit log alongside. Reading any of that by hand is
miserable. This tool flattens it into:

- **`session.html`** — formatted reading view with light/dark mode, syntax
  highlighting, collapsible tool calls and reasoning, a TOC of user prompts,
  and links to every upload / output / touched file.
- **`session.md`** — GitHub-flavoured Markdown for archiving / sharing.
- **`session.json`** — structured per-block dump for feeding to another LLM.
- **`session.csv`** — flat per-block table for spreadsheets or batch ingestion.
- **`uploads/`, `outputs/`, `assets/`** — the actual files.
- **`transcript.jsonl`, `task.json`, `audit.jsonl`** — the lossless source.

## Install (Windows)

Requires Python 3.9+. Get it from the Microsoft Store, [python.org](https://python.org),
or `winget install Python.Python.3.12`.

Recommended (isolated, gives you a `claude-cowork-export` command on PATH):

```powershell
# install pipx if you don't have it
python -m pip install --user pipx
python -m pipx ensurepath
# (open a new shell so PATH picks up)

# install the tool from the windows branch
pipx install "git+https://github.com/PeriChu/claude-cowork-export.git@windows"
```

Or just clone and run the script directly — it has no third-party deps:

```powershell
git clone -b windows https://github.com/PeriChu/claude-cowork-export.git
cd claude-cowork-export
python cowork_export.py --help
```

### Install (macOS / Linux)

The same script works there too — see the [`macos`](../../tree/macos) branch
for the original macOS-targeted version, or use this branch:

```bash
pipx install "git+https://github.com/PeriChu/claude-cowork-export.git@windows"
```

## Usage

Same commands on every platform; only the path conventions differ:

```powershell
# list all your Cowork chats, newest first
claude-cowork-export list

# export the most recent chat (default formats: html, md, json, csv)
claude-cowork-export export latest

# export by task-id prefix
claude-cowork-export export 5d6bfbdd --output .\exports

# export everything
claude-cowork-export export all --output .\exports

# pick a subset of formats
claude-cowork-export export latest --formats html,json

# don't bundle uploads/outputs/touched files (transcript-only)
claude-cowork-export export latest --no-files

# legacy Claude Code sessions
claude-cowork-export --source code list
claude-cowork-export --source code export latest
```

## Output bundle layout

Each exported task gets its own folder under `--output`:

```
exports/<task-id>/
├── README.md            # bundle summary
├── session.html         # rendered reading view
├── session.md           # markdown export
├── session.json         # structured per-block dump
├── session.csv          # flat tabular dump
├── transcript.jsonl     # raw source (lossless)
├── task.json            # original Cowork task metadata
├── audit.jsonl          # Cowork audit log
├── uploads/             # files the user attached to the chat
├── outputs/             # files the assistant generated (Cowork output dir)
└── assets/              # files written/edited outside the output dir
```

## Where Cowork data lives

- **Cowork tasks (chats):**
  - Windows: `%APPDATA%\Claude\local-agent-mode-sessions\<account>\<workspace>\`
    (= `C:\Users\<you>\AppData\Roaming\Claude\local-agent-mode-sessions\...`)
  - macOS: `~/Library/Application Support/Claude/local-agent-mode-sessions/<account>/<workspace>/`
  - Linux: `~/.config/Claude/local-agent-mode-sessions/<account>/<workspace>/`

  Inside the workspace dir:
  - `local_<task>.json` — task metadata (title, model, dates, initial message)
  - `local_<task>/.claude/projects/<encoded-cwd>/<cli-session>.jsonl` — transcript
  - `local_<task>/uploads/` — user uploads
  - `local_<task>/outputs/` — assistant-generated files
  - `local_<task>/audit.jsonl` — audit log
  - `spaces.json` — space (project) registry, used to derive the chat's space
- **Legacy Claude Code:** `~/.claude/projects/<encoded-cwd>/<session>.jsonl`
  (= `C:\Users\<you>\.claude\projects\...` on Windows)

## Notes & limitations

- **Recorded-content fallback.** When a `Write`-d file isn't readable from
  disk anymore (deleted, moved, or — on macOS — blocked by TCC for a
  protected folder), the tool recovers it from the `Write` tool call's
  `input.content` field captured in the transcript itself. This is the
  version the assistant originally produced, which is often more faithful
  than the live filesystem.
- **`Edit`/`MultiEdit` only.** If a file was only ever modified via diffs
  (no full `Write`) and isn't readable from disk, we can't reconstruct it.
  The README in each bundle notes which paths fell through.
- **Windows path quirks.** Windows is case-insensitive but Python's
  `Path.relative_to` isn't, so the Windows branch normalises with
  `os.path.normcase` when matching paths. Long paths (> 260 chars) may
  fail unless you've enabled `LongPathsEnabled` in the registry.
- **Console encoding (Windows).** The script reconfigures stdout/stderr
  to UTF-8 at startup so Chinese / emoji titles print correctly. If you
  still see mojibake, run `chcp 65001` before invoking the tool, or use
  Windows Terminal instead of legacy `cmd.exe`.
- **Tool-result truncation.** Tool outputs over 8000 chars are truncated in
  HTML / MD for readability. The full text is always preserved in
  `session.json` and `transcript.jsonl`.
- **No external dependencies.** The HTML uses `marked.js` and `highlight.js`
  from a CDN at runtime, so the output renders nicely offline once cached but
  needs network access on first open (or you can self-host the CDN URLs).

## License

MIT. See [LICENSE](LICENSE).
