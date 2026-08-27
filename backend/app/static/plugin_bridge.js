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
    /**
     * The plugin's own phrases from the catalogs of the house, in the language of the
     * person. Keys are `<slug>.<name>`; the prefix is set by the host, not here.
     *
     * Fetched once as a map instead of a round trip per label: a list of a hundred rows
     * would otherwise be a hundred messages before the first one is drawn.
     */
    texts: function () { return ask("i18n.texts"); },
    /** The plugin's own storage (tables from the manifest). */
    store: {
      list: function (table) { return ask("store.list", { table: table }); },
      create: function (table, row) { return ask("store.create", { table: table, row: row }); },
      remove: function (table, id) { return ask("store.delete", { table: table, id: id }); },
    },
    /**
     * The breakdown behind a tile's figures.
     *
     * A tile is a figure like the host's own ones, and behind those stands a list of where
     * the number comes from. The tile cannot draw that list itself: its frame takes no mouse
     * (a click on it belongs to the host, not to foreign code), so a tooltip in here would
     * never open. The plugin says what stands in it, the host draws it over its own card.
     *
     * `note` covers the whole tile, `notes` gives every figure its own — pass the element
     * each one belongs to and the bridge measures it. Measuring here and not in the plugin
     * is the point: the host needs the rectangle, every plugin would otherwise write the
     * same `getBoundingClientRect` loop, and none of them would remember to redo it when the
     * tile is resized. This does, on its own.
     *
     * Text only, at most a dozen rows, and the host cuts what is too long.
     */
    tile: {
      note: function (note) { zones = []; send({ note: note || null }); },
      notes: function (list) {
        zones = (list || []).filter(function (z) { return z && z.el; });
        measure();
      },
    },
    /** For anything the conveniences above do not cover. */
    call: ask,
  };

  // -------------------------------------------------------------- tile notes

  var zones = [];
  var pendingMeasure = 0;

  function send(payload) {
    payload.source = "plugin";
    parent.postMessage(payload, "*");
  }

  /**
   * Where each figure sits, in the frame's own pixels.
   *
   * The host lays the frame over its card edge to edge, so these are the card's pixels too
   * and it can put its hover zones straight onto them. A figure that has been laid out to
   * nothing (hidden, not yet drawn) is left out rather than sent as a zero-sized target.
   */
  function measure() {
    pendingMeasure = 0;
    if (!zones.length) return send({ note: null });
    var out = [];
    for (var i = 0; i < zones.length; i++) {
      var z = zones[i];
      var box = z.el.getBoundingClientRect();
      if (!box.width || !box.height) continue;
      out.push({
        key: z.key || String(i),
        title: z.title,
        rows: z.rows,
        rect: { x: box.left, y: box.top, w: box.width, h: box.height },
      });
    }
    send({ note: out.length ? { zones: out } : null });
  }

  function remeasure() {
    if (pendingMeasure) return;
    pendingMeasure = requestAnimationFrame(measure);
  }

  // The tile is resized by the host — its card follows the grid, and below `xl` the whole row
  // changes shape. Every rectangle sent before that is then pointing at the wrong place.
  if (window.ResizeObserver) new ResizeObserver(remeasure).observe(document.documentElement);
  window.addEventListener("resize", remeasure);

  // Tell the host the page is up.
  parent.postMessage({ source: "plugin", ready: true }, "*");
})();
