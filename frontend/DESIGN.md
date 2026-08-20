# Gestaltung der Oberfläche

Ein Leitfaden, damit alle Seiten wie aus einem Guss wirken. Er beschreibt, **welche Bausteine
es gibt** und **wann welcher genommen wird** — nicht, wie sie innen gebaut sind.

Die Bausteine stehen in `src/components/ui.tsx`. Wer eine Klassenkette von Hand baut, die es
dort schon gibt, erzeugt genau die Unterschiede, die diese Datei abschaffen soll.

## Die drei Ebenen

Die Oberfläche kennt genau drei Flächen, und ihre Reihenfolge ist die Ordnung des Bildes:

| Ebene | Farbe | wofür |
|---|---|---|
| Seite | `bg-surface` | der Hintergrund, auf dem alles steht |
| Karte | `bg-card` | ein Bereich der Seite (`Bereich`) |
| Eintrag | `bg-surface` | eine Zeile **in** einer Karte (`ListenZeile`) |

Daraus folgt die wichtigste Regel: **Eine Liste braucht eine Karte um sich.** Zeilen tragen
Seitenfarbe; ohne Karte stünden sie farbgleich auf dem Seitenhintergrund, und man sähe nur
noch Text. Umgekehrt gilt: Was direkt auf der Seite steht (Popover, Menü, eine Kachel ohne
Bereich), trägt `bg-card`.

Rahmen sind immer `border-line`, Text ist `text-ink` (wichtig) oder `text-muted` (alles
Erklärende). Akzentfarbe ist `brand` — sie markiert, wo man ist und was der Hauptweg ist.

## Bausteine

### `Knopf` — jede Handlung

**Ein Knopf ist blau. Grau heißt abgeschaltet, sonst nichts.**

Vorher war es umgekehrt: Die meisten Knöpfe waren grau umrandet, und in einer Kopfzeile mit
vier davon ging jeder einzelne unter — während die Farbe, die eigentlich „hier ist nichts zu
holen" bedeutet, der Normalzustand war.

Drei Arten, mehr braucht es nicht:

**Blau ist die Fläche, nicht die Schrift.** Ein Knopf mit blauem Rahmen und blauer Schrift
ist immer noch überwiegend Hintergrund — und damit fast so leise wie der graue, den er
ablösen sollte.

| Art | Aussehen | Wofür |
|-----|----------|-------|
| `haupt` | gefüllt blau | die eine Handlung, um die es auf dieser Fläche geht |
| `neben` (Vorgabe) | gefüllt blau | alles andere, was man tun kann |
| `gefahr` | gefüllt rot | was man nicht versehentlich tut |

`haupt` und `neben` sehen gleich aus. Der Unterschied steht im Code, nicht im Bild: Er sagt,
worum es auf der Fläche geht, und ist kein Versprechen auf ein anderes Aussehen. Wer später
abstufen will, ändert eine Zeile in `ui.tsx`.

```tsx
<Knopf art="haupt" onClick={speichern} disabled={!geaendert}>Speichern</Knopf>
<Knopf onClick={pruefen} zeichen="✓" stand={geprueft ? "gut" : "offen"}>Prüfen</Knopf>
<Knopf art="gefahr" onClick={() => setLoeschen(true)}>Löschen</Knopf>
```

**Ein Knopf, der nichts tun kann, ist abgeschaltet** — nicht klickbar mit anschließender
Fehlermeldung. „Speichern" ohne Änderung, „Veröffentlichen" ohne neuen Stand: beide `disabled`,
mit einem `titel`, der den Grund nennt. Das ist der Unterschied zwischen einer Oberfläche, die
den Zustand zeigt, und einer, die ihn verschweigt und danach schimpft.

`zeichen` ist das Kurzzeichen für schmale Bildschirme: Dort steht nur es, sonst der Text.

