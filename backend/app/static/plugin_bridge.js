/**
 * A plugin's bridge to Traccoon.
 *
 * A plugin runs in an iframe without `allow-same-origin`. It therefore has no origin, no
 * access to the logged-in person and none to the API — `fetch` would be blocked by the CSP
 * (`connect-src 'none'`) anyway. Everything it needs it asks the host for, and the host only
 * hands out what an admin has granted.
 *
 * Include:
 *
 *     <script src="/api/plugins/_bridge.js"></script>
 *     const series = await traccoon.series("location");
 */
(function () {
  var pending = new Map();
  var counter = 0;
  var TIMEOUT = 30000;

  window.addEventListener("message", function (e) {
    var d = e.data;
    if (!d || d.source !== "traccoon" || !pending.has(d.id)) return;
    var entry = pending.get(d.id);
    pending.delete(d.id);
    clearTimeout(entry.timer);
    if (d.ok) entry.resolve(d.data);
    else entry.reject(new Error(d.error || "refused"));
  });

  /**
   * One question to the host.
   *
   * Sent to `*` because the plugin cannot know the host's address — its own origin is
   * `null` and `document.referrer` stays empty. That is harmless: the message carries only
   * the question, and the host checks for its part that it came from exactly this iframe.
   */
  function ask(call, args) {
    return new Promise(function (resolve, reject) {
      var id = ++counter;
      var timer = setTimeout(function () {
        if (!pending.has(id)) return;
        pending.delete(id);
        reject(new Error("Traccoon does not answer"));
      }, TIMEOUT);
      pending.set(id, { resolve: resolve, reject: reject, timer: timer });
      parent.postMessage({ source: "plugin", id: id, call: call, args: args || {} }, "*");
    });
  }

  window.traccoon = {
    /** Who is logged in (name, locale, timezone, theme) — without the mail address. */
    me: function () { return ask("me"); },
    /** Series of one kind this person may see. */
    series: function (kind) { return ask("series.list", { kind: kind }); },
    /** Points of one series. `opt`: {kind, from, to, limit}. */
    points: function (key, opt) {
      var a = { key: key, kind: (opt && opt.kind) || "location" };
      if (opt) { a.from = opt.from; a.to = opt.to; a.limit = opt.limit; }
      return ask("series.points", a);
    },
    /** The latest state of every visible series of one kind. */
    live: function (kind) { return ask("series.live", { kind: kind || "location" }); },
    /** This person's named places (geofences). */
    places: function () { return ask("places.list"); },
    /** The plugin's own storage (tables from the manifest). */
    store: {
      list: function (table) { return ask("store.list", { table: table }); },
      create: function (table, row) { return ask("store.create", { table: table, row: row }); },
      remove: function (table, id) { return ask("store.delete", { table: table, id: id }); },
    },
    /** For anything the conveniences above do not cover. */
    call: ask,
  };

  // Tell the host the page is up.
  parent.postMessage({ source: "plugin", ready: true }, "*");
})();
