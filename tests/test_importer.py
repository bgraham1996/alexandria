"""Offline unit tests for the importer (no network, no Zotero)."""

from alexandria import importer
from alexandria.importer import extract_doi, find_existing_item


# --- extract_doi -----------------------------------------------------------

def test_article_doi_beats_table_and_figure_components():
    text = (
        "Citation: ... PLoS ONE 2(3): e308. doi:10.1371/journal.pone.0000308\n"
        "doi:10.1371/journal.pone.0000308.t001\n"
        "doi:10.1371/journal.pone.0000308.g001\n"
    )
    assert extract_doi(text) == "10.1371/journal.pone.0000308"


def test_component_rule_does_not_shorten_a_complete_doi():
    # A line-wrapped stub of a longer DOI must not make the full DOI look
    # like a "component" of the stub.
    text = "https://doi.org/10.1016/j.cell.2020\n10.1016/j.cell.2020.01.001\n"
    assert extract_doi(text) == "10.1016/j.cell.2020.01.001"


def test_publisher_stub_still_loses():
    text = "www.pnas.org/cgi/doi/10.1073/pnas.\n10.1073/pnas.2023123118\n"
    assert extract_doi(text) == "10.1073/pnas.2023123118"


# --- find_existing_item ----------------------------------------------------

class FakeZot:
    def __init__(self, items, children=None):
        self._items = items
        self._children = children or {}
        self.queries = []

    def top(self, q=None, limit=None):
        self.queries.append(q)
        return [i for i in self._items if q.lower() in i["data"]["title"].lower()]

    def children(self, key):
        return self._children.get(key, [])


def _item(key, title, doi="", added="2026-01-01T00:00:00Z"):
    return {"data": {"key": key, "itemType": "journalArticle", "title": title,
                     "DOI": doi, "dateAdded": added}}


def _pdf(md5):
    return {"data": {"key": "ATT", "itemType": "attachment",
                     "linkMode": "imported_file",
                     "contentType": "application/pdf", "md5": md5}}


NEW = {"title": "Tracing the spread of Celtic languages using ancient genomics",
       "DOI": "10.1038/S41586-026-11198-9"}


def test_finds_existing_by_doi_via_title_search():
    zot = FakeZot([_item("AAA", NEW["title"], "10.1038/s41586-026-11198-9")])
    assert find_existing_item(zot, NEW)["data"]["key"] == "AAA"
    # the search must be on the title, since the API's q ignores the DOI field
    assert "10.1038" not in zot.queries[0]


def test_same_title_different_doi_is_not_a_duplicate():
    zot = FakeZot([_item("AAA", NEW["title"], "10.1101/preprint.1")])
    assert find_existing_item(zot, NEW) is None


def test_title_match_when_existing_has_no_doi():
    zot = FakeZot([_item("AAA", NEW["title"].upper() + ".")])
    assert find_existing_item(zot, NEW)["data"]["key"] == "AAA"


def test_prefers_copy_with_stored_pdf_else_oldest():
    doi = NEW["DOI"].lower()
    items = [_item("NEWER", NEW["title"], doi, "2026-11-14T00:00:00Z"),
             _item("OLDER", NEW["title"], doi, "2026-11-04T00:00:00Z")]
    assert find_existing_item(FakeZot(items), NEW)["data"]["key"] == "OLDER"
    zot = FakeZot(items, {"NEWER": [_pdf("abc")], "OLDER": [_pdf(None)]})
    assert find_existing_item(zot, NEW)["data"]["key"] == "NEWER"


def test_untitled_never_matches():
    zot = FakeZot([_item("AAA", "Untitled")])
    assert find_existing_item(zot, {"title": "Untitled", "DOI": ""}) is None


# --- _attach_pdf -----------------------------------------------------------

class UploadZot(FakeZot):
    def __init__(self, children, fail=None):
        super().__init__([], children)
        self.calls = []
        self.fail = fail

    def upload_attachments(self, payload):
        self.calls.append(("placeholder", payload[0]["key"]))
        self._children["P"] = [_pdf("abc")]

    def attachment_simple(self, files, parent):
        self.calls.append(("new", parent))
        if self.fail:
            raise RuntimeError(self.fail)
        self._children[parent] = [_pdf("abc")]


def test_attach_reuses_empty_placeholder():
    zot = UploadZot({"P": [_pdf(None)]})
    assert importer._attach_pdf(zot, "P", "/x/a.pdf") == (True, "")
    assert zot.calls == [("placeholder", "ATT")]


def test_attach_creates_new_attachment_when_no_placeholder():
    zot = UploadZot({})
    assert importer._attach_pdf(zot, "P", "/x/a.pdf") == (True, "")
    assert zot.calls == [("new", "P")]


def test_attach_reports_quota_numbers():
    zot = UploadZot({}, fail="Code: 413\nFile would exceed quota (2004.7 &gt; 2000)")
    ok, err = importer._attach_pdf(zot, "P", "/x/a.pdf")
    assert not ok
    assert err == "Zotero storage quota exceeded (would need 2004.7 MB of 2000 MB)"
