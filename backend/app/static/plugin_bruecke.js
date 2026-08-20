/**
 * Die Bruecke eines Plugins zu Traccoon.
 *
 * Ein Plugin laeuft in einem iframe ohne `allow-same-origin`. Es hat damit keine Herkunft,
 * keinen Zugriff auf den angemeldeten Menschen und keinen auf die API — `fetch` waere hier
 * ohnehin durch die CSP (`connect-src 'none'`) gesperrt. Alles, was es an Daten braucht,
 * erfragt es beim Wirt, und der gibt nur heraus, was ein Admin freigegeben hat.
 *
 * Einbinden:
 *
 *     <script src="/api/plugins/_bruecke.js"></script>
 *     const reihen = await traccoon.reihen("location");
 */
(function () {
  var offen = new Map();
  var zaehler = 0;
  var WARTEZEIT = 30000;

  window.addEventListener("message", function (e) {
    var d = e.data;
    if (!d || d.quelle !== "traccoon" || !offen.has(d.id)) return;
    var eintrag = offen.get(d.id);
    offen.delete(d.id);
    clearTimeout(eintrag.uhr);
    if (d.ok) eintrag.fertig(d.daten);
    else eintrag.fehler(new Error(d.fehler || "abgelehnt"));
  });

  /**
   * Eine Frage an den Wirt.
   *
   * Gesendet wird an `*`, weil das Plugin die Adresse des Wirts nicht kennen kann — seine
   * eigene Herkunft ist `null`, `document.referrer` bleibt leer. Das ist unbedenklich: Die
   * Nachricht enthaelt nur die Frage, und der Wirt prueft seinerseits, dass sie aus genau
   * diesem iframe kommt.
   */
  function frage(ruf, args) {
    return new Promise(function (fertig, fehler) {
      var id = ++zaehler;
      var uhr = setTimeout(function () {
        if (!offen.has(id)) return;
        offen.delete(id);
        fehler(new Error("Traccoon antwortet nicht"));
      }, WARTEZEIT);
      offen.set(id, { fertig: fertig, fehler: fehler, uhr: uhr });
      parent.postMessage({ quelle: "plugin", id: id, ruf: ruf, args: args || {} }, "*");
    });
  }

  window.traccoon = {
    /** Wer gerade angemeldet ist (Name, Sprache, Zeitzone) — ohne Mailadresse. */
    ich: function () { return frage("ich"); },
    /** Reihen einer Art, die dieser Mensch sehen darf. */
    reihen: function (art) { return frage("reihen.liste", { art: art }); },
    /** Punkte einer Reihe. `opt`: {art, von, bis, grenze}. */
    punkte: function (key, opt) {
      var a = { key: key, art: (opt && opt.art) || "location" };
      if (opt) { a.von = opt.von; a.bis = opt.bis; a.grenze = opt.grenze; }
      return frage("reihen.punkte", a);
    },
    /** Der letzte Stand aller sichtbaren Reihen einer Art. */
    live: function (art) { return frage("reihen.live", { art: art || "location" }); },
    /** Die benannten Orte (Geozaeune) dieses Menschen. */
    orte: function () { return frage("orte.liste"); },
    /** Eigene Ablage des Plugins (Tabellen aus dem Manifest). */
    ablage: {
      lesen: function (tabelle) { return frage("ablage.lesen", { tabelle: tabelle }); },
      schreiben: function (tabelle, zeile) {
        return frage("ablage.schreiben", { tabelle: tabelle, zeile: zeile });
      },
      loeschen: function (tabelle, id) {
        return frage("ablage.loeschen", { tabelle: tabelle, id: id });
      },
    },
    /** Fuer alles, was die Bequemlichkeiten oben nicht abdecken. */
    ruf: frage,
  };

  // Dem Wirt sagen, dass die Seite steht. Er antwortet mit der Sprache und den Farben, damit
  // ein Plugin nicht raten muss, ob es hell oder dunkel gezeichnet werden soll.
  parent.postMessage({ quelle: "plugin", bereit: true }, "*");
})();
