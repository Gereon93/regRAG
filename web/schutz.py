import json
import math
import time


class Drossel:
    """Festes Zeitfenster je Schlüssel, nur im Prozess — docs/adr/0016."""

    def __init__(self, max_anfragen, fenster_sekunden=60.0, uhr=time.monotonic):
        if max_anfragen < 1:
            raise ValueError("max_anfragen muss mindestens 1 sein.")
        self.max_anfragen = max_anfragen
        self.fenster_sekunden = fenster_sekunden
        self.uhr = uhr
        self._fenster = None
        self._zaehler = {}

    def pruefe(self, schluessel):
        jetzt = self.uhr()
        fenster = math.floor(jetzt / self.fenster_sekunden)
        if fenster != self._fenster:
            self._fenster = fenster
            self._zaehler.clear()
        anzahl = self._zaehler.get(schluessel, 0) + 1
        self._zaehler[schluessel] = anzahl
        if anzahl <= self.max_anfragen:
            return 0
        return (fenster + 1) * self.fenster_sekunden - jetzt

    def anzahl_schluessel(self):
        return len(self._zaehler)


async def _antworte(send, status, detail, kopfzeilen=()):
    koerper = json.dumps({"detail": detail}, ensure_ascii=False).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(koerper)).encode()),
                *kopfzeilen,
            ],
        }
    )
    await send({"type": "http.response.body", "body": koerper})


class ChatSchutz:
    """ASGI-Middleware: Rate-Limit je Client-IP und Body-Grenze vor dem Parsen — docs/adr/0016."""

    def __init__(self, app, pfad, drossel, max_bytes):
        self.app = app
        self.pfad = pfad
        self.drossel = drossel
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] != self.pfad:
            await self.app(scope, receive, send)
            return

        client = scope.get("client")
        wartezeit = self.drossel.pruefe(client[0] if client else "")
        if wartezeit:
            await _antworte(
                send,
                429,
                "Zu viele Anfragen — bitte kurz warten.",
                [(b"retry-after", str(max(1, math.ceil(wartezeit))).encode())],
            )
            return

        laenge = dict(scope["headers"]).get(b"content-length", b"")
        if laenge.isdigit() and int(laenge) > self.max_bytes:
            await self._zu_gross(send)
            return

        teile, gelesen = [], 0
        while True:
            nachricht = await receive()
            if nachricht["type"] == "http.disconnect":
                return
            teile.append(nachricht.get("body", b""))
            gelesen += len(teile[-1])
            if gelesen > self.max_bytes:
                await self._zu_gross(send)
                return
            if not nachricht.get("more_body"):
                break

        gepuffert = [{"type": "http.request", "body": b"".join(teile), "more_body": False}]

        async def wiedergabe():
            return gepuffert.pop() if gepuffert else await receive()

        await self.app(scope, wiedergabe, send)

    async def _zu_gross(self, send):
        await _antworte(send, 413, f"Anfrage zu groß (höchstens {self.max_bytes} Bytes).")
