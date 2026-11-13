"""Mocked transport verifies protocol and rejection behavior; no live-model claims."""
import json
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

import pytest

from finreport.answers import answer_question, load_verified_table
from finreport.narrative import ollama, render, scripted, validate_narrative
from finreport.schemas import FactTable

ROOT = Path(__file__).resolve().parents[2]

def sample():
    t = FactTable.model_validate_json((ROOT/'evidence/reference_fact_table.json').read_text())
    return answer_question('What was net income in FY2023?',t)

class Response:
    def __init__(self, body): self.body = body
    def __enter__(self): return self
    def __exit__(self,*args): pass
    def read(self, limit): return self.body[:limit]

@pytest.mark.parametrize('mode', ['ok','bad_id','bad_value','extra_prose','wrong_model','incomplete','malformed','too_large'])
def test_ollama_contract(mode):
    a = sample()
    n = scripted(a);n.update(adapter='ollama',model='installed:test')
    if mode == 'bad_id': n['claims'][0]['evidence_id'] = 'FAKE'
    if mode == 'bad_value': n['claims'][0]['value'] = '999'
    if mode == 'extra_prose': n['text'] = 'Debt caused profit growth.'
    body = json.dumps({'model':'other' if mode == 'wrong_model' else 'installed:test',
                       'done':mode != 'incomplete','response':json.dumps(n)}).encode()
    if mode == 'malformed': body = b'not json'
    if mode == 'too_large': body = b'x'*1_000_001
    with patch('finreport.narrative.urllib.request.build_opener') as mock:
        mock.return_value.open.return_value = Response(body)
        if mode == 'ok':
            assert ollama(a,'installed:test')['claims'] == a['claims']
            req = mock.return_value.open.call_args.args[0]
            payload = json.loads(req.data)
            assert req.full_url == 'http://127.0.0.1:11434/api/generate'
            assert payload['stream'] is False and isinstance(payload['format'],dict)
        else:
            with pytest.raises((ValueError,KeyError)): ollama(a,'installed:test')
        assert mock.return_value.open.call_count == 1

def test_connection_error_is_not_scripted_success():
    with patch('finreport.narrative.urllib.request.build_opener') as mock:
        mock.return_value.open.side_effect = URLError('no local model')
        with pytest.raises(URLError): ollama(sample(),'installed:test')

def test_thousands_render_without_rounding_away_amount():
    a = sample();a['claims'][0]['value'] = '12345000'
    assert '12.345 million' in render(a,scripted(a))

def test_unknown_narrative_style():
    a = sample();n = scripted(a);n['style'] = 'invented'
    assert not validate_narrative(n,a)

@pytest.mark.parametrize('tamper',['missing','same_size','wrong_manifest_name','fact_checksum','source_company'])
def test_current_source_verified(tmp_path,tamper):
    import shutil
    shutil.copytree(ROOT/'data',tmp_path/'data')
    (tmp_path/'output').mkdir()
    shutil.copy2(ROOT/'evidence/reference_fact_table.json',tmp_path/'output/fact_table.json')
    source = tmp_path/'data/cache/msft-20240630.htm'
    # Cache may not exist in a fresh clone; materialize the bundled exact bytes.
    import gzip
    source.parent.mkdir(exist_ok=True)
    source.write_bytes(gzip.decompress((ROOT/'data/filing.html.gz').read_bytes()))
    assert len(load_verified_table(tmp_path).facts) == 6
    if tamper == 'missing': source.unlink()
    elif tamper == 'same_size':
        data=source.read_bytes();source.write_bytes(bytes([data[0]^1])+data[1:])
    elif tamper == 'wrong_manifest_name':
        p=tmp_path/'data/source_manifest.json';d=json.loads(p.read_text());d['external_import']['cached_filename']='wrong.htm';p.write_text(json.dumps(d))
    else:
        p=tmp_path/'output/fact_table.json';d=json.loads(p.read_text())
        if tamper == 'fact_checksum': d['source']['checksum']='0'*64
        else: d['source']['company']='Other company'
        p.write_text(json.dumps(d))
    with pytest.raises(ValueError): load_verified_table(tmp_path)
