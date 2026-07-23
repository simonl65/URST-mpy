#!/usr/bin/env bash
# Release URST to PyPI and GitHub. Run from the repository root.
set -euo pipefail
abort() { echo "Aborting: $*" >&2; exit 1; }
latest_tag=$(git describe --tags --abbrev=0 2>/dev/null) || abort "no release tag found"
[[ "$latest_tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || abort "latest tag is not semantic: $latest_tag"
arg="${1:-}"
if [[ "$arg" == "-h" || "$arg" == "--help" ]]; then echo "Usage: ./release.sh [--no-publish]"; exit 0; fi
[[ "$arg" == "" || "$arg" == "--no-publish" ]] || abort "unrecognized argument: $arg"
no_publish=false; [[ "$arg" == "--no-publish" ]] && no_publish=true
[[ -z "$(git status --porcelain)" ]] || abort "worktree is dirty"
upstream=$(git rev-parse --abbrev-ref --symbolic-full-name "@{upstream}" 2>/dev/null) || abort "current branch has no upstream"
git fetch --quiet || abort "could not fetch $upstream"
[[ -z "$(git log "$upstream..HEAD")" ]] || abort "push all local commits before releasing"
major=$(cut -d. -f1 <<<"$latest_tag" | tr -d v); minor=$(cut -d. -f2 <<<"$latest_tag"); patch=$(cut -d. -f3 <<<"$latest_tag"); range="$latest_tag..HEAD"
if git log --format='%s%n%b' "$range" | grep -Eq '(^[[:alnum:]]+(\([^)]*\))?!:|BREAKING CHANGE:)'; then suggested="$((major + 1)).0.0"
elif git log --format='%s' "$range" | grep -Eq '^feat(\([^)]*\))?:'; then suggested="$major.$((minor + 1)).0"
else suggested="$major.$minor.$((patch + 1))"; fi
echo "Current version: $(uv version)"
read -r -p "Enter the new URST version [$suggested]: " version
[[ -n "$version" ]] || version=$suggested
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || abort "version must use MAJOR.MINOR.PATCH format"
uv version "$version"
sed -i -E "0,/\"version\": \"[0-9]+\.[0-9]+\.[0-9]+\"/s//\"version\": \"$version\"/" package.json
git diff --check -- pyproject.toml package.json uv.lock
git add pyproject.toml package.json uv.lock
git commit -m "chore(release): bump version to v$version"
[[ -z "$(git status --porcelain)" ]] || abort "commit documentation changes before continuing"
uv run ruff check .
uv run pytest
rm -rf release-dist
uv build --out-dir release-dist
[[ "$(find release-dist -maxdepth 1 -type f | wc -l)" -eq 2 ]] || abort "expected one wheel and one sdist"
if [[ "$no_publish" == true ]]; then echo "Release gate passed; nothing was published, tagged, pushed, or released."; exit 0; fi
command -v gh >/dev/null 2>&1 || abort "gh is required before publishing"
uv publish release-dist/*
git push
for attempt in $(seq 1 30); do
    sleep 10
    if uvx --refresh --from "urst-mpy==$version" python -c "import urst; print(urst.__file__)"; then break; fi
    [[ "$attempt" -lt 30 ]] || abort "PyPI verification failed after publication; do not rerun this script"
done
verify_dir=$(mktemp -d /tmp/urst-release-verification.XXXXXX)
( cd "$verify_dir"; uv init --bare; uv add "urst-mpy==$version"; uv run python -c "import urst; print(urst.__file__)" )
rm -rf "$verify_dir"
git tag -a "v$version" -m "URST $version"
git push origin "v$version"
notes=$(mktemp --suffix=.md /tmp/urst-release-notes.XXXXXX)
printf '<!-- Write notes for v%s below the separator. -->\n---\n\n' "$version" >"$notes"
editor=$(printenv EDITOR || echo vi); "$editor" "$notes"
sed -i '1,/^---$/d' "$notes"
[[ -n "$(sed '/^[[:space:]]*$/d' "$notes")" ]] || abort "release notes were empty"
gh release create "v$version" release-dist/* --title "URST v$version" --notes-file "$notes"
rm -f "$notes"
