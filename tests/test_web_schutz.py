import asyncio
import json

import httpx
import pytest

from web.schutz import ChatSchutz, Drossel

MAX_BYTES = 100


class Uhr:
    def __init__(self, jetzt=1000.0):
        self.jetzt = jetzt

    def __call__(self):
        return self.jetzt


class Ziel:
    def __init__(self):
        self.koerper = []

    async def __call__(self, scope, receive, send):
        teile = []
        while True:
            nachricht = await receive()
            teile.append(nachricht.get("body", b""))
            if not nachricht.get("more_body"):
                break
        self.koerper.append(b"".join(teile))
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


def _anfragen(app, *anfragen):
    async def lauf():
        transport = httpx.ASGITransport(app=app, client=("10.0.0.1", 1234))
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            return [await c.request(*a, **k) for a, k in anfragen]

    return asyncio.run(lauf())


def anfrage(*args, **kwargs):
    return args, kwargs


@pytest.fixture
def aufbau():
    uhr = Uhr()
    ziel = Ziel()
    drossel = Drossel(max_anfragen=2, fenster_sekunden=60, uhr=uhr)
    return ziel, uhr, ChatSchutz(ziel, pfad="/chat", drossel=drossel, max_bytes=MAX_BYTES)


def test_drossel_laesst_bis_zur_grenze_durch_und_nennt_dann_die_wartezeit():
    uhr = Uhr(1000.0)
    drossel = Drossel(max_anfragen=2, fenster_sekunden=60, uhr=uhr)

    assert drossel.pruefe("a") == 0
    assert drossel.pruefe("a") == 0
    assert drossel.pruefe("a") == pytest.approx(20.0)
    assert drossel.pruefe("b") == 0


def test_drossel_gibt_im_neuen_fenster_wieder_frei():
    uhr = Uhr(1000.0)
    drossel = Drossel(max_anfragen=1, fenster_sekunden=60, uhr=uhr)
    drossel.pruefe("a")
    assert drossel.pruefe("a") > 0

    uhr.jetzt = 1020.0

    assert drossel.pruefe("a") == 0


def test_drossel_vergisst_alte_fenster():
    uhr = Uhr(1000.0)
    drossel = Drossel(max_anfragen=1, fenster_sekunden=60, uhr=uhr)
    for i in range(50):
        drossel.pruefe(f"10.0.0.{i}")

    uhr.jetzt = 1100.0
    drossel.pruefe("neu")

    assert drossel.anzahl_schluessel() == 1


@pytest.mark.parametrize("max_anfragen", [0, -1])
def test_drossel_verlangt_mindestens_eine_anfrage(max_anfragen):
    with pytest.raises(ValueError):
        Drossel(max_anfragen=max_anfragen)


def test_dritte_anfrage_im_fenster_bekommt_429_mit_retry_after(aufbau):
    ziel, _, app = aufbau

    antworten = _anfragen(app, *[anfrage("POST", "/chat", json={"frage": "x"})] * 3)

    assert [a.status_code for a in antworten] == [200, 200, 429]
    assert antworten[2].headers["retry-after"] == "20"
    assert "Anfragen" in antworten[2].json()["detail"]
    assert len(ziel.koerper) == 2


def test_andere_pfade_sind_weder_gedrosselt_noch_begrenzt(aufbau):
    ziel, _, app = aufbau

    antworten = _anfragen(app, *[anfrage("POST", "/upload", content=b"x" * (MAX_BYTES * 10))] * 3)

    assert [a.status_code for a in antworten] == [200, 200, 200]
    assert len(ziel.koerper[0]) == MAX_BYTES * 10


def test_zu_grosser_body_laut_content_length_wird_vor_dem_lesen_abgelehnt(aufbau):
    ziel, _, app = aufbau

    (antwort,) = _anfragen(app, anfrage("POST", "/chat", content=b"x" * (MAX_BYTES + 1)))

    assert antwort.status_code == 413
    assert "Bytes" in antwort.json()["detail"]
    assert ziel.koerper == []


def test_zu_grosser_body_ohne_content_length_wird_beim_lesen_abgebrochen(aufbau):
    ziel, _, app = aufbau
    gelesen = []

    async def stroeme():
        for _ in range(10):
            gelesen.append(1)
            yield b"x" * (MAX_BYTES // 2)

    (antwort,) = _anfragen(app, anfrage("POST", "/chat", content=stroeme()))

    assert antwort.status_code == 413
    assert ziel.koerper == []
    assert len(gelesen) < 10


def test_body_an_der_grenze_kommt_unveraendert_an(aufbau):
    ziel, _, app = aufbau
    koerper = json.dumps({"frage": "ä" * 40}).encode()[:MAX_BYTES].ljust(MAX_BYTES, b" ")

    (antwort,) = _anfragen(app, anfrage("POST", "/chat", content=koerper))

    assert antwort.status_code == 200
    assert ziel.koerper == [koerper]
