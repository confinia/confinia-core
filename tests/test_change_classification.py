"""What counts as a change (issues #167, #169).

Both fixtures are real, and both were measured against the production API before
these tests were written — they are not invented edge cases.
"""
import os
import re
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..")


def _load():
    """Pull the pure helpers out of api/main.py without importing FastAPI."""
    src = open(os.path.join(ROOT, "api", "main.py"), encoding="utf-8").read()
    ns = {"BOUNDARY_NOISE_PCT": float(
        re.search(r"^BOUNDARY_NOISE_PCT = ([\d.]+)", src, re.M).group(1))}
    for fn in ("_norm_name", "name_delta", "_ring_area_km2", "boundary_delta"):
        m = re.search(rf"^def {fn}\(.*?(?=^def |\Z)", src, re.S | re.M)
        assert m, f"{fn} not found in api/main.py"
        exec(m.group(0), ns)
    return ns


def _square(lon, lat, side):
    return {"type": "Polygon", "coordinates": [[
        [lon, lat], [lon + side, lat], [lon + side, lat + side],
        [lon, lat + side], [lon, lat]]]}


def test_a_deleted_space_is_not_a_renaming():
    """09472116 Bad Berneck: the whole difference between two BKG vintages.

    `prev["nom"] != p["nom"]` called this a rename, so Confinia minted a version
    — and charged a report for it — on one space character.
    """
    ns = _load()
    d = ns["name_delta"]("Bad Berneck i. Fichtelgebirge", "Bad Berneck i.Fichtelgebirge")
    assert d is not None, "the difference is real and must be reported"
    assert d["kind"] == "respelled", "a space is typography, not an authority renaming"
    assert d["removed"] == [" "] and d["added"] == [], \
        "the reader must be told exactly what changed, since it is invisible"


def test_a_real_renaming_is_still_a_renaming():
    """01028 Labastida alternates between the Spanish and Basque forms."""
    ns = _load()
    d = ns["name_delta"]("Labastida", "Labastida / Bastida")
    assert d["kind"] == "renamed"
    assert "".join(d["added"]).strip().endswith("Bastida")


def test_normalisation_does_not_swallow_a_different_name():
    ns = _load()
    for before, after in (("Sainte-Marie", "Sainte-Anne"), ("Le Havre", "Havre"),
                          ("Colmar", "Kolmar")):
        assert ns["name_delta"](before, after)["kind"] == "renamed", \
            f"{before} -> {after} is a real change and must survive normalisation"


def test_identical_names_produce_no_event():
    assert _load()["name_delta"]("Paris", "Paris") is None


def test_redigitisation_is_not_a_boundary_change():
    """The measured noise, made a fixture.

    Labastida's area moves 0.115 % across five INE vintages and Bad Berneck's
    0.022 % across two BKG ones, as the same outline is re-digitised. Drawing a
    panel per version told the reader the boundary moved; it did not.
    """
    ns = _load()
    base = _square(-2.8, 42.6, 0.09)
    for pct in (0.115, 0.022, -0.115, 0.0):
        grown = _square(-2.8, 42.6, 0.09 * (1 + pct / 100) ** 0.5)
        d = ns["boundary_delta"](base, grown)
        assert d is not None
        assert not d["changed"], f"{pct}% is vintage noise, not a boundary change"


def test_a_real_boundary_change_is_reported():
    ns = _load()
    base = _square(-2.8, 42.6, 0.09)
    bigger = _square(-2.8, 42.6, 0.09 * (1.13) ** 0.5)      # +13 %
    d = ns["boundary_delta"](base, bigger)
    assert d["changed"], "a 13% change is a real event"
    assert d["area_delta_pct"] > 10


def test_a_missing_geometry_is_unknown_and_never_unchanged():
    """The distinction the founder's rule depends on.

    'We measured that it did not move' and 'we cannot tell' must not collapse:
    hiding a boundary on the second would assert something never checked.
    """
    ns = _load()
    assert ns["boundary_delta"](None, _square(-2.8, 42.6, 0.09)) is None
    assert ns["boundary_delta"](_square(-2.8, 42.6, 0.09), None) is None
    assert ns["boundary_delta"](None, None) is None


