# Workbench access and maintenance

This branch extends [Nico's Video Studio](https://github.com/nicoechaniz/video-studio)
with a scene production workbench. The maintained fork is
[SairaAsua/video-studio, branch workbench](https://github.com/SairaAsua/video-studio/tree/workbench).
The layer editor and workbench share one FastAPI runtime and one storyboard.

## Open an existing project

From the checkout:

```sh
.venv/bin/python scripts/open_workbench.py --project PROJECT --body BODY
```

This opens the workbench in the local browser and starts the existing `start.sh`
only when needed. It binds to loopback. It does not submit image/video generations.
Omit `--body` to ask which body should host the app; a different body returns
`needs_routing` without making a remote request. Reuse `--request-id UUID` and the
same purpose/body after an ambiguous reply to recover the same intake record.

An optional local `$XDG_CONFIG_HOME/video-studio/launcher.json` (default
`~/.config/video-studio/launcher.json`) may remember a choice already made by the
human for one project:

```json
{"default_project":"my-project","body":"my-authorized-body"}
```

The saved body applies only to that project. Keep this local configuration,
private project contents and credentials outside Git. The app menu/desktop can
invoke this helper with that saved choice. New hosts must independently verify
their runtime, provider authentication and human authorization.

For xAI OAuth, set `HERMES_HOME` to the authorized Hermes profile and
`HERMES_AGENT_ROOT` to the existing Hermes source checkout in the ignored local
`.env`. The generation bridge uses Hermes's credential pool and refresh resolver;
it does not copy tokens or read a stale singleton from `auth.json`. An explicit
`XAI_API_KEY` remains supported. Verify access with a read-only models request;
successful authentication does not prove model entitlement or remaining quota.
OAuth video uses the existing subscription according to Hermes
(https://hermes-agent.nousresearch.com/docs/guides/xai-grok-oauth). Set
`VIDEO_STUDIO_BILLING_MODE=subscription` locally to forbid paid-key fallback;
API price quotes are reference only for this mode. API-key spending still needs
concrete human cost authorization.
Never place credential values in commands, source, logs or this repository.

For agents, use `scripts/request_video.py` directly with an explicit project,
purpose and stable UUID. Read `reference/workbench-contract.txt` for receipt
semantics and `skills/video-workbench/SKILL.md` for the neutral workflow. A
`prepared` receipt only proves local readiness; it is not a Matrix receipt,
generation result or human approval. Remote receiving requires a maintained,
authenticated Matrix interface and genuine grants. No inbox poller is included.

## Bring upstream changes in safely

Updates are deliberate maintenance, not an automatic service. Keep `origin`
pointed at Sai's fork and `upstream` at Nico's repository. Before any integration,
inspect both remote URLs, the checked out branch, `git status --short`, and the
actual upstream default branch (`git remote show upstream`).

1. Back up the current source diff, untracked files that matter, and a coherent
   copy of local project/storyboard state. `git stash` alone omits ignored media;
   projects and original recordings need their own local backup. Never upload
   the backup or `.env` to GitHub.
2. Finish and review the current feature checkpoint on `workbench`. Stage only
   explicit audited source paths. Keep private projects, generated media,
   credentials, coordination material and local logs out of the commit.
3. Fetch, without modifying the running checkout:
   `git fetch origin` and `git fetch upstream`. Compare
   `git log --oneline workbench..upstream/master` and
   `git diff --stat workbench...upstream/master` (replace `master` if upstream's
   default branch changed).
4. Integrate in an isolated maintenance worktree from the reviewed feature
   checkpoint, e.g. `git worktree add -b maintenance/upstream-YYYYMMDD PATH workbench`.
   In that worktree merge `upstream/master`. Resolve conflicts with the existing
   workbench behavior in mind; do not reset the main dirty checkout.
5. Install/check dependencies in the maintenance worktree as needed. Run
   `.venv/bin/python -m pytest tests/ --ignore=tests/test_e2e.py -q`,
   `node --check app/static/workbench.js`, and focused browser/API checks against
   disposable project fixtures. A green unit test suite does not prove generation,
   Matrix reception or real-film playback. Preserve actual source media.
6. Review the diff, including dependencies and source paths. Push the reviewed
   maintenance branch to `origin` and integrate it into `workbench`. Never
   force-push upstream or replace local changes with `reset --hard`/`clean`.
7. Schedule the local restart after work in progress is safe. Confirm the PID
   belongs to this checkout and this loopback port, stop only that process, and
   relaunch with the existing launcher. Reopen the intended project and verify
   its storyboard, references and selected media. Report repository integration
   and deployed local verification separately.

Do not combine independently planned films through the legacy whole-project
export. Preserve source clips, reference versions, sound edits and provenance.
Concrete paid generation requires the human's authorization for its actual cost.
