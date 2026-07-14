import pytest

import dokumente


def test_traversal_wird_auf_basename_reduziert():
    assert dokumente.saeubere_dateiname("../../etc/passwd.pdf") == "passwd.pdf"
    assert dokumente.saeubere_dateiname("/abs/pfad/marisk.pdf") == "marisk.pdf"
    assert dokumente.saeubere_dateiname("C:\\temp\\eba.pdf") == "eba.pdf"


def test_sonderzeichen_werden_ersetzt():
    assert dokumente.saeubere_dateiname("EBA ICT-Guidelines (2019).pdf") == "EBA_ICT-Guidelines__2019_.pdf"


def test_nicht_pdf_wird_abgelehnt():
    with pytest.raises(dokumente.UploadFehler) as e:
        dokumente.saeubere_dateiname("schaden.exe")
    assert e.value.status == 400


def test_leerer_name_wird_abgelehnt():
    with pytest.raises(dokumente.UploadFehler):
        dokumente.saeubere_dateiname(".pdf")


def test_pruefe_pdf_akzeptiert_magic():
    dokumente.pruefe_pdf(b"%PDF-1.7\n...")


def test_pruefe_pdf_lehnt_fremde_bytes_ab():
    with pytest.raises(dokumente.UploadFehler) as e:
        dokumente.pruefe_pdf(b"PK\x03\x04zip")
    assert e.value.status == 400


def test_pruefe_pdf_lehnt_uebergroesse_ab(monkeypatch):
    monkeypatch.setattr(dokumente, "MAX_MB", 0.001)
    with pytest.raises(dokumente.UploadFehler) as e:
        dokumente.pruefe_pdf(b"%PDF-" + b"x" * 2000)
    assert e.value.status == 413


def _schreibe(tmp_path, name, md, quelle=None):
    pfad = tmp_path / name
    pfad.write_text(md, encoding="utf-8")
    if quelle is not None:
        pfad.with_suffix(".source.json").write_text(quelle, encoding="utf-8")
    return pfad


def test_fingerprint_erfasst_sidecar(tmp_path):
    pfad = _schreibe(tmp_path, "a.md", "text", '{"titel": "A"}')
    vorher = dokumente.fingerprint([pfad], "bge-m3", "cosine")

    pfad.with_suffix(".source.json").write_text('{"titel": "B"}', encoding="utf-8")
    nachher = dokumente.fingerprint([pfad], "bge-m3", "cosine")

    assert vorher["dokumente"]["a.md"] != nachher["dokumente"]["a.md"]


def test_diff_erkennt_neue_datei():
    alt = {"embedding_modell": "m", "metrik": "cosine", "dokumente": {"a.md": "1"}}
    neu = {"embedding_modell": "m", "metrik": "cosine", "dokumente": {"a.md": "1", "b.md": "2"}}

    assert dokumente.diff(alt, neu) == (["b.md"], [], False)


def test_diff_erkennt_geaenderte_datei():
    alt = {"embedding_modell": "m", "metrik": "cosine", "dokumente": {"a.md": "1"}}
    neu = {"embedding_modell": "m", "metrik": "cosine", "dokumente": {"a.md": "2"}}

    assert dokumente.diff(alt, neu) == (["a.md"], ["a.md"], False)


def test_diff_erkennt_geloeschte_datei():
    alt = {"embedding_modell": "m", "metrik": "cosine", "dokumente": {"a.md": "1", "b.md": "2"}}
    neu = {"embedding_modell": "m", "metrik": "cosine", "dokumente": {"a.md": "1"}}

    assert dokumente.diff(alt, neu) == ([], ["b.md"], False)


def test_modellwechsel_erzwingt_voll_rebuild():
    alt = {"embedding_modell": "alt", "metrik": "cosine", "dokumente": {"a.md": "1"}}
    neu = {"embedding_modell": "neu", "metrik": "cosine", "dokumente": {"a.md": "1"}}

    zu_indexieren, zu_loeschen, voll = dokumente.diff(alt, neu)
    assert voll and zu_indexieren == ["a.md"] and zu_loeschen == []


def test_leerer_fingerprint_indexiert_alles():
    neu = {"embedding_modell": "m", "metrik": "cosine", "dokumente": {"a.md": "1"}}

    assert dokumente.diff({}, neu) == (["a.md"], [], True)


def test_fingerprint_aus_altem_release_erzwingt_voll_rebuild():
    """Vor #4 war 'dokumente' ein einzelner Hash über den ganzen Korpus, kein Dict."""
    alt = {"embedding_modell": "m", "metrik": "cosine", "dokumente": "ein-hash-ueber-alles"}
    neu = {"embedding_modell": "m", "metrik": "cosine", "dokumente": {"a.md": "1"}}

    assert dokumente.diff(alt, neu) == (["a.md"], [], True)


def test_md_name_traversal_wird_auf_basename_reduziert():
    assert dokumente.saeubere_md_name("../../etc/passwd.md") == "passwd.md"
    assert dokumente.saeubere_md_name("/abs/pfad/marisk.md") == "marisk.md"
    assert dokumente.saeubere_md_name("C:\\temp\\eba.md") == "eba.md"


def test_md_name_sonderzeichen_werden_ersetzt():
    assert dokumente.saeubere_md_name("EBA ICT-Guidelines (2019).md") == "EBA_ICT-Guidelines__2019_.md"


def test_md_name_ohne_md_endung_wird_abgelehnt():
    with pytest.raises(dokumente.UploadFehler) as e:
        dokumente.saeubere_md_name("fingerprint.json")
    assert e.value.status == 400


def test_md_name_ohne_stamm_wird_abgelehnt():
    with pytest.raises(dokumente.UploadFehler):
        dokumente.saeubere_md_name(".md")


def test_ohne_dokument_entfernt_nur_den_gesuchten_eintrag():
    fp = {"embedding_modell": "m", "metrik": "cosine", "dokumente": {"a.md": "1", "b.md": "2"}}

    ergebnis = dokumente.ohne_dokument(fp, "a.md")

    assert ergebnis["dokumente"] == {"b.md": "2"}
    assert ergebnis["embedding_modell"] == "m"
    assert ergebnis["metrik"] == "cosine"


def test_ohne_dokument_unbekannter_name_ist_kein_fehler():
    fp = {"embedding_modell": "m", "metrik": "cosine", "dokumente": {"a.md": "1"}}

    ergebnis = dokumente.ohne_dokument(fp, "unbekannt.md")

    assert ergebnis["dokumente"] == {"a.md": "1"}
