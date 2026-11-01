"""One captured-source classifier child for bounded initial qualification.

The outer reviewed launcher supplies authenticated source and load evidence;
this module has no default authority, network setup, retry or model selection.
"""
from __future__ import annotations

import asyncio
import math
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from guardrails.evolution.classifier_contract import (
    ERRORS,
    MAX_RESULT_BYTES,
    MAX_WIRE_BYTES,
    ClassifierError,
    canonical,
    parse,
    score_checked,
)

from services.classifier.bounded_qualification_core import Specification, require
from services.classifier.wolf_full_qualification import evaluate_complete, read, sha

# Closed names only. Adding another candidate requires a reviewed source delta.
CANDIDATE_MODULES = {
    3: ('services.classifier.piguard_candidate_03_compat',
        'services/classifier/piguard_candidate_03_compat.py'),
}
BASE_ORDER = (
    ('guardrails', 'mvp/guardrails/__init__.py', True),
    ('guardrails.evolution', 'mvp/guardrails/evolution/__init__.py', True),
    ('guardrails.evolution.classifier_contract', 'mvp/guardrails/evolution/classifier_contract.py', False),
    ('services', None, True), ('services.classifier', None, True),
    ('services.classifier.worker', 'services/classifier/worker.py', False),
    ('services.classifier.network_isolation', 'services/classifier/network_isolation.py', False),
    ('services.classifier.wolf_full_compat', 'services/classifier/wolf_full_compat.py', False),
    ('services.classifier.wolf_full_qualification', 'services/classifier/wolf_full_qualification.py', False),
    ('services.classifier.shieldlm_compat', 'services/classifier/shieldlm_compat.py', False),
    ('services.classifier.bounded_qualification_core', 'services/classifier/bounded_qualification_core.py', False),
    ('services.classifier.bounded_qualification_process_v3', 'services/classifier/bounded_qualification_process_v3.py', False),
)

# Independent immutable capture before executing any product byte. The sole
# site-packages path belongs to the outer-authenticated CPython/wheel closure.
BOOTSTRAP = r'''
import hashlib,json,os,re,stat,sys,types
from pathlib import Path

def checked(value):
    if not value: raise ValueError('bounded_child_capture')

def main():
    checked(sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode and not sys.flags.optimize)
    root=Path(sys.argv[1]); config=json.loads(sys.argv[2]); directory=Path(sys.argv[3])
    order=config['order'];pins=config['sources'];captured={}
    checked(root.is_absolute() and root.resolve()==root)
    checked(type(order) is list and 1<=len(order)<=24)
    checked(len({row[0] for row in order})==len(order))
    checked(set(pins)=={row[1] for row in order if row[1] is not None})
    checked(not any(name in sys.modules for name,_,_ in order))
    checked(not any(os.path.lexists(root/p) for p in ('services/__init__.py','services/classifier/__init__.py')))
    for name,pin in pins.items():
        checked(type(name) is str and re.fullmatch(r'(?:mvp|services)/[A-Za-z0-9_/.-]+\.py',name))
        checked(all(p not in ('','.','..') for p in name.split('/')))
        checked(type(pin) is str and re.fullmatch('[0-9a-f]{64}',pin))
        path=root/name;checked(path.resolve()==path)
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        with os.fdopen(fd,'rb') as stream:
            row=os.fstat(stream.fileno())
            checked(stat.S_ISREG(row.st_mode) and row.st_uid==os.getuid() and row.st_nlink==1
                    and stat.S_IMODE(row.st_mode) in (0o600,0o644) and 0<row.st_size<=1024*1024)
            raw=stream.read(1024*1024+1)
            def key(value):
                return tuple(getattr(value,n) for n in ('st_dev','st_ino','st_mode','st_uid','st_nlink','st_size','st_mtime_ns','st_ctime_ns'))
            checked(key(row)==key(os.fstat(stream.fileno()))==key(path.lstat()) and path.resolve()==path)
            checked(len(raw)==row.st_size and hashlib.sha256(raw).hexdigest()==pin)
        captured[name]=raw
    previous=list(sys.path)
    try:
        sys.path.insert(0,str(Path(sys.executable).parent.parent/'lib/python3.13/site-packages'))
        for name,path,package in order:
            module=types.ModuleType(name);module.__package__=name if package else name.rpartition('.')[0]
            if package:module.__path__=[]
            if path is not None:module.__file__=str(root/path)
            sys.modules[name]=module
            if '.' in name:
                parent,_,leaf=name.rpartition('.');setattr(sys.modules[parent],leaf,module)
            if path is not None:exec(compile(captured[path],str(root/path),'exec'),module.__dict__)
        module=sys.modules['services.classifier.bounded_qualification_process_v3']
        checked(module.captured_sources(root,config['selection_index'])==pins)
        checked([list(row) for row in module.module_order(config['selection_index'])]==order)
        return module.child_main(directory,config)
    finally:
        sys.path[:]=previous
        for name,_,_ in reversed(order):sys.modules.pop(name,None)

if __name__=='__main__':raise SystemExit(main())
'''