`stand` hängt ein Ergebnis an den Knopf: `gut` (grüner Haken), `schlecht` (rotes Kreuz),
`offen` (nichts). Für Handlungen, deren Ausgang man später noch sehen will, ohne sie zu
wiederholen — eine Prüfung zum Beispiel. Wichtig: Das Ergebnis gehört zu EINEM Stand der
Sache. Ändert sich die, steht der Knopf wieder auf `offen`, sonst zeigt er ein Ergebnis, das
für etwas anderes galt.

In Zeilen und Werkzeugleisten dieselben Knöpfe in klein: `<Knopf klein>` bzw.
`KNOPF_KLEIN.*`. Farbe und Bedeutung bleiben, nur die Höhe zieht die Zeile nicht auseinander.

`IconKnopf` folgt derselben Regel — blauer Rahmen, blaues Zeichen, aber ohne Füllung: Eine
Liste mit zwanzig Zeilen à drei Handgriffen wäre sonst ein Feuerwerk.

Knöpfe mit eigener Mechanik (Umschalter, Dateiauswahl, Reiter) bleiben `<button>`, nehmen
aber dieselben Klassen: `className={KNOPF.haupt}` / `KNOPF.neben` / `KNOPF.gefahr`. Eine
Quelle, zwei Zugänge — neu geschrieben wird mit `<Knopf>`.

**Nicht** darunter fallen echte Umschalter (ein Reiter, der aktiv oder inaktiv ist): Dort
heißt Grau „gerade nicht gewählt", nicht „abgeschaltet", und Blau markiert die Auswahl.

### `Bereich` — der Rahmen eines Reiters

Jeder Reiter, jede abgeschlossene Einheit einer Seite steckt in genau einem `Bereich`:
Erklärsatz oben (`hinweis`), darunter optional eine Werkzeugleiste (`werkzeuge`: Filter,
Zähler, Umschalter), dann der Inhalt.

```tsx
<Bereich hinweis={tr("jobs_panel.einleitung")} werkzeuge={<>
  <label>…Filter…</label><div className="flex-1" /><span className="text-xs text-muted">12 Stück</span>
</>}>
  <Liste>…</Liste>
</Bereich>
```

Kein eigenes `rounded-lg border border-line bg-card p-4` mehr von Hand. Zwei Bereiche
untereinander (Auslöser: Auslöser + Ereignisse) stehen in einem `space-y-4`.

### `Liste`, `ListenZeile`, `ListeLeer` — Einträge

Eine Fläche, Einträge durch Linien getrennt, kein Kachelmeer.

```tsx
<Liste>
  {sachen.map((s) => (
    <ListenZeile key={s.id} gedimmt={!s.enabled} warnung={s.haengt} onClick={() => oeffnen(s)}>
      …
    </ListenZeile>
  ))}
  {sachen.length === 0 && <ListeLeer>Noch nichts da.</ListeLeer>}
</Liste>
```

- `gedimmt` — abgeschaltet: sichtbar, aber sichtbar außer Dienst.
- `warnung` — ein Streifen links, für das, was Aufmerksamkeit braucht (hängender Lauf,
  Reihe läuft leer). Keine eingefärbte Fläche: der Text muss lesbar bleiben.
- `onClick` — die ganze Zeile wird der Weg hinein. Knöpfe darin fangen ihren Klick selbst ab.
- `spalten` — ein Grid-Template (`sm:grid-cols-[…]`), wenn Werte wirklich untereinander
  fluchten sollen. **Kopf und Zeilen müssen dasselbe Template und denselben Spaltenabstand
  benutzen**, sonst steht die Überschrift neben statt über ihrer Spalte.

Aufbau einer Zeile: Der **Name** trägt sie (`font-medium text-ink`), alles Technische steht
eine Etage tiefer und leiser (Schlüssel in `font-mono text-xs text-muted`, Art als Etikett).
Rechts der Zustand, ganz rechts die Handgriffe.

`ListenKopf` gibt es für Listen mit vielen Zeilen und echten Spalten. Bei einer Handvoll
Einträgen weglassen — eine Überschriftenzeile über fünf Zeilen Inhalt ist Rauschen.

### `Etikett` — ein kurzer, wiederkehrender Wert

