"""Tests for local Home Assistant branding assets."""

import json
from pathlib import Path
import struct


ROOT = Path(__file__).parents[1]
INTEGRATION = ROOT / "custom_components" / "homeassistantedupage"


def _png_size(path: Path) -> tuple[int, int]:
    """Read a PNG's dimensions directly from its IHDR chunk."""
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert data[12:16] == b"IHDR"
    return struct.unpack(">II", data[16:24])


def test_brand_icons_have_expected_dimensions():
    """Home Assistant receives normal and high-density square icons."""
    assert _png_size(INTEGRATION / "brand" / "icon.png") == (256, 256)
    assert _png_size(INTEGRATION / "brand" / "icon@2x.png") == (512, 512)


def test_vector_brand_source_is_kept_in_repository():
    """The editable source remains available for future branding changes."""
    source = ROOT / "img" / "edupage-integration-icon.svg"
    assert source.is_file()
    assert "viewBox=\"0 0 512 512\"" in source.read_text(encoding="utf-8")


def test_integration_uses_readable_display_name():
    """Manifest and HACS show the same concise integration name."""
    manifest = json.loads((INTEGRATION / "manifest.json").read_text())
    hacs = json.loads((ROOT / "hacs.json").read_text())

    assert manifest["domain"] == "homeassistantedupage"
    assert manifest["name"] == "EduPage"
    assert hacs["name"] == "EduPage"
