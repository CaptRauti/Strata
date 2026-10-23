# Strata community result — fenris (2× RTX 3060, AVX-1 Xeon)

Measured by the maintainer of this fork on **fenris**: Intel Xeon E5-2697 v2 (AVX-1,
NO AVX2/AVX-512) · **2× NVIDIA RTX 3060 12 GB** (two different brands, 100 W power limit,
1650 MHz clock cap) · 128 GB RAM · 128k context · KV Q4_0 · Engine **0.1.45** (local
AVX-floor build) · model Swift 1.5 IQ2_XS (SC117 abliterated).

Follows `docs/COMMUNITY_BENCHMARKS.md`. Numbers are engine-internal (decode tok/s and
expert-cache hit rate from the engine logs), not HTTP end-to-end.

## What is here

| File | Content |
|---|---|
| `report-concurrency-2026-10-20.md` | Full scheduler audit + 1/2/4 concurrent-stream measurements + fork blueprint |
| `FINAL-concurrency-findings.md` | Conclusion: on **two 12 GB** cards `parallel:1` (sequential) is optimal; every `--batch` variant loses expert-cache hit rate (99 % → 86–88 %) because each slot allocates its own KV copy |

## Key finding (why it matters upstream)

`--batch N` (the engine's stream-batching) is measured fast on 16/24 GB cards (2× A4000,
2× RTX 3090). On **2× RTX 3060 (12 GB)** it is **not**: each slot's KV copy shrinks the
expert cache, so hit rate falls 99 % → 86 % and PCIe expert reads grow. Sequential
(`parallel: 1`) has the best single-stream rate AND best average completion time here.
The likely on-device path to batch speedup is **shared/pooled KV across slots**
(paged KV) so streams fuse without a per-slot `max_context` KV reservation.

Hardware the maintainers do not have (dual-socket AVX-1 Xeon + two 12 GB cards) — hence
the value of reporting.