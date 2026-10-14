"""Byte-sized pages preserve complete scans rather than refusing ordinary large rows."""
import re
import pytest
from store.scoped_topic_inventory import _Reader
from store.topic_manifest import ManifestLimit


class Pages:
    def __init__(self,rows):self.rows,self.queries=rows,[]
    def run(self,query,params=None,raw=True):
        self.queries.append(query)
        offset=int(re.search(r'SKIP (\d+)',query).group(1)) if 'SKIP ' in query else 0
        limit=int(re.search(r'LIMIT (\d+)',query).group(1))
        rows=sorted(self.rows,key=lambda r:r['id']) if 'ORDER BY' in query else self.rows
        return rows[offset:offset+limit]


def test_adaptive_byte_pages_keep_every_duplicate_and_stable_order():
    rows=[{'id':i//2,'text':'합성 본문'*4000} for i in reversed(range(140))]
    store=Pages(rows)
    observed=list(_Reader(store).scan('MATCH (n)','n.id AS id,n.text AS text','n.id',ordered=True))
    assert observed==sorted(rows,key=lambda r:r['id'])
    assert any('LIMIT 64' in q or 'LIMIT 32' in q for q in store.queries)
    assert len(observed)==140


def test_single_row_failure_is_distinct_and_never_returns_partial_success():
    store=Pages([{'id':1,'text':'x'*(4*1024*1024+1)}])
    with pytest.raises(ManifestLimit) as error:
        list(_Reader(store).scan('MATCH (n)','n.id AS id,n.text AS text','n.id'))
    assert error.value.reason=='single_row_bytes'


def test_scan_budget_still_applies_to_adaptive_read_attempts():
    store=Pages([{'id':i,'text':'합성'*5000} for i in range(140)])
    with pytest.raises(ManifestLimit) as error:
        list(_Reader(store,scan_rows=10).scan('MATCH (n)','n.id AS id,n.text AS text','n.id'))
    assert error.value.reason=='scan_rows'
