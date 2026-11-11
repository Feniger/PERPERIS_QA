"""Opt-in local image smoke; no registry push/cloud API or infrastructure.

Build first, then: python tests/container_smoke.py
"""
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

IMAGE='perperis-qa:qa-cloud-05'
ENV={k:v for k,v in os.environ.items() if k not in {'DOCKER_HOST','DOCKER_CONTEXT','DOCKER_TLS','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH'}}
DOCKER=['docker','--host=unix:///var/run/docker.sock']


def docker(*args,check=True,env=None):
    return subprocess.run([*DOCKER,*args],env=ENV if env is None else env,check=check,capture_output=True,text=True)


def main():
    result=docker('run','--rm','--network','none','--entrypoint','python',IMAGE,'-c',
        "import os,pathlib;assert os.getuid()==65532;assert not pathlib.Path('/run/secrets/proxy_ca').exists();"
        "assert not list(pathlib.Path('/app').rglob('*.sqlite3'));assert not list(pathlib.Path('/app').rglob('.env*'));"
        "assert not list(pathlib.Path('/app').rglob('*sentinel*'));print('Non-root and image context checks passed')")
    print(result.stdout.strip())
    blocked=docker('run','--rm','--network','none','--read-only','--tmpfs','/tmp:rw,nosuid,nodev,size=32m',
        '-e','K_SERVICE=synthetic-qa-service',IMAGE,check=False)
    assert blocked.returncode!=0 and 'cloud startup is blocked' in blocked.stderr, 'Cloud startup was not blocked: '+blocked.stderr[-1500:]
    with socket.socket() as available:
        available.bind(('127.0.0.1',0));port=available.getsockname()[1]
    password=secrets.token_urlsafe(24)
    process_env={**ENV,'PERPERIS_QA_PASSWORD':password,'PERPERIS_QA_SECRET':secrets.token_urlsafe(48)}
    name='qa-image-smoke-'+secrets.token_hex(6)
    docker('run','-d','--name',name,'--read-only','--cap-drop','ALL','--security-opt','no-new-privileges',
        '--tmpfs','/tmp:rw,nosuid,nodev,size=64m','-p',f'127.0.0.1:{port}:8080',
        '-e','PERPERIS_QA_PASSWORD','-e','PERPERIS_QA_SECRET',IMAGE,env=process_env)
    origin=f'http://127.0.0.1:{port}'
    try:
        for _ in range(100):
            try:
                with urlopen(origin,timeout=1) as response:assert response.status==200
                break
            except OSError:time.sleep(.1)
        else:raise RuntimeError('Image failed to start')
        request=Request(origin+'/api/login',data=json.dumps({'password':password}).encode(),headers={'Content-Type':'application/json'})
        with urlopen(request,timeout=5) as response:
            cookie=response.headers['Set-Cookie'].split(';')[0]
        request=Request(origin+'/api/tickets',data=json.dumps({'title':'Synthetic image smoke','reporter':'Test','testing_round':'R01'}).encode(),headers={
            'Content-Type':'application/json','Cookie':cookie,'X-QA-Request':'1','Origin':origin})
        with urlopen(request,timeout=5) as response:
            assert response.status==201
        with urlopen(Request(origin+'/api/analytics',headers={'Cookie':cookie}),timeout=5) as response:
            assert json.load(response)['total']==1
        print('Container smoke passed: blocked cloud startup and local HTTP/API (in-memory tickets) on read-only, capability-dropped, non-root image.')
    finally:
        docker('rm','-f',name,check=False)


if __name__=='__main__':
    main()
