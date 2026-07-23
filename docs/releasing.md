# Releasing URST

`release.sh` publishes the desktop Python package (`urst-mpy`) to PyPI,
verifies a clean consumer install, tags the verified commit, and creates a
GitHub release with the wheel and sdist attached.

Run it from a clean worktree whose commits have been pushed:

```bash
./release.sh
```

It suggests a semantic version from conventional commits, updates
`pyproject.toml`, `uv.lock`, and `package.json`, runs Ruff and pytest, then
builds exactly two artifacts in `release-dist/`. PyPI propagation is checked
with a fresh isolated install before the tag and GitHub release are created.

Use a dry rehearsal to stop before any external changes:

```bash
./release.sh --no-publish
```

That mode still creates the version-bump commit locally. The MIP distribution
is a separate `micropython-lib` PR process; see
[`URST-publish-to-micropython-lib.md`](URST-publish-to-micropython-lib.md).
