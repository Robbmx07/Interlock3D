# Interlock3D — Web Viewer

A separate, parallel track from the Python desktop app: a browser-based
reimplementation, started because the desktop app requires a Python
install and the user wanted something they could open with no setup.

This is **not** a port of the desktop app's code — it's a from-scratch
JS/WebGL rebuild, developed the same way the desktop app was (one phase
at a time, validated before moving on). See the "Web Reimplementation
Track" section in the project root's `PROGRESS.md` for phase status,
design decisions, and validation notes.

## Phase 1 — Load & view STL (done)

`viewer.html` is a single self-contained file: open it directly in any
modern browser (Chrome, Firefox, Safari, Edge — needs WebGL2) with no
server, no build step, and no dependency fetched at runtime (everything
is inlined except two Google Fonts). It:

- Loads one or more STL files (binary or ASCII) via drag-and-drop or a
  file picker.
- Renders them with an orbit camera (drag to rotate, scroll to zoom,
  shift+drag to pan) over a print-bed-style reference grid.
- Ships with two bundled example parts (the same
  `plate_with_matched_hole.stl` / `boss_cylinder.stl` pair used to
  validate the Python app's Phase 5 hole+peg compensation) so it opens
  with something to look at.

No feature detection, pairing, compensation, or export yet — that's
later phases, matching how the desktop app's own Phase 1 was scoped.

### Try it live

Published as a Claude Artifact: https://claude.ai/artifact/7WfUs7CACk7Yxhz4RxbYtH

Or open `web/viewer.html` directly from a local clone / this zip in
your browser — it's fully self-contained.
