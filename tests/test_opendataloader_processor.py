"""OpenDataLoader configuration, execution, and canonical mapping contracts."""

import json
import tomllib
from datetime import date
from pathlib import Path

import pytest
from document_ocr.config import GlmOcrConfig, OpenDataLoaderConfig, load_arxiv_config
from document_ocr.identity import request_id
from document_ocr.processors import opendataloader
from document_ocr.processors.opendataloader import (
    OpenDataLoaderError,
    canonical_elements,
    load_page_markdown,
    run_upstream,
)
from document_ocr.protocol import OcrDocumentRequest, OpenDataLoaderJob, parse_document_job


def _job() -> OpenDataLoaderJob:
    processor = load_arxiv_config().pipeline("opendataloader")
    assert isinstance(processor, OpenDataLoaderConfig)
    source_hash = "a" * 64
    request_key = request_id(
        document_id="2607.00001",
        source_record_sha256=source_hash,
        configuration_hash=processor.configuration_hash,
    )
    request = OcrDocumentRequest(
        request_id=request_key,
        document_id="2607.00001",
        source_updated_date=date(2026, 7, 29),
        source_record_sha256=source_hash,
        pdf_url="https://arxiv.org/pdf/2607.00001",
    )
    return processor.build_job(request, attempt=1)


def test_arxiv_configuration_selects_opendataloader_by_default() -> None:
    config = load_arxiv_config()

    assert config.default_pipeline == "opendataloader"
    assert set(config.pipelines) == {"opendataloader", "glm_ocr"}
    assert isinstance(config.pipeline("opendataloader"), OpenDataLoaderConfig)
    assert isinstance(config.pipeline("glm_ocr"), GlmOcrConfig)


def test_yaml_adapter_version_matches_the_locked_worker_package() -> None:
    processor = load_arxiv_config().pipeline("opendataloader")
    assert isinstance(processor, OpenDataLoaderConfig)
    project = tomllib.loads(Path("ocr/pyproject.toml").read_text(encoding="utf-8"))
    worker_dependencies = project["project"]["optional-dependencies"]["worker"]

    assert f"opendataloader-pdf=={processor.adapter_version}" in worker_dependencies


def test_opendataloader_job_round_trips_through_discriminated_protocol() -> None:
    job = _job()

    decoded = parse_document_job(job.model_dump_json())

    assert isinstance(decoded, OpenDataLoaderJob)
    assert decoded.options.reading_order == "xycut"


def test_opendataloader_sdk_receives_the_pinned_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def convert_pdf(**options: object) -> None:
        captured.update(options)
        output = Path(str(options["output_dir"]))
        (output / "source.json").write_text("{}", encoding="utf-8")
        (output / "source.md").write_text("", encoding="utf-8")

    monkeypatch.setattr(opendataloader, "convert_pdf", convert_pdf)
    raw = tmp_path / "raw"
    run_upstream(_job(), tmp_path / "source.pdf", raw, log=lambda _: None)

    assert captured["format"] == ["json", "markdown"]
    assert captured["quiet"] is True
    assert captured["markdown_with_html"] is True
    assert captured["image_output"] == "external"
    assert captured["image_dir"] == str(raw / "imgs")
    assert captured["threads"] == "1"
    assert captured["hybrid"] == "off"
    assert "content_safety_off" not in captured


def test_schema_shaped_output_maps_table_and_bbox_without_vendor_leakage() -> None:
    nodes = [
        {
            "type": "heading",
            "id": 1,
            "page number": 1,
            "bounding box": [72.0, 700.0, 540.0, 730.0],
            "font": "Helvetica-Bold",
            "font size": 24.0,
            "text color": "[0.0]",
            "content": "A sufficiently descriptive scientific paper heading",
            "heading level": 1,
        },
        {
            "type": "table",
            "id": 2,
            "page number": 1,
            "bounding box": [72.0, 400.0, 540.0, 650.0],
            "number of rows": 1,
            "number of columns": 1,
            "rows": [
                {
                    "type": "table row",
                    "row number": 1,
                    "cells": [
                        {
                            "type": "table cell",
                            "id": 3,
                            "page number": 1,
                            "bounding box": [72.0, 400.0, 540.0, 650.0],
                            "row number": 1,
                            "column number": 1,
                            "row span": 2,
                            "column span": 1,
                            "is_header": True,
                            "kids": [
                                {
                                    "type": "paragraph",
                                    "id": 4,
                                    "page number": 1,
                                    "bounding box": [80.0, 420.0, 500.0, 620.0],
                                    "font": "Helvetica",
                                    "font size": 10.0,
                                    "text color": "[0.0]",
                                    "content": "Table value",
                                }
                            ],
                        }
                    ],
                }
            ],
        },
        {
            "type": "image",
            "page number": 1,
            "bounding box": [72.0, 200.0, 300.0, 350.0],
            "source": "imgs/imageFile1.png",
            "format": "png",
            "alt": "Scientific [figure]",
            "alt_source": "original",
        },
    ]

    elements = canonical_elements(
        nodes,
        current_processing_id="b" * 64,
        page_sizes=((612.0, 792.0),),
    )

    assert [element.element_type for element in elements] == ["heading", "table", "image"]
    assert all(element.markdown_content is None for element in elements)
    assert json.loads(elements[1].bbox_json or "null") == [72.0, 400.0, 540.0, 650.0]
    assert "rows" not in json.loads(elements[1].raw_attributes_json or "{}")
    assert json.loads(elements[2].raw_attributes_json or "{}")["asset_path"] == (
        "imgs/imageFile1.png"
    )


def test_official_markdown_preserves_html_table_and_asset_semantics(
    tmp_path: Path,
) -> None:
    job = _job()
    marker = job.run_id
    markdown = tmp_path / "source.md"
    markdown.write_text(
        f"<!-- mini-lakehouse-page-{marker}:1 -->\n\n"
        '<table>\n<tr>\n<th rowspan="2">Header</th>\n'
        '<td colspan="3"><p>Line<br>More</p>'
        '<ul style="list-style-type:none;"><li>Item</li></ul>'
        '<ol style="list-style-type:none;"><li>First</li></ol></td>\n'
        "</tr>\n</table>\n\n"
        "![](\u003cimgs/imageFile1.png\u003e)\n\n"
        f"<!-- mini-lakehouse-page-{marker}:2 -->\n\n"
        "$$\nx^2\n$$\n",
        encoding="utf-8",
    )

    bundle = load_page_markdown(
        markdown,
        expected_pages=2,
        run_id=job.run_id,
        max_bytes=1024 * 1024,
    )

    assert '<th rowspan="2">Header</th>' in bundle.markdown(1)
    assert '<td colspan="3"><p>Line<br>More</p>' in bundle.markdown(1)
    assert '<ul style="list-style-type:none;"><li>Item</li></ul>' in bundle.markdown(1)
    assert '<ol style="list-style-type:none;"><li>First</li></ol>' in bundle.markdown(1)
    assert "![](<imgs/imageFile1.png>)" in bundle.markdown(1)
    assert bundle.markdown(2) == "$$\nx^2\n$$"


def test_out_of_page_bbox_fails_closed() -> None:
    with pytest.raises(OpenDataLoaderError, match="outside the page"):
        canonical_elements(
            [
                {
                    "type": "paragraph",
                    "page number": 1,
                    "bounding box": [0, 0, 900, 900],
                    "content": "This content is long enough to pass the native text quality gate.",
                }
            ],
            current_processing_id="b" * 64,
            page_sizes=((612.0, 792.0),),
        )
