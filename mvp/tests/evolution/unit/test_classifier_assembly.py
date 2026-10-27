"""Classifier profile convergence through fake Kubernetes metadata, real IO.

No Docker, DB, model or cluster calls. Helm template tests invoke only the
already prepared offline renderer, with synthetic values and no kubeconfig.
"""
import base64
import copy
import importlib.util
import json
import os
import socket
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_assembly import render_chart

ROOT = Path(__file__).resolve().parents[4]
spec = importlib.util.spec_from_file_location("classifier_assembly_test", ROOT / "deploy/evolution/assembly.py")
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)
original_gateway = a._gateway


def put(path, value):
    missing, parent = [], path.parent
    while not parent.exists():
        missing.append(parent); parent = parent.parent
    for parent in reversed(missing):parent.mkdir(mode=0o700)
    a._write(path, value)


class Cluster:
    def __init__(self, root):
        self.root, self.state = ROOT, root
        self.owner_value = {"owner_id": "fixture-owner", "container_id": "fixture-node"}
        self.secrets, self.rows, self.events = {}, {}, []
        self.hold_pods, self.fail_helm, self.secret_race = False, False, False
        self.service = {"metadata": {"name": "classifier", "namespace": "gr-platform", "uid": "classifier-service",
            "labels": {"guardrails.evolution/owner": "fixture-owner"}},
            "spec": {"type": "ClusterIP", "clusterIP": "10.96.0.35", "selector": {"guardrails.evolution/component": "classifier"},
                "ports": [{"name": "https", "port": 8443, "targetPort": 8443, "protocol": "TCP"}]}}
    def owner(self):return self.owner_value
    def image(self,name):return "docker.io/fixture/" + name + "@sha256:" + "a" * 64
    def read_json(self,path):return json.loads(a.seed.private_read(path,maximum=4*1024*1024))
    def install(self,values):
        self.rows = {name:{"metadata":{"name":name,"namespace":row["namespace"],"uid":"uid-"+name,
            "resourceVersion":"1","labels":{"guardrails.evolution/owner":"fixture-owner"}},
            "spec":{"replicas":1,"template":{"metadata":{"annotations":{"guardrails.evolution/config-revision":row["configRevision"]}},
                "spec":{"containers":[{"name":row["component"],"image":row["image"],"resources":copy.deepcopy(row["resources"])}]}}}}
            for name,row in values["components"].items() if row["enabled"]}
    def kubectl(self,*args,namespace,**kwargs):
        if args[:2]==("get","service"):return 0,json.dumps(self.service)
        if args[:2]==("get","deployments"):
            return 0,json.dumps({"items":[row for row in self.rows.values() if row["metadata"]["namespace"]==namespace]})
        if args[:2]==("get","pods"):
            return 0,json.dumps({"items":[{"metadata":{"labels":{"guardrails.evolution/instance":"runtime-1"}}}] if self.hold_pods and namespace=="gr-platform" else []})
        if args[:2]==("get","secret"):
            row=self.secrets.get((namespace,args[2]));return 0,"" if row is None else json.dumps(row)
        if args[0]=="create":
            row=json.loads(kwargs["input_text"]);key=(namespace,row["metadata"]["name"])
            assert key not in self.secrets
            row["metadata"].update(uid="secret-"+key[1],resourceVersion="1")
            self.secrets[key]=row;self.events.append("secret-create");return 0,""
        assert args[0]=="patch" and args[1]=="secret" and args[3:]==("--type=json","--patch-file=/dev/stdin")
        row=self.secrets[(namespace,args[2])]
        if self.secret_race:row["metadata"]["resourceVersion"]="concurrent"
        for op in json.loads(kwargs["input_text"]):
            path=op["path"].strip("/").split("/");part=row
            for key in path[:-1]:part=part[key]
            if op["op"]=="test":
                if part[path[-1]]!=op["value"]:raise a.AssemblyError("fixture_cas_race")
            else:
                assert op["op"]=="replace";part[path[-1]]=op["value"]
        self.events.append("secret-patch");return 0,""


