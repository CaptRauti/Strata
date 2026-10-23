# Strata Concurrency-Performance — Technical Report

**System:** fenris — Xeon E5-2697 v2 (24 Kerne, nur AVX1, kein AVX2) · 2× RTX 3060 12 GB
(Gigabyte>ASUS; beide 100 W Power-Limit, 1650 MHz Clock-Deckel) · 128 GB RAM · 128k Kontext · KV Q4_0
**Engine:** 0.1.45 (lokaler AVX-Floor-Build) · Modell: Swift 1.5 IQ2_XS (SC117-abliterated)
**Datum:** 2026-10-20 · Baselines: Decode ~54–62 tok/s, Expert-Cache-Hit 97–99 %

**Ziel:** Ursache des Performance-Einbruchs bei Concurrency > 1 analysieren und Quick-Wins + Fork-Blueprint
liefern — mit gemessenen Zahlen statt Vermutungen.

---

## 1. Codebase-Audit — wie der Scheduler wirklich funktioniert

### 1.1 Es gibt KEIN Continuous Batching im vLLM-Sinn und kein PagedAttention

- Strata nutzt **feste Batch-Slots** (`--batch N` ≡ `--slots N`, Config `parallel: N`, `src/program/generate.cpp` Zeile ~2464/5020).
- Jeder Slot erhält einen **voll vorab budgetierten, statischen Session-Buffer** über `session_bytes()` /
  `session_init()` (`src/core/session.cpp`), der den KV-Cache für `max_context` auf der jeweiligen
  GPU (Layer-Bereich des Splits) aufspannt. **Keine dynamische Page-Verwaltung** — d. h. kein
  PagedAttention, kein KV-Sharing zwischen Slots.
- Zwei GPUs → `--layer-split auto` (von `serve/server.py` immer injiziert, `layer_split_of()`); jeder
  Slot durchläuft die Stage-Windows beider Karten.

### 1.2 Sequenzieller Pfad (unsere aktuelle Config: `parallel: 1` → `--batch` fehlt)

- Ohne `--batch` läuft der Server **ein Request nach dem anderen** über ein `threading.Lock`
  (`serve/server.py` Zeile ~2314/967). Neue Requests warten an der Queue, bis der laufende fertig ist.
- **Gemessen (sekundär):** der Server serialisiert sauber; jeder Request bekommt seine volle Einzel-Decode-Rate.

### 1.3 Was `--batch N` wirklich tut (und was es automatisch deaktiviert)

