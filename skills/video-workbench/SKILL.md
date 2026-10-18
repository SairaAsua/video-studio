---
name: video-workbench
description: Open and use the existing Video Studio scene workbench for video requests, shot editing, continuity references and music timelines, or maintain its fork. Use for this app rather than creating another video service.
---

# Video Workbench

Reuse the existing Video Studio checkout and loopback FastAPI app. Locate it
from the user's current workspace, local launcher or project instructions.
Read its `AGENTS.md` and `reference/workbench-contract.txt` before editing project
data. Scene workbench is `/studio?project=SLUG`; the layer editor is `/` and both
share the project's `storyboard.json`. Preserve originals and previous versions.

For a human's new video request, establish the existing project and authorized
hosting body. If no body was selected, ask the requesting daimon which body should
host the app; do not infer a remote choice from identity, model or installed tools.
A saved choice may be reused only for the project it belongs to.

Local intake from the checkout:

```sh
.venv/bin/python scripts/request_video.py --project SLUG --body BODY --purpose 'Concrete human-authorized video task' --request-id UUID --start --open
```

Use a stable UUID and identical purpose/body on retry. Omitted body returns
`needs_body`; a different host returns `needs_routing`. `prepared` proves only
that the local workbench is ready. It does not prove Matrix delivery, execution
on another body, generation or cost approval. A maintained Matrix receiving
interface must independently verify real grants and the current human scope;
never add an inbox poller or copy credentials to make this command remote.

The finite desktop helper `scripts/open_workbench.py` can reuse a per-project
choice in local `~/.config/video-studio/launcher.json`. It starts only the existing
loopback runtime. Read `docs/MAINTENANCE.md` in the checkout for launcher setup,
fork/upstream tracking and isolated manual updates preserving dirty edits/media.

For production, preserve shot IDs, film membership, source provenance, selected
music and append-only character/location reference versions. Pin reference
versions per shot. Dialogue timing is optional; a music video does not imply
added narration. Keep short visual cuts as edit decisions over retained source
clips. Do not concatenate separate films using the legacy whole-project export.
Check the actual installed provider/authentication without printing credentials;
obtain concrete cost authorization before paid generation. Finish with API and
browser checks plus technical media checks and playback review of real outputs.