@pytest.fixture
def setup(tmp_path,monkeypatch):
    monkeypatch.setattr(socket,"socket",lambda *x,**k:pytest.fail("network forbidden"))
    d=Cluster(tmp_path)
    directory=tmp_path/"assembly";directory.mkdir(mode=0o700)
    values={"ownerId":"fixture-owner","initImage":d.image("python"),"servicesOnly":False,"components":{},"classifierService":True}
    for name,(namespace,component,artifact) in a._APPLICATIONS.items():
        row=a._component(namespace,component,d.image(artifact),"original")
        row["enabled"]=component!="ollama"
        if component=="postgres":row["postgresTunnel"]={"image":d.image("python")}
        if component=="ollama":row.update(sidecar={"image":d.image("caddy")},env=dict(a.MODEL_ENV))
        values["components"][name]=row
    d.install(values)
    put(directory/"values.json",values)
    for name,raw in (("tls.crt",b"synthetic prepared leaf"),("tls.key",b"synthetic prepared private key"),("ca.crt",b"synthetic CA")):
        put(directory/"tls/gr-platform/classifier"/name,raw)
    put(directory/"private/classifier.token",b"shared-classifier-token")
    put(directory/"mock.token",b"mock-token")
    put(directory/"inventory.json",{"tenants":{"team-a":{},"team-b":{}}})
    current={"profile":"deterministic_mock","seed_inventory_file":str(directory/"inventory.json"),"mock_token_file":str(directory/"mock.token")}
    for name in ("runtime-1","runtime-2","gateway-team-a","gateway-team-b"):
        namespace=a._APPLICATIONS[name][0]
        files={"service.json":a.canonical_json_bytes({"runtime_id":name,"zone":"local-shared","worker_count":2}),
            "database-url":b"unchanged password and DB URL","ca.crt":b"synthetic CA","workload.token":b"unchanged renewed token",
            "runtime-slots.json":b"unchanged current slots"}
        data={key:base64.b64encode(raw).decode() for key,raw in files.items()}
        d.secrets[namespace,name+"-config"]={"metadata":{"name":name+"-config","namespace":namespace,"uid":"secret-"+name,
            "resourceVersion":"1","labels":{"guardrails.evolution/owner":"fixture-owner"}},"type":"Opaque","data":data}
    rows=[]
    for tenant in ("team-a","team-b"):
        rows.append({"tenant_id":tenant,"provider":{"provider_id":"provider-"+tenant,"resource_digest":"immutable-provider"},
            "destination":{"destination_id":"destination-"+tenant,"resource_digest":"immutable-destination","host":"classifier.gr-platform.svc.cluster.local",
                "port":8443,"path_prefix":"/v1/classifier","cidrs":["10.96.0.35/32"]},
            "language_profile":"en","bearer_token_file":str(directory/"private/classifier.token"),"ca_file":str(directory/"tls/gr-platform/classifier/ca.crt")})
    config=directory/"operator.json"
    put(config,{"schema_version":"evolution-evaluator-endpoints-v1","classifier":rows,"bedrock":[]})
    def loader(path,**kwargs):
        assert kwargs=={"runtime_zone":"local-shared","external_processing":False,"tenant_ids":("team-a","team-b")}
        # Full Provider/Destination crypto/schema is covered by the separate
        # loader suite; this boundary permits no transport or model activity.
        return {"classifier_endpoints":{"team-a":object(),"team-b":object()},"bedrock_endpoints":{}}
    monkeypatch.setitem(sys.modules,"guardrails.evolution.evaluator_config",SimpleNamespace(load_evaluator_endpoints=loader))
    def gateway(deployment,target,owner,tenant,item,profile,token,*,secret_writer=None):
        assert token=="mock-token"
        revision=secret_writer(deployment,owner,"gateway-"+tenant,"gr-"+tenant,{"settings.json":a.canonical_json_bytes({"profile":"mock" if profile=="classifier" else profile})})
        return a._component("gr-"+tenant,"gateway",d.image("evolution-gateway"),revision)
    monkeypatch.setattr(a,"_gateway",gateway)
    def helm(deployment,target,v):
        d.events.append(("helm",[name for name,row in v["components"].items() if row["enabled"]]))
        put(target/"values.json",v)
        if d.fail_helm:raise a.AssemblyError("fixture_helm_failed")
        d.install(v)
    monkeypatch.setattr(a,"_helm",helm)
    monkeypatch.setattr(a,"stop_forwards",lambda deployment:d.events.append("stop-forwards"))
    return d,directory,current,values,config


