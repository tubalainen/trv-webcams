#!/usr/bin/env bash
# Fail if API keys, tokens or other secrets are about to be committed.
# Scans every file tracked by git (or staged, with --staged). Used by CI and
# before every push. Exit code 1 = something looks like a secret.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

if [ "${1:-}" = "--staged" ]; then
    files=$(git diff --cached --name-only --diff-filter=ACMR)
else
    files=$(git ls-files)
fi

fail=0
report() { echo "::error file=$1::$2"; fail=1; }

# .env files hold real secrets and must never be tracked (.env.example is the template)
while IFS= read -r f; do
    [ -n "$f" ] || continue
    case "$(basename "$f")" in
        .env.example) ;;
        .env|.env.*) report "$f" "tracked .env file – remove it with 'git rm --cached $f'" ;;
    esac
done <<< "$files"

# key=value assignments with a real-looking value (16+ chars of letters/digits only)
assign='(api[_-]?key|apikey|secret|token|passw(or)?d|private[_-]?key|access[_-]?key)[A-Za-z0-9_]*["'"'"']?[[:space:]]*[:=][[:space:]]*["'"'"']?[A-Za-z0-9+/_]{16,}["'"'"']?([[:space:]#]|$)'
# well-known token formats
known='(ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{40,}|gh[ousr]_[A-Za-z0-9]{36}|AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9_-]{32,}|xox[abpr]-[A-Za-z0-9-]{10,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)'

while IFS= read -r f; do
    [ -n "$f" ] && [ -f "$f" ] || continue
    [ "$f" = "scripts/check-secrets.sh" ] && continue
    grep -Iq . "$f" 2>/dev/null || continue  # skip binary files
    if hits=$(grep -nEi "$assign" "$f" | grep -Ev 'os\.environ|getenv|settings\.|\$\{|example|dummy|placeholder|xxxx' || true) && [ -n "$hits" ]; then
        while IFS= read -r h; do report "$f" "possible secret on line ${h%%:*}"; done <<< "$hits"
    fi
    if hits=$(grep -nE "$known" "$f" || true) && [ -n "$hits" ]; then
        while IFS= read -r h; do report "$f" "token-like value on line ${h%%:*}"; done <<< "$hits"
    fi
done <<< "$files"

if [ "$fail" -ne 0 ]; then
    echo "Secrets must never be committed. Put them in .env (gitignored) or environment variables." >&2
    exit 1
fi
echo "check-secrets: no secrets found"
