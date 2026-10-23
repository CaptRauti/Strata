# Projekt: fenris Strata-Fork — Concurrency-Ausbau

Branch: `fenris-fork-blueprint` · Stand: 2026-10-21 · Engine-Basis 0.1.45 (AVX-Floor)
System: Xeon E5-2697 v2 (AVX1) · 2× RTX 3060 12 GB (100 W / 1650 MHz) · 128 GB · 128k · KV Q4_0

## Ziel

Den gemessenen Concurrency-Engpass aufheben: mehrere Streams im selben Decode-Fenster stapeln,
ohne dabei `--pipeline-windows` (die Overlap-Optimierung) zu verlieren.

## Nachweislage (aus report-concurrency-2026-10-20.md)

- Sequenziell (`parallel:1`): 4 Streams je 62 tok/s, aber TTFT wächst auf 4 s (Queue).
- `parallel:2` (`--batch 2`): 46 tok/s — **schlechter**, weil `--pipeline-windows` abgeschaltet und
  Slots als getrennte Sessions laufen. → Der Parallel-Weg verzichtet auf den Overlap-Gewinn.
- Expert-Hit-Rate 97–99 %, PCIe-Reads 0 % → **kein** Locality-/VRAM-/PCIe-Engpass mehr.

## Wahl der ersten Bausteine (Mess-begründet)

### Baustein A — Shared Decode Batch (GEMV-Token-Fusion) — PRIO 1
Der eigentliche Hebel für den gemessenen Engpass. Statt `--batch` als N getrennte Session-Läufe
sollen B Streams ihre Experten-MatVeks im selben GPU-Batch stapeln, **während** die
`pipeline-windows`-Overlaps weiterlaufen. Das ist die Lücke zwischen sequenziell und vollem `--batch`.

**Empirischer Teilbefund (2026-10-21) — `--batch 4` (2 Gruppen à 2 Slots), die Originallösung der Engine:**
Die Stream-Fusion-Logik der Engine ist gut (an 2×A4000/3090 gemessen: 25-30 % schneller). Auf fenris
**greift sie nicht**, weil der Expert-Cache darunter leidet: je Slot eine KV-Kopie (4×) frisst VRAM →
**Hit-Rate fällt 99 % → 66 %, PCIe-Reads 0 % → 5 %**, Einzel-Decode 62 → 38 tok/s.
**Kernwurzel:** Auf 2× 12 GB ist VRAM (KV-Kopie pro Slot) der Concurrency-Engpass, nicht die
Scheduling-Logik. Die Engine braucht hier **KV-Sharing zwischen Slots** (Paged-KV), damit Streams
fusionieren OHNE je eine eigene max_context-KV-Reservierung zu fressen — das IST der Fork.

### Baustein B — Locality-Routing / GPU-Expert-Zuordnung — zurückgestellt
Der Code hat bereits `primary_gpu_pairs`, `complement_device_`, `device_alias`, `remote_experts.cpp`
und ein Profil-Ranking ("hottest first"). Hit-Rate ist schon 97–99 % ⇒ Umverteilen hätte kaum
messbaren Spielraum. Nur neu aufnehmen, falls ein A/B einen echten GPU-Lokalitäts-Engpass zeigt.

## Projekt-Milestones

- [ ] **M1** Scheduler-/Pipeline-Codepfad des Decode-Fensters freilegen (wo ein Batch stapeln könnte,
      ohne die `pipeline-windows`-Logik zu verlieren) — Dateien: `generate.cpp` (Fenster/`--batch`),
      `verify.cpp`, MoE-Adapter (`s2_expert_grouped.cu`), `batch_groups.hpp`.
- [ ] **M2** Minimal-Prototyp: 2 Streams im selben Decode-Fenster, Overlap beibehalten (~Re-Implementierung
      der `--batch`-Fenster über die bestehende Gruppe).
- [ ] **M3** Lokal bauen (AVX-Floor) + A/B gegen die 62-tok/s-Baseline.
- [ ] **M4** Entscheidung Datenspur: nur dann weiter (voller Batch + Paged-KV-Hand-off), wenn M3 misst.

## Regeln (aus den Lektionen)

- Eigener Branch, nie Production-Clone-/Server berühren; Start nur über `start-strata.bat`-Pfad (Power-Limits).
- Vorher/Nachher messen; kein Default-Wechsel ohne Zahl; Sicherung/Rollback-Pfad.
- Engine-intern messen (`/metrics`, DONE-Zeilen), nicht HTTP-Ende-zu-Ende.

## Verknüpfung
- Technical Report: `bench/report-concurrency-2026-10-20.md`
- Obsidian: [[Strata-Fork-Roadmap]] · [[Strata-Benchmarks]] · [[Server-Lektionen-fenris]]