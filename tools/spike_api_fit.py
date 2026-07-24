"""The requirements specific to us, not to APIs in general."""
import json, subprocess, sys, warnings, logging
warnings.filterwarnings("ignore"); logging.disable(logging.INFO)

FOREIGN = {
    "type": "Note", "attributedTo": "a", "to": ["b"], "content": "hi",
    "x:mood": "cheerful",                       # an extension we have never seen
    "sensitive": True,                           # a real AS2 property we do not model
    "tag": [{"type": "Hashtag", "name": "#ops"}],
}

print("1. ADR 0006 — unknown properties must survive a round trip\n")

from pydantic import BaseModel, ConfigDict, Field
class PNote(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")
    type: str = "Note"
    attributed_to: str = Field(alias="attributedTo")
    to: str | list[str]
    content: str = ""
p = PNote.model_validate(FOREIGN)
out = p.model_dump(by_alias=True)
kept = {k for k in ("x:mood", "sensitive", "tag") if k in out}
print(f"   pydantic  extra='allow'      keeps {sorted(kept) or 'nothing'}")

import msgspec
class MNote(msgspec.Struct, rename={"attributed_to": "attributedTo"}):
    attributed_to: str
    to: str | list[str]
    type: str = "Note"
    content: str = ""
m = msgspec.convert(FOREIGN, MNote)
out_m = json.loads(msgspec.json.encode(m))
kept_m = {k for k in ("x:mood", "sensitive", "tag") if k in out_m}
print(f"   msgspec   Struct             keeps {sorted(kept_m) or 'nothing'}")

# msgspec's idiomatic answer: decode twice — typed view + raw dict
raw = dict(FOREIGN)
print(f"   msgspec   Struct + raw dict  keeps {sorted(k for k in ('x:mood','sensitive','tag') if k in raw)}")
print("     (we already store the raw document alongside typed columns — ADR 0006)")

print("\n2. Does the schema still get generated? (for MCP tools, not for humans)\n")
from litestar import Litestar, post
@post("/outbox")
async def h(data: MNote) -> MNote: ...
app = Litestar(route_handlers=[h])
schema = app.openapi_schema.to_schema()
print(f"   litestar  openapi {schema['openapi']}  camelCase kept:",
      "attributedTo" in schema["components"]["schemas"]["MNote"]["properties"])

print("\n3. Cold start — matters for a container\n")
for label, mod in (("fastapi+pydantic", "import fastapi, pydantic"), ("litestar+msgspec", "import litestar, msgspec")):
    times = []
    for _ in range(3):
        r = subprocess.run([sys.executable, "-X", "importtime", "-c", mod],
                           capture_output=True, text=True)
        total = sum(int(l.split("|")[1]) for l in r.stderr.splitlines()[1:] if "|" in l)
        times.append(total/1e6)
    print(f"   {label:18} {min(times):5.2f} s cumulative import")
