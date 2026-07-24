"""FastAPI vs Litestar, on our actual problem: AS2 shapes over the House."""
import asyncio, time, statistics, json, logging, warnings
logging.disable(logging.INFO); warnings.filterwarnings("ignore")

# ---- a realistic AS2 Create/Note, including the awkward polymorphic bits ----
AS2 = {
    "@context": "https://www.w3.org/ns/activitystreams",
    "type": "Create",
    "actor": "https://hub/actors/rosemary_nasrin",
    "object": {
        "type": "Note",
        "attributedTo": "https://hub/actors/rosemary_nasrin",
        "to": ["https://hub/actors/trevor_mahmood"],
        "cc": [],
        "summary": "flaky tests",
        "content": "The payment suite fails about one run in five. Any idea?" * 4,
        "inReplyTo": None,
        "published": "2026-07-24T14:02:11Z",
    },
}

# ---------------------------------------------------------------- FastAPI
from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict, Field

class PNote(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    type: str = "Note"
    attributed_to: str = Field(alias="attributedTo")
    to: str | list[str]
    cc: str | list[str] = []
    summary: str | None = None
    content: str = ""
    in_reply_to: str | None = Field(default=None, alias="inReplyTo")
    published: str = ""

class PCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    context: str = Field(default="https://www.w3.org/ns/activitystreams", alias="@context")
    type: str = "Create"
    actor: str
    object: PNote

fast = FastAPI()

@fast.post("/actors/{name}/outbox")
async def p_send(name: str, activity: PCreate) -> PNote:
    return activity.object

@fast.get("/actors/{name}/inbox")
async def p_inbox(name: str) -> list[PNote]:
    return [PNote.model_validate(AS2["object"]) for _ in range(20)]

# ---------------------------------------------------------------- Litestar
import msgspec
from litestar import Litestar, get, post

class MNote(msgspec.Struct, rename={"attributed_to": "attributedTo", "in_reply_to": "inReplyTo"}):
    attributed_to: str
    to: str | list[str]
    type: str = "Note"
    cc: str | list[str] = []
    summary: str | None = None
    content: str = ""
    in_reply_to: str | None = None
    published: str = ""

class MCreate(msgspec.Struct, rename={"context": "@context"}):
    actor: str
    object: MNote
    context: str = "https://www.w3.org/ns/activitystreams"
    type: str = "Create"

@post("/actors/{name:str}/outbox")
async def m_send(name: str, data: MCreate) -> MNote:
    return data.object

@get("/actors/{name:str}/inbox")
async def m_inbox(name: str) -> list[MNote]:
    return [msgspec.convert(AS2["object"], MNote) for _ in range(20)]

lite = Litestar(route_handlers=[m_send, m_inbox], openapi_config=None)

# ------------------------------------------------------------------ bench
import httpx

async def bench(app, label, n=1500):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        await c.post("/actors/x/outbox", json=AS2)          # warm
        t0 = time.perf_counter()
        for _ in range(n):
            await c.post("/actors/x/outbox", json=AS2)
        send = time.perf_counter() - t0
        t0 = time.perf_counter()
        for _ in range(n):
            await c.get("/actors/x/inbox")
        inbox = time.perf_counter() - t0
    print(f"  {label:10} send {n/send:8.0f} req/s   inbox(20 notes) {n/inbox:8.0f} req/s")
    return n/send, n/inbox

async def main():
    print("round-trip through the ASGI stack, no network:")
    f = await bench(fast, "FastAPI")
    l = await bench(lite, "Litestar")
    print(f"\n  Litestar is {l[0]/f[0]:.1f}x on send, {l[1]/f[1]:.1f}x on inbox")

    print("\npure (de)serialisation of one AS2 Create, 20k iterations:")
    raw = json.dumps(AS2).encode()
    t0 = time.perf_counter()
    for _ in range(20000):
        PCreate.model_validate_json(raw).model_dump_json(by_alias=True)
    p = time.perf_counter() - t0
    dec = msgspec.json.Decoder(MCreate); enc = msgspec.json.Encoder()
    t0 = time.perf_counter()
    for _ in range(20000):
        enc.encode(dec.decode(raw))
    m = time.perf_counter() - t0
    print(f"  pydantic {p*1e6/20000:7.1f} us/op     msgspec {m*1e6/20000:7.1f} us/op     -> {p/m:.1f}x")

asyncio.run(main())
