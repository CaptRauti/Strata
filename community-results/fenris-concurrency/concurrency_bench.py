"""Concurrency-Benchmark v2: 1 vs 2 vs 4 parallele Streams.
Wertet pro Request die ENGINE-Zeile aus (decode tok/s, prompt read) statt der groben HTTP-Zeit,
und misst parallel wallclock + TTFT. Engine-Zeilen werden aus dem Log-Delta pro Lauf extrahiert.
"""
import json, threading, time, urllib.request, subprocess, sys, os, statistics, re

HOST = "http://192.168.1.135"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
KEY = sys.argv[3] if len(sys.argv) > 3 else os.environ.get("STRATA_API_KEY", "")
URL = f"{HOST}:{PORT}/v1/chat/completions"
ENGINE_LOG = sys.argv[2] if len(sys.argv) > 2 else r"C:\Users\ki\Strata\strata-swift-iq2_xs.log"

FILLER = ("The quarterly logistics review covered depot routing, manifest accuracy, fleet utilisation "
          "and the backorder backlog in the northern region. ")
PROMPT = (FILLER * 40) + ("Summarize the operational risk in one sentence.")  # ~1.1k tokens

LINE_RE = re.compile(
    r"prompt (\d+) tokens = (\d+) reused \+ (\d+) read in (\d+) ms \(([\d.]+) tok/s\), "
    r"(\d+) generated in (\d+) ms \(([\d.]+) tok/s\), drafts accepted (\d+) of (\d+)")

def read_engine_lines():
    try:
        with open(ENGINE_LOG, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception:
        return ""

def gen(prompt, max_tokens=80, tid=0, results=None):
    body = json.dumps({
        "model": "swift-1.5-iq2_xs",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0, "max_tokens": max_tokens,
    }).encode()
    req = urllib.request.Request(URL, data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {KEY}"})
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            json.load(r)
            dt = time.monotonic() - t0
            results[tid] = {"dt": dt}
    except Exception as e:
        results[tid] = {"err": str(e)[:100]}

def run_concurrency(n, runs=3):
    print(f"\n=== {n} parallele Streams (Port {PORT}) ===")
    for r in range(runs):
        before = read_engine_lines()
        results = {}
        threads = [threading.Thread(target=gen, args=(PROMPT, 80, i, results)) for i in range(n)]
        t0 = time.monotonic()
        for t in threads: t.start()
        for t in threads: t.join()
        wall = time.monotonic() - t0
        after = read_engine_lines()
        delta = after[len(before):] if after.startswith(before) else after
        lines = LINE_RE.findall(delta)
        ok = [v for v in results.values() if "dt" in v]
        if not ok:
            print(f"  run {r+1}: HTTP-FEHLER {[v for v in results.values() if 'err' in v][:2]}")
            continue
        decodes = [float(l[7]) for l in lines] if lines else []
        ttfts = [v["dt"] for v in ok]
        wall_s = wall
        n_gen = [int(l[5]) for l in lines] if lines else []
        print(f"  run {r+1}: wall={wall_s:.1f}s | HTTP ok={len(ok)}/{n}"
              + (f" | ENGINE decode: {statistics.median(decodes):.1f} tok/s (min {min(decodes):.1f}, max {max(decodes):.1f})"
                 + f" über {len(decodes)} Engine-Zeile(n)" if decodes else " | keine Engine-Zeile im Delta")
              + f" | TTFT/req median {statistics.median(ttfts):.1f}s")

if __name__ == "__main__":
    for n in (1, 2, 4):
        run_concurrency(n)