def test_the_page_draws_nothing_when_the_boundary_did_not_move():
    """The founder's decision, checked where it is implemented.

    'if the change is only on town name, and not in borders, then don't expose
    its borders' — so consecutive versions sharing a boundary must collapse into
    one card, and an UNKNOWN boundary must still be drawn.
    """
    page = open(os.path.join(ROOT, "deploy", "site", "commune.html"),
                encoding="utf-8").read()
    assert "draw_boundary === false" in page, \
        "the page must group versions that share a boundary into one card"
    assert "d.versions.map(f =>" not in page.split('getElementById("grid")')[1][:200], \
        "one card per version is the rendering this issue removes"

    api = open(os.path.join(ROOT, "api", "main.py"), encoding="utf-8").read()
    m = re.search(r'"draw_boundary": (.+?),?\n', api)
    assert m and "bd is None" in m.group(1), \
        "an unknown boundary must still be drawn, never hidden as if measured"


def _report_versions(pcts):
    """Report-shaped versions: plain rings, one per area delta (in %)."""
    out = []
    for i, pct in enumerate(pcts):
        side = 0.09 * (1 + pct / 100) ** 0.5
        out.append({"nom": f"v{i}", "rings": [[
            [0, 42.6], [side, 42.6], [side, 42.6 + side], [0, 42.6 + side], [0, 42.6]]]})
    return out


def test_the_report_groups_boundaries_like_the_page_does():
    """The PDF drew Bad Berneck's deleted space as two identical maps.

    The first fix annotated the API's GeoJSON features and claimed the report
    read them. It did not: the report builds its OWN version list, with plain
    rings and no geometry on those features, so every boundary read as unknown
    and every panel was drawn. A customer paid for a two-page report whose
    second page repeated the first.
    """
    ns = _load()
    src = open(os.path.join(ROOT, "api", "main.py"), encoding="utf-8").read()
    for fn in ("_rings_area_km2", "boundary_groups"):
        m = re.search(rf"^def {fn}\(.*?(?=^def |\Z)", src, re.S | re.M)
        assert m, f"{fn} missing"
        exec(m.group(0), ns)

    # Labastida's five measured vintages
    g = ns["boundary_groups"](_report_versions([0, -0.0144, 0.1294, -0.0113, -0.0188]))
    assert len(g) == 1, f"five vintages of one boundary must be one panel, got {len(g)}"

    # Bad Berneck's two
    assert len(ns["boundary_groups"](_report_versions([0, 0.0224]))) == 1

    # and a real change still separates
    assert len(ns["boundary_groups"](_report_versions([0, 13.0]))) == 2


def test_the_report_canvas_is_sized_on_panels_not_versions():
    """Sizing on the version count leaves a blank half-page per removed panel."""
    src = open(os.path.join(ROOT, "api", "main.py"), encoding="utf-8").read()
    assert "((len(groups) + 1) // 2) * CELL_H" in src, \
        "the SVG height must follow the panels actually drawn"


def test_both_report_renderers_use_the_grouping():
    """SVG and PDF must not disagree about what the report shows."""
    src = open(os.path.join(ROOT, "api", "main.py"), encoding="utf-8").read()
    assert src.count("boundary_groups(d[\"versions\"])") == 2, \
        "both the SVG and the PDF panel loops must iterate groups"
    assert "for i, v in enumerate(d[\"versions\"])" not in src, \
        "a renderer still loops versions and will draw a panel per version"


def _notes():
    ns = _load()
    src = open(os.path.join(ROOT, "api", "main.py"), encoding="utf-8").read()
    exec(re.search(r"^def change_note\(.*?(?=^def |\Z)", src, re.S | re.M).group(0), ns)
    return ns


def test_the_report_says_what_changed_in_words():
    """The founder read the 2023 line and had to ask what changed.

    "Bad Berneck i. Fichtelgebirge → Bad Berneck i.Fichtelgebirge" is two
    identical-looking strings. The report must state the difference, not print
    it and hope.
    """
    ns = _notes()
    nd = ns["name_delta"]("Bad Berneck i. Fichtelgebirge", "Bad Berneck i.Fichtelgebirge")
    fr, en = ns["change_note"](nd, "fr"), ns["change_note"](nd, "en")
    assert "espace" in fr and "orthographe" in fr, fr
    assert "space" in en and "spelling" in en, en


def test_the_note_never_relies_on_a_glyph_the_pdf_font_lacks():
    """A substitution glyph would reproduce the bug one level down.

    The page can highlight a space with U+2423; the PDF is drawn in Helvetica,
    where it renders as a blank or a tofu box -- an invisible difference again.
    """
    ns = _notes()
    for before, after in (("a b", "ab"), ("a  b", "a b"), ("x", "x y")):
        nd = ns["name_delta"](before, after)
        for lang in ("fr", "en"):
            note = ns["change_note"](nd, lang)
            assert all(ord(ch) < 0x2000 or ch in "·—" for ch in note), \
                f"{note!r} carries a glyph the PDF font may not have"


