"""Regression tests for the console's file-upload import handlers. NiceGUI 3.x replaced the old
`event.content.read()` upload API with `event.file` (async `.read()`/`.text()`); using the wrong
one made both importers die with an uncaught AttributeError and no toast. These drive the extracted
adapters with a fake FileUpload so that mismatch can't return silently."""

from conductor.mappings import MappingStore
from conductor.store import ConfigStore
from conductor.ui.pages import _apply_bundle_upload, _apply_targets_upload


class FakeUpload:
    """Stands in for NiceGUI's FileUpload — only the async read/text surface the handlers use."""

    def __init__(self, data: bytes) -> None:
        self._data = data

    async def read(self) -> bytes:
        return self._data

    async def text(self, encoding: str = "utf-8") -> str:
        return self._data.decode(encoding)


async def test_apply_targets_upload_imports(mappings: MappingStore) -> None:
    await mappings.set_project("p1", enabled=True, webhook_secret="whsec")
    await mappings.set_repo("p1", "ui", github_repo="octo/ui")
    text = await mappings.export_targets("dem")
    await mappings.delete_project("p1")

    imported = await _apply_targets_upload(mappings, FakeUpload(text.encode()))

    assert imported == 1
    projects = await mappings.list_projects()
    assert [p.plane_project_id for p in projects] == ["p1"]
    assert [r.key for r in projects[0].repos] == ["ui"]
    assert await mappings.get_webhook_secret("p1") == "whsec"


async def test_apply_bundle_upload_imports(store: ConfigStore) -> None:
    await store.set_setting("plane_workspace_slug", "dem")
    blob = await store.export_bundle("pass123")

    imported = await _apply_bundle_upload(store, FakeUpload(blob), "pass123")

    assert imported >= 1
    assert (await store.resolved()).get("plane_workspace_slug") == "dem"
