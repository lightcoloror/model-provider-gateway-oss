from pathlib import Path
import pytest

from model_provider_gateway.adapters import adapter_contract,task_contract
from model_provider_gateway.consent import build_consent_plan
from model_provider_gateway.execution import build_execution_request,execute_model_request
from model_provider_gateway.profile import build_profile
from model_provider_gateway.route import build_route_plan

def test_translation_task_is_text_only():
    assert task_contract('translation_desk','translate_text')['capabilities'] == ['text']
    assert 'api_key' in adapter_contract('translation_desk')['forbidden_fields']
    with pytest.raises(ValueError):
        task_contract('translation_desk','visual')

@pytest.mark.parametrize('content,finish_reason,ok',[('完整译文','stop',True),('非空截断','length',False),('', 'stop',False),('未知结束',None,False)])
def test_translation_exact_consent_and_response_completion(tmp_path: Path,content,finish_reason,ok):
    artifact = tmp_path / 'source.txt'; artifact.write_text('synthetic source')
    profile = build_profile('modelscope-glm-5-2','env:DESK_FIXTURE_TOKEN',max_calls=1,max_cost_usd=1.0)
    route = build_route_plan('desk','text',[profile],max_calls=1,max_cost_usd=1.0)
    consent = build_consent_plan(route,[artifact],capability='text',purpose='Translate faithfully',data_type='synthetic_fixture',max_calls=1,max_cost_usd=1.0)
    request = build_execution_request(profile,route,consent,consumer_id='translation_desk',task='translate_text',max_estimated_cost_usd=0.1)
    class Fake:
        mode = 'synthetic_fixture'
        def execute(self,**kwargs):
            return {'content':content,'finish_reason':finish_reason,'usage':{},'estimated_cost_usd':0.01}
    result = execute_model_request(request,profile=profile,route=route,consent=consent,ledger_path=tmp_path / 'ledger.json',transport=Fake(),secret_resolver=lambda ref:'synthetic-only')
    assert result['ok'] is ok
    assert result['redacted_audit']['automatic_fallback_used'] is False
    if not ok:
        assert result['output'] is None
