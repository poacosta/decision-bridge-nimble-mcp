# Hardware guide

Nimble is a 9-billion-parameter model. It does not run comfortably on every computer, and the bridge cannot change that: the bridge itself is a small Python process, while all the heavy work happens inside Ollama. This page helps you judge, before downloading about 9.5 GB, whether your machine can run `nimble:latest` well enough to use from an MCP client.

Nothing here is a guarantee. Figures are labelled **observed** (measured on a named machine) or **estimated** (derived from the model's size). The only reliable answer for your machine is to measure it ([below](#check-your-own-machine)).

## What the model needs

| | `nimble:latest` |
|---|---|
| Parameters | 8.95 B ([model page](https://ollama.com/library/nimble:latest)) |
| Quantization | Q8_0 (about 8.5 bits per weight) |
| Download / disk | about 9.5 GB |
| Context | about 8K tokens (`num_ctx` 8194) |
| Ollama | 0.35 or newer |

**Estimated memory while loaded: about 10.5-11.5 GB.** The weights account for about 9.5 GB (8.95 B × 8.5 bits ÷ 8). The context is capped at about 8K tokens, so the cache for it is small by comparison, and Ollama adds some runtime overhead on top. To get good speed, all of this should fit in GPU memory (VRAM, or unified memory on Apple Silicon). Whatever does not fit runs on the CPU, which is much slower.

Only one tag is published, and it is Q8_0. A smaller quantization (such as Q4) would need less memory, but no official one exists. This bridge only supports Nimble models that report decision support ([configuration](configuration.md#model-rules)).

## What "comfortable" means here

Using the MCP server comfortably means:

- A warm decision returns in a few seconds, well inside the default 120 s deadline (`DECISION_BRIDGE_REQUEST_TIMEOUT_SECONDS`).
- The model stays fully on the GPU while your MCP client, editor, and browser are also open.
- The machine does not swap, which would slow every other application too.

The MCP client also uses memory: an IDE or a desktop agent with a few browser tabs can easily take 4-8 GB. Count it, because it shares the machine with the model.

## Hardware tiers

### Apple Silicon (M1 and later)

Ollama uses the GPU through Metal. Memory is unified, so the model and every open application share the same RAM.

| Unified memory | Expectation |
|---|---|
| 8 GB | **Not viable.** The model is larger than total memory. |
| 16 GB | **Works, tight.** Observed: M1 Pro, 16 GB, warm median about 1.9 s per request ([evaluation](evaluation.md#observations-from-one-run)). macOS lets the GPU use only part of unified memory (roughly two thirds on smaller machines), and this model sits at that edge, so part of it may run on the CPU. Close heavy applications while you use it. |
| 24-32 GB | **Comfortable** (estimated). Room for the model plus an editor, browser, and agent. |
| 36 GB or more | **Comfortable with headroom** (estimated). |

Intel Macs have no Metal acceleration for Ollama in practice and fall into the CPU-only tier.

### NVIDIA GPUs (Windows and Linux)

Ollama supports compute capability 5.0 and newer with driver 550 or newer (5.0-6.2 need driver 570 or newer); see [Ollama's GPU documentation](https://docs.ollama.com/gpu).

| VRAM | Expectation (estimated) |
|---|---|
| 16 GB or more (for example RTX 4060 Ti 16 GB, 4070 Ti Super, 4080, 5070 Ti, 3090, 4090) | **Comfortable.** The whole model fits with room to spare. |
| 12 GB (for example RTX 3060 12 GB, 4070) | **Fits, tight.** Little margin left; a desktop compositor or another GPU application can push part of the model onto the CPU. |
| 8-10 GB | **Partial offload.** Ollama splits the model between GPU and CPU. It works, but expect several times slower responses. Have at least 32 GB of system RAM. |
| under 8 GB | Effectively CPU-only. |

### AMD GPUs

Linux needs the ROCm v7 driver; Windows needs a ROCm v7 / HIP7-capable driver and supports a shorter list of cards (Radeon RX 7600-7900 XTX, Radeon PRO W7500-W7900). The VRAM guidance is the same as for NVIDIA. Cards outside the ROCm lists may still work through Ollama's Vulkan backend. See [Ollama's GPU documentation](https://docs.ollama.com/gpu) for the current lists.

### CPU only

It works, but it is not comfortable for interactive agent use.

- **System RAM:** 16 GB is the bare minimum and leaves almost nothing for other applications. Prefer 32 GB.
- **Speed:** expect responses in tens of seconds rather than seconds (estimated, depends heavily on the CPU and the request size). Raise `DECISION_BRIDGE_REQUEST_TIMEOUT_SECONDS` before you start, and keep `DECISION_BRIDGE_MAX_CONCURRENCY` at `1`.

## Disk

- About 9.5 GB free for the model, plus margin for Ollama updates.
- An SSD is strongly recommended: a cold start reads the whole model from disk, and a hard drive makes that first request much slower.

## Settings that help on modest hardware

These are bridge settings ([configuration](configuration.md)). The bridge never configures Ollama for you.

| Setting | Why |
|---|---|
| `DECISION_BRIDGE_KEEP_ALIVE=30m` | Keeps the model loaded between agent calls, so you pay the cold start once. Use `0` instead if you need the memory back right after each call. |
| `DECISION_BRIDGE_REQUEST_TIMEOUT_SECONDS` | Raise it (for example to `300`) on partial-offload or CPU-only machines. A timeout does not stop Ollama computing. |
| `DECISION_BRIDGE_MAX_CONCURRENCY=1` (default) | Parallel requests compete for the same GPU and memory; on limited hardware they slow each other down. |

Keep requests short. Evidence counts toward the ~8K-token budget, and longer prompts take longer on any hardware.

## Check your own machine

1. **See how much memory you have.**

   | Platform | Command |
   |---|---|
   | macOS | `sysctl -n hw.memsize` (bytes) and `sysctl -n machdep.cpu.brand_string` |
   | Linux | `free -h`, plus `nvidia-smi` or `rocm-smi` if you have a GPU |
   | Windows | Task Manager → Performance (Memory and GPU, "Dedicated GPU memory"), or `nvidia-smi` |

2. **Run a real request after installing the model**, then look at where Ollama placed it:

   ```sh
   decision-bridge doctor --smoke-test
   ollama ps
   ```

   The `PROCESSOR` column is the key figure. `100% GPU` means the model fits. A split such as `40%/60% CPU/GPU` means part of it runs on the CPU, and responses will be slower. `SIZE` shows how much memory the loaded model actually uses on your machine.

3. **Time a warm request** in the same session, with your usual applications open:

   ```sh
   decision-bridge decide --input examples/task-routing.json
   ```

   The response includes `metadata.duration_ms`. For a broader picture, run the [evaluation script](evaluation.md#2-evaluation-script).

## Reference points

| Machine | Runtime | Latency per request | Source |
|---|---|---|---|
| Apple M1 Pro, 16 GB | Ollama 0.35.0 | median about 1.9 s, warm | Observed in this project ([evaluation](evaluation.md)) |
| Apple M5 Pro, 64 GB | Upstream Python/MLX runner | median 444 ms | [Nimble README](https://github.com/bespokelabsai/nimble) |
| NVIDIA H100 | Upstream Python runner | median 106 ms | [Nimble README](https://github.com/bespokelabsai/nimble) |

The upstream figures use a different runtime and different requests, so they are not directly comparable to Ollama or to each other. They show the order of magnitude between hardware classes, not what you will get.

If you measure Nimble on hardware not listed here, a report with your machine, Ollama version, model digest, `ollama ps` output, and latency is welcome ([contributing](../CONTRIBUTING.md)).
