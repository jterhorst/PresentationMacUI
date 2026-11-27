#!/usr/bin/env python3
"""First-party import adapter: Word documents (.docx).

Reader ported from pp7gen (Apache-2.0, WorshipKit); see docx_reader.py.
"""
import pathlib
import shutil
import zipfile

import docx_reader
from importer_common import (PROTOCOL_VERSION, AdapterFailure, build_result, paragraphs_to_items,
                             pb, progress, serve)

INFO = pb.AdapterInfo(
    protocol_version=PROTOCOL_VERSION,
    adapter_id="core.office-import/docx",
    version="1.0.0",
    file_types=[pb.FileType(
        uttype="org.openxmlformats.wordprocessingml.document", extensions=["docx"],
        mime_types=["application/vnd.openxmlformats-officedocument.wordprocessingml.document"])],
    supports_multiple_documents=True,
)


def do_import(req, options):
    temp_dirs = []
    try:
        progress(0.1, "Reading document")
        try:
            paragraphs = docx_reader.parse_paragraphs(req.input_path, temp_dirs)
        except docx_reader.NotADocx:
            if _encrypted(req.input_path):
                raise AdapterFailure(pb.AdapterError.CODE_PASSWORD_PROTECTED,
                                     "This document is password protected.")
            raise AdapterFailure(pb.AdapterError.CODE_CORRUPT_FILE,
                                 "This file isn't a valid Word document (.docx).")
        warnings = []
        if _has_body_tables(req.input_path):
            warnings.append(pb.ImportWarning(severity=pb.ImportWarning.SEVERITY_WARNING, document_index=-1,
                                             message="Tables were skipped."))
        progress(0.6, "Making slides")
        stem = pathlib.Path(req.original_filename or req.input_path).stem
        return build_result(paragraphs_to_items(paragraphs), options, stem, req.output_dir, warnings)
    finally:
        for d in temp_dirs:
            shutil.rmtree(d, ignore_errors=True)


def _encrypted(path):
    # Password-protected Office files are OLE compound files, not zips.
    with open(path, "rb") as f:
        return f.read(8) == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def _has_body_tables(path):
    try:
        with zipfile.ZipFile(path) as z:
            return b"<w:tbl>" in z.read("word/document.xml") or b"<w:tbl " in z.read("word/document.xml")
    except Exception:
        return False


if __name__ == "__main__":
    serve(INFO, do_import)
