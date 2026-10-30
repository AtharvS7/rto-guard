$ErrorActionPreference = 'Stop'
$repoUrl = 'https://github.com/AtharvS7/rto-guard.git'

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw 'Git is not installed or not on PATH. Install Git for Windows, reopen PowerShell, and run this script again.'
}

if (-not (Test-Path '.git')) {
    git init --initial-branch=main
    if ($LASTEXITCODE -ne 0) { throw 'git init failed.' }
}

$remote = git remote get-url origin 2>$null
if ($LASTEXITCODE -ne 0 -or -not $remote) {
    git remote add origin $repoUrl
} elseif ($remote.Trim() -ne $repoUrl) {
    git remote set-url origin $repoUrl
}
if ($LASTEXITCODE -ne 0) { throw 'Could not configure the origin remote.' }

git add --all
if ($LASTEXITCODE -ne 0) { throw 'git add failed.' }

Write-Host "`nFiles staged for: $repoUrl" -ForegroundColor Cyan
git status --short
Write-Host "`nThe supplied customer CSV is excluded by .gitignore. Review the staged file list above."
$answer = Read-Host 'Type PUSH to commit and push these files to GitHub'
if ($answer -cne 'PUSH') { Write-Host 'Stopped without committing or pushing.'; exit 0 }

$staged = git diff --cached --quiet
if ($LASTEXITCODE -eq 0) {
    Write-Host 'No staged changes to commit.'
} else {
    git commit -m 'Initial submission: RTO Guard COD decision system'
    if ($LASTEXITCODE -ne 0) { throw 'git commit failed. Configure your Git user.name and user.email, then rerun.' }
}

git push -u origin main
if ($LASTEXITCODE -ne 0) { throw 'Push failed. Complete GitHub sign-in when prompted, or check repository write permissions, then rerun.' }

Write-Host "`nPush complete: https://github.com/AtharvS7/rto-guard" -ForegroundColor Green