Art, Modus, Herkunft, Anzahl, Projektschlüssel. Die Farbe ist eine **Rolle**, kein Farbwert:

| Farbe | Bedeutung |
|---|---|
| `neutral` | eine Angabe ohne Wertung (Standard) |
| `gruen` | in Ordnung, läuft, fertig |
| `gelb` | aufpassen, unfertig, Abweichung |
| `rot` | kaputt, abgeschaltet, gescheitert |
| `blau` | in Arbeit |
| `violett` | Ereignis, Zuhörer, eigener Satz |
| `brand` | das, was gerade gilt |

Nie `bg-amber-500/15` und Verwandte von Hand — dieselbe Bedeutung sah dadurch auf drei Seiten
dreimal anders aus.

### `Zustand` — Punkt plus Wort

Für **den einen** Zustand eines Eintrags (veröffentlicht · nur Entwurf · aus). Die Farbe trägt
die Dringlichkeit, das Wort die Bedeutung — Farbe allein ist keine Auskunft.

### Handlungen

- `Aktionen` + `IconKnopf` — die Handgriffe am rechten Ende einer Zeile (bearbeiten, an/aus,
  löschen). `gefahr` färbt erst beim Hover rot: die Warnung gehört an den Moment, nicht in
  die Ruhelage.
- `Zeilenknopf` — benannte Nebenhandlung in einer Zeile („Versionen", „Verlauf", „Anpassen").
- Die **eine** Hauptsache einer Seite ist ein Knopf in Markenfarbe: `rounded bg-brand px-3
  py-1.5 text-sm text-white`, unter der Liste („+ Ablauf anlegen").

### Formulare und Dialoge

`Dialog` + `DialogFuss` fürs Anlegen und Bearbeiten, `Feld` für Beschriftung und Hinweis,
`EINGABE` als Klassenkette für Eingabefelder, `Fehlerzeile` für Fehler über einer Liste oder
in einem Dialog. Ein Fehler wird nie als roher roter `div` gebaut.

### Reiter innerhalb einer Seite

`Reiter` — für Umschalter, die die Adresse **nicht** ändern (Assistent: Chat · Eingänge ·
Regeln; ein Filter über einer Liste). Sieht aus wie die Seiten-Navigation, weil es dasselbe
tut; das Auge soll für dieselbe Bewegung keine zweite Sprache lernen müssen.

### `ZEILE`

Die Klassen eines Eintrags als Konstante — für die Fälle, in denen der Eintrag ein `<Link>`
sein muss (Mittelklick, Kontextmenü) und deshalb keine `ListenZeile` sein kann. Sonst immer
die Komponente nehmen.

## Navigation

Die Reiterleiste (`usePageChrome`) hat **keinen** eigenen Rahmen: darunter stehen Karten, und
eine Navigation im selben Rahmen wie der Inhalt liest sich als weitere Kiste. Getragen wird
sie von einer Linie — seitlich rechts, oben unten. Der aktive Reiter trägt die Markenfarbe.

**Immer `layout="seite"`** — die schmale Spalte neben dem Inhalt. Eine Bewegung, eine Form:
Prozesse standen als einzige Seite quer oben und fielen dadurch aus dem Bild, obwohl an ihnen
sonst nichts falsch war. `"oben"` bleibt nur für eine Seite übrig, die ihre volle Breite
wirklich braucht. Unter `md` sehen beide gleich aus, weil neben dem Inhalt kein Platz ist.

## Sprache

Beschriftungen sind deutsch und sagen, was passiert („Aussortierung melden", nicht „Notify").
Der Erklärsatz eines Bereichs ist ein Satz, keine Überschrift. Zahlen bekommen ihre Einheit
(„12 Vorgänge", nicht „12").

## Wenn etwas fehlt

Erst hier nachsehen, dann in `ui.tsx`. Fehlt der Baustein wirklich, kommt er **dorthin** und
in diese Datei — nicht als Klassenkette in die eigene Seite. Genau so sind die fünf
verschiedenen Prozess-Reiter entstanden.
