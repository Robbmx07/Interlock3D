# Interlock3D — Web Viewer

A separate, parallel track from the Python desktop app: a browser-based
reimplementation, started because the desktop app requires a Python
install and the user wanted something they could open with no setup.

This is **not** a port of the desktop app's code — it's a from-scratch
JS/WebGL rebuild (no external libraries, hand-written STL parser,
renderer, linear algebra, RANSAC-style feature fitting, ZIP writer,
etc.), developed the same way the desktop app was (one phase at a
time, validated before moving on — see the "Web Reimplementation
Track" section in the project root's `PROGRESS.md` for full phase
status and validation notes). All phases (W1-W7) are now complete,
matching the full feature set of the Python desktop app.

`viewer.html` is a single self-contained file: open it directly in any
modern browser (Chrome, Firefox, Safari, Edge — needs WebGL2) with no
server, no build step, and no dependency fetched at runtime (everything
is inlined except two Google Fonts, which degrade gracefully to system
fonts if unavailable).

## What it does

- **Load & view** — drag-and-drop or browse for one or more STL files
  (binary or ASCII), rendered with an orbit camera over a print-bed
  grid. Ships with two bundled example parts (a matched hole+peg pair)
  so it opens with something to look at.
- **Feature detection** — automatically finds holes, pegs, and flat
  faces on every loaded part (dihedral-angle mesh segmentation +
  PCA/Kasa circle fitting), shown as colored highlight overlays and a
  per-part feature count in the parts list.
- **Click-to-pair** — click a highlighted hole or peg, then a
  compatible feature on a *different* part, to confirm a mating pair
  (armed selection = gold, confirmed pair = green). Click a paired
  feature again to remove it. Only hole+peg and flat+flat pairs are
  allowed.
- **Material & fit** — a global material choice (PLA / PETG / ABS) and
  a per-pair fit type (press / sliding / clearance), using the same
  clearance table as the desktop app.
- **Calibration wizard** — generate and download a small hole+peg test
  print, measure it with calipers, and save the printer's real
  dimensional bias (hole undersize / peg oversize) so future exports
  correct for it on top of the generic material clearance. Saved to
  the browser's `localStorage`, so it persists across sessions on the
  same device/browser.
- **Export** — applies the compensation to every paired feature,
  checks mesh integrity (watertight / winding-consistent / no negative
  volume), and offers a single `.zip` download containing every part
  (corrected or unchanged, suffixed `_corrected.stl`) plus a
  `compensation_report.txt` describing every change made. (`.stl`
  alone isn't in the Artifact platform's download allowlist, hence the
  zip.)

### Try it live

Published as a Claude Artifact: https://claude.ai/artifact/7WfUs7CACk7Yxhz4RxbYtH

Or open `web/viewer.html` directly from a local clone / this zip in
your browser — it's fully self-contained. Outside the Artifact
platform (e.g. opened as a plain local file) the export/calibration
download buttons will say downloads aren't available, since the
`downloads` capability is only granted inside a claude.ai-hosted view;
everything else (load, view, detect, pair) works identically either
way.