def test_both_report_renderers_print_the_note():
    src = open(os.path.join(ROOT, "api", "main.py"), encoding="utf-8").read()
    assert src.count('ev["change_note"]') == 2, \
        "the SVG and the PDF chronology must both print the note"


def test_no_change_no_note():
    ns = _notes()
    assert ns["change_note"](None, "fr") is None


def test_the_payment_badge_comes_from_the_api_not_the_hostname():
    """A host-driven badge is silent in the one case that matters.

    If PRODUCTION were ever pointed at Polar sandbox, customers would "pay"
    while nothing is collected, on www, with every page looking normal. So the
    banner must reflect what the API reports about the Polar host it actually
    calls -- and the API must derive that from POLAR_API_BASE, not from an
    environment name.
    """
    api = open(os.path.join(ROOT, "api", "main.py"), encoding="utf-8").read()
    assert "def payment_mode()" in api
    fn = api.split("def payment_mode()")[1].split("\ndef ")[0]
    # Strip the docstring: it NAMES the things the code must not read.
    body = fn.split('"""')[2] if fn.count('"""') >= 2 else fn
    assert "POLAR_API_BASE" in body, "the mode must come from the Polar host in use"
    for smell in ("CONFINIA_ENV", "hostname", "POLAR_MODE"):
        assert smell not in body, f"payment_mode reads {smell}; that is intent, not fact"
    assert '"payment_mode": payment_mode()' in api, "/healthz must expose it"

    for page in ("account.html", "pricing.html", os.path.join("sbx", "account.html")):
        html = open(os.path.join(ROOT, "deploy", "site", page), encoding="utf-8").read()
        assert 'id="paymode"' in html, f"{page} has no payment banner"
        assert 'payment_mode === "sandbox"' in html, \
            f"{page} must show the banner only on an explicit sandbox answer"
        assert "location.hostname" not in html.split('id="paymode"')[1][:800], \
            f"{page} decides the banner from the hostname"


def _notes2():
    ns = _load()
    src = open(os.path.join(ROOT, "api", "main.py"), encoding="utf-8").read()
    for fn in ("_boundary_phrase", "change_note"):
        exec(re.search(rf"^def {fn}\(.*?(?=^def |\Z)", src, re.S | re.M).group(0), ns)
    return ns


def test_the_note_never_claims_the_boundary_held_when_it_moved():
    """The report asserted the opposite of what happened.

    Haut Valromey absorbed four communes on 2016-01-01 -- 107.9 -> 121.8 km² --
    and the chronology read "nom seul — limites inchangées", because the note
    was derived from the name alone. A false statement, in a document sold on
    per-fact provenance.
    """
    ns = _notes2()
    nd = ns["name_delta"]("Hotonnes", "Haut Valromey")
    note = ns["change_note"](nd, "fr", {"changed": True, "area_delta_pct": 12.91})
    assert "inchangées" not in note, note
    assert "agrandies" in note and "12,9" in note, note


def test_an_uncomparable_boundary_is_not_reported_as_unchanged():
    ns = _notes2()
    nd = ns["name_delta"]("Vendeuvre", "Vendeuvre-du-Poitou")
    note = ns["change_note"](nd, "fr", None)
    assert "non comparables" in note and "inchangées" not in note, note


def test_a_fragmented_diff_is_summarised_not_dissected():
    """difflib on a wholesale rename produces true, useless fragments.

    "Hotonnes" -> "Haut Valromey" gave `retiré tonn, s · ajouté aut_Valr, m, y`
    on the staged report: correct character-by-character, and it makes the
    document look broken. One clean insertion is worth showing; five stray
    letters are not.
    """
    ns = _notes2()
    messy = ns["change_note"](ns["name_delta"]("Hotonnes", "Haut Valromey"), "fr",
                              {"changed": True, "area_delta_pct": 12.9})
    assert "tonn" not in messy and "aut_Valr" not in messy, messy

    clean = ns["change_note"](ns["name_delta"]("Labastida", "Labastida / Bastida"), "fr",
                              {"changed": False, "area_delta_pct": 0.01})
    assert "Bastida" in clean, "one clean insertion must still be shown"


def test_the_french_phrase_is_grammatical():
    """"limites agrandie 12.9 %" loses a notaire before the number is read."""
    ns = _notes2()
    fr = ns["_boundary_phrase"](12.91, True)
    assert fr == "limites agrandies de 12,9 %", fr
    assert ns["_boundary_phrase"](-3.5, True) == "limites réduites de 3,5 %"
    assert ns["_boundary_phrase"](12.91, False) == "boundary grew by 12.9%"
