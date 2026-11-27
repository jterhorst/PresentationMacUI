# PresentationMacUI

The native macOS app for [PresentationEngine](https://github.com/jterhorst/PresentationEngine): a SwiftUI controller and editor that talks to the engine process over the engine protocol. It never draws to outputs itself.

> **Status:** design. The UI spec is the "UI client" and "Import" sections of the engine's [architecture plan](https://github.com/jterhorst/PresentationEngine/blob/main/docs/architecture.md). The first code here is the `.docx` / `.pages` importers.

## What the app does

- **One window:** a toolbar, then three panes. Libraries and playlists on the left; the selected playlist or document in the center; live playback (output previews, live layers, plugin options) on the right.
- **Edit mode** takes over the left and center panes as one editing area (slide strip, canvas, inspector). Live playback stays visible.
- **Plugin drawers** open at the bottom of the center pane from the toolbar's Plugins menu.
- **Every block is a panel**, arranged by a workspace layout file, so plugins can add panels now and layout plugins can rearrange them later.
- **Import** goes through import adapters chosen in the Open panel; `.docx` and `.pages` ship in v1.

## Contracts

The app builds against the contracts in PresentationEngine:

| Contract | Path in PresentationEngine |
| --- | --- |
| Engine protocol | `proto/engine/v1/` |
| Library, document and playlist files | `proto/library/v1/` |
| Import adapters | `proto/importer/v1/` |
| Plugin panels, inspectors, settings | `schemas/ui-schema.schema.json` |
| Workspace layouts | `schemas/workspace-layout.schema.json` |

## Layout

| Path | What |
| --- | --- |
| `importers/core.office-import/` | First-party `.docx` and `.pages` import adapters. The readers are ported from pp7gen (Apache-2.0, WorshipKit); see its NOTICE and README |
| `scripts/test_importers.sh` | Runs the importer tests against a PresentationEngine checkout |

## Tests

```sh
pip install grpcio-tools jsonschema
ENGINE_DIR=../PresentationEngine scripts/test_importers.sh
```

Set `PP7GEN` to a pp7gen checkout to also run every pp7gen fixture end to end and check formatting fidelity against its `expected.json` files.

## License

[MPL-2.0](LICENSE). `importers/core.office-import` includes code ported from pp7gen under Apache-2.0; see its NOTICE.
