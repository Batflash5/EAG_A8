"""FastAPI server — wraps flow.py and streams graph events to the browser via SSE."""
from __future__ import annotations

import asyncio
import json
import traceback
import uuid
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse

ROOT = Path(__file__).resolve().parent
SESSIONS_ROOT = ROOT / "state" / "sessions"
PYTHON = ROOT / ".venv" / "bin" / "python"

app = FastAPI()

_procs:      dict[str, asyncio.subprocess.Process] = {}
_logs:       dict[str, list[str]] = {}
_finals:     dict[str, str]       = {}
_exit_codes: dict[str, int]       = {}   # non-zero only


def _server_log(msg: str) -> None:
    """Print to the server terminal with flush so it appears immediately."""
    print(msg, flush=True)


# ── routes ────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return (ROOT / "frontend.html").read_text()


@app.post("/run")
async def run_query(body: dict) -> dict:
    query = (body.get("query") or "").strip()
    if not query:
        return {"error": "query is required"}

    sid = f"s8-{uuid.uuid4().hex[:8]}"
    _logs[sid] = []
    _finals[sid] = ""

    try:
        proc = await asyncio.create_subprocess_exec(
            str(PYTHON), "flow.py", "--session", sid, query,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(ROOT),
        )
    except Exception as exc:
        tb = traceback.format_exc()
        _server_log(f"\n[app] ERROR spawning subprocess for session {sid}:\n{tb}")
        return {"error": f"Failed to start agent process: {exc}"}

    _procs[sid] = proc
    asyncio.create_task(_collect_stdout(sid, proc))
    _server_log(f"[app] session {sid} started (pid {proc.pid})")
    return {"session_id": sid}


@app.get("/stream/{session_id}")
async def stream_events(session_id: str) -> StreamingResponse:
    return StreamingResponse(
        _generate(session_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ── stdout collector ──────────────────────────────────────────────────────────

async def _collect_stdout(sid: str, proc: asyncio.subprocess.Process) -> None:
    try:
        async for raw in proc.stdout:
            _logs[sid].append(raw.decode("utf-8", errors="replace").rstrip())
    except Exception as exc:
        msg = f"[app] stdout read error for {sid}: {exc}"
        _server_log(msg)
        _logs[sid].append(msg)

    await proc.wait()
    rc = proc.returncode

    if rc != 0:
        msg = f"[app] flow.py exited with code {rc} for session {sid}"
        _server_log(msg)
        _logs[sid].append(msg)
        _exit_codes[sid] = rc
    else:
        _server_log(f"[app] session {sid} completed OK")

    # Extract final answer from FINAL: banner in stdout
    parts: list[str] = []
    capturing = False
    for line in _logs[sid]:
        if "FINAL:" in line:
            capturing = True
            tail = line.split("FINAL:", 1)[1].strip()
            if tail:
                parts.append(tail)
        elif capturing:
            if "═" * 8 in line:
                break
            parts.append(line)
    _finals[sid] = "\n".join(parts).strip()


# ── SSE generator ─────────────────────────────────────────────────────────────

def _read_graph(session_id: str) -> dict | None:
    p = SESSIONS_ROOT / session_id / "graph.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception as exc:
        _server_log(f"[app] failed to parse graph.json for {session_id}: {exc}")
        return None


def _diff_graph(
    data: dict,
    seen: dict[str, str],
    sent_edges: set[tuple[str, str]],
) -> tuple[list[dict], list[dict]]:
    changed_nodes: list[dict] = []
    new_edges: list[dict] = []

    for node in data.get("nodes", []):
        nid    = node.get("id", "")
        status = node.get("status", "pending")
        if seen.get(nid) == status:
            continue
        seen[nid] = status
        result = node.get("result") or {}
        changed_nodes.append({
            "id":      nid,
            "skill":   node.get("skill", "?"),
            "status":  status,
            "elapsed": result.get("elapsed_s") if isinstance(result, dict) else None,
            "error":   result.get("error")     if isinstance(result, dict) else None,
            "question": (node.get("metadata") or {}).get("question", ""),
            "output":  result.get("output")    if isinstance(result, dict) else None,
        })

    for edge in data.get("edges", []):
        src, tgt = edge.get("source", ""), edge.get("target", "")
        key = (src, tgt)
        if key not in sent_edges:
            sent_edges.add(key)
            new_edges.append({"from": src, "to": tgt})

    return changed_nodes, new_edges


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


async def _generate(session_id: str):
    log_cursor = 0
    seen: dict[str, str] = {}
    sent_edges: set[tuple[str, str]] = set()

    try:
        while True:
            # flush new log lines
            curr = _logs.get(session_id, [])
            while log_cursor < len(curr):
                yield _sse("log", {"text": curr[log_cursor]})
                log_cursor += 1

            # diff graph
            data = _read_graph(session_id)
            if data:
                nodes, edges = _diff_graph(data, seen, sent_edges)
                if nodes or edges:
                    yield _sse("graph", {"nodes": nodes, "edges": edges})

            # check done
            proc = _procs.get(session_id)
            if proc is not None and proc.returncode is not None:
                # final log flush
                curr = _logs.get(session_id, [])
                while log_cursor < len(curr):
                    yield _sse("log", {"text": curr[log_cursor]})
                    log_cursor += 1

                # final graph snapshot
                data = _read_graph(session_id)
                if data:
                    nodes, edges = _diff_graph(data, seen, sent_edges)
                    if nodes or edges:
                        yield _sse("graph", {"nodes": nodes, "edges": edges})

                # surface non-zero exit code as an explicit error event
                rc = _exit_codes.get(session_id)
                if rc is not None:
                    yield _sse("run_error", {
                        "message": f"Agent process exited with code {rc}. "
                                   f"Check the execution log for details.",
                        "exit_code": rc,
                    })

                yield _sse("done", {"final_answer": _finals.get(session_id, "")})
                return

            await asyncio.sleep(0.45)

    except asyncio.CancelledError:
        pass  # client disconnected — normal
    except Exception as exc:
        tb = traceback.format_exc()
        _server_log(f"\n[app] ERROR in SSE generator for {session_id}:\n{tb}")
        try:
            yield _sse("run_error", {"message": str(exc), "traceback": tb})
        except Exception:
            pass