def module_order(selection_index):
    require(type(selection_index) is int and selection_index in CANDIDATE_MODULES,'candidate_backend')
    name,path=CANDIDATE_MODULES[selection_index]
    return (*BASE_ORDER,(name,path,False))


def captured_sources(root, selection_index):
    root=Path(root)
    require(root.is_absolute() and root.resolve()==root,'source_root')
    require(not any(os.path.lexists(root/name) for name in ('services/__init__.py','services/classifier/__init__.py')),'namespace_added')
    return {path:sha(read(root/path,maximum=1024*1024)) for _,path,_ in module_order(selection_index) if path is not None}


@dataclass(frozen=True)
class ChildConfiguration:
    specification: Specification
    root: Path
    model_directory: Path
    output_directory: Path
    loaded_metadata_json: bytes

    def __post_init__(self):
        require(type(self.specification) is Specification,'child_specification')
        for path in (self.root,self.model_directory,self.output_directory):
            require(isinstance(path,Path) and path.is_absolute() and path.resolve()==path,'child_path')
        loaded=parse(self.loaded_metadata_json,limit=65536)
        require(canonical(loaded)==self.loaded_metadata_json and loaded.get('qualification_only') is True
                and loaded.get('quality_verified') is False and loaded.get('scoring_requests')==0,'loaded_projection')
        require(loaded.get('repository')==self.specification.identity['repository']
                and loaded.get('revision')==self.specification.identity['revision']
                and loaded.get('weights_sha256')==self.specification.identity['weights_sha256'],'loaded_identity')
        module_order(self.specification.selection_index)

    def payload(self):
        actual=captured_sources(self.root,self.specification.selection_index)
        require(all(self.specification.sources.get(name)==pin for name,pin in actual.items()),'child_source_pin')
        return {'selection_index':self.specification.selection_index,'identity':self.specification.identity,
            'case_count':self.specification.case_count,'loaded':parse(self.loaded_metadata_json,limit=65536),
            'sources':actual,'order':[list(row) for row in module_order(self.specification.selection_index)]}


def child_main(directory, configuration):
    """Only the captured, outer-admitted child calls this actual ML boundary."""
    try:
        require(len(os.sched_getaffinity(0))==2,'child_cpu_affinity')
        module_name,_=CANDIDATE_MODULES[configuration['selection_index']]
        adapter=sys.modules[module_name]
        identity=configuration['identity']
        require(identity['repository']==adapter.REPOSITORY and identity['revision']==adapter.REVISION
                and identity['weights_sha256']==adapter.WEIGHTS_SHA256
                and identity['bundle_sha256']==sha(canonical(dict(adapter.FILES)))
                and identity['candidate_manifest_sha256']==adapter.MANIFEST_SHA256,'child_identity')
        tokenizer,predict,metadata=adapter.load_for_qualification(directory)
        require(canonical(metadata)==canonical(configuration['loaded']),'child_loaded')
        torch=sys.modules['torch']
        require(torch.get_num_threads()==2 and torch.get_num_interop_threads()==1,'child_threads')
        sys.stdout.buffer.write(canonical({'ready':True,'identity':identity,'loaded':metadata})+b'\n');sys.stdout.buffer.flush()
        expected=0
        while raw:=sys.stdin.buffer.readline(MAX_WIRE_BYTES+2):
            require(len(raw)<=MAX_WIRE_BYTES+1 and raw.endswith(b'\n'),'worker_input')
            request=parse(raw[:-1],limit=MAX_WIRE_BYTES)
            require(set(request)=={'identity','sequence','text','deadline'} and request['identity']==identity
                    and type(request['sequence']) is int and request['sequence']==expected<configuration['case_count']
                    and type(request['text']) is str and len(request['text'])<=65536 and len(request['text'].encode())<=65536
                    and type(request['deadline']) in (float,int) and math.isfinite(request['deadline']),'worker_request')
            expected+=1
            try:result=evaluate_complete(request['text'],tokenizer,predict,request['deadline'])
            except ClassifierError as error:result={'error':error.code}
            except ValueError:result={'error':'evaluator_unavailable'}
            sys.stdout.buffer.write(canonical({'identity':identity,'sequence':request['sequence'],**result})+b'\n');sys.stdout.buffer.flush()
    except BaseException:  # noqa: BLE001 - no arbitrary text/errors across child IPC
        return 1
    return 0


