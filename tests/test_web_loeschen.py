import sys
import threading
import time
import types

import pytest
from fastapi.testclient import TestClient

AUFRUFE = []


def _fake_loesche_dokument(md_name):
    AUFRUFE.append(md_name)
    import web.main as modul

    pfad = modul.DOKUMENTE / md_name
    pfad.unlink(missing_ok=True)
    pfad.with_suffix(".source.json").unlink(missing_ok=True)


def _stub(name, **attrs):
    modul = types.ModuleType(name)
    for schluessel, wert in attrs.items():
        setattr(modul, schluessel, wert)
    sys.modules[name] = modul


@pytest.fixture(scope="session")
def main():
    """Stubt rag/agent/convert unbedingt, damit `import web.main` nie die echten,
    schweren Module lädt — unabhängig davon, ob vorher schon etwas den echten `rag`
    importiert hat. Macht die Stubs danach wieder rückgängig."""
    stubs = {
        "rag": {
            "loesche_dokument": _fake_loesche_dokument,
            "loesche_nodes": lambda *a, **k: None,
            "indexiere": lambda *a, **k: None,
        },
        "agent": {
            "pruefe": lambda *a, **k: types.SimpleNamespace(verweigert=True, vorgemerkt=True),
            "llm": None,
            "prompt": None,
            "retriever": None,
        },
        "convert": {"pdf_nach_markdown": lambda *a, **k: None},
    }
    alte_module = {name: sys.modules.get(name) for name in stubs}
    for name, attrs in stubs.items():
        _stub(name, **attrs)

    import web.main as modul

    yield modul

    for name, alt in alte_module.items():
        if alt is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = alt


@pytest.fixture
def dokumente_verzeichnis(tmp_path, monkeypatch, main):
    monkeypatch.setattr(main, "DOKUMENTE", tmp_path)
    main.NAMEN_IN_ARBEIT.clear()
    AUFRUFE.clear()
    return tmp_path


@pytest.fixture
def client(main, dokumente_verzeichnis):
    return TestClient(main.app)


def _lege_dokument_an(verzeichnis, stamm, titel):
    (verzeichnis / f"{stamm}.md").write_text("Inhalt", encoding="utf-8")
    (verzeichnis / f"{stamm}.source.json").write_text(
        f'{{"titel": "{titel}"}}', encoding="utf-8"
    )


def test_loeschen_entfernt_dokument_und_ruft_rag_auf(client, dokumente_verzeichnis):
    _lege_dokument_an(dokumente_verzeichnis, "a", "A")

    antwort = client.delete("/documents/a.md")

    assert antwort.status_code == 204
    assert AUFRUFE == ["a.md"]
    assert client.get("/documents").json() == []


def test_traversal_mit_slashes_wird_von_routing_abgewiesen(
    client, dokumente_verzeichnis, tmp_path
):
    """URL-codierte Slashes (%2F) werden vom Routing abgewiesen, bevor die App-Logik lädt."""
    ausserhalb = tmp_path.parent / "passwd.md"
    ausserhalb.write_text("geheim", encoding="utf-8")

    antwort = client.delete("/documents/..%2F..%2Fetc%2Fpasswd.md")

    assert antwort.status_code == 404
    assert ausserhalb.exists()
    assert AUFRUFE == []


def test_traversal_mit_backslashes_wird_von_saeubere_md_name_blockiert(
    client, dokumente_verzeichnis, tmp_path
):
    """URL-codierte Backslashes (%5C) passieren das Routing und treffen saeubere_md_name."""
    ausserhalb = tmp_path.parent / "passwd.md"
    ausserhalb.write_text("geheim", encoding="utf-8")

    antwort = client.delete("/documents/..%5C..%5Cetc%5Cpasswd.md")

    assert antwort.status_code == 404
    assert antwort.json()["detail"] == "Dokument nicht gefunden."
    assert ausserhalb.exists()
    assert AUFRUFE == []


def test_upload_startet_keinen_job_waehrend_geloescht_wird(
    main, client, dokumente_verzeichnis, monkeypatch
):
    """Beide Pfade schreiben fingerprint.json — ein Upload darf sich nicht dazwischenschieben."""
    _lege_dokument_an(dokumente_verzeichnis, "a", "A")
    ablauf = []

    def langsam_loeschen(md_name):
        ablauf.append("loeschen-start")
        time.sleep(0.2)
        _fake_loesche_dokument(md_name)
        ablauf.append("loeschen-ende")

    class MitschreibenderPool:
        def submit(self, *args, **kwargs):
            ablauf.append("jobstart")

    monkeypatch.setattr(sys.modules["rag"], "loesche_dokument", langsam_loeschen)
    monkeypatch.setattr(main, "POOL", MitschreibenderPool())

    loescher = threading.Thread(target=lambda: client.delete("/documents/a.md"))
    loescher.start()
    time.sleep(0.05)

    antwort = client.post(
        "/upload", files={"datei": ("neu.pdf", b"%PDF-1.7 inhalt", "application/pdf")}
    )
    loescher.join()

    assert antwort.status_code == 202
    assert ablauf == ["loeschen-start", "loeschen-ende", "jobstart"]


