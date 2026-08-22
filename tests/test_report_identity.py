"""The document's own identity: a reference, an issue date, a way to re-obtain it.

Issue #205 asks for four things under "identity and traceability of the document
itself". Three were already there -- page n/N, the data cut-off on page 1, the
citable record identifier. This is the fourth: a reference for the PIECE OF
PAPER, so a professional can name it in a letter, and a third party can tell an
altered copy from a genuine one.

The tests that matter here are about what the reference PROMISES. It is only
worth printing if the same facts always produce it and different facts never do
-- otherwise comparing two copies proves nothing, and a reference that proves
nothing is worse than none, because a reader believes it.
"""
import copy
import datetime
import os
import sys

import pytest

HERE = os.path.dirname(__file__)
SRC = open(os.path.join(HERE, "..", "api", "main.py"), encoding="utf-8").read()

sys.path.insert(0, os.path.join(HERE, "..", "api"))
os.environ.setdefault("PG_DSN", "postgresql://unused@127.0.0.1:1/unused")
os.environ.setdefault("OPS_DSN", "postgresql://unused@127.0.0.1:1/unused")
try:                                    # no connection is opened at import time
    import main as m                    # -- the pool is built in the lifespan
except Exception as exc:                # pragma: no cover - environment, not logic
    pytest.skip(f"api/main.py not importable here: {exc}", allow_module_level=True)


def _fn(name):
    body = SRC.split(f"def {name}(")[1].split("\ndef ")[0]
    if '"""' in body:
        head, _, rest = body.partition('"""')
        body = head + rest.partition('"""')[2]
    return body


def _version(nom, vf, vt, **kw):
    v = {"nom": nom, "valid_from": vf, "valid_to": vt, "parents": [], "children": [],
         "source": "insee-cog", "vintage": datetime.date(2026, 1, 1),
         "approx": False, "rings": [[(4.0, 46.0), (4.1, 46.0), (4.1, 46.1),
                                     (4.0, 46.1), (4.0, 46.0)]],
         "unit_type": "commune", "neighbours": [], "gained": [],
         "gained_undrawable": []}
    v.update(kw)
    return v


def bundle(lang="en"):
    """A report's facts without a database: the renderers take only this."""
    return {
        "code": "01187", "country": "FR", "lang": lang,
        "versions": [
            _version("Hotonnes", datetime.date(1943, 1, 1), datetime.date(2016, 1, 1)),
            _version("Haut Valromey", datetime.date(2016, 1, 1), m.FAR_FUTURE,
                     parents=["01176", "01187", "01330"]),
        ],
        "locator": None, "district": None,
        "facts": {"area": {"km2": 121.83, "prev_km2": 107.90, "delta_pct": 12.9,
                           "approx": False},
                  "stability": {"never_changed": False, "since": "2016-01-01"},
                  "formed_from": [{"nom": "Hotonnes", "from": "1943-01-01",
                                   "to": "2016-01-01"}],
                  "neighbours": ["Ruffieu", "Songieu"], "declined": []},
        "cutoff": "2026-08-14",
        "uid": "x7k3m9qp",
        "source_annex": [{"source": "insee-cog", "attribution": "INSEE",
                          "license": "Licence Ouverte 2.0", "url": "https://insee.fr",
                          "vintages": ["2026-01-01"], "gap": None}],
        "events": [{"date": datetime.date(2016, 1, 1), "type": "merged",
                    "detail": "Hotonnes → Haut Valromey"}],
        "bbox": (4.0, 46.0, 4.1, 46.1),
        "population": None,
        "attributions": [("INSEE", "Licence Ouverte 2.0")],
    }


def labels(lang="en"):
    return m.REPORT_LABELS[lang]


# --------------------------------------------------------------- the promise

def test_the_same_facts_produce_the_same_reference():
    """Asked twice, answered identically -- or two genuine copies would accuse
    each other."""
    d, lab = bundle(), labels()
    assert m.report_digest(d, lab) == m.report_digest(copy.deepcopy(d), lab)
    assert m.document_ref(d, lab)["ref"] == m.document_ref(d, lab)["ref"]


def test_the_day_it_was_printed_never_enters_the_reference():
    """Otherwise a reader comparing a March printout with today's copy would see
    a mismatch and conclude, wrongly, that the document was tampered with."""
    fn = _fn("report_digest")
    assert "date.today" not in fn and "time.time" not in fn
    assert "issued" not in fn


def test_changing_a_stated_fact_changes_the_reference():
    d, lab = bundle(), labels()
    before = m.report_digest(d, lab)
    altered = copy.deepcopy(d)
    altered["facts"]["area"]["km2"] = 121.84      # 1 are, the smallest edit there is
    assert m.report_digest(altered, lab) != before


