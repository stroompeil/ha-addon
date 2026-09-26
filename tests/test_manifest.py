"""Tests for the integration manifest (`manifest.json`).

Catches packaging mistakes before the zip is built: required fields, a valid
HACS/HASS domain, `requirements` being a list, and the version matching
`custom_components/.../manifest.json`.
"""
from __future__ import annotations

import json
from pathlib import Path

from custom_components.stroompeil_ha_addon.const import DOMAIN

REPO_ROOT = Path(__file__).resolve().parents[1]
CC_MANIFEST = REPO_ROOT / "custom_components" / "stroompeil_ha_addon" / "manifest.json"
ROOT_MANIFEST = REPO_ROOT / "manifest.json"

REQUIRED_FIELDS = {
    "domain",
    "name",
    "documentation",
    "issue_tracker",
    "codeowners",
    "config_flow",
    "requirements",
    "version",
    "homeassistant",
    "iot_class",
    "integration_type",
}


def _load(path: Path) -> dict:
    return json.loads(path.read_text())


def test_cc_manifest_has_all_required_fields():
    manifest = _load(CC_MANIFEST)

    missing = REQUIRED_FIELDS - manifest.keys()
    assert not missing, f"manifest missing fields: {missing}"


def test_cc_manifest_domain_matches_const():
    manifest = _load(CC_MANIFEST)
    assert manifest["domain"] == DOMAIN


def test_cc_manifest_requirements_is_a_list_of_strings():
    manifest = _load(CC_MANIFEST)
    assert isinstance(manifest["requirements"], list)
    for req in manifest["requirements"]:
        assert isinstance(req, str)


def test_cc_manifest_codeowners_non_empty():
    manifest = _load(CC_MANIFEST)
    assert manifest["codeowners"], "codeowners must not be empty"
    for owner in manifest["codeowners"]:
        assert owner.startswith("@")


def test_cc_manifest_config_flow_enabled():
    manifest = _load(CC_MANIFEST)
    assert manifest["config_flow"] is True


def test_root_manifest_version_matches_cc_manifest():
    # hacs.json says content_in_root; the root manifest.json is the HACS entry.
    root = _load(ROOT_MANIFEST)
    cc = _load(CC_MANIFEST)

    assert root["version"] == cc["version"]
    assert root["domain"] == cc["domain"]
