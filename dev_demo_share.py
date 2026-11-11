"""Start/stop only this temporary DEV demonstration (24 hours from activation)."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import signal
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parent
ROOT = REPO/'instance/dev/mobile-demo'
SHARE_SECONDS = 24 * 60 * 60


def activate(token, host):
    activated = time.time()
    return {'enabled':True, 'digest':hashlib.sha256(token.encode()).hexdigest(),
            'activated_at':activated, 'expires':activated + SHARE_SECONDS, 'host':host}



def write(name, data):
    target = ROOT/name
    temporary = target.with_suffix('.new')
    temporary.write_text(json.dumps(data))
    temporary.replace(target)


def stop():
    # Revocation is immediate, including cookies already issued.
    write('grant.json', {'enabled':False})
    try:
        pid = int((ROOT/'supervisor.pid').read_text())
        cmd = Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')
        cwd = Path(f'/proc/{pid}/cwd').resolve()
        script_matches = any(arg and arg.endswith(b'dev_demo_share.py') and
            (cwd/os.fsdecode(arg)).resolve() == REPO/'dev_demo_share.py' for arg in cmd)
        if script_matches and b'start' in cmd:
            os.kill(pid,signal.SIGTERM)
    except (OSError,ValueError):
        pass
    print('Partage révoqué et arrêté.')


def start():
    import dev_runtime
    dev_runtime.check_paths()
    with (ROOT/'supervisor.lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        (ROOT/'supervisor.pid').write_text(str(os.getpid()))
        token = secrets.token_urlsafe(32)
        # Startup time does not consume the 24-hour grant.
        grant = {'enabled':False,'expires':0,'host':'pending.invalid'}
        write('grant.json',grant)
        children = []
        def terminate(*args):
            raise KeyboardInterrupt
        signal.signal(signal.SIGTERM,terminate)
        signal.signal(signal.SIGINT,terminate)
        try:
            with (ROOT/'web.log').open('w') as web_log, (ROOT/'tunnel.log').open('w') as tunnel_log:
                children.append(subprocess.Popen([str(REPO/'.venv/bin/python'),'-B','-m','gunicorn',
                    '--bind','127.0.0.1:17011','--workers','1','--threads','4',
                    '--worker-tmp-dir',str(ROOT),'--access-logfile','/dev/null',
                    '--no-control-socket','dev_demo_gateway:create_application()'],
                    cwd=REPO,stdout=web_log,stderr=web_log))
                children.append(subprocess.Popen(['/tmp/digger-cloudflared','tunnel',
                    '--config','/dev/null','--no-autoupdate','--protocol','http2',
                    '--url','http://127.0.0.1:17011'],cwd=ROOT,stdout=tunnel_log,stderr=tunnel_log))
                deadline=time.monotonic()+60
                while time.monotonic()<deadline:
                    if any(p.poll() is not None for p in children):
                        raise RuntimeError('Demo process failed; inspect private logs')
                    match=re.search(r'https://[a-z0-9-]+\.trycloudflare\.com',
                                    (ROOT/'tunnel.log').read_text())
                    if match:
                        host=match.group().split('://',1)[1]
                        grant=activate(token,host)
                        expires=grant['expires']
                        write('grant.json',grant)
                        (ROOT/'link.txt').write_text(match.group()+'/#'+token+'\n')
                        print('Lien prêt dans instance/dev/mobile-demo/link.txt',flush=True)
                        break
                    time.sleep(.5)
                else:
                    raise RuntimeError('No Quick Tunnel URL received')
                while time.time()<expires and all(p.poll() is None for p in children):
                    time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            write('grant.json',{'enabled':False})
            for p in children:
                if p.poll() is None: p.terminate()
            for p in children:
                try: p.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    p.kill(); p.wait()


if __name__=='__main__':
    os.umask(0o077)
    ROOT.mkdir(mode=0o700,exist_ok=True)
    if sys.argv[1:]==['start']: start()
    elif sys.argv[1:]==['stop']: stop()
    else: raise SystemExit('Usage: dev_demo_share.py start|stop')
