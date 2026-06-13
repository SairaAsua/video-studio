# Continue Here — Video Studio (2026-06-13, post-v1.1)

## Where we are

**v1.1 SHIPPED.** Per-scene workspace with Assets / Captions / Render
tabs, mini-strip for cross-scene scan, and a feature flag (`?v3=1`).
Tag: `v1.1` (commit TBD, will be set at end of v1.1.7).

**Baseline before v1.1 work:** tag `v1.0-mvp` at commit `0d5feb3`.

**v1.1 release notes:** `~/wiki/projects/video-studio/notes/
video-studio-v1.1-release-2026-06-13.md`

## Next up (v2 backlog)

Nico flagged in the middle of v1.1 execution:

> "the SCRIPT panel should be resizable and minimizable"

This is the next concrete ask. It's a single dashboard tweak — a
resizer handle on the script pane (draggable, stores width in
localStorage) and a minimize/maximize toggle (icon button in
pane-header, animated transition). Localised to `app/static/
index.html`. ~80 lines of CSS + JS.

## v2 candidates (not yet prioritised)

From earlier conversation, gathered but not promised:

- **B-roll tab + cross-scene reference planner** (deferred from
  v1.1 per forum review). When b-roll is actually wanted, build
  this. Reference images auto-suggest from the scene's approved
  still + a few cross-scene candidates.
- **Concept viz tab (diagrams)** — **freeform prompt** input, not
  JSON. Render-time placement (replace still vs overlay) is the
  load-bearing unknown; resolve when we actually want a diagram.
- **Audio sub-timings editor** — split the per-scene TTS into
  per-phrase timings. Currently the `caption_phrases[].time_in/out`
  is set manually or estimated from word count.
- **Multi-asset primary per scene** — still + b-roll + concept
  choosing what to render. Deferred; today the approved still is
  the only primary.
- **Drag-reorder reference UI** — when b-roll lands.

## v1.1 lessons (baked into MEMORY.md)

- Parallel writes to the same file (`index.html`) lose work in
  the dispatcher's reset. Serialise or worktree.
- `kanban --parent` is a hard prerequisite, not hierarchy. Leave
  epics orphan and use `kanban link` for actual chain deps.
- Several `kanban create` flags are missing from the CLI; set
  via SQL on `kanban.db.tasks` columns.
- `goal_mode=1` is essential for cards needing multi-turn in-loop
  work.
- Robin may ask the user for permission on read-only tests; drop a
  comment with `--yolo --accept-hooks` to unlock.

## Project entry

When re-entering:
1. Read `MEMORY.md` (this project, in `~/Projects/video-studio/`).
2. Check kanban `video-studio` — any `running`/`blocked` cards?
3. Wiki notes under `~/wiki/projects/video-studio/notes/`.
4. Server alive? `curl http://127.0.0.1:8900/`.
5. Current state of `costa-rica/storyboard.json` (was clean as of
   v1.0-mvp reset; check what's been touched since).
