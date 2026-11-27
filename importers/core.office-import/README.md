# core.office-import: Word and Pages importers

First-party import adapters for `.docx` and `.pages`, speaking `importer.v1` (defined in [PresentationEngine](https://github.com/jterhorst/PresentationEngine), `proto/importer/v1`). The file readers are a faithful Python port of pp7gen's input strategies (Apache-2.0, WorshipKit; see `NOTICE`). **Pages files are read directly**: the IWA archives are decoded without the Pages app.

| File | What it does | Ported from |
| --- | --- | --- |
| `adapters/iwork.py` | IWA framing, pure Snappy decompressor, object map | `pages_input_strategy.rb` |
| `adapters/pages_reader.py` | BODY storage, char/paragraph style resolution, highlights, comment ranges, anchored and floating images | `pages_input_strategy.rb` |
| `adapters/docx_reader.py` | Paragraphs, runs, strict-underline mode, highlight/strike/size/color, images | `docx_input_strategy.rb` + `docx` gem 0.10.0 semantics |
| `adapters/segments.py` | Segment model, color snapping, punctuation merge | `segment.rb`, `text_color.rb`, `highlight_color.rb`, `paragraph_helpers.rb` |
| `adapters/iwork.desc` | iWork protobuf schemas as a FileDescriptorSet | extracted from `lib/iwork_proto` by `extract_descriptors.rb` |
| `adapters/importer_common.py` | Paragraphs → semantic slides, RTF writer, stdio framing | new |
| `adapters/docx.py`, `adapters/pages.py` | The two adapter executables | new |

## How the port was verified

`tests/verify_against_pp7gen.py` runs the readers on every pp7gen fixture with an `expected.json` and checks that every formatted run pp7gen produced reappears with identical formatting (bold, italic, underline, strike, highlight, size, color, exact hex, comment highlight). Images are compared by file name.

- **All 12 Pages fixtures: 100%**, images included.
- **5 of 8 Word fixtures: 100%.** The other three differ only where pp7gen's slide-building strategies deliberately restyle text after reading:
  - punctuation runs folded into neighbors per slide (`merge_punctuation_segments` on the legacy `parse_slides` path and in the rules engine)
  - quote authors forced bold (Spurgeon, Taylor, Stott in `sample_doc_with_images`)
  - the two-segment quote merge on the Matthew 6 slide in `Acts24`, which only applies after the reference line is removed

  In each case the port's runs are the ones pp7gen reads; the difference is downstream of the reader.

`tests/test_office_import.py` covers the rest without pp7gen's files: Snappy vectors, a synthetic `.pages` built with real IWA framing and iWork messages, generated `.docx` files, error paths, and (with `PP7GEN` set) all 24 pp7gen `.docx`/`.pages` files end to end.

## Beyond pp7gen

`docx_reader.parse_paragraphs(..., faithful=True)` reproduces pp7gen exactly. The default adds, without changing any run's formatting:

- soft line breaks (Shift-Return) become line breaks; pp7gen joined the lines with no space
- tabs become a space; pp7gen dropped them, gluing words together
- Title and Heading styles (matched by name, so localized Word works) and explicit page breaks, for splitting song sets into documents
- folder-package `.pages` files, a clear message for Pages '09 files, and password-protected `.docx` detection
- bold, italic, underline, strike, accent colors (exact source hex) and highlights carried into slide RTF

## Not ported (yet)

- pp7gen's sermon content strategies: Bible-reference detection, quote/author detection, the bracket/asterisk rules engine. They decide *slide structure* for sermon notes; this importer uses the lyrics-aware rules from CONTRACTS.md. A "Sermon notes" option could port them next.
- `.pptx` and RTF input.
- Tables are skipped, as in pp7gen.

## Shipping

These adapters are Python, which keeps them line-for-line comparable with the Ruby and easy to test. A shipping Mac app shouldn't depend on a Python runtime, so the release build should port these files to Swift or C++ and keep the same tests as the gate. `iwork.desc` works with `protoc --descriptor_set_in=iwork.desc --cpp_out=...` (or `--swift_out`), so no `.proto` sources are needed.

**Check before shipping:** pp7gen's own provenance notes describe `lib/iwork_proto` as community-maintained iWork schemas without a recorded license. Confirm where they came from before redistributing `iwork.desc`.