def test_distinct_tenant_resources_unchanged_shared_secret_paths_explicit(setup):
    d,directory,_,_,path=setup
    original=json.loads(path.read_bytes())
    raw,token,ca,digest=a._classifier_bundle(d,directory,d.owner(),path)
    rewritten=json.loads(raw)
    assert token==b"shared-classifier-token" and ca==b"synthetic CA" and len(digest)==64
    for before,after in zip(original["classifier"],rewritten["classifier"],strict=True):
        assert {k:v for k,v in before.items() if k not in {"bearer_token_file","ca_file"}}=={k:v for k,v in after.items() if k not in {"bearer_token_file","ca_file"}}
        assert after["bearer_token_file"]=="/config/classifier.token" and after["ca_file"]=="/config/ca.crt"
    assert json.loads(path.read_bytes())==original


def test_operator_public_ca_copy_allowed_only_when_exact_prepared_trust(setup):
    d,directory,_,_,path=setup
    public=directory/"public-ca.crt";put(public,b"synthetic CA");public.chmod(0o644)
    value=json.loads(path.read_bytes())
    for row in value["classifier"]:row["ca_file"]=str(public)
    put(path,value)
    assert a._classifier_bundle(d,directory,d.owner(),path)[2]==b"synthetic CA"
    public.write_bytes(b"different trust")
    with pytest.raises(a.AssemblyError,match="binding_mismatch"):
        a._classifier_bundle(d,directory,d.owner(),path)


@pytest.mark.parametrize("drift",["duplicate-tenant","shared-provider","shared-destination","wrong-ip","broad-cidr","wrong-host","wrong-port","wrong-path","extra-bedrock","token"])
def test_operator_scope_mismatch_refuses_before_mutation(setup,drift):
    d,directory,_,_,path=setup
    value=json.loads(path.read_bytes());second=value["classifier"][1]
    if drift=="duplicate-tenant":second["tenant_id"]="team-a"
    elif drift=="shared-provider":second["provider"]=value["classifier"][0]["provider"]
    elif drift=="shared-destination":second["destination"]["destination_id"]=value["classifier"][0]["destination"]["destination_id"]
    elif drift in {"wrong-ip","broad-cidr"}:second["destination"]["cidrs"]=["10.96.0.36/32" if drift=="wrong-ip" else "10.96.0.0/24"]
    elif drift=="wrong-host":second["destination"]["host"]="foreign.invalid"
    elif drift=="wrong-port":second["destination"]["port"]=443
    elif drift=="wrong-path":second["destination"]["path_prefix"]="/"
    elif drift=="extra-bedrock":value["bedrock"]=[{}]
    else:
        put(directory/"foreign.token",b"foreign-token");second["bearer_token_file"]=str(directory/"foreign.token")
    put(path,value)
    with pytest.raises(a.AssemblyError):a._classifier_bundle(d,directory,d.owner(),path)
    assert d.events==[]


@pytest.mark.parametrize("drift",["owner","ip","type","selector","ports","external"])
def test_classifier_service_exact_owned_cluster_route(setup,drift):
    d,*_=setup
    if drift=="owner":d.service["metadata"]["labels"]["guardrails.evolution/owner"]="foreign"
    elif drift=="ip":d.service["spec"]["clusterIP"]="127.0.0.1"
    elif drift=="type":d.service["spec"]["type"]="LoadBalancer"
    elif drift=="selector":d.service["spec"]["selector"]={}
    elif drift=="ports":d.service["spec"]["ports"][0]["targetPort"]=8080
    else:d.service["spec"]["externalIPs"]=["1.2.3.4"]
    with pytest.raises(a.AssemblyError,match="service_invalid"):a._classifier_service_info(d,d.owner())