def test_nicht_md_name_wird_abgelehnt(client, dokumente_verzeichnis):
    (dokumente_verzeichnis / "fingerprint.json").write_text("{}", encoding="utf-8")

    antwort = client.delete("/documents/fingerprint.json")

    assert antwort.status_code == 400
    assert (dokumente_verzeichnis / "fingerprint.json").exists()
    assert AUFRUFE == []


def test_bootstrap_marker_ist_nicht_loeschbar(client, dokumente_verzeichnis):
    (dokumente_verzeichnis / ".bootstrap").write_text("", encoding="utf-8")

    antwort = client.delete("/documents/.bootstrap")

    assert antwort.status_code == 400
    assert (dokumente_verzeichnis / ".bootstrap").exists()
    assert AUFRUFE == []


def test_unbekanntes_dokument_wird_mit_404_abgelehnt(client, dokumente_verzeichnis):
    antwort = client.delete("/documents/unbekannt.md")

    assert antwort.status_code == 404
    assert AUFRUFE == []


def test_loeschen_waehrend_laufender_indexierung_wird_abgelehnt(
    client, dokumente_verzeichnis, main
):
    _lege_dokument_an(dokumente_verzeichnis, "a", "A")
    main.NAMEN_IN_ARBEIT.add("anderes.pdf")

    antwort = client.delete("/documents/a.md")

    assert antwort.status_code == 409
    assert (dokumente_verzeichnis / "a.md").exists()
    assert AUFRUFE == []


def test_dokument_liste_liefert_datei_und_titel(client, dokumente_verzeichnis):
    _lege_dokument_an(dokumente_verzeichnis, "a", "A-Titel")
    _lege_dokument_an(dokumente_verzeichnis, "b", "B-Titel")

    antwort = client.get("/documents")

    assert antwort.status_code == 200
    assert antwort.json() == [
        {"datei": "a.md", "titel": "A-Titel"},
        {"datei": "b.md", "titel": "B-Titel"},
    ]


class ZaehlenderPool:
    def __init__(self):
        self.jobs = []

    def submit(self, *args):
        self.jobs.append(args)


PDF = {"datei": ("neu.pdf", b"%PDF-1.7 inhalt", "application/pdf")}


@pytest.fixture
def pool(main, monkeypatch, dokumente_verzeichnis):
    zaehler = ZaehlenderPool()
    monkeypatch.setattr(main, "POOL", zaehler)
    return zaehler


def test_upload_mit_kaputtem_datum_liefert_422_und_keinen_job(client, pool):
    antwort = client.post("/upload", files=PDF, data={"gueltig_ab": "2026-02-30"})

    assert antwort.status_code == 422
    assert "gueltig_ab" in antwort.json()["detail"]
    assert pool.jobs == []


def test_upload_prueft_felder_vor_dem_pdf(client, pool):
    kein_pdf = {"datei": ("neu.pdf", b"kein pdf", "application/pdf")}

    antwort = client.post("/upload", files=kein_pdf, data={"gueltig_bis": "kaputt"})

    assert antwort.status_code == 422


def test_upload_reicht_gueltige_felder_an_den_job(client, pool):
    daten = {"gueltig_ab": "2026-01-01", "gueltig_bis": "2026-12-31", "fassung": " 2. Fassung "}

    antwort = client.post("/upload", files=PDF, data=daten)

    assert antwort.status_code == 202
    assert pool.jobs[0][-1] == {"gueltig_ab": "2026-01-01", "gueltig_bis": "2026-12-31", "fassung": "2. Fassung"}


def test_upload_leere_formularfelder_sind_nicht_gesetzt(client, pool):
    antwort = client.post("/upload", files=PDF, data={"gueltig_ab": "", "fassung": ""})

    assert antwort.status_code == 202
    assert pool.jobs[0][-1] == {}


def test_upload_ignoriert_ein_segment_im_formular(client, pool):
    client.post("/upload", files=PDF, data={"segment": "gesetz"})

    assert "segment" not in pool.jobs[0][-1]


def test_job_reicht_metadaten_an_die_konvertierung(main, monkeypatch, dokumente_verzeichnis):
    gesehen = {}

    def konvertiere(pdf, ausgabe, metadaten=None):
        gesehen["metadaten"] = metadaten
        return ausgabe / "neu.md"

    monkeypatch.setattr(main, "pdf_nach_markdown", konvertiere)
    main.JOBS["j"] = {"status": "pending", "dateiname": "neu.pdf", "fehler": None}

    main._indexiere("j", b"%PDF", "neu.pdf", {"fassung": "2"})

    assert gesehen["metadaten"] == {"fassung": "2"}
    assert main.JOBS["j"]["status"] == "ready"
