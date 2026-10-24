# 2026-10-24 - no-AVX2: llama.cpp vs Strata (community data point)

Hardware: Dell Precision T5610, 2x Xeon E5-2697 v2 (Ivy Bridge, **no AVX2**), 128 GB DDR3-1866, 2x RTX 3060 12 GB (sm_86), SATA striped-SSD.
Model (identical): Swift-1.5-Qwen3.8-Flash-Next-GSQ-RCO-abliterated **IQ3_XXS**.

## Decode / prefill (localhost, greedy)
| engine | decode tok/s | prefill tok/s |
| --- | ---: | ---: |
| Strata 0.1.47 (prod) | 44.9 | ~150 |
| llama.cpp main (no-AVX2 build, moe-cache, `--cpu-moe --moe-cache-mib 6000`) | 6.3 | 4.8 |

## Raw llama.cpp probe (3 runs, 512-token gen)
```
run 1: decode=5.6 tok/s | prefill=5.1 tok/s | wall=106.9s
run 2: decode=6.3 tok/s | prefill=4.3 tok/s | wall=82.9s
run 3: decode=6.3 tok/s | prefill=4.4 tok/s | wall=82.3s
MEDIAN decode: 6.3 (engine predicted_per_second) / 6.2 wall
```

Quality identical (same weights): r_count 3, neuner 20, reverse EDALOKOHCS, op_order correct - purely a throughput gap (~7x).

## Build notes (no-AVX2)
- **Default upstream build SIGILLs** on Ivy Bridge (AVX2 intrinsics). Needed `-DGGML_AVX2=OFF -DGGML_AVX=ON -DGGML_FMA=OFF -DGGML_AVX512=OFF -DCMAKE_C_FLAGS=/arch:AVX -DCMAKE_CXX_FLAGS=/arch:AVX` + CUDA arch 86.
- Strata required no such workaround: `STRATA_ISA_FLOOR=avx` from the start.

-> docs/LLAMA-CPP-vs-STRATA-noavx2.md (full narrative)