def test_real_transition_drains_before_secrets_then_fits_envelope_and_preserves_tokens(setup):
    d,directory,current,before,path=setup
    preserved=copy.deepcopy(d.secrets["gr-platform","runtime-1-config"]["data"])
    desired,attempt=a._classifier_transition(d,directory,d.owner(),current,before,"classifier",path,None)
    assert a._classifier_capacity(desired)=={"cpu_millicores":5950,"memory_mib":7936}
    assert not desired["components"]["ollama"]["enabled"]
    events=d.events
    quiet=next(i for i,event in enumerate(events) if isinstance(event,tuple) and event[0]=="helm")
    assert not set(events[quiet][1]) & a._HEAVY
    assert all(not str(event).startswith("secret-") for event in events[:quiet])
    assert events[-1][0]=="helm" and "classifier" in events[-1][1]
    data=d.secrets["gr-platform","runtime-1-config"]["data"]
    for key in ("workload.token","database-url","runtime-slots.json","ca.crt"):assert data[key]==preserved[key]
    assert (attempt/"plan.json").stat().st_mode & 0o777==0o600
    assert (attempt/"drained.json").is_file() and not (attempt/"complete.json").exists()


def test_interrupted_transition_reuses_same_plan_before_any_restart(setup):
    d,directory,current,before,path=setup
    d.fail_helm=True
    with pytest.raises(a.AssemblyError,match="helm_failed"):
        a._classifier_transition(d,directory,d.owner(),current,before,"classifier",path,None)
    pointer=(directory/"classifier-transitions/active.json").read_bytes()
    plan_path=directory/"classifier-transitions"/json.loads(pointer)["id"]/"plan.json"
    plan=plan_path.read_bytes()
    d.fail_helm=False
    desired,attempt=a._classifier_transition(d,directory,d.owner(),current,d.read_json(directory/"values.json"),"classifier",path,None)
    assert (directory/"classifier-transitions/active.json").read_bytes()==pointer
    assert plan_path.read_bytes()==plan and attempt==plan_path.parent
    assert desired["components"]["classifier"]["enabled"]


def test_partial_desired_apply_resumes_without_second_drain_or_new_journal(setup):
    d,directory,current,before,path=setup
    desired,attempt=a._classifier_transition(d,directory,d.owner(),current,before,"classifier",path,None)
    count=sum(event=="stop-forwards" for event in d.events)
    again,same=a._classifier_transition(d,directory,d.owner(),current,desired,"classifier",path,None)
    assert again==desired and same==attempt
    assert sum(event=="stop-forwards" for event in d.events)==count


@pytest.mark.parametrize("drift",["owner","values","plan","input","drained"])
def test_pending_transition_refuses_drift_without_new_plan(setup,drift):
    d,directory,current,before,path=setup
    desired,attempt=a._classifier_transition(d,directory,d.owner(),current,before,"classifier",path,None)
    pointer=(directory/"classifier-transitions/active.json").read_bytes()
    if drift=="owner":d.owner_value["container_id"]="other"
    elif drift=="values":put(directory/"values.json",{**desired,"extra":"unexpected"})
    elif drift=="plan":put(attempt/"plan.json",b"{}")
    elif drift=="input":put(path,{**json.loads(path.read_bytes()),"extra":"unexpected"})
    else:put(attempt/"drained.json",{"plan_sha256":"0"*64})
    with pytest.raises((a.AssemblyError,ValueError)):
        a._classifier_transition(d,directory,d.owner(),current,d.read_json(directory/"values.json"),"classifier",path,None)
    assert (directory/"classifier-transitions/active.json").read_bytes()==pointer


def test_runtime_secret_cas_race_preserves_existing_credentials(setup):
    d,directory,current,before,path=setup
    plan=a._classifier_plan(d,directory,d.owner(),current,before,"classifier",path,None)
    row=next(row for row in plan["secrets"] if row["name"]=="runtime-1")
    original=copy.deepcopy(d.secrets["gr-platform","runtime-1-config"]["data"])
    d.secret_race=True
    with pytest.raises(a.AssemblyError,match="cas_race"):a._classifier_secret_apply(d,d.owner(),row)
    assert d.secrets["gr-platform","runtime-1-config"]["data"]==original


