#!/usr/bin/env python3
"""Promote retained CI bytes only after matching real-workflow qualification."""
import argparse, hashlib, json, subprocess, sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import webgpu_contract
REPO=Path(__file__).resolve().parents[2]
# Retained candidate artifacts come from this repository, identified by its numeric ID. Names move
# (after the organization rename the old owner/name of this repository resolves to the new monorepo), so
# every run is read by ID and the owner/name used for downloads is the one the ID resolves to now.
REPO_ID=295956585
REMOTE='facemorph-me/facemorph.me-classic'
def command(*args):return subprocess.check_output(list(args),cwd=REPO,text=True)
def source_repository():
    data=json.loads(command('gh','api',f'repositories/{REPO_ID}'))
    if data['id']!=REPO_ID or data['full_name']!=REMOTE:
        raise ValueError(f"Repository {REPO_ID} is {data['full_name']}, not {REMOTE}")
    return data
def passed_run(run,path):
    data=json.loads(command('gh','api',f'repositories/{REPO_ID}/actions/runs/{run}'))
    if data['conclusion']!='success' or data['path']!=path or data['repository']['id']!=REPO_ID or data['head_repository']['id']!=REPO_ID:
        raise ValueError('Required successful workflow/source is missing')
    return data
p=argparse.ArgumentParser();p.add_argument('--build-run',type=int,required=True);p.add_argument('--qualification-run',type=int,required=True);p.add_argument('--runtime',type=Path);p.add_argument('--catalogue',type=Path);p.add_argument('--work',type=Path);p.add_argument('--publish',action='store_true');p.add_argument('--discard-after',action='store_true',help='Remove this run\'s work directory once the receipt is written.');p.add_argument('--source-only',action='store_true',help='Check the source repository and both runs by ID, print them and stop: no download, staging or publication.');a=p.parse_args()
if not a.source_only and not (a.runtime and a.catalogue and a.work):p.error('--runtime, --catalogue and --work are required unless --source-only')
repository=source_repository()
build=passed_run(a.build_run,'.github/workflows/next-site.yml')
# The artifact build calls the qualification as a reusable workflow, so the usual case is one
# run that both built and qualified these bytes. A separate run is still accepted when the
# qualification was pointed at an earlier artifact by hand.
qualified=build if a.qualification_run==a.build_run else passed_run(a.qualification_run,'.github/workflows/next-e2e.yml')
if a.source_only:
    print(json.dumps({'repositoryId':repository['id'],'repository':repository['full_name'],'buildRun':build['id'],'qualificationRun':qualified['id'],'source':build['head_sha'],'published':False}))
    sys.exit(0)
