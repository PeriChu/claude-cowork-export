# claude-cowork-export

Export Claude **Cowork** chats — and legacy Claude Code sessions — to clean
HTML / Markdown / JSON / CSV bundles, with all uploads, generated outputs, and
the original transcript packaged alongside.

Zero dependencies (pure Python stdlib, 3.9+). One file, one command.

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

## Install

Requires Python 3.9+. macOS only for Cowork mode (the path layout is
macOS-specific); the legacy `--source code` mode also works on Linux/Windows.

Recommended (isolated, gives you a `claude-cowork-export` command on PATH):

```bash
pipx install git+https://github.com/<GITHUB_USER>/claude-cowork-export.git
```

Or just clone and run the script directly — it has no third-party deps:

```bash
git clone https://github.com/<GITHUB_USER>/claude-cowork-export.git
cd claude-cowork-export
python3 cowork_export.py --help
```

## Usage

```bash
# list all your Cowork chats, newest first
claude-cowork-export list

# export the most recent chat (default formats: html, md, json, csv)
claude-cowork-export export latest

# export by task-id prefix
claude-cowork-export export 5d6bfbdd --output ./exports

# export everything
claude-cowork-export export all --output ./exports

# pick a subset of formats
claude-cowork-export export latest --formats html,json

# don't bundle uploads/outputs/touched files (transcript-only)
claude-cowork-export export latest --no-files

# legacy Claude Code sessions (~/.claude/projects/...)
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
  `~/Library/Application Support/Claude/local-agent-mode-sessions/<account>/<workspace>/`
  - `local_<task>.json` — task metadata (title, model, dates, initial message)
  - `local_<task>/.claude/projects/<encoded-cwd>/<cli-session>.jsonl` — transcript
  - `local_<task>/uploads/` — user uploads
  - `local_<task>/outputs/` — assistant-generated files
  - `local_<task>/audit.jsonl` — audit log
  - `spaces.json` — space (project) registry, used to derive the chat's space
- **Legacy Claude Code:** `~/.claude/projects/<encoded-cwd>/<session>.jsonl`

## Notes & limitations

- **macOS TCC fallback.** When the assistant `Write`s files into protected
  folders (e.g. `~/Documents/...`), the tool can't always read them back due
  to macOS sandboxing. In that case it recovers the file from the `Write`
  tool call's `input.content` field — i.e. the version the assistant
  originally produced, captured from the transcript itself. This is more
  faithful than the live filesystem (no risk of being overwritten by later
  manual edits).
- **`Edit`/`MultiEdit` only.** If a file was only ever modified via diffs
  (no full `Write`) and isn't readable from disk, we can't reconstruct it.
  The README in each bundle notes which paths fell through.
- **Tool-result truncation.** Tool outputs over 8000 chars are truncated in
  HTML / MD for readability. The full text is always preserved in
  `session.json` and `transcript.jsonl`.
- **No external dependencies.** The HTML uses `marked.js` and `highlight.js`
  from a CDN at runtime, so the output renders nicely offline once cached but
  needs network access on first open (or you can self-host the CDN URLs).

## License

MIT. See [LICENSE](LICENSE).