def test_leaving_classifier_removes_private_dispatch_but_keeps_governance_and_stable_service(setup):
    d,directory,current,before,path=setup
    desired,attempt=a._classifier_transition(d,directory,d.owner(),current,before,"classifier",path,None)
    put(attempt/"complete.json",{"status":"profile_ready","profile":"classifier",
        "values_sha256":a.hashlib.sha256(a.canonical_json_bytes(desired)).hexdigest()});current["profile"]="classifier"
    restored,second=a._classifier_transition(d,directory,d.owner(),current,desired,"local_model",None,None)
    assert second!=attempt and not restored["components"]["classifier"]["enabled"]
    assert restored["components"]["ollama"]["enabled"] and restored["classifierService"]
    assert a._classifier_capacity(restored)=={"cpu_millicores":6000,"memory_mib":6496}
    data=d.secrets["gr-platform","runtime-1-config"]["data"]
    assert "classifier.token" not in data and "evaluators.json" not in data
    assert "evaluator_config_file" not in json.loads(base64.b64decode(data["service.json"]))
    assert base64.b64decode(data["workload.token"])==b"unchanged renewed token"


def test_completed_replay_keeps_same_plan_and_does_not_drain(setup):
    d,directory,current,before,path=setup
    desired,attempt=a._classifier_transition(d,directory,d.owner(),current,before,"classifier",path,None)
    put(attempt/"complete.json",{"status":"profile_ready","profile":"classifier",
        "values_sha256":a.hashlib.sha256(a.canonical_json_bytes(desired)).hexdigest()})
    current["profile"]="classifier";original=list(d.events)
    again,pending=a._classifier_transition(d,directory,d.owner(),current,desired,"classifier",path,None)
    assert again==desired and pending is None and d.events==original


def test_drain_timeout_preserves_plan_and_changes_no_secret(setup,monkeypatch):
    d,directory,current,before,path=setup
    original=copy.deepcopy(d.secrets);d.hold_pods=True
    drain=a._classifier_drain
    monkeypatch.setattr(a,"_classifier_drain",lambda d,v,o:drain(d,v,o,timeout=0))
    with pytest.raises(a.AssemblyError,match="drain_timeout"):
        a._classifier_transition(d,directory,d.owner(),current,before,"classifier",path,None)
    assert d.secrets==original
    pointer=json.loads((directory/"classifier-transitions/active.json").read_bytes())
    attempt=directory/"classifier-transitions"/pointer["id"]
    assert (attempt/"plan.json").exists() and not (attempt/"drained.json").exists()


def test_journal_parent_replacement_never_redirects_private_output(tmp_path,monkeypatch):
    parent=tmp_path/"journal";parent.mkdir(mode=0o700);old=tmp_path/"preserved"
    real=a.os.open
    def swapped(path,flags,*args,**kwargs):
        if str(path)=="plan.json":
            parent.rename(old);parent.mkdir(mode=0o700)
        return real(path,flags,*args,**kwargs)
    monkeypatch.setattr(a.os,"open",swapped)
    with pytest.raises(a.AssemblyError,match="directory_changed"):
        a._classifier_write(parent/"plan.json",b"synthetic private bytes")
    assert list(parent.iterdir())==[]
    assert (old/"plan.json").read_bytes()==b"synthetic private bytes"


@pytest.mark.parametrize("drift",["mode","symlink","ancestor","fifo"])
def test_operator_private_reader_refuses_unsafe_inputs_without_opening_pipe(tmp_path,drift):
    parent=tmp_path/"private";parent.mkdir(mode=0o700)
    path=parent/"value";put(path,b"private")
    if drift=="mode":path.chmod(0o644)
    elif drift=="symlink":path.unlink();path.symlink_to(tmp_path/"elsewhere")
    elif drift=="ancestor":
        parent.rename(tmp_path/"real");parent.symlink_to(tmp_path/"real",target_is_directory=True)
    else:path.unlink();os.mkfifo(path,0o600)
    with pytest.raises(a.AssemblyError):a._classifier_private(path)


def matches(selector,labels):
    if any(labels.get(k)!=v for k,v in selector.get("matchLabels",{}).items()):return False
    for row in selector.get("matchExpressions",[]):
        if row["operator"]=="In" and labels.get(row["key"]) not in row["values"]:return False
        if row["operator"]=="NotIn" and labels.get(row["key"]) in row["values"]:return False
    return True


