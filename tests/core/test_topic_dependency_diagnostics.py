"""Owned diagnostics separate absent metadata from unassigned ownership without disclosing another owner."""
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parent))
from test_topic_conversion import native
from test_topic_transfer import SOURCE,TOPIC
from api.topic_conversion import inventory_diagnostics


def test_missing_and_unassigned_document_metadata_are_distinct(native):
    for doc in ('missing','unassigned'):
        native.run('CREATE (n:Concept {concept_id:$id,owner_id:$owner,domain:$topic})',
                   {'id':SOURCE+'\x1fconcept:doc.'+doc,'owner':SOURCE,'topic':TOPIC})
    native.run("CREATE (m:_DocMeta {doc_id:'unassigned'})")
    result=inventory_diagnostics(native,SOURCE,TOPIC)
    assert result['counts_known'] is True and result['ready'] is False
    assert result['document_ownership']=={'identities':3,'owned_metadata':1,'missing_metadata':1,'unassigned_metadata':1}
    assert 'document_owner_missing' in result['blockers']
    assert not any(key in result for key in ('doc_ids','metadata_rows','owners'))


def test_foreign_document_ownership_suppresses_the_detail_and_all_counts(native):
    native.run("MATCH (m:_DocMeta) WHERE m.doc_id='doc1' SET m.owner_id='user:foreign'")
    result=inventory_diagnostics(native,SOURCE,TOPIC)
    assert result['counts_known'] is False
    assert result['counts'] is None and result['documents'] is None
    assert 'document_ownership' not in result
