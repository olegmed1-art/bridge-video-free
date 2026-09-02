import hashlib, json, pathlib, subprocess, sys

RUNNER = pathlib.Path(__file__).parents[1] / 'dds_training' / 'a11_mass_dds_runner.py'

def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()

def test_a11_pass_and_checkpoint(tmp_path):
    corpus=tmp_path/'c.jsonl'; corpus.write_text('{}\n', encoding='utf-8')
    ev=tmp_path/'e.json'; cp=tmp_path/'cpt.json'
    evaluator=tmp_path/'eval.py'
    evaluator.write_text('''import json,os\np=os.environ["A11_EVIDENCE"]\nt=int(os.environ["A11_TARGET"])\njson.dump({"processed":t,"target":t,"corpus_sha256":os.environ["A11_CORPUS_SHA256"],"dd_trajectory_complete":True,"legal_alternatives_complete":True,"regret_complete":True,"first_swing_complete":True,"unrecovered_damage_complete":True},open(p,"w"))\n''',encoding='utf-8')
    r=subprocess.run([sys.executable,str(RUNNER),'--target','10000','--corpus',str(corpus),'--checkpoint',str(cp),'--evidence',str(ev),'--evaluator',f'{sys.executable} {evaluator}'])
    assert r.returncode==0
    state=json.loads(cp.read_text()); assert state['status']=='passed'; assert state['authority']=='EVIDENCE_ONLY'

def test_a11_rejects_incomplete_evidence(tmp_path):
    corpus=tmp_path/'c'; corpus.write_text('x')
    ev=tmp_path/'e'; cp=tmp_path/'cp'; evaluator=tmp_path/'eval.py'
    evaluator.write_text('import json,os; json.dump({"processed":10000},open(os.environ["A11_EVIDENCE"],"w"))')
    r=subprocess.run([sys.executable,str(RUNNER),'--target','10000','--corpus',str(corpus),'--checkpoint',str(cp),'--evidence',str(ev),'--evaluator',f'{sys.executable} {evaluator}'])
    assert r.returncode==66
    assert json.loads(cp.read_text())['status']=='failed_gate'
