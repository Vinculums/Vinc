"""이미 공개된 측정 자료의 지문만 저장한다. 텍스트나 라벨을 복사하지 않는다."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .data_contracts import digest, encoded, normalized, require, stamp


def build(inputs, at, reference):
    stamp(at)
    require(reference.strip(), "measurement reference required")
    events = []
    for namespace, path in inputs:
        data = path.read_bytes()
        pairs = [json.loads(line) for line in data.decode("utf-8").splitlines() if line.strip() and not line.startswith("#")]
        require(pairs, "empty measured input")
        families, texts, nodes, docs = set(), set(), set(), set()
        for pair in pairs:
            families.add(namespace + ":" + pair["family_id"])
            for side in ("a", "b"):
                texts.add(digest(normalized(pair[side]["text"]).encode()))
                nodes.add(pair[side]["id"])
                provenance = pair.get("provenance", {}).get(side, {})
                if provenance.get("node_id"):
                    nodes.add(provenance["node_id"])
                if provenance.get("doc_id"):
                    docs.add(provenance["doc_id"])
            if pair.get("context"):
                texts.add(digest(normalized(pair["context"]).encode()))
        events.append({"event_id": "exposure:" + namespace, "kind": "measurement_opened", "at": at,
                       "reference": reference, "input_sha256": digest(data), "pairs": len(pairs),
                       "family_ids": sorted(families), "text_hashes": sorted(texts),
                       "source_ids": sorted(nodes), "document_ids": sorted(docs), "source_hashes": []})
    require(len({e["event_id"] for e in events}) == len(events), "duplicate namespace")
    return {"schema": "vinc.laya.exposures.v1", "events": events,
            "scope": "listed inputs only; absent document identities remain unknown"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", action="append", required=True, help="namespace=path to measured pairs")
    parser.add_argument("--at", required=True)
    parser.add_argument("--reference", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    result = build([(s.split("=", 1)[0], Path(s.split("=", 1)[1])) for s in args.input], args.at, args.reference)
    with args.out.open("xb") as stream:
        stream.write(encoded(result))
    print(json.dumps({"events": len(result["events"]), "sha256": digest(encoded(result))}))


if __name__ == "__main__":
    main()
