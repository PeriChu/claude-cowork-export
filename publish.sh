#!/usr/bin/env bash
# One-click publish: creates the GitHub repo via gh CLI and pushes everything.
# Re-runnable: subsequent runs just push fresh commits to the existing repo.
set -euo pipefail

REPO_NAME="${REPO_NAME:-claude-cowork-export}"
REPO_VISIBILITY="${REPO_VISIBILITY:-public}"   # public | private
REPO_DESCRIPTION="${REPO_DESCRIPTION:-Export Claude Cowork (and Claude Code) sessions to HTML / Markdown / JSON / CSV.}"

cd "$(dirname "$0")"

step() { printf "\n\033[1;36m==> %s\033[0m\n" "$*"; }
warn() { printf "\033[1;33m!! %s\033[0m\n" "$*" >&2; }
fail() { printf "\033[1;31mxx %s\033[0m\n" "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# 1. gh CLI present?
# ---------------------------------------------------------------------------
step "Checking gh CLI"
if ! command -v gh >/dev/null 2>&1; then
  cat >&2 <<'EOF'

GitHub CLI (gh) is not installed. Install it first:

  # macOS (Homebrew)
  brew install gh

  # macOS (MacPorts)
  sudo port install gh

  # other platforms: https://github.com/cli/cli#installation

Then re-run this script.
EOF
  exit 1
fi
echo "gh: $(gh --version | head -1)"

# ---------------------------------------------------------------------------
# 2. gh authenticated?
# ---------------------------------------------------------------------------
step "Checking gh auth"
if ! gh auth status >/dev/null 2>&1; then
  cat >&2 <<'EOF'

You are not logged into gh yet. Run:

  gh auth login

Pick "GitHub.com", then "HTTPS", then "Login with a web browser". Make sure
the token scope includes `repo` (the default for full account access is fine).
Then re-run this script.
EOF
  exit 1
fi

GH_USER="$(gh api user --jq .login)"
[ -n "$GH_USER" ] || fail "Could not determine GitHub username from gh."
echo "Authenticated as: $GH_USER"

# ---------------------------------------------------------------------------
# 3. Substitute <GITHUB_USER> placeholders
# ---------------------------------------------------------------------------
step "Substituting <GITHUB_USER> placeholders"
for f in README.md pyproject.toml; do
  if [ -f "$f" ] && grep -q "<GITHUB_USER>" "$f"; then
    if [ "$(uname)" = "Darwin" ]; then
      sed -i '' "s|<GITHUB_USER>|$GH_USER|g" "$f"
    else
      sed -i "s|<GITHUB_USER>|$GH_USER|g" "$f"
    fi
    echo "  patched $f"
  fi
done

# ---------------------------------------------------------------------------
# 4. git init + identity
# ---------------------------------------------------------------------------
step "Preparing git"
if [ ! -d .git ]; then
  git init -q -b main
  echo "  initialized fresh repo on 'main'"
fi

if [ -z "$(git config user.name 2>/dev/null || true)" ]; then
  git config user.name "$GH_USER"
  echo "  set user.name=$GH_USER (local)"
fi
if [ -z "$(git config user.email 2>/dev/null || true)" ]; then
  GH_EMAIL="$(gh api user --jq .email 2>/dev/null || true)"
  if [ -z "$GH_EMAIL" ] || [ "$GH_EMAIL" = "null" ]; then
    GH_ID="$(gh api user --jq .id 2>/dev/null || echo 0)"
    GH_EMAIL="${GH_ID}+${GH_USER}@users.noreply.github.com"
  fi
  git config user.email "$GH_EMAIL"
  echo "  set user.email=$GH_EMAIL (local)"
fi

# ---------------------------------------------------------------------------
# 5. Stage explicit files on the current branch
# ---------------------------------------------------------------------------
step "Staging files on branch $(git branch --show-current 2>/dev/null || echo '?')"
TRACKED=(README.md LICENSE .gitignore .gitattributes pyproject.toml cowork_export.py publish.sh publish.ps1)
for f in "${TRACKED[@]}"; do
  [ -f "$f" ] && git add "$f"
done

if git diff --cached --quiet; then
  echo "  nothing new to commit"
else
  git commit -q -m "Update from publish.sh"
  echo "  committed"
fi

# ---------------------------------------------------------------------------
# 6. Create remote repo (idempotent)
# ---------------------------------------------------------------------------
step "Ensuring remote repo $GH_USER/$REPO_NAME exists"
NEWLY_CREATED=0
if gh repo view "$GH_USER/$REPO_NAME" >/dev/null 2>&1; then
  echo "  remote repo already exists, will push to it"
else
  case "$REPO_VISIBILITY" in
    public)  VIS="--public"  ;;
    private) VIS="--private" ;;
    *) fail "REPO_VISIBILITY must be 'public' or 'private', got: $REPO_VISIBILITY" ;;
  esac
  gh repo create "$GH_USER/$REPO_NAME" $VIS --description "$REPO_DESCRIPTION"
  NEWLY_CREATED=1
  echo "  created $GH_USER/$REPO_NAME ($REPO_VISIBILITY)"
fi

if ! git remote get-url origin >/dev/null 2>&1; then
  git remote add origin "https://github.com/$GH_USER/$REPO_NAME.git"
fi

# ---------------------------------------------------------------------------
# 7. Push every local branch
# ---------------------------------------------------------------------------
step "Pushing all local branches"
git push -u origin --all

# ---------------------------------------------------------------------------
# 8. On first creation, set default branch to macos if it exists
# ---------------------------------------------------------------------------
if [ "$NEWLY_CREATED" = "1" ]; then
  DEFAULT_BRANCH=""
  for b in macos main windows; do
    if git show-ref --verify --quiet "refs/heads/$b"; then
      DEFAULT_BRANCH="$b"; break
    fi
  done
  if [ -n "$DEFAULT_BRANCH" ]; then
    step "Setting default branch to $DEFAULT_BRANCH"
    gh repo edit "$GH_USER/$REPO_NAME" --default-branch "$DEFAULT_BRANCH" || \
      warn "could not set default branch (will fall back to gh's choice)"
  fi
fi

# ---------------------------------------------------------------------------
step "Done"
URL="https://github.com/$GH_USER/$REPO_NAME"
echo "Repo: $URL"
echo
echo "Install (default / macOS branch):"
echo "  pipx install git+$URL.git"
if git show-ref --verify --quiet refs/heads/windows; then
  echo "Install (Windows branch):"
  echo "  pipx install git+$URL.git@windows"
fi
