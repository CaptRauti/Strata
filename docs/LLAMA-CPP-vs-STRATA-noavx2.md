# llama.cpp vs Strata on no-AVX2 hardware (measured, 2026-10-24)

Community data point the maintainers asked for: benchmarks on hardware they do not run. This rig
is the flagship `no AVX2` case: a Dell Precision T5610 - 2x Xeon E5-2697 v2 (Ivy Bridge, **no
AVX2**), 128 GB DDR3-1866 quad-channel, 2x RTX 3060 12 GB (sm_86), model files on SATA striped-SSD.

## Setup (identical model both engines)
- Model: **Swift-1.5-Qwen3.8-Flash-Next-GSQ-RCO-abliterated, IQ3_XXS** - the same abliterated pack.
- **Strata** (production): engine 0.1.47 (v0.2.2), `STRATA_ISA_FLOOR=avx`, CUDA arch 86, hybrid
  CPU experts + GPU decode, MTP_STAGE0, pipeline windows 2, conversation cache 8192 MiB,
  `--vram-reserve-mib 1344`. Default config.
- **llama.cpp**: ggml-org/llama.cpp `main` (2026-10), CUDA sm_86, **no-AVX2** rebuild
  (`-DGGML_AVX2=OFF -DGGML_AVX=ON -DGGML_FMA=OFF -DGGML_AVX512=OFF
   -DCMAKE_C_FLAGS=/arch:AVX -DCMAKE_CXX_FLAGS=/arch:AVX`).
  Launch: `llama-server -sm layer -ngl 999 -cmoe --moe-cache-mib 6000 -c 32768 -fa on`.
- Both localhost, chat completion, greedy (temp 0), engine-reported `predicted_per_second`.

## Result
| engine | decode (tok/s) | prefill (tok/s) |
| --- | ---: | ---: |
| **Strata 0.1.47** (prod) | **44.9** | ~150 |
| **llama.cpp** (moe-cache, no-AVX2) | **6.3** | 4.8 |

Quality is identical (same weights): the tricky sample (r_count 3, neuner 20, reverse EDALOKOHCS,
op_order) passes identically - the gap is purely throughput (~**7x** decode, ~30x prefill).

## Findings
1. **Upstream llama.cpp default build SIGILLs** on Ivy Bridge (it emits AVX2 intrinsics the CPU
   doesn't have); only a no-AVX2 rebuild (`GGML_AVX2=OFF` + `/arch:AVX`) runs at all.
2. Even rebuilt with the MoE expert cache, llama.cpp decodes at ~6 tok/s on this class of CPU: its
   MoE CPU-expert path runs near-baseline SIMD with no expert profiling / DRAM-streaming.
3. **Strata is uniquely adapted to exactly this hardware** (`STRATA_ISA_FLOOR=avx`, expert
   profiling, DRAM streaming of CPU-resident experts, hardware-tuned decode). This rig is the case
   Strata was built for; a generic llama.cpp cannot compete here without substantial SIMD/offload
   engineering.

Raw probe + build notes: `bench/results/2026-10-24-community-rtx-3060-noavx2-llamacpp-vs-strata/`