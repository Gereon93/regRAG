const assert = require("node:assert/strict");
const test = require("node:test");

const { gueltigkeitsAngaben, uploadFehler, gesetzteFelder } = require("../../web/static/anzeige.js");

test("422 mit String-Detail ersetzt API-Feldnamen durch UI-Labels", () => {
  assert.equal(
    uploadFehler(422, "gueltig_bis liegt vor gueltig_ab."),
    "Gültigkeit abgelehnt: „Gültig bis“ liegt vor „Gültig ab“. Bitte korrigieren und die PDF erneut wählen.",
  );
  assert.match(uploadFehler(422, "fassung: erwartet 1 bis 100 Zeichen Text."), /^Gültigkeit abgelehnt: „Fassung“: /);
});

test("422 mit Listen-Detail (FastAPI-Validierung) zeigt keinen Objekttext", () => {
  assert.equal(uploadFehler(422, [{ loc: ["body", "datei"], msg: "Field required" }]), "Upload abgelehnt (HTTP 422).");
});

test("andere Status zeigen das Detail unverändert, ohne Detail den Status", () => {
  assert.equal(uploadFehler(409, "Dokument ist bereits indexiert."), "Dokument ist bereits indexiert.");
  assert.equal(uploadFehler(500, undefined), "Upload abgelehnt (HTTP 500).");
});

test("bekannte Status haben Text und Klasse", () => {
  assert.deepEqual(gueltigkeitsAngaben({ gueltigkeit: "gueltig" }).status, { text: "gültig", klasse: "ok" });
  assert.deepEqual(gueltigkeitsAngaben({ gueltigkeit: "unbekannt" }).status, { text: "Gültigkeit unbekannt", klasse: "" });
  for (const s of ["abgelaufen", "noch_nicht_gueltig", "metadaten_ungueltig"]) {
    assert.equal(gueltigkeitsAngaben({ gueltigkeit: s }).status.klasse, "warnung");
  }
});

test("unbekannter Status erscheint als Warnung mit Rohwert", () => {
  assert.deepEqual(gueltigkeitsAngaben({ gueltigkeit: "neu_im_backend" }).status, {
    text: "neu_im_backend",
    klasse: "warnung",
  });
});

test("Zeitraum in deutschem Format, offene Seite als Auslassung", () => {
  assert.equal(gueltigkeitsAngaben({ gueltig_ab: "2025-01-17", gueltig_bis: "2026-12-31" }).zeitraum, "17.01.2025 – 31.12.2026");
  assert.equal(gueltigkeitsAngaben({ gueltig_ab: "2025-01-17" }).zeitraum, "17.01.2025 – …");
  assert.equal(gueltigkeitsAngaben({ gueltig_bis: "2023-06-28" }).zeitraum, "… – 28.06.2023");
  assert.equal(gueltigkeitsAngaben({ gueltigkeit: "unbekannt" }).zeitraum, null);
});

test("Fassung nur, wenn gesetzt", () => {
  assert.equal(gueltigkeitsAngaben({ fassung: "2. Fassung" }).fassung, "Fassung: 2. Fassung");
  assert.equal(gueltigkeitsAngaben({}).fassung, null);
});

test("nur gesetzte Formularfelder werden verschickt, in fester Reihenfolge", () => {
  assert.deepEqual(gesetzteFelder({ fassung: "2", gueltig_ab: "", gueltig_bis: "2026-12-31" }), [
    ["gueltig_bis", "2026-12-31"],
    ["fassung", "2"],
  ]);
  assert.deepEqual(gesetzteFelder({ gueltig_ab: "", gueltig_bis: "", fassung: "" }), []);
});
