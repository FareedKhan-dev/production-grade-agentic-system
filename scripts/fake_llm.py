"""An OpenAI-compatible stand-in for load tests: it answers every request after a fixed delay.

A load test against a real provider measures the provider (and its rate limits), and costs
money. This server lets the test measure the system itself. It implements what the app
calls: /v1/chat/completions, with and without streaming, and /v1/embeddings.

Run it next to the API and point the API at it:

    FAKE_LLM_DELAY=1.0 uv run uvicorn scripts.fake_llm:app --host 0.0.0.0 --port 9000

    # in the API's env file
    OPENAI_BASE_URL=http://<this host>:9000/v1
    DEFAULT_LLM_MODEL=fake-model         # any name that is not a built-in preset
    LLM_FALLBACK_MODELS=
    LONG_TERM_MEMORY_MODEL=fake-model
"""

import asyncio
import hashlib
import json
import os
import time
import uuid

from fastapi import (
    FastAPI,
    Request,
)
from fastapi.responses import (
    JSONResponse,
    StreamingResponse,
)

DELAY = float(os.getenv("FAKE_LLM_DELAY", "1.0"))
ANSWER = (
    "Quantum computing uses qubits, which can be in a superposition of 0 and 1, so some problems "
    "can be explored in parallel. It is still early, but it may speed up chemistry and cryptography."
)
# mem0 asks for a JSON object when it extracts memories; an empty list adds nothing
MEMORY_ANSWER = json.dumps({"memory": []})

app = FastAPI(title="Fake LLM")


def _completion_id() -> str:
    return f"chatcmpl-{uuid.uuid4().hex}"


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    """Answer a chat completion request after DELAY seconds."""
    body = await request.json()
    await asyncio.sleep(DELAY)
    response_format = (body.get("response_format") or {}).get("type")
    content = MEMORY_ANSWER if response_format in ("json_object", "json_schema") else ANSWER
    model = body.get("model", "fake-model")
    created = int(time.time())
    usage = {"prompt_tokens": 100, "completion_tokens": 40, "total_tokens": 140}

    if not body.get("stream"):
        return JSONResponse(
            {
                "id": _completion_id(),
                "object": "chat.completion",
                "created": created,
                "model": model,
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}
                ],
                "usage": usage,
            }
        )

    completion_id = _completion_id()

    async def events():
        for start in range(0, len(content), 12):
            chunk = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "delta": {"content": content[start : start + 12]}, "finish_reason": None}],
            }
            yield f"data: {json.dumps(chunk)}\n\n"
        last = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "usage": usage,
        }
        yield f"data: {json.dumps(last)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


@app.post("/v1/embeddings")
async def embeddings(request: Request):
    """Return a deterministic vector for each input, sized as requested."""
    body = await request.json()
    inputs = body.get("input")
    inputs = [inputs] if isinstance(inputs, str) else inputs
    dimensions = int(body.get("dimensions") or 1536)
    data = []
    for index, text in enumerate(inputs):
        seed = hashlib.sha256(str(text).encode()).digest()
        vector = [((seed[i % len(seed)] / 255.0) - 0.5) for i in range(dimensions)]
        data.append({"object": "embedding", "index": index, "embedding": vector})
    return JSONResponse(
        {"object": "list", "data": data, "model": body.get("model"), "usage": {"prompt_tokens": 8, "total_tokens": 8}}
    )
