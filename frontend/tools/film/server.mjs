// The filmer: a bare `node:http` server, no more is needed.
//
// No framework, because there is nothing to route: two paths, one verb per path. Every
// dependency here would be a `package.json`, a lockfile and an npm step in the image, for
// thirty lines that have worked unchanged since Node 0.x.
//
// **No authentication surface, because there is nothing to protect.** The service fetches
// nothing, knows no credentials and no database; the backend sends the log along and gets a
// picture back. It hangs on the internal compose network and publishes no port.

import { createServer } from "node:http";
import { baueFilm } from "./film.mjs";

const PORT = Number(process.env.FILMER_PORT ?? 8710);

/** A day with 20 000 events is around 12 MB of JSON; 64 MB leave enough air and still prevent
 *  a broken caller from filling the memory of the sidecar. */
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
      // The error text goes to the backend, not to a user: it lands in the job log, and without
      // it only "500" would stand there for a day that never repeats.
      console.error("[filmer] Aufbau gescheitert:", e);
      res.writeHead(500, { "Content-Type": "text/plain; charset=utf-8" });
      res.end("Film gescheitert: " + fehlerText(e));
      return;
    }

    // A day without events is not an error. Python then writes the message without a medium
    // ("it was quiet in the office today"); an empty GIF would be the worse answer.
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
