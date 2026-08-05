// Der filmer — ein nackter `node:http`-Server, mehr braucht es nicht.
//
// Kein Framework, weil es nichts zu routen gibt: zwei Pfade, ein Verb je Pfad. Jede
// Abhängigkeit hier wäre eine `package.json`, ein Lockfile und ein npm-Schritt im Bild — für
// dreißig Zeilen, die seit Node 0.x unverändert funktionieren.
//
// **Keine Authentifizierungsfläche, weil es nichts zu schützen gibt.** Der Dienst holt nichts,
// kennt keine Zugangsdaten und keine Datenbank; das Backend schickt den Log mit und bekommt ein
// Bild zurück. Er hängt am internen Compose-Netz und veröffentlicht keinen Port.

import { createServer } from "node:http";
import { baueFilm } from "./film.mjs";

const PORT = Number(process.env.FILMER_PORT ?? 8710);

/** Ein Tag mit 20 000 Ereignissen ist rund 12 MB JSON; 64 MB lassen genug Luft und verhindern
 *  trotzdem, dass ein kaputter Aufrufer den Speicher des Sidecars füllt. */
const MAX_BODY = 64 * 1024 * 1024;

const server = createServer((req, res) => {
  if (req.method === "GET" && req.url === "/healthz") {
    res.writeHead(200, { "Content-Type": "text/plain; charset=utf-8" });
    res.end("ok");
    return;
  }
  if (req.method !== "POST" || (req.url ?? "").split("?")[0] !== "/film") {
    res.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" });
    res.end("nicht gefunden");
    return;
  }

  const teile = [];
  let laenge = 0;
  let abgebrochen = false;
  req.on("data", (c) => {
    if (abgebrochen) return;
    laenge += c.length;
    if (laenge > MAX_BODY) {
      abgebrochen = true;
      res.writeHead(413, { "Content-Type": "text/plain; charset=utf-8" });
      res.end("Auftrag zu groß");
      req.destroy();
      return;
    }
    teile.push(c);
  });
  req.on("end", () => {
    if (abgebrochen) return;
    let auftrag;
    try {
      auftrag = JSON.parse(Buffer.concat(teile).toString("utf8"));
    } catch (e) {
      res.writeHead(400, { "Content-Type": "text/plain; charset=utf-8" });
      res.end("kein gültiges JSON: " + fehlerText(e));
      return;
    }

    let film;
    try {
      film = baueFilm(auftrag);
    } catch (e) {
      // Der Fehlertext geht an das Backend, nicht an einen Nutzer: er landet im Job-Log, und
      // ohne ihn stünde dort nur „500" für einen Tag, der sich nie wiederholt.
      console.error("[filmer] Aufbau gescheitert:", e);
      res.writeHead(500, { "Content-Type": "text/plain; charset=utf-8" });
      res.end("Film gescheitert: " + fehlerText(e));
      return;
    }

    // Ein Tag ohne Ereignisse ist kein Fehler. Python schreibt dann die Nachricht ohne Medium
    // („Heute war es still im Büro") — ein leeres GIF wäre die schlechtere Antwort.
    if (film === null) {
      res.writeHead(204);
      res.end();
      return;
    }

    const body = Buffer.from(film.bytes.buffer, film.bytes.byteOffset, film.bytes.byteLength);
    res.writeHead(200, {
      "Content-Type": "image/gif",
      "Content-Length": String(body.length),
      "X-Film-Kapitel": String(film.kapitel),
      "X-Film-Inseln": String(film.inseln),
      "X-Film-Bilder": String(film.bilder),
      "X-Film-Gekappt": film.gekappt ? "1" : "0",
      "X-Film-Dauer-Ms": String(film.dauerMs),
    });
    res.end(body);
    console.log(`[filmer] ${film.bilder} Bilder, ${film.kapitel}/${film.inseln} Szenen, `
      + `${body.length} B, ${film.dauerMs} ms`);
  });
});

function fehlerText(e) {
  return e && e.message ? String(e.message) : String(e);
}

server.listen(PORT, () => {
  console.log(`[filmer] hört auf ${PORT}`);
});
