# CLAUDE.md

Guidance for Claude Code in this repository.

## What this is

PresentationMacUI: the native macOS (SwiftUI) client for PresentationEngine (`jterhorst/PresentationEngine`). The UI spec is in that repo's `docs/architecture.md` ("UI client" and "Import"); the contracts are in its `proto/`, `schemas/` and `CONTRACTS.md`. Never change a contract here: change it in PresentationEngine, then consume it.

## Commands

- Importer tests: `ENGINE_DIR=../PresentationEngine scripts/test_importers.sh` (needs `pip install grpcio-tools jsonschema`). Set `PP7GEN` to a pp7gen checkout to add the real-fixture and fidelity checks.

## Rules

- **Pure protocol client.** The app uses only what the engine protocol exposes, so the web UI can be a second client rather than a rewrite.
- **Live playback stays visible.** No mode or layout may hide the playback pane; Edit mode replaces only the sidebar and main panes.
- **Round-trip unknown data.** Keep document extensions, metadata, plugin elements and unknown protobuf fields byte for byte when editing or saving.
- **Importers** run as separate processes speaking `importer.v1` over framed stdin/stdout. They read a copy of the input, write only their output folder, and never touch the engine or the library.
- `importers/core.office-import` is a port of pp7gen (Apache-2.0). Keep its NOTICE and per-file attribution headers, and keep its tests passing when porting it to Swift.
- License: MPL-2.0.
