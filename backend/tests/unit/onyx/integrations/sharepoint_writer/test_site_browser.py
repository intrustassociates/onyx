from __future__ import annotations

from unittest.mock import patch

from onyx.integrations.sharepoint_writer.site_browser import get_drive_root_id
from onyx.integrations.sharepoint_writer.site_browser import list_drives
from onyx.integrations.sharepoint_writer.site_browser import list_folders
from onyx.integrations.sharepoint_writer.site_browser import list_sites


class _FakeTokenProvider:
    def acquire(self) -> str:
        return "tok"

    graph_host = "https://graph.microsoft.com"
    graph_base = "https://graph.microsoft.com/v1.0"


def test_list_sites_returns_typed_refs() -> None:
    payload = {
        "value": [
            {
                "id": "tenant,site-1,web-1",
                "displayName": "Marketing",
                "webUrl": "https://t.sharepoint.com/sites/marketing",
            },
            {
                "id": "tenant,site-2,web-2",
                "name": "legal",
                "webUrl": "https://t.sharepoint.com/sites/legal",
            },
        ]
    }
    with patch(
        "onyx.integrations.sharepoint_writer.site_browser.graph_get",
        return_value=payload,
    ):
        sites = list_sites(_FakeTokenProvider())
        assert [s.display_name for s in sites] == ["Marketing", "legal"]
        assert sites[0].id == "tenant,site-1,web-1"


def test_list_sites_skips_entries_without_id() -> None:
    payload = {
        "value": [{"displayName": "no id here"}, {"id": "x", "displayName": "ok"}]
    }
    with patch(
        "onyx.integrations.sharepoint_writer.site_browser.graph_get",
        return_value=payload,
    ):
        sites = list_sites(_FakeTokenProvider())
        assert len(sites) == 1
        assert sites[0].id == "x"


def test_list_drives_returns_typed_refs() -> None:
    payload = {
        "value": [
            {"id": "drive-1", "name": "Documents", "webUrl": "https://t.../docs"},
            {"id": "drive-2", "name": "Other"},
        ]
    }
    with patch(
        "onyx.integrations.sharepoint_writer.site_browser.graph_get",
        return_value=payload,
    ):
        drives = list_drives(_FakeTokenProvider(), site_id="site-1")
        assert [d.name for d in drives] == ["Documents", "Other"]


def test_list_folders_filters_files() -> None:
    """Even with $filter applied server-side, defensively re-check on the
    client in case the API returns mixed types."""
    items = [
        {"id": "f1", "name": "Folder A", "folder": {"childCount": 2}},
        {"id": "x1", "name": "file.txt"},  # no "folder" key — should be skipped
        {"id": "f2", "name": "Folder B", "folder": {"childCount": 0}},
    ]
    with patch(
        "onyx.integrations.sharepoint_writer.site_browser.graph_get_all_pages",
        return_value=items,
    ):
        folders = list_folders(_FakeTokenProvider(), drive_id="drive-1")
        assert [f.name for f in folders] == ["Folder A", "Folder B"]


def test_list_folders_uses_root_alias_when_root() -> None:
    with patch(
        "onyx.integrations.sharepoint_writer.site_browser.graph_get_all_pages",
        return_value=[],
    ) as mock_pages:
        list_folders(_FakeTokenProvider(), drive_id="drive-1")
        called_url = mock_pages.call_args.args[0]
        assert called_url.endswith("/drives/drive-1/root/children")


def test_list_folders_uses_item_path_when_given_parent_id() -> None:
    with patch(
        "onyx.integrations.sharepoint_writer.site_browser.graph_get_all_pages",
        return_value=[],
    ) as mock_pages:
        list_folders(_FakeTokenProvider(), drive_id="drive-1", parent_id="01ABC")
        called_url = mock_pages.call_args.args[0]
        assert called_url.endswith("/drives/drive-1/items/01ABC/children")


def test_get_drive_root_id_extracts_id() -> None:
    with patch(
        "onyx.integrations.sharepoint_writer.site_browser.graph_get",
        return_value={"id": "root-item-id-xyz"},
    ):
        assert get_drive_root_id(_FakeTokenProvider(), "drive-1") == "root-item-id-xyz"
