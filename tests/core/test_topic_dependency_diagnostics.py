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
    assert 'document_missing_evidence' not in result


def test_missing_metadata_evidence_distinguishes_payload_history_and_unknown(native):
    for doc in ('missing-body','missing-vector','missing-history','missing-unknown'):
        native.run('CREATE (n:Concept {concept_id:$id,owner_id:$owner,domain:$topic})',
                   {'id':SOURCE+'\x1fconcept:doc.'+doc,'owner':SOURCE,'topic':TOPIC})
    native.run("CREATE (n:Chunk {chunk_id:'missing-body#0',doc_id:'missing-body',owner_id:$owner,text:'synthetic'})",{'owner':SOURCE})
    native.run("CREATE (n:ChunkVec {vec_id:'missing-vector#vec',doc_id:'missing-vector',chunk_id:'missing-vector#0',owner_id:$owner,embedding:$embedding})",
               {'owner':SOURCE,'embedding':[1.0,2.0,3.0,4.0]})
    for doc in ('missing-history','doc1'):
        native.run('CREATE (n:_DocTombstone {doc_id:$doc,owner_id:$owner,deleted_at:$stamp})',
                   {'doc':doc,'owner':SOURCE,'stamp':'2020-01-01T00:00:00Z'})
    result=inventory_diagnostics(native,SOURCE,TOPIC)
    assert result['ready'] is False
    assert result['document_ownership']['missing_metadata']==4
    assert result['document_missing_evidence']=={'missing_with_chunks':1,'missing_with_vectors':1,
        'missing_with_owned_tombstone':1,'missing_without_payload_with_owned_tombstone':1,
        'missing_without_payload_or_owned_tombstone':1,'live_metadata_with_owned_tombstone':1}
    assert not any(key in result for key in ('doc_ids','metadata_rows','owners'))
