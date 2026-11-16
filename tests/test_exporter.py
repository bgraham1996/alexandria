"""Offline unit tests for the exporter's note-reuse logic."""

from pyzotero.errors import ResourceNotFoundError

from alexandria.exporter import build_note, plan_note

TITLE = "Dead Zones Enable Flexible Phase Organization"
FILENAME = TITLE + ".md"


class FakeZot:
    def __init__(self, live=(), trashed=(), broken=False):
        self.live, self.trashed, self.broken = set(live), set(trashed), broken

    def item(self, key):
        if self.broken:
            raise RuntimeError("network down")
        if key in self.trashed:
            return {"data": {"key": key, "deleted": 1}}
        if key in self.live:
            return {"data": {"key": key}}
        raise ResourceNotFoundError("gone")


def item(key, doi="10.1/abc", title=TITLE):
    return {"key": key, "title": title, "DOI": doi, "itemType": "journalArticle"}


def write_note(tmp_path, name, data):
    path = tmp_path / name
    path.write_text(build_note(data, vault_name="V"))
    return path


def test_new_paper_creates_plain_filename(tmp_path):
    assert plan_note(tmp_path, FILENAME, item("NEWKEY01"), FakeZot()) == (
        "create", tmp_path / FILENAME, "")


def test_same_key_is_left_alone(tmp_path):
    note = write_note(tmp_path, FILENAME, item("AAAAAAAA"))
    assert plan_note(tmp_path, FILENAME, item("AAAAAAAA"), FakeZot()) == (
        "exists", note, "AAAAAAAA")


def test_reimported_paper_relinks_instead_of_numbering(tmp_path):
    note = write_note(tmp_path, FILENAME, item("OLDKEY01"))
    assert plan_note(tmp_path, FILENAME, item("NEWKEY01"), FakeZot()) == (
        "relink", note, "OLDKEY01")


def test_doi_case_does_not_matter(tmp_path):
    note = write_note(tmp_path, FILENAME, item("OLDKEY01", doi="10.7554/eLife.1"))
    action, path, _ = plan_note(
        tmp_path, FILENAME, item("NEWKEY01", doi="10.7554/elife.1"), FakeZot())
    assert (action, path) == ("relink", note)


def test_trashed_item_counts_as_gone(tmp_path):
    write_note(tmp_path, FILENAME, item("OLDKEY01"))
    zot = FakeZot(trashed={"OLDKEY01"})
    assert plan_note(tmp_path, FILENAME, item("NEWKEY01"), zot)[0] == "relink"


def test_duplicate_live_item_keeps_existing_note(tmp_path):
    note = write_note(tmp_path, FILENAME, item("OLDKEY01"))
    zot = FakeZot(live={"OLDKEY01"})
    assert plan_note(tmp_path, FILENAME, item("NEWKEY01"), zot) == (
        "exists", note, "OLDKEY01")


def test_api_failure_never_relinks(tmp_path):
    write_note(tmp_path, FILENAME, item("OLDKEY01"))
    zot = FakeZot(broken=True)
    assert plan_note(tmp_path, FILENAME, item("NEWKEY01"), zot)[0] == "exists"


def test_different_paper_with_same_title_gets_numbered_file(tmp_path):
    write_note(tmp_path, FILENAME, item("OLDKEY01", doi="10.1/preprint"))
    assert plan_note(tmp_path, FILENAME, item("NEWKEY01", doi="10.1/published"), FakeZot()) == (
        "create", tmp_path / f"{TITLE} (2).md", "")


def test_finds_matching_note_under_a_numbered_name(tmp_path):
    write_note(tmp_path, FILENAME, item("OLDKEY01", doi="10.1/preprint"))
    numbered = write_note(tmp_path, f"{TITLE} (2).md", item("OLDKEY02", doi="10.1/published"))
    assert plan_note(tmp_path, FILENAME, item("NEWKEY01", doi="10.1/published"), FakeZot()) == (
        "relink", numbered, "OLDKEY02")


def test_non_paper_file_with_same_name_is_not_touched(tmp_path):
    (tmp_path / FILENAME).write_text("# my own note\n")
    assert plan_note(tmp_path, FILENAME, item("NEWKEY01"), FakeZot()) == (
        "create", tmp_path / f"{TITLE} (2).md", "")