work=a.work.resolve();work.mkdir(parents=True,exist_ok=False)
web=work/'web';proof=work/'qualification'
command('gh','run','download',str(a.build_run),'-R',REMOTE,'-n','next-site-'+build['head_sha'],'-D',str(web))
command('gh','run','download',str(a.qualification_run),'-R',REMOTE,'-n','next-e2e-'+str(a.qualification_run),'-D',str(proof))
command(sys.executable,'desktop/scripts/verify-web-artifact.py','--artifact',str(web),'--revision',build['head_sha'],'--receipt',str(work/'web-receipt.json'))
report=json.loads((proof/'report.json').read_text());source=json.loads((proof/'source-run.json').read_text())
required={'nameSeed','repeatOriginal','syntheticPhotoE4e','localCrop','morphPlayableMp4','routeRejectionNamed'}
# Project export and open sit behind `projectFilesVisible` in Product.fs and have been off since
# 21 September (operator), so no browser can demonstrate the save/reopen round trip through the
# surface. The qualification states which artifact it saw: a missing record still fails, and the
# pass is required again the moment the control ships. What the UI no longer covers stays covered
# by src/Next/verify-fable.mjs, which round-trips every path kind and latent space against actual
# Fable output in the artifact job.
project=report.get('checks',{}).get('projectSaveReopen')
if not isinstance(project,dict):raise ValueError('Required real-workflow evidence is missing')
if project.get('shipped') is not False:required={*required,'projectSaveReopen'}
if report.get('passed') is not True or not all(report.get('checks',{}).get(k,{}).get('passed') is True for k in required):raise ValueError('Required real-workflow evidence is missing')
if source['id']!=a.build_run or source['head_sha']!=build['head_sha'] or (proof/'next-site-SHA256SUMS').read_bytes()!=(web/'next-site-SHA256SUMS').read_bytes():raise ValueError('Qualification tested a different artifact')
# The catalogue is built into the artifact now. Publishing a different one would ship a names
# index no qualification ever served, so the one passed here must be the one that was tested.
built_catalogue=web/'deploy-next'/'catalogue.json'
if built_catalogue.exists() and built_catalogue.read_bytes()!=a.catalogue.read_bytes():raise ValueError('Catalogue differs from the one built into the qualified artifact')
manifest_sha=hashlib.sha256((a.runtime/'manifest.json').read_bytes()).hexdigest()
if manifest_sha!=report['runtimeSha256']:raise ValueError('Runtime differs from qualified bundle')
# Structural gate: a bundle may not be built from a kernel with no keep row in
# autoresearch/results.tsv. The build must embed kernel provenance; promotion names it.
manifest=json.loads((a.runtime/'manifest.json').read_text())
kernel=manifest.get('kernel') or {}
for field in ('file','sha256','candidateId','sourceHash'):
    if not kernel.get(field):raise ValueError(f'Runtime manifest lacks kernel provenance field {field!r} — rebuild with a research-backed kernel')
ledger=REPO/'autoresearch'/'results.tsv'
if ledger.exists():
    keep=[line.split('\t') for line in ledger.read_text().splitlines()[1:] if '\t' in line]
    if not any(row and row[0]==kernel['candidateId'] and 'keep' in row for row in keep):
        raise ValueError(f"Kernel candidate {kernel['candidateId']!r} has no keep row in autoresearch/results.tsv")
    if not any(row and row[0]==kernel['candidateId'] and kernel['sourceHash'] in row for row in keep):
        raise ValueError(f"Kernel source hash {kernel['sourceHash'][:12]}… not recorded for {kernel['candidateId']!r}")
# The gate above reads provenance the runtime never loads, so it passed for days while the served
# bundle carried the pre-promotion kernel and the retired unsplit graph, and every WebGPU device
# fell back to CPU after a 183 MB download. This checks the assets the engine actually imports.
webgpu_contract.require(a.runtime, manifest)
receipt={'catalogueSha256':hashlib.sha256(a.catalogue.read_bytes()).hexdigest(),'buildRun':a.build_run,'qualificationRun':a.qualification_run,'source':build['head_sha'],'runtimeSha256':manifest_sha,'kernel':{k:kernel[k] for k in ('file','sha256','candidateId','sourceHash')},'projectFiles':('qualified' if project.get('shipped') is not False else 'not in this artifact'),'published':False}
command(sys.executable,'hosting/next-static/stage.py','--runtime',str(a.runtime.resolve()),'--frontend',str(web/'deploy-next'),'--catalogue',str(a.catalogue.resolve()),'--output',str(work/'public'))
(work/'promotion.json').write_text(json.dumps(receipt,indent=2)+'\n')
if a.publish:
    subprocess.run(['npx','wrangler','whoami'],cwd=REPO,check=True)
    subprocess.run(['npx','wrangler','deploy','--config','hosting/next-static/wrangler.jsonc','--assets',str(work/'public')],cwd=REPO,check=True)
    receipt['published']=True
    (work/'promotion.json').write_text(json.dumps(receipt,indent=2)+'\n')
# Staging hard-links the runtime, so a work directory costs little, but they still accumulate one
# per promotion. Removing this run's own directory is opt-in and never touches anything else.
if a.discard_after:
    import shutil as _shutil
    _shutil.rmtree(work,ignore_errors=True)
    receipt['workDiscarded']=True
print(json.dumps(receipt))
