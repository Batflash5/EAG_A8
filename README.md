# EAG Assignment 8 — Growing-Graph Multi-Skill Agent (Session 8)

A multi-role AI agent built on a **dynamically growing NetworkX DAG**, where each node is a distinct skill (Planner, Researcher, Coder, Formatter, Critic, etc.) that is dispatched in parallel via an MCP stdio server, coordinated by a FastAPI backend, and visualised in real time through a browser-based DAG explorer.

---

## Table of Contents

- [Architecture Overview](#architecture-overview)
- [Project Structure](#project-structure)
- [Skills Catalogue](#skills-catalogue)
- [Setup & Installation](#setup--installation)
- [Running the Project](#running-the-project)
- [Query Run Logs](#query-run-logs)
  - [Query 1 — Simple Greeting](#query-1--simple-greeting)
  - [Query 2 — Web Fetch + Distillation](#query-2--web-fetch--distillation)
  - [Query 3 — Parallel Fan-out + Coder](#query-3--parallel-fan-out--coder)
  - [Query 4 — Graceful Failure Handling](#query-4--graceful-failure-handling)
  - [Query 5 — Parallel Research + Coder (two-part)](#query-5--parallel-research--coder-two-part)
  - [Session s8-d442a563 — GDP Comparison Fan-out](#session-s8-d442a563--gdp-comparison-fan-out)
  - [Session s8-231fc4df — Haiku with Critic Re-plan Loop](#session-s8-231fc4df--haiku-with-critic-re-plan-loop)
  - [Session s8-dbfe2b67 — Wikipedia Fetch + Spanish Translation](#session-s8-dbfe2b67--wikipedia-fetch--spanish-translation)
- [Key Design Decisions](#key-design-decisions)
- [Known Fixes Applied](#known-fixes-applied)

---

## Architecture Overview

```
Browser (frontend.html)
      │  HTTP POST /run  ──►  app.py (FastAPI)
      │  GET /stream/{sid} ◄── SSE events (log, graph, done)
      │
      └─► flow.py  (Executor)
               │
               ├─► Planner skill  ──►  gateway (LLM)
               │        │ emits NodeSpec DAG
               │
               ├─► [parallel] Researcher × N  ──►  mcp_runner.py
               │                                        │
               │                                  MCP stdio subprocess
               │                                  (mcp_server.py)
               │                                  web_search / fetch_url
               │
               ├─► Coder  ──►  sandbox_executor (sandboxed Python)
               ├─► Distiller / Critic / Summariser / Translator
               └─► Formatter  ──►  final answer
                        │
                   persistence.py  (atomic JSON writes per node)
                   memory.py       (FAISS vector memory)
                   artifacts.py    (content-addressed blob store)
```

**Key properties:**
- The DAG grows at runtime — the Planner emits `NodeSpec` objects that the Executor adds as graph nodes.
- Parallel nodes (e.g., three Researchers covering three countries) run concurrently via `asyncio.gather`.
- Every node result is persisted atomically so a crash mid-run does not corrupt previous snapshots.
- The browser receives live DAG diffs over SSE — no polling, no full-page refresh.

---

## Project Structure

```
eag_assignment8/
├── app.py              # FastAPI server — /run, /stream SSE, graph diff
├── flow.py             # Executor: grows the DAG, dispatches nodes, persists state
├── skills.py           # Skill registry, prompt rendering, run_skill dispatcher
├── mcp_runner.py       # Multi-turn tool-use loop over MCP stdio subprocess
├── mcp_server.py       # MCP server — web_search, fetch_url, sandbox tools
├── action.py           # Action layer: wraps MCP tool calls
├── decision.py         # Decision / recovery logic
├── perception.py       # Perception layer
├── memory.py           # FAISS-backed vector memory with keyword fallback
├── vector_index.py     # FAISS IndexFlatIP wrapper (cosine similarity)
├── artifacts.py        # Content-addressed artifact store (sha256 key)
├── persistence.py      # SessionStore — atomic JSON writes, graph.json
├── schemas.py          # Pydantic models: AgentResult, NodeState, NodeSpec, etc.
├── sandbox.py          # Sandboxed Python execution (subprocess isolation)
├── replay.py           # Session replay from persisted node files
├── recovery.py         # Recovery subgraph generation on node failure
├── gateway.py          # Gateway client (LLM(), embed(), ensure_gateway())
├── gateway/            # LLM Gateway V8 — provider failover, router, embedders
│   └── main.py
├── agent_config.yaml   # Skills catalogue — prompts, tools_allowed, temperature
├── prompts/            # Per-skill system prompt markdown files
├── frontend.html       # Single-file browser DAG explorer (vis-network)
├── requirements.txt    # Core pip dependencies
├── pyproject.toml      # uv project definition
├── state/              # Runtime state (sessions, memory, artifacts, FAISS index)
└── query*.log          # Captured run logs (see below)
```

---

## Skills Catalogue

Defined in [`agent_config.yaml`](agent_config.yaml):

| Skill | Tools | Role |
|---|---|---|
| `planner` | — | Decomposes queries into a DAG; synthesises recovery subgraphs on failure |
| `researcher` | `web_search`, `fetch_url` | Multi-step web research; produces normalised text |
| `retriever` | `search_knowledge` | Searches the FAISS vector index for relevant memory |
| `distiller` | — | Extracts structured fields from raw text |
| `summariser` | — | Condenses long content |
| `critic` | — | Pass/fail evaluator inserted on Distiller edges |
| `formatter` | — | Renders the final answer for the user (terminal node) |
| `coder` | — | Writes a self-contained Python script from upstream findings |
| `sandbox_executor` | — | Runs the Coder's script in an isolated subprocess |
| `translator` | — | Translates text to a specified target language |
| `browser` | — | Stub — reserved for Session 9 |

---

## Setup & Installation

### Prerequisites

- **Conda environment** `eag_a6` with all dependencies installed, **or** install manually:

```bash
pip install -r requirements.txt
# or with uv:
uv sync
```

Key packages: `mcp[cli]`, `fastapi`, `httpx`, `ddgs`, `tavily-python`, `crawl4ai`, `python-dotenv`, `pydantic>=2.13`, `faiss-cpu`, `numpy`, `networkx`, `pyyaml`.

### Environment Variables

Create a `.env` file in the project root (the `env` file already present contains keys — rename/copy it):

```
TAVILY_API_KEY="..."
GEMINI_API_KEY="..."
GROQ_API_KEY="..."
GITHUB_ACCESS_TOKEN="..."
CEREBRAS_API_KEY="..."
OPEN_ROUTER_API_KEY="..."
NVIDIA_API_KEY="..."
```

> **Important:** All three processes (gateway, app, flow) must be started from a terminal with `conda activate eag_a6` — the MCP subprocess is spawned using `CONDA_PREFIX` to resolve the correct Python interpreter.

---

## Running the Project

Open **three terminals**, each with `conda activate eag_a6`:

**Terminal 1 — LLM Gateway**
```bash
cd gateway
uvicorn main:app --host 0.0.0.0 --port 8108
```

**Terminal 2 — FastAPI App Server**
```bash
uvicorn app:app --host 0.0.0.0 --port 8000 --reload
```

**Terminal 3 — (Optional) Direct CLI run**
```bash
python flow.py "Your query here"
```

Then open **http://localhost:8000** in your browser to use the DAG Explorer UI.

---

## Query Run Logs

The following logs capture real agent runs end-to-end, showing the session ID, nodes executed, timing, and final answer.

---

### Query 1 — Simple Greeting

**Session:** `s8-86507671`  
**Query:** `Say hello.`

```
══════════════════════════════════════════════════════════════════════════════
session s8-86507671  ─  query: Say hello.
══════════════════════════════════════════════════════════════════════════════
[n:1] planner            complete (4.0s)
[n:2] formatter          complete (5.0s)

══════════════════════════════════════════════════════════════════════════════
FINAL: Hello! How can I help you today?
══════════════════════════════════════════════════════════════════════════════
```

**DAG shape:** `planner → formatter` (2 nodes, no tools)  
**Total time:** ~9 s

---

### Query 2 — Web Fetch + Distillation

**Session:** `s8-e572adcb`  
**Query:** `Fetch https://en.wikipedia.org/wiki/Claude_Shannon and tell me his birthdate, death date, and three key contributions to information theory.`

```
══════════════════════════════════════════════════════════════════════════════
session s8-e572adcb  ─  query: Fetch https://en.wikipedia.org/wiki/Claude_Shannon ...
══════════════════════════════════════════════════════════════════════════════
[n:1] planner            complete (4.4s)
[INIT].... → Crawl4AI 0.8.6
[FETCH]... ↓ https://en.wikipedia.org/wiki/Claude_Shannon  ✓ | ⏱: 1.57s
[SCRAPE].. ◆ https://en.wikipedia.org/wiki/Claude_Shannon  ✓ | ⏱: 0.24s
[COMPLETE] ● https://en.wikipedia.org/wiki/Claude_Shannon  ✓ | ⏱: 1.83s
[INIT].... → Crawl4AI 0.8.6
[FETCH]... ↓ https://en.wikipedia.org/wiki/Claude_Shannon  ✓ | ⏱: 1.36s
[SCRAPE].. ◆ https://en.wikipedia.org/wiki/Claude_Shannon  ✓ | ⏱: 0.22s
[COMPLETE] ● https://en.wikipedia.org/wiki/Claude_Shannon  ✓ | ⏱: 1.60s
[n:2] researcher         complete (17.9s)
[n:3] distiller          complete (3.0s)
[n:4] formatter          complete (3.9s)

══════════════════════════════════════════════════════════════════════════════
FINAL: Claude Shannon was born on April 30, 1916, and passed away on February 24,
2001. His three key contributions to information theory include:
1) Establishing the 'bit' as the fundamental unit of information linked to entropy;
2) Developing the mathematical model for communication systems and channel capacity;
3) Establishing the connection between Boolean algebra and digital switching circuits.
══════════════════════════════════════════════════════════════════════════════
```

**DAG shape:** `planner → researcher → distiller → formatter` (4 nodes, fetch_url tool used twice)  
**Total time:** ~29 s

---

### Query 3 — Parallel Fan-out + Coder

**Session:** `s8-ff77e5f0`  
**Query:** `Find the populations of London, Paris, Berlin and tell me which two are closest in size.`

```
══════════════════════════════════════════════════════════════════════════════
session s8-ff77e5f0  ─  query: Find the populations of London, Paris, Berlin ...
══════════════════════════════════════════════════════════════════════════════
[n:1] planner            complete (4.5s)
[n:2] researcher         complete (20.6s)   ← London branch
[n:3] researcher         complete (16.0s)   ← Paris branch
[n:4] researcher         complete (32.3s)   ← Berlin branch (fetched businesslocationcenter.de)
[n:5] coder              complete (4.0s)
[n:6] formatter          complete (3.0s)
[n:7] sandbox_executor   complete (0.0s)

══════════════════════════════════════════════════════════════════════════════
FINAL:
1. London: 9.1 million (Greater London)
2. Berlin: 3.9 million
3. Paris: 2.05 million (City proper)

The two cities closest in size are Berlin and Paris,
with a population difference of approximately 1.85 million.
══════════════════════════════════════════════════════════════════════════════
```

**DAG shape:** `planner → [researcher × 3 in parallel] → coder → sandbox_executor → formatter` (7 nodes)  
**Parallel speedup:** 3 researchers ran concurrently; wall-clock ≈ 32 s vs. sequential ≈ 69 s  

---

### Query 4 — Graceful Failure Handling

**Session:** `s8-0103e09d`  
**Query:** `Read /nonexistent/path.txt and tell me what's in it.`

```
══════════════════════════════════════════════════════════════════════════════
session s8-0103e09d  ─  query: Read /nonexistent/path.txt and tell me what's in it.
══════════════════════════════════════════════════════════════════════════════
[memory.read] 2 hit(s) visible to every skill this run
[n:1] planner            complete (4.1s)
[n:2] formatter          complete (3.8s)

══════════════════════════════════════════════════════════════════════════════
FINAL: I am unable to read the file at /nonexistent/path.txt because it does not exist.
══════════════════════════════════════════════════════════════════════════════
```

**DAG shape:** `planner → formatter` (2 nodes — planner correctly recognised no tools were applicable)  
**Notes:** Memory returned 2 FAISS hits from previous runs, demonstrating cross-session memory persistence.

---

### Query 5 — Parallel Research + Coder (two-part)

**Session:** `s8-d03387f8`  
**Query:** `For Lagos, Cairo, and Kinshasa, find current populations and growth rates and tell me which is growing fastest.`

**Part A — Research phase** (`query5a.log`):

```
══════════════════════════════════════════════════════════════════════════════
session s8-d03387f8  ─  query: For Lagos, Cairo, and Kinshasa ...
══════════════════════════════════════════════════════════════════════════════
[n:1] planner            complete (4.7s)
[INIT].... → Crawl4AI 0.8.6
[FETCH]... ↓ https://www.worldometers.info/...democratic-republic-of-the-congo-population  ✓ | ⏱: 4.07s
[FETCH]... ↓ https://www.macrotrends.net/...lagos/population                               ✓ | ⏱: 1.55s
[FETCH]... ↓ https://www.macrotrends.net/...cairo/population                               ✓ | ⏱: 1.39s
[n:2] researcher         complete (32.2s)   ← Kinshasa branch
[n:3] researcher         complete (38.4s)   ← Lagos branch
[n:4] researcher         complete (28.3s)   ← Cairo branch
```

**Part B — Compute + Format phase** (`query5b.log`):

```
[memory.read] 2 hit(s) visible to every skill this run
[n:5] coder              complete (4.4s)
[n:6] formatter          complete (3.7s)
[n:7] sandbox_executor   complete (0.0s)

══════════════════════════════════════════════════════════════════════════════
FINAL:
1. Cairo:    ~23,535,000 people | growth rate: 2.00%
2. Lagos:    ~17,804,000 people | growth rate: 3.78%
3. Kinshasa: ~17,000,000 people | growth rate: ~4.5% (midpoint estimate)

Kinshasa is the fastest-growing city, with an estimated growth rate of ~4.5%.
Sources: MacroTrends (Lagos, Cairo) and Worldometer (Kinshasa).
══════════════════════════════════════════════════════════════════════════════
```

**DAG shape:** `planner → [researcher × 3 in parallel] → coder → sandbox_executor → formatter` (7 nodes)  
**Notes:** The log is split across two files because the run was captured in two chunks (research phase then compute phase). Memory returned 2 hits, showing carry-over knowledge from Query 4's session.

---

### Session s8-d442a563 — GDP Comparison Fan-out

**Session:** `s8-d442a563`  
**Query:** `Find the current GDP per capita, capital city, and largest export of Brazil, South Korea, and Nigeria. Present a comparison across all three countries.`

**Node execution trace** (from persisted `state/sessions/s8-d442a563/nodes/`):

```
[n:1] planner            complete (9.6s)
       rationale: "Fetch required data for each country in parallel, then use a coder
                   node to structure the comparison before formatting the final response."
       emitted nodes:
         researcher[brazil]  ← "What is the current GDP per capita, capital city,
                                 and largest export of Brazil?"
         researcher[korea]   ← "What is the current GDP per capita, capital city,
                                 and largest export of South Korea?"
         researcher[nigeria] ← "What is the current GDP per capita, capital city,
                                 and largest export of Nigeria?"
         coder[compare]      ← receives all three researcher outputs
         formatter[out]      ← receives USER_QUERY + coder output

[n:2] researcher (brazil)   complete   — source: web search
[n:3] researcher (korea)    complete   — source: web search
[n:4] researcher (nigeria)  complete   — source: web search
  (ran in parallel)

[n:5] coder                 complete
       code: pandas DataFrame with GDP per capita, capital, largest export
       rationale: "organise findings into a structured DataFrame and print as table"

[n:6] formatter             complete (7.4s)
[n:7] sandbox_executor      complete (0.4s)
       exit_code: 0
       stdout:
         Country  GDP per capita (USD)  Capital           Largest Export
         Brazil               9564.58  Brasília          Soybeans/Iron Ore
     South Korea             36238.60  Seoul     Electrical/Electronic Equipment
         Nigeria              2447.64  Abuja             Crude Petroleum
```

**Final answer:**

```
The following table provides a comparison of the GDP per capita, capital city,
and largest export for Brazil, South Korea, and Nigeria:

| Country     | GDP per capita (USD) | Capital  | Largest Export                   |
|:------------|:---------------------|:---------|:---------------------------------|
| Brazil      | 9,564.58             | Brasília | Soybeans/Iron Ore                |
| South Korea | 36,238.60            | Seoul    | Electrical/Electronic Equipment  |
| Nigeria     | 2,447.64             | Abuja    | Crude Petroleum                  |
```

**DAG shape:** `planner → [researcher×3 in parallel] → coder → sandbox_executor → formatter` (7 nodes)  
**Memory:** 8 FAISS hits visible to every skill (including prior GDP facts from a related earlier run)  
**Notable:** Planner scoped each researcher to a single country via `metadata.question` (did NOT pass `USER_QUERY`) so each worker answered only its own sub-question, not all three.

---

### Session s8-231fc4df — Haiku with Critic Re-plan Loop

**Session:** `s8-231fc4df`  
**Query:** `Write a haiku about the concept of machine learning. Strict requirement: exactly 5 syllables in line 1, exactly 7 syllables in line 2, exactly 5 syllables in line 3.`

**Node execution trace** (12 nodes — multiple coder→critic→replanner cycles):

```
[n:1]  planner           complete (4.3s)
        rationale: "Generate a haiku and use a critic to verify the strict 5-7-5
                    syllable constraint before formatting."
        emitted: coder[writer] → critic[validator] → formatter[out]

[n:2]  coder (attempt 1) complete (4.0s)
        haiku: "Data finds the path,
               Patterns emerge from the code,
               Logic starts to learn."

[n:3]  critic (attempt 1) complete (181.5s)   ← FAIL verdict
        verdict: fail
        rationale: "After recounting, the critic initially thought the haiku
                    was wrong but then conceded it actually meets 5-7-5 — emitted
                    'fail' anyway (self-contradictory reasoning)."
        → orchestrator triggers RE-PLAN

[n:5]  planner (recovery) complete
        emits new coder[writer2] → critic[validator2] → formatter

[n:6]  coder (attempt 2) complete
[n:7]  sandbox_executor  complete (0.0s)
[n:8]  critic (attempt 2) complete   ← FAIL again
        → another RE-PLAN cycle

[n:10] planner (recovery 2) complete
[n:11] coder (attempt 3) complete (final attempt)
        haiku: "Data learns to think,
               Patterns emerge from the code,
               Silicon finds truth."

[n:12] critic (attempt 3) complete   ← PASS
        verdict: pass
        rationale: "Da-ta learns to think (5), Pat-terns e-merge from the code (7),
                    Si-li-con finds truth (5) — all syllable counts confirmed."

[n:13] formatter          complete (0.9s)
[n:14] sandbox_executor   complete (0.03s)
        stdout: "Data learns to think,
                Patterns emerge from the code,
                Silicon finds truth."
```

**Final answer:**

```
Data learns to think,
Patterns emerge from the code,
Silicon finds truth.
```

**DAG shape:** `planner → [coder → critic]×3 (re-plan loop) → formatter → sandbox_executor` (12 nodes total)  
**Memory:** 8 FAISS hits (3 related haiku entries from earlier runs of the same query)  
**Notable:** This session demonstrates the **critic-triggered re-plan loop** in action. The critic emitted contradictory reasoning and returned `fail` twice despite the haiku meeting the constraint. On the third coder attempt the critic finally returned `pass`. This showcases the recovery subgraph machinery in `recovery.py` — on critic failure, a new planner call is made to generate a fresh coder node rather than retrying the exact same code.

---

### Session s8-dbfe2b67 — Wikipedia Fetch + Spanish Translation

**Session:** `s8-dbfe2b67`  
**Query:** `Fetch the Wikipedia introduction for 'quantum entanglement' and translate the key explanation into Spanish.`

**Node execution trace** (from persisted `state/sessions/s8-dbfe2b67/nodes/`):

```
[n:1] planner      complete (8.1s)
       rationale: "Fetch the Wikipedia intro for quantum entanglement and translate
                   the key explanation into Spanish."
       emitted:
         researcher[r1]   ← "Fetch the Wikipedia introduction for quantum entanglement."
         translator[t1]   ← input: n:r1 | question: "Translate the key explanation into Spanish."
         formatter[out]   ← inputs: USER_QUERY + n:t1

[n:2] researcher      complete (34.2s)
       source: https://en.wikipedia.org/wiki/Quantum_entanglement
       findings: "Quantum entanglement is a physical phenomenon that occurs when a
                  group of particles is generated, interacts, or shares spatial
                  proximity in such a way that the quantum state of each particle
                  cannot be described independently of the state of the others,
                  even when the particles are separated by a large distance.
                  It is a fundamental feature of quantum mechanics that
                  distinguishes it from classical physics."

[n:3] translator     complete (181.7s)
       source_language: English
       target_language: Spanish
       translated_text: "El entrelazamiento cuántico es un fenómeno físico que
                         ocurre cuando un grupo de partículas se genera, interactúa
                         o comparte proximidad espacial de tal manera que el estado
                         cuántico de cada partícula no puede describirse
                         independientemente del estado de las demás, incluso cuando
                         las partículas están separadas por una gran distancia.
                         Es una característica fundamental de la mecánica cuántica
                         que la distingue de la física clásica."

[n:4] formatter      complete (14.7s)
```

**Final answer:**

```
El entrelazamiento cuántico es un fenómeno físico que ocurre cuando un grupo de
partículas se genera, interactúa o comparte proximidad espacial de tal manera que
el estado cuántico de cada partícula no puede describirse independientemente del
estado de las demás, incluso cuando las partículas están separadas por una gran
distancia. Es una característica fundamental de la mecánica cuántica que la
distingue de la física clásica.
```

**DAG shape:** `planner → researcher → translator → formatter` (4 nodes, strictly sequential)  
**Memory:** 8 FAISS hits (prior quantum entanglement and haiku runs visible — planner chose to re-fetch rather than use memory because the earlier hit was marked `tool_outcome`, not a full `fact`)  
**Notable:** The `translator` skill is a pure LLM node with no tool calls — it reads `findings` from the upstream researcher node via the `INPUTS` field. The planner correctly wired `researcher → translator` as a sequential dependency (translator listed `n:r1` in its inputs), so both nodes could not run in parallel.

---

## Key Design Decisions

| Decision | Rationale |
|---|---|
| **Per-node atomic JSON writes** | Crash-safe: a `SIGKILL` mid-write leaves the last good snapshot intact (write to `.tmp`, then `os.replace`) |
| **MCP subprocess per node** | No shared mutable state between skill invocations; each node gets a clean MCP session |
| **`CONDA_PREFIX` for subprocess Python** | Ensures the MCP server inherits the same conda environment regardless of which terminal launched the app |
| **FAISS vector memory with keyword fallback** | Vector search first; if no hits, keyword overlap is used — no silent failure |
| **SSE over polling** | The browser gets live DAG diffs pushed by the server; `graph.json` is diffed before each push to avoid redundant events |
| **`PYTHONIOENCODING=utf-8` on Popen** | Forces the `flow.py` child process to use UTF-8 stdout on Windows, preventing cp1252 encoding crashes from LLM output |

---

## Known Fixes Applied

The following bugs were found and fixed during development:

| File | Issue | Fix |
|---|---|---|
| `mcp_runner.py` | `sys.executable` resolved to wrong Python when gateway was started outside the conda env | Read `CONDA_PREFIX` env var to resolve the correct interpreter |
| `mcp_runner.py` | Windows ProactorEventLoop `ResourceWarning` noise on pipe teardown | `asyncio.sleep(0)` drain + `warnings.catch_warnings(ResourceWarning)` |
| `persistence.py` | `UnicodeEncodeError: cp1252` crash when web search results contained `\ufffd` | All file I/O uses `encoding="utf-8"` |
| `memory.py` | Same cp1252 issue on `memory.json` read/write | `encoding="utf-8"` on both `read_text` and `write_text` |
| `artifacts.py` | Artifact metadata JSON written without encoding | `encoding="utf-8"` added |
| `vector_index.py` | `index_ids.json` read/write without encoding | `encoding="utf-8"` added |
| `skills.py` | Prompt `.md` files and `agent_config.yaml` read without encoding | `encoding="utf-8"` added |
| `app.py` | `graph.json` read and `Popen` stdout without encoding | `encoding="utf-8"` + `PYTHONIOENCODING=utf-8` env var |
| `gateway/main.py` | `agent_routing.yaml` read without encoding | `encoding="utf-8"` added |
