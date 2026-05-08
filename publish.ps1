# One-click publish for Windows / PowerShell.
# Mirrors publish.sh: creates the GitHub repo via gh CLI and pushes every local branch.
# Re-runnable: subsequent runs just push fresh commits.

param()

$ErrorActionPreference = "Stop"

$RepoName        = if ($env:REPO_NAME)        { $env:REPO_NAME }        else { "claude-cowork-export" }
$RepoVisibility  = if ($env:REPO_VISIBILITY)  { $env:REPO_VISIBILITY }  else { "public" }
$RepoDescription = if ($env:REPO_DESCRIPTION) { $env:REPO_DESCRIPTION } else { "Export Claude Cowork (and Claude Code) sessions to HTML / Markdown / JSON / CSV." }

Set-Location -Path $PSScriptRoot

function Step($msg) { Write-Host ""; Write-Host "==> $msg" -ForegroundColor Cyan }
function Warn($msg) { Write-Host "!! $msg" -ForegroundColor Yellow }
function Fail($msg) { Write-Host "xx $msg" -ForegroundColor Red; exit 1 }

# 1. gh CLI present?
Step "Checking gh CLI"
if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    Write-Host @"

GitHub CLI (gh) is not installed. Install it with one of:

  winget install --id GitHub.cli
  scoop install gh
  choco install gh

or download from https://github.com/cli/cli/releases

Then re-run this script.
"@
    exit 1
}
gh --version | Select-Object -First 1 | Write-Host

# 2. gh authenticated?
Step "Checking gh auth"
gh auth status 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host @"

You are not logged into gh yet. Run:

  gh auth login

Pick "GitHub.com", then "HTTPS", then "Login with a web browser". The default
scope (repo) is enough. Then re-run this script.
"@
    exit 1
}

$GhUser = (gh api user --jq .login).Trim()
if (-not $GhUser) { Fail "Could not determine GitHub username from gh." }
Write-Host "Authenticated as: $GhUser"

# 3. Substitute <GITHUB_USER> placeholders
Step "Substituting <GITHUB_USER> placeholders"
foreach ($f in @("README.md","pyproject.toml")) {
    if (Test-Path $f) {
        $content = Get-Content -Raw -Path $f
        if ($content -match "<GITHUB_USER>") {
            $content = $content.Replace("<GITHUB_USER>", $GhUser)
            [System.IO.File]::WriteAllText((Resolve-Path $f), $content, [System.Text.UTF8Encoding]::new($false))
            Write-Host "  patched $f"
        }
    }
}

# 4. git init + identity
Step "Preparing git"
if (-not (Test-Path ".git")) {
    git init -q -b macos
    Write-Host "  initialized fresh repo on 'macos'"
}

if (-not (git config user.name 2>$null)) {
    git config user.name $GhUser
    Write-Host "  set user.name=$GhUser (local)"
}
if (-not (git config user.email 2>$null)) {
    $GhEmail = gh api user --jq .email 2>$null
    if (-not $GhEmail -or $GhEmail -eq "null") {
        $GhId = gh api user --jq .id 2>$null
        if (-not $GhId) { $GhId = "0" }
        $GhEmail = "$GhId+$GhUser@users.noreply.github.com"
    }
    git config user.email $GhEmail
    Write-Host "  set user.email=$GhEmail (local)"
}

# 5. Stage explicit files
$Branch = git branch --show-current
Step "Staging files on branch $Branch"
$Tracked = @("README.md","LICENSE",".gitignore",".gitattributes","pyproject.toml","cowork_export.py","publish.sh","publish.ps1")
foreach ($f in $Tracked) {
    if (Test-Path $f) { git add $f }
}

git diff --cached --quiet
if ($LASTEXITCODE -eq 0) {
    Write-Host "  nothing new to commit"
} else {
    git commit -q -m "Update from publish.ps1"
    Write-Host "  committed"
}

# 6. Create remote repo (idempotent)
Step "Ensuring remote repo $GhUser/$RepoName exists"
gh repo view "$GhUser/$RepoName" 2>$null | Out-Null
$NewlyCreated = $false
if ($LASTEXITCODE -eq 0) {
    Write-Host "  remote repo already exists, will push to it"
} else {
    $VisFlag = switch ($RepoVisibility) {
        "public"  { "--public"  }
        "private" { "--private" }
        default   { Fail "REPO_VISIBILITY must be 'public' or 'private', got: $RepoVisibility" }
    }
    gh repo create "$GhUser/$RepoName" $VisFlag --description $RepoDescription
    if ($LASTEXITCODE -ne 0) { Fail "gh repo create failed" }
    $NewlyCreated = $true
    Write-Host "  created $GhUser/$RepoName ($RepoVisibility)"
}

git remote get-url origin 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    git remote add origin "https://github.com/$GhUser/$RepoName.git"
}

# 7. Push every local branch
Step "Pushing all local branches"
git push -u origin --all
if ($LASTEXITCODE -ne 0) { Fail "git push failed" }

# 8. On first creation, set default branch
if ($NewlyCreated) {
    $DefaultBranch = ""
    foreach ($b in @("macos","main","windows")) {
        git show-ref --verify --quiet "refs/heads/$b"
        if ($LASTEXITCODE -eq 0) { $DefaultBranch = $b; break }
    }
    if ($DefaultBranch) {
        Step "Setting default branch to $DefaultBranch"
        gh repo edit "$GhUser/$RepoName" --default-branch $DefaultBranch
        if ($LASTEXITCODE -ne 0) { Warn "could not set default branch" }
    }
}

Step "Done"
$Url = "https://github.com/$GhUser/$RepoName"
Write-Host "Repo: $Url"
Write-Host ""
Write-Host "Install (default branch):"
Write-Host "  pipx install git+$Url.git"
git show-ref --verify --quiet refs/heads/windows
if ($LASTEXITCODE -eq 0) {
    Write-Host "Install (Windows branch):"
    Write-Host "  pipx install git+$Url.git@windows"
}
