import json
import sys
import yaml
sys.path.insert(0, ".")
from pathlib import Path
from src.app.agents.search import answer
from src.app.retrieval.store import ChunkRecord, InMemoryStore
store = InMemoryStore([ChunkRecord(**json.loads(line)) for line in
                       Path("data/chunks.jsonl").read_text(encoding="utf-8").splitlines()])
ev = yaml.safe_load(Path("tests/eval_questions.yaml").read_text())["questions"]
ok = 0
for item in ev:
    r = answer(item["q"], None, store)
    good = r.output["declined"] == item["out_of_scope"]
    ok += good
    mark = "OK" if good else "MISS"
    conf = "declined" if r.output["declined"] else round(r.confidence or 0, 2)
    print(mark, conf, item["q"][:60])
print(f"{ok}/{len(ev)} correct")
