from pathlib import Path

service_path = Path("app/services/image_generation_service.py")
service = service_path.read_text(encoding="utf-8")
old = '''            stable_krea_seed=None\n            stable_krea_norm_applied=False\n            if adult_generation and ADULT_PRIMARY_GENERATION_MODEL in model_plan:\n                stable_krea_seed, stable_krea_norm_applied = normalize_venice_seed(\n                    job.seed,\n                    salt=f'job:{job.id}:{ADULT_PRIMARY_GENERATION_MODEL}:identity',\n                )\n'''
new = '''            stable_krea_seed=None\n            stable_krea_norm_applied=False\n            stable_krea_seed_source=None\n            if adult_generation and ADULT_PRIMARY_GENERATION_MODEL in model_plan:\n                # Use the profile-level identity seed, not the per-request scene\n                # seed. This keeps Krea in one identity family across separate\n                # scenes and messages while prompt fields control composition.\n                stable_krea_seed_source = (\n                    getattr(job, 'identity_seed', None)\n                    or (job.metadata_json or {}).get('identity_seed')\n                    or job.seed\n                )\n                stable_krea_seed, stable_krea_norm_applied = normalize_venice_seed(\n                    stable_krea_seed_source,\n                    salt=f'user:{job.user_id}:{ADULT_PRIMARY_GENERATION_MODEL}:identity',\n                )\n'''
if old not in service:
    raise SystemExit("stable Krea seed anchor not found")
service = service.replace(old, new, 1)
old_meta = "'stable_krea_identity_seed':stable_krea_seed,'seed_normalization_applied'"
new_meta = "'stable_krea_identity_seed':stable_krea_seed,'stable_krea_identity_seed_source':stable_krea_seed_source,'seed_normalization_applied'"
if old_meta not in service:
    raise SystemExit("stable Krea metadata anchor not found")
service_path.write_text(service.replace(old_meta, new_meta, 1), encoding="utf-8")

worker_path = Path("tests/test_krea_adult_worker_delivery.py")
worker = worker_path.read_text(encoding="utf-8")
old_job = '''        seed=123456,\n        model="krea-2-turbo",\n'''
new_job = '''        seed=123456,\n        identity_seed=777777,\n        final_provider_seed=123456,\n        model="krea-2-turbo",\n'''
if old_job not in worker:
    raise SystemExit("worker job seed anchor not found")
worker = worker.replace(old_job, new_job, 1)
worker = worker.replace(
    '        assert client.calls[0]["seed"] == client.calls[1]["seed"]\n        second_prompt',
    '        assert client.calls[0]["seed"] == client.calls[1]["seed"] == 777777\n        assert client.calls[0]["seed"] != job.seed\n        assert result.metadata_json["stable_krea_identity_seed_source"] == 777777\n        second_prompt',
    1,
)
worker = worker.replace(
    '        assert client.calls[0]["seed"] == client.calls[1]["seed"]\n        assert client.calls[2]["seed"] != client.calls[0]["seed"]',
    '        assert client.calls[0]["seed"] == client.calls[1]["seed"] == 777777\n        assert client.calls[2]["seed"] != client.calls[0]["seed"]',
    1,
)
worker_path.write_text(worker, encoding="utf-8")

gate_path = Path("conftest.py")
gate = gate_path.read_text(encoding="utf-8")
old_gate = '    base_seed = int(plan.seed_strategy["final_provider_seed"])\n'
new_gate = '    base_seed = int(plan.seed_strategy["identity_seed"])\n'
if old_gate not in gate:
    raise SystemExit("live gate identity seed anchor not found")
gate_path.write_text(gate.replace(old_gate, new_gate, 1), encoding="utf-8")

contract_path = Path("tests/test_adult_generation_policy_contract.py")
contract = contract_path.read_text(encoding="utf-8")
contract += '''\n\ndef test_live_gate_anchors_krea_to_profile_identity_seed():\n    source = Path("conftest.py").read_text(encoding="utf-8")\n    assert 'plan.seed_strategy["identity_seed"]' in source\n    assert 'plan.seed_strategy["final_provider_seed"]' not in source\n'''
contract_path.write_text(contract, encoding="utf-8")
