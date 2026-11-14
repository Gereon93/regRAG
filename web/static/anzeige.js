(function (wurzel) {
  const STATUS = {
    gueltig: { text: 'gültig', klasse: 'ok' },
    unbekannt: { text: 'Gültigkeit unbekannt', klasse: '' },
    noch_nicht_gueltig: { text: 'noch nicht gültig', klasse: 'warnung' },
    abgelaufen: { text: 'abgelaufen', klasse: 'warnung' },
    metadaten_ungueltig: { text: 'Angaben ungültig — nicht durchsuchbar', klasse: 'warnung' },
  };
  const FELDNAMEN = { gueltig_ab: 'Gültig ab', gueltig_bis: 'Gültig bis', fassung: 'Fassung' };

  function datumDe(iso) {
    const [jahr, monat, tag] = iso.split('-');
    return `${tag}.${monat}.${jahr}`;
  }

  function gueltigkeitsAngaben(d) {
    const status = STATUS[d.gueltigkeit] || { text: String(d.gueltigkeit), klasse: 'warnung' };
    const zeitraum = d.gueltig_ab || d.gueltig_bis
      ? `${d.gueltig_ab ? datumDe(d.gueltig_ab) : '…'} – ${d.gueltig_bis ? datumDe(d.gueltig_bis) : '…'}`
      : null;
    return { status, zeitraum, fassung: d.fassung ? 'Fassung: ' + d.fassung : null };
  }

  function uploadFehler(status, detail) {
    if (typeof detail !== 'string') return `Upload abgelehnt (HTTP ${status}).`;
    if (status !== 422) return detail;
    const lesbar = detail.replace(/gueltig_ab|gueltig_bis|fassung/g, (feld) => `„${FELDNAMEN[feld]}“`);
    return 'Gültigkeit abgelehnt: ' + lesbar + ' Bitte korrigieren und die PDF erneut wählen.';
  }

  function gesetzteFelder(werte) {
    return Object.keys(FELDNAMEN).filter((name) => werte[name]).map((name) => [name, werte[name]]);
  }

  const api = { gueltigkeitsAngaben, uploadFehler, gesetzteFelder };
  const inNode = typeof module === 'object' && module.exports;
  if (inNode) module.exports = api;
  else wurzel.Anzeige = api;
})(this);