def test_a_dropped_source_changes_the_reference():
    """Provenance is the product. A copy with the annex quietly shortened must
    not keep the reference of the one that carried it."""
    d, lab = bundle(), labels()
    thinner = copy.deepcopy(d)
    thinner["source_annex"] = []
    assert m.report_digest(thinner, lab) != m.report_digest(d, lab)


def test_the_reference_carries_the_edition_so_two_vintages_are_two_documents():
    """A reader must see the vintage without reading the page: the same commune
    from two ingestions is two documents, not two copies of one."""
    d, lab = bundle(), labels()
    assert "20260814" in m.document_ref(d, lab)["ref"]
    later = copy.deepcopy(d)
    later["cutoff"] = "2026-09-01"
    assert m.document_ref(later, lab)["ref"] != m.document_ref(d, lab)["ref"]


def test_the_language_is_part_of_the_document_and_of_its_reference():
    """The French and English reports state the same facts in different words,
    so they are different documents and must not share one reference."""
    en, fr = bundle("en"), bundle("fr")
    assert (m.document_ref(en, labels("en"))["ref"]
            != m.document_ref(fr, labels("fr"))["ref"])
    assert "lang=fr" in m.document_ref(fr, labels("fr"))["verify"]


def test_an_unreachable_identifier_register_still_yields_a_reference():
    """`unit_uid` returns None rather than raising when the ops database is
    down. A report with no reference at all would help nobody, so the subject
    falls back to what the title already says."""
    d, lab = bundle(), labels()
    d["uid"] = None
    ref = m.document_ref(d, lab)["ref"]
    assert ref.startswith("CFN-")
    assert "fr01187" in ref


# ------------------------------------------------------- on the document itself

def _page_text(d):
    """What the PDF actually draws, page by page, read off the canvas."""
    from reportlab.pdfgen import canvas as pdf_canvas
    drawn = []
    original = pdf_canvas.Canvas.drawString
    def spy(self, x, y, text, *a, **kw):
        drawn.append((self._pageNumber, y, text))
        return original(self, x, y, text, *a, **kw)
    pdf_canvas.Canvas.drawString = spy
    try:
        m._report_pdf(d)
    finally:
        pdf_canvas.Canvas.drawString = original
    return drawn


def test_the_reference_is_on_page_one_under_the_cut_off_date():
    """Both dates on page 1, in this order: how current the DATA is, then when
    the DOCUMENT was issued. A reader who confuses the two draws the wrong
    conclusion about what is missing from it."""
    d, lab = bundle(), labels()
    drawn = _page_text(d)
    page1 = [(y, t) for p, y, t in drawn if p == 1]
    ref = m.document_ref(d, lab)["ref"]
    cutoff = [y for y, t in page1 if t.startswith("Situation as known on")]
    line = [y for y, t in page1 if ref in t]
    summary = [y for y, t in page1 if t == m.numbered(d, lab, lab["summary"])]
    assert cutoff and line and summary, "all three must be on page 1"
    assert cutoff[0] > line[0] > summary[0], "cut-off, then reference, then the answer"


def test_the_reference_does_not_collide_with_the_summary_beneath_it():
    """Adding a line to page 1 is where a report starts overprinting itself."""
    d = bundle()
    page1 = [(round(y), t) for p, y, t in _page_text(d) if p == 1]
    ref_y = [y for y, t in page1 if t.startswith("Reference CFN-")][0]
    assert not [t for y, t in page1 if y == ref_y and not t.startswith("Reference")]


def test_the_svg_and_the_pdf_print_the_same_reference():
    """Two renderers, one document. They have disagreed before."""
    d, lab = bundle(), labels()
    ref = m.document_ref(d, lab)["ref"]
    assert ref in m._report_svg(d)
    assert ref in [t for _, _, t in _page_text(d) if ref in t][0]


def test_the_citation_section_says_how_to_re_obtain_it():
    """A reference nobody can act on is decoration."""
    d, lab = bundle(), labels()
    rows = dict(m.citation_block(d, lab))
    assert rows[lab["doc_verify"]].startswith("https://")
    assert "/report.pdf" in rows[lab["doc_verify"]]
    assert m.document_ref(d, lab)["ref"] in rows[lab["doc_ref"]]


def test_the_document_states_what_the_digest_does_not_cover():
    """It covers the facts, never the typesetting -- and the PDF cannot hash its
    own bytes, since it carries the digest inside itself. Saying so is the
    difference between a claim we keep and one we do not."""
    assert "not the typesetting" in labels("en")["doc_note"]
    assert "non sur la mise en page" in labels("fr")["doc_note"]


def test_both_languages_carry_the_identity_labels():
    for key in ("doc_line", "doc_ref", "doc_verify", "doc_note"):
        for lang in ("en", "fr"):
            assert labels(lang).get(key), f"{key} missing in {lang}"