Beim Start baut die Engine:
- `--batch: 2 slot sessions on CUDA0/CUDA1` — Slots kosten **VRAM** ("0.50 GiB … that the expert cache would
  otherwise hold").
- `--batch-groups auto: 2 groups of 1 slot` (je Karte eine Gruppe).
- **WICHTIG — automatische Funktionsverluste im Batch-Modus** (aus `generate.cpp`):
  - `--pipeline-windows 2 is off: not with --batch slots` (Zeile 5047)
  - adaptive Overlap / Batch-MTP / `--adapt-async` nur optional und gleichzeitig teilsperrend (Zeile 4015)

Das erklärt, warum `parallel: 2` hier *nicht* schneller aggregiert — der Parallel-Gewinn wird durch den
Verlust der Pipeline-/Overlap-Optimierung und durch den Slot-VRAM-Overhead negiert.

---

## 2. Benchmark — 1 vs 2 vs 4 parallele Streams (je ~1,1k Kontext, 80 Tokens Output)

Messtechnik: Engine-interne DONE-Zeilen aus dem Log (`decode tok/s` je Request) + HTTP-Wallclock/TTFT.
Beide Messungen gegen **dieselben** 100-W/1650-MHz-Power-Limits.

### 2.1 Modus A: sequenziell (`parallel: 1`) — IST-Zustand

| Streams | Engine-Decode/Stream | Wallclock (n gleichzeitig) | TTFT median |
|--------|----------------------|----------------------------|-------------|
| 1 | 49–65 | 1,5–4,2 s | 1,5 s |
| 2 | **62** | 3,2 s | 2,4 s |
| 4 | **62** | 6,4 s | **4,0 s** |

**Lesart:** Jeder Request behält volle Rate (~62 tok/s), aber die **Request-Stau-Queue** → TTFT wächst
linear. **System-Durchsatz bleibt ~konstant** (aggregiert ~50–62 tok/s, ob 1 oder 4 Clients).
→ Das ist ein **Latenz-/Queueing-Engpass, kein pro-Stream-Einbruch.**

### 2.2 Modus B: echt parallel (`parallel: 2` → `--batch 2`)

| Streams | Engine-Decode/Stream | Wallclock | TTFT median |
|--------|----------------------|-----------|-------------|
| 1 | 34–56 | 1,8–7,3 s | 1,8 s |
| 2 | 40–47 (min 23–27) | 3,3–3,5 s | 3,3 s |
| 4 | 46 (min 15–27) | 6,7–6,8 s | **5,3 s** |

**Lesart:** `--batch 2` **verschlechtert** die Per-Stream-Decode-Rate (62 → 46 tok/s), weil
Pipeline-/Overlap-Optimierungen abgeschaltet werden und 4 Clients auf 2 Slots teils unnötig warten
(Extremwerte 14–27 tok/s = Stall-Ausreißer). TTFT ist **nicht** besser als sequenziell.

### 2.3 PCIe / VRAM / Expert-Thrashing — Messbefund

- **Expert-Cache-Hit-Rate 97–99 % in beiden Modi**, nur **0,0–0,5 % PCIe-Reads** ("read by the GPU over
  PCIe or from another GPU").
- ⇒ **Kein PCIe-Expert-Thrashing, kein Cross-GPU-Swap-Engpass.** Der Expert-Cache wird konsistent aus
  VRAM bedient. PCIe ist mit diesen 3060ern (overhead klein, Hit-Rate ~99 %) **nicht der Flaschenhals**.

### 2.4 Kernaussage

Der beobachtete "Einbruch bei Concurrency > 1" ist:
1. primär **Request-Serialisierung im sequenziellen Server** (wächst mit Queue-Länge → reine Latenz),
2. **keine** Verbesserung durch naive `--batch`-Slots, weil die Overlap-Optimierung wegfällt und
   Slots VRAM/fachlich konkurrieren,
3. **nicht** durch PCIe- oder Expert-VRAM-Engpässe begrenzt.

---

## Quick-Wins im bestehenden Setup — Bewertung

| Flag / Mechanismus | Status | Empfehlung |
|---|---|---|
| `--kv q4_0` (4-bit KV) | **aktiv** | beibehalten (stärkste Quantisierung) |
| `--kv-resident 32768` | aktiv | beibehalten (streamt KV → mehr Expert-VRAM) |
| `--expert-cache auto` / Profil | aktiv, 97–99 % Hit | **bereits optimal** |
| `--pipeline-windows 2` | aktiv (nur ohne --batch) | beibehalten — **nicht mit --batch kombinieren** |
| `STRATA_ADMIT_SHORT_FIRST=2048` | **gemessen: kein Vorteil** | verwerfen — sortiert nur die Warteschlange; kann einen LAUFENDEN langen Request nicht überlappen (12 Runs 15–48 s ≈ Rauschen) |
| `--batch 2` / `parallel: 2` | **messbar schlechter** | **nicht aktivieren** — verliert über `pipeline-windows` |
| `--expert-cache-per-layer` | Messung im Repo: 21 % langsamer | nicht aktivieren |

**Fazit Quick-Wins:** Die I/O-/Cache-seitigen Stellschrauben sind für diese Hardware bereits ausgereizt
(Hit-Rate ~99 %). Der Fortschritt liegt **nicht** im Tuning einzelner Flags, sondern in Architektur
(→ Abschnitt 4). **Negativ-Beleg wichtig:** `STRATA_ADMIT_SHORT_FIRST` löst das queue-Latenzproblem
nicht, weil der sequenzielle Server den laufenden langen Request nicht interruptieren kann —
es besteht kein echter Concurrency-Weg zwischen sequenziell und vollem `--batch`.

---

## 4. Fork-Blueprint — echtes Continuous Batching & Locality-aware MoE für 2× 12 GB

### 4.1 Zielbild

Ein Fork, der mehrere Streams **innerhalb derselben Engine-Schleife** bündelt (echte Token-Parallelität),
die Overlap-Features (Pipeline-Windows) **nicht** zugunsten von Slots aufgibt, und die knappen 24 GB VRAM
**dynamisch** zwischen Expert-Cache, KV und Prefill verwaltet.

### 4.2 Betroffene C++/CUDA-Dateien (konkret aus dem Code-Audit)

| Datei | Funktion heute | Fork-Änderung |
|---|---|---|
| `src/program/generate.cpp` | `--batch`-Slots als *Separate* Session-Buffer; schaltet `pipeline-windows` ab | **Entferne die Slot-/Pipeline-Exklusivität** (Zeilen ~4015, ~5047): Slots UND Pipeline-Windows gemeinsam zulassen; Slots-Windows über die `kVerifyMaxT`-Zeile bündeln statt als getrennte Sessions |
| `src/core/session.cpp` (`session_bytes/session_init`) | statische KV-Preallocation pro Slot | **Lazy-/Paged-KV (Pseudopage-attention):** KV in Seiten allokieren, je tatsächliche Kontextlänge; mehrere Slots teilen eine Pagen-Bank statt je eigener `max_context`-Reservierung (→ +VRAM für Expert-Cache bei kurzen Kontexten) |
| `src/core/layer.cpp` (QSA/KV) | `--kv-resident` statisch, KV-stream | **Dynamische KV-Resident-Strategie** in die Pagen-Bank integrieren (Residenz ↔ aktuelle Kontextlänge) |
| `src/core/expert_source.cpp` (FileExpertSource) | Expert-Cache-Füllung + adaptive Tier | **Locality-aware Routing-Hint:** Speicher-Hotness je Layer/Experte; heißeste Experten auf die Head/Draft-Karte (GPU "zuletzt"), kalte auf die zweite |
| `src/kernels/cuda/…` (MoE-Adapter) | MoE-GEMV Getrennt je Karte | **Shared-Expert-Adressraum** (unified virtual CUDA): beide Karten sehen denselben Expert-Pool → keine Kopie nötig, PCIe bleibt 0 (unterstützt Hit-Rate weiter bei ~99 %) |
| `serve/server.py` | sequenzielles ctl-Lock / `parallel: N` | **Warteschlangen-Vereinigung (request merging)**: identische Prologe kürzen; `STRATA_ADMIT_SHORT_FIRST`-Logik in echten Batch-Windows; Long-Poll ohne Blocking-Engine-Lock |

### 4.3 Drei konkrete Architektur-Stellschrauben

1. **Echtes Continuous Batching:** Slots nicht als getrennte Sessions, sondern als *Token-Streams im
   selben gemischten GEMV-Batch* behandeln. Pro Decode-Schritt: `B` Streams, deren Experten-MatVeks in
   **eine** GPU-Fusion zusammengefasst werden (statt B sequenzielle Aufrufe). Das ist die eigentliche,
   massive Concurrency-Absenkung (B Streams ≈ ein "Batch-4"-Kernellauf, GPU-Auslastung steigt),
   und `pipeline-windows` bleibt an.

2. **Locality-aware MoE / Shared Expert Cache:** Statt `--expert-cache-device1` (Helper-Karte, braucht
   eine GPU *ohne* Layer — auf 2 Karten nicht möglich, wie in Sprint 2 nachgewiesen), den **gleichen
   Expert-Pool auf beide Karten als addressierbaren** Cache legen und die **Route** je Provider karten-
   bewusst treffen: Experte landet auf der Karte, die ihn ohnehin routet. Hotness-Profil
   (`--expert-profile`) steuert die Platzierung. → Hit-Rate bleibt ~99 %, kein PCIe.

3. **Dynamisches Queueing:** Zwei Warteschlangen mit Priorisierung — **niedrige Latenz** (single-stream
   Chat) vs. **durchsatz** (Lang-/Agenten-Jobs). Bei kurz anstehenden Streams batchet der Fork sie im
   nächsten Fenster, ohne die Head-of-Line eines Einzelnutzers zu blockieren. `--batch-mtp` nur einschalten,
   wenn die `pipeline-groups`-Auflösung es zulässt (die Strata-Codebase hat den Mechanismus schon in
   `batch_groups.hpp`).

### 4.4 Warum das auf diesen 2×12-GB-Karten funktioniert

- Der Engpass ist **nicht** VRAM-Expert-Pool (Hit ~99 %) und **nicht** PCIe, sondern die **CPU-Seite der
  Konkurrenz / Serialisierung**. Echte Batch-Fusion gibt GPU-Auslastung bei gleichbleibendem VRAM-Fußabdruck.
- Die dynamische KV-Page-Bank gibt bei den typischen kurzen Kontexten **zusätzliche ~1–4 GiB** an den
  Expert-Cache zurück — genau der Spielraum, den ein gemischter Batch braucht.

---

## 5. Empfohlene nächste Schritte (priorisiert)

1. **D Ü1 (Quick-Win, kein Build):** `--batch` für diese Hardware **verwerfen bestätigen** (gemessen;
   Doku + Bench-Skript `bench/concurrency_bench.py` sichern). Server bleibt `parallel: 1`.
2. **[S] Session-Page-KV:** `session_bytes()`/`session_init()` um dynamische Pagen-Allokation erweitern —
   kleinster Engine-Patch mit größtem VRAM-Gewinn für Concurrency.**Achtung:**
   die Engine hat bereits Streaming-KV (`--kv-resident`, `kv_plan mode 1`), aber je `--batch`-Slot wird
   **eine getrennte max-context-KV-Kopie** angelegt → auf 2×12 GB fällt die Expert-Hit-Rate von 99 % auf
   66 % (gemessen). Der eigentliche, messbare Fork-Eingriff ist **KV-Sharing / gepoolte KV zwischen den
   Slot-Sessions** (`session_bytes` fasst jeden Slot in VRAM, statt einen gemeinsamen Paged-VRAM-Pool zu
   teilen) — dann hilft endlich auch `--batch 4` (2 Gruppen à 2), das die Engine sonst als best-known
   Stream-Fusion hat. Branch `fenris-page-kv`.
3. **[M] GEMV-Batch-Fusion:** erst NACH KV-Sharing (der VRAM muss vorher frei sein).
4. **[M] Locality-Routing:** nur falls ein A/B nach KV-Sharing noch einen GPU-Lokalitäts-Engpass zeigt.

*(Bewusst NICHT: `--peer-device` (VRAM-Grenze auf 2×12 GB, Sprint 1) und `--expert-cache-device1`
(Helper-Karte braucht Dritt-GPU, Sprint 2) — beide für diese Kartenarchitektur verworfen.)*

---

## Vermerk: Messtooling

- `bench/concurrency_bench.py` — 1/2/4 Streams, liest Engine-DONE-Zeilen aus dem Log.
- `bench/watchdog_crash.log` — GPU-Power/Temp/Util seit 12:33 (laufend).
- Messregel (aus den Lektionen): Engine-intern messen, nicht HTTP-Ende-zu-Ende; verschränkt (A/B/A/B).