class ProcessChild:
    """Single model child; first protocol/deadline failure closes it permanently."""

    def __init__(self, configuration: ChildConfiguration):
        require(type(configuration) is ChildConfiguration,'child_configuration')
        self.configuration=configuration;self.process=None;self.loaded=None;self.attempts=0;self.unavailable=False;self.cleanup_failed=False

    async def start(self):
        require(self.process is None and not self.unavailable,'child_started')
        config=self.configuration
        payload=config.payload()  # Refuse current source drift before Popen.
        environment={'PYTHONDONTWRITEBYTECODE':'1','HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1',
            'HF_HUB_DISABLE_TELEMETRY':'1','TOKENIZERS_PARALLELISM':'false','OMP_NUM_THREADS':'2',
            'MKL_NUM_THREADS':'2','CUDA_VISIBLE_DEVICES':''}
        self.process=await asyncio.create_subprocess_exec(sys.executable,'-I','-S','-B','-X',
            'pycache_prefix='+str(config.output_directory/'unused-pycache'),'-c',BOOTSTRAP,
            str(config.root),canonical(payload).decode(),str(config.model_directory),
            stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL,
            env=environment,limit=MAX_RESULT_BYTES)
        try:
            async with asyncio.timeout(120):raw=await self.process.stdout.readline()
            value=parse(raw,limit=MAX_RESULT_BYTES)
            require(set(value)=={'ready','identity','loaded'} and value['ready'] is True
                    and value['identity']==config.specification.identity
                    and canonical(value['loaded'])==config.loaded_metadata_json and self.process.returncode is None,'worker_handshake')
            self.loaded=value['loaded']
        except BaseException:
            try:await self.close()
            except BaseException:  # noqa: BLE001 - retain primary refusal; outer owns group cleanup
                self.cleanup_failed=True
            raise

    async def evaluate(self, sequence, text, deadline):
        if type(deadline) not in (float,int) or not math.isfinite(deadline):raise ClassifierError('validation_failed')
        if time.monotonic()>=deadline:raise ClassifierError('evaluation_timeout')
        if self.unavailable or self.process is None or self.process.returncode is not None:raise ClassifierError('evaluator_unavailable')
        explicit=False
        try:
            async with asyncio.timeout_at(deadline):
                identity=self.configuration.specification.identity
                self.attempts+=1
                self.process.stdin.write(canonical({'identity':identity,'sequence':sequence,'text':text,'deadline':deadline})+b'\n')
                await self.process.stdin.drain()
                raw=await self.process.stdout.readline();require(raw.endswith(b'\n'),'worker_response')
                value=parse(raw,limit=MAX_RESULT_BYTES)
                require(value.get('identity')==identity and type(value.get('sequence')) is int and value['sequence']==sequence,'response_identity')
                if set(value)=={'identity','sequence','error'} and value['error'] in ERRORS:
                    explicit=True;raise ClassifierError(value['error'])
                require(set(value)=={'identity','sequence','complete','score','windows'} and value['complete'] is True
                        and score_checked(value['score']) and type(value['windows']) is int and 1<=value['windows']<=16,'response')
                return {key:value[key] for key in ('complete','score','windows')}
        except ClassifierError:
            if not explicit:await self.close()
            raise
        except TimeoutError:
            await self.close();raise ClassifierError('evaluation_timeout') from None
        except asyncio.CancelledError:
            await self.close();raise
        except Exception:  # noqa: BLE001 - closed finite protocol refusal
            await self.close();raise ClassifierError('evaluator_unavailable') from None

    async def close(self):
        self.unavailable=True
        if self.process is not None:
            if self.process.returncode is None:
                try:self.process.kill()
                except ProcessLookupError:pass
            async with asyncio.timeout(5):await self.process.communicate()