def test_rendered_classifier_security_and_effective_network_union(setup,tmp_path):
    d,directory,current,before,path=setup
    plan=a._classifier_plan(d,directory,d.owner(),current,before,"classifier",path,None)
    documents=render_chart(tmp_path,"platform",plan["desired"])
    probes=render_chart(tmp_path,"probes",{"ownerId":"fixture-owner"})
    deployment=next(row for row in documents if row["kind"]=="Deployment" and row["metadata"]["name"]=="classifier")
    assert a._classifier_settings_match(plan["desired"]["components"]["classifier"],deployment["spec"]["template"])
    pod=deployment["spec"]["template"]["spec"]
    assert pod["initContainers"][0]["image"]==d.image("python")
    assert pod["containers"][0]["resources"]["limits"]=={"cpu":"2000m","memory":"3072Mi"}
    policies=[row for row in documents+probes if row["kind"]=="NetworkPolicy" and row["metadata"]["namespace"]=="gr-platform"]
    for component,expected_ports in (("classifier",set()),("runtime",{53,5432,8443})):
        ports={item["port"] for row in policies if matches(row["spec"]["podSelector"],{"guardrails.evolution/component":component})
               for rule in row["spec"].get("egress",[]) for item in rule.get("ports",[])}
        assert ports==expected_ports
    ingress=[rule for row in policies if matches(row["spec"]["podSelector"],{"guardrails.evolution/component":"classifier"}) for rule in row["spec"].get("ingress",[])]
    assert ingress==[{"from":[{"podSelector":{"matchLabels":{"guardrails.evolution/component":"runtime"}}}],"ports":[{"protocol":"TCP","port":8443}]}]
    assert len([row for row in documents if row["kind"]=="Service"])==8


def test_default_render_has_no_classifier_or_extra_route(setup,tmp_path):
    _,_,_,before,_=setup
    before.pop("classifierService")
    docs=render_chart(tmp_path,"platform",before)
    assert len([row for row in docs if row["kind"]=="Service"])==7
    assert not any("classifier" in row["metadata"]["name"] for row in docs)


@pytest.mark.parametrize("profile,selected",[("classifier","mock"),("deterministic_mock","mock"),("local_model","ollama")])
def test_real_gateway_route_keeps_classifier_on_original_mock_transport(setup,monkeypatch,profile,selected):
    d,directory,_,_,_=setup
    monkeypatch.setattr(a,"_tls",lambda *args:{"ca.crt":b"CA","tls.crt":b"cert","tls.key":b"key"})
    put(directory/"route.json",{"ca_files":{"local-ca":"original-file"},"routes":["unchanged-governed-route"]})
    put(directory/"workload.token",b"current-workload")
    put(directory/"gateway.token",b"current-gateway")
    routes={name:{"destination":{"host":name+".gr-model.svc.cluster.local","port":8443},
                  "provider":{"provider_id":name+"-provider","model_id":name+"-model"},
                  "route_manifest_file":str(directory/"route.json")} for name in ("mock","ollama")}
    value={"profiles":routes,"workload":{"workload_id":"original-workload"},
           "roles":{"workload":{"token_file":str(directory/"workload.token")}},"gateway_token_file":str(directory/"gateway.token")}
    captured={}
    def record(_d,_owner,name,namespace,files):
        assert name=="gateway-team-a" and namespace=="gr-team-a"
        captured.update(files);return "revision"
    original_gateway(d,directory,d.owner(),"team-a",value,profile,"mock-secret",secret_writer=record)
    settings=json.loads(captured["settings.json"])
    assert settings["provider_id"]==selected+"-provider"
    assert settings["profile"]==("deterministic_mock" if profile=="classifier" else profile)
    if profile == "classifier":
        assert settings["evaluation_profile"] == "classifier"
    else:
        assert "evaluation_profile" not in settings
    native=json.loads(captured["litellm.yaml"])
    assert native["model_list"][0]["litellm_params"]["timeout"]==(10 if selected=="mock" else 45)
    assert captured["workload.token"]==b"current-workload" and captured["gateway.token"]==b"current-gateway"
    assert json.loads(captured["routes.json"])["routes"]==["unchanged-governed-route"]
