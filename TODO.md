# URST-mpy TODO:

Project-wide outstanding-work tracker — the source of truth for implementation status.

This file lists only what remains open. Update it in the same change as newly discovered work; when a task is done, remove it here and let the commit/PR that did the work be the record, rather than leaving completed items in this file.

Non-trivial tasks get their own dev log in `docs/development/`, named for the task (e.g. `docs/development/<task slug>>-log.md`), not one shared file.

## Tasks in priority order

[ ] **Run the `micropython-nasa-power-of-ten` skill against this repo.** Surfaced 2026-08-20 as a `Needs Review`/deferred item (D-1) in `diff-drive-robot`'s own NASA Power of Ten audit (`docs/development/NASA-Power-of-Ten-review.md`), which explicitly can't audit vendored code per its own `CLAUDE.md` convention -- `diff-drive-robot/robot/device/lib/urst` is synced verbatim from here (and `lib/otampy` from the `otampy` repo), not maintained in that repo. That audit's shallow grep pass (not a deep read) flagged one spot in this repo worth a proper look, plus three more in `otampy` (see that repo's own `TODO.md` for the matching entry), evidence as of urst-mpy 3.2.0:
  - `device/lib/urst/core_handler.py:274` -- `while True:`, not yet reviewed.
  - Rather than one-off reading that spot, run the full skill against both repos to get a proper structured audit (same as `diff-drive-robot`'s own, which found real value beyond just this one line) instead of a partial manual pass.
