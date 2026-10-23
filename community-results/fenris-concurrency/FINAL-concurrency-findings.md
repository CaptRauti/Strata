# Fenris Concurrency — Endbefund (alle Modi warm gemessen, 2026-10-21)

Vergleich aller Konfigurationen bei **2 parallelen Streams** (80 Tokens, 1,1k Prompt, 100 W/1650 MHz,
Expert-Cache via `--expert-profile` warmgefahren mit 3 langen Prompts):

| Konfiguration | Per-Stream Decode | Expert-Hit | 2-Stream wall | mittlere Fertigstellung |
|---|---|---|---|---|
| **parallel:1 (sequenziell)** | **62 tok/s** | **99 %** | 2,4 s | **~1,8 s** |
| parallel:2 (2 Gr. à 1, pipeline AUS) | 40 tok/s | 88,5 % | 4,0 s | ~4,0 s |
| parallel:2 (1 Gr. à 2 = Fusion, pipeline AN) | 40 tok/s | 88,5 % | 4,0 s | ~4,0 s |
| parallel:4 (2 Gr. à 2, pipeline AUS) | 46 tok/s | 86 % | 5,8 s | ~5,8 s |

**Kernaussage — die Maschine ist am besten MITM parallel:1:**
- **Per-Stream-Rate:** sequenziell 62 tok/s > jede Batch-Variante (40–46). Der VRAM-Verlust der
  Slot-KV-Kopien lässt die Expert-Hit-Rate von 99 % auf 86–88,5 % fallen → langsamerer Einzelstream.
- **Mittlere Fertigstellungszeit:** sequenziell ~1,8 s < Batch ~4–5,8 s. Weil jede Einzelanfrage schnell
  durchläuft, wird die durchschnittliche Antwortzeit kürzer — die Batch-"Beschleunigung" existiert hier
  nicht, sie teilt nur die (kleinere) Bandbreite.
- **`--batch-groups 1` behält zwar `--pipeline-windows` an** (die Fusion), aber ohne VRAM-Gewinn hilft das
  nichts — die Hit-Rate bleibt bei 88,5 %, weil die 2 KV-Kopien den Expert-Cache unabhängig von der
  Gruppierung verkleinern.

**Der dynamische Weg, den die Hardware wirklich zulässt:**
`parallel:1` IST bereits die dynamische Antwort auf "1 Anfrage = volle Leistung": Jede einzelne Anfrage
bekommt die volle 62-tok/s-Power und 99 % Expert-Hit, weil nie VRAM für Parallel-Slots abgezweigt wird.
Die Queue arbeitet einen Request nach dem anderen ab. Für MASSIVE parallele Last (viele gleichzeitige
LAN-Clients) wäre ein **Paged-KV-Sharing** nötig (eine gepoolte KV statt je Slot eine eigene Kopie) — dann
fällt die Hit-Rate nicht mehr und `--batch` könnte wirklich gewinnen. Das ist der dokumentierte M3-Fork-Schritt.

## Empfehlung (produktiv)
**Server bleibt auf `parallel:1`** (`strata-swift-iq2_xs.json`). Das ist das gemessene Optimum für diese
2×12-GB-Hardware: beste Einzelantwortzeit, beste Hit-Rate, stabil. Keine der `--batch`/`parallel:N`-Optionen
verbessert den Durchsatz bei parallelen Anfragen auf diesen Karten.

## Verifikations-Details
- Hit-Rate parallel:4 (kalt direkt nach Start): 66 % → warm: 86 %. Nicht nur Cold-Start — realer VRAM-Verlust.
- Hit-Rate parallel:2 (warm): 88,5 % in allen Gruppierungsvarianten.
- `--batch-groups 1` ist die einzige Variante, bei der `--pipeline-windows 2` aktiv bleibt (verifiziert im
  Engine-Startkommando).
- Produktiv-Server nach jedem Test zurückgesetzt und mit `loaded: true` verifiziert.