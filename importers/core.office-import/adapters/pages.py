#!/usr/bin/env python3
"""First-party import adapter: Apple Pages documents (.pages).

Reads the Pages file directly (IWA protobuf archives); no Pages app needed.
Reader ported from pp7gen (Apache-2.0, WorshipKit); see pages_reader.py.
"""
import pathlib
import shutil
import zipfile

import pages_reader
from importer_common import (PROTOCOL_VERSION, AdapterFailure, build_result, paragraphs_to_items,
                             pb, progress, serve)

INFO = pb.AdapterInfo(
    protocol_version=PROTOCOL_VERSION,
    adapter_id="core.office-import/pages",
    version="1.0.0",
    file_types=[pb.FileType(uttype="com.apple.iwork.pages.sffpages", extensions=["pages"],
                            mime_types=["application/vnd.apple.pages"])],
    supports_multiple_documents=False,
)


def do_import(req, options):
    temp_dirs = []
    try:
        progress(0.1, "Reading document")
        path = pathlib.Path(req.input_path)
        if path.is_dir():
            path = _zip_package(path, temp_dirs)  # Pages can also save as a folder package
        try:
            paragraphs = pages_reader.parse_paragraphs(str(path), temp_dirs)
        except zipfile.BadZipFile:
            raise AdapterFailure(pb.AdapterError.CODE_CORRUPT_FILE, "This file isn't a valid Pages document.")
        warnings = []
        if not paragraphs:
            with zipfile.ZipFile(path) as z:
                if not any(n.startswith("Index/") and n.endswith(".iwa") for n in z.namelist()):
                    raise AdapterFailure(pb.AdapterError.CODE_UNSUPPORTED_FORMAT,
                                         "This Pages file uses an older format (Pages '09 or earlier). "
                                         "Open it in Pages and save it again.")
        progress(0.6, "Making slides")
        stem = path.stem if not req.original_filename else pathlib.Path(req.original_filename).stem
        return build_result(paragraphs_to_items(paragraphs), options, stem, req.output_dir, warnings)
    finally:
        for d in temp_dirs:
            shutil.rmtree(d, ignore_errors=True)


def _zip_package(folder, temp_dirs):
    """Repack a folder-style .pages package (optionally holding Index.zip) as a
    single-file archive with the same entry names."""
    import tempfile
    tmp = tempfile.mkdtemp(prefix="pages_pkg")
    temp_dirs.append(tmp)
    out = pathlib.Path(tmp) / "package.pages"
    with zipfile.ZipFile(out, "w") as z:
        for f in sorted(folder.rglob("*")):
            if not f.is_file():
                continue
            rel = f.relative_to(folder).as_posix()
            if rel == "Index.zip":
                with zipfile.ZipFile(f) as inner:
                    for name in inner.namelist():
                        z.writestr(name, inner.read(name))
            else:
                z.write(f, rel)
    return out


if __name__ == "__main__":
    serve(INFO, do_import)
