"""SSH adapter for the normal frame-selection service; one model session per batch."""
from __future__ import annotations
import json
import shlex
import subprocess
import time
import uuid
from pathlib import Path
from .storage import atomic_write_json

_SESSIONS = {}

class RemoteSelector:
    def __init__(self, options):
        self.host = options['remote_host']
        self.control = options.get('remote_control_path')
        self.base = '/data04/swenz/ocr-on-demand'
        self.root = self.base+'/jobs/frame-session-'+uuid.uuid4().hex
        self.ssh = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15', '-o', 'ServerAliveInterval=30', '-o', 'ServerAliveCountMax=3']
        self.scp = ['scp', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15', '-o', 'ServerAliveInterval=30', '-o', 'ServerAliveCountMax=3']
        if self.control:
            self.ssh += ['-o', 'ControlPath='+self.control]
            self.scp += ['-o', 'ControlPath='+self.control]
        self.ssh.append(self.host)
        self.call(['mkdir','-p',self.root+'/requests'])
        acceleration = ['env', 'PYTHONPATH='+self.base+'/jobs/accel-validation-20261110/deps', 'TRITON_CACHE_DIR='+self.base+'/jobs/accel-validation-20261110/triton-cache'] if options.get('remote_acceleration') else []
        if options.get('remote_engine', 'auto') in ('auto', 'vllm'):
            acceleration = ['env', 'OCR_FRAME_ENGINE='+options.get('remote_engine', 'auto'), 'VLLM_WORKER_MULTIPROC_METHOD=spawn', 'VLLM_USE_FLASHINFER_SAMPLER=0', 'CUDA_HOME=/usr/local/cuda-12.4', 'FLASHINFER_WORKSPACE_BASE='+self.base+'/cache/flashinfer', 'MAX_JOBS=4']
        profile = ['--model-profile', 'auto']
        command = shlex.join(acceleration+[self.base+'/ocr','dish',*profile,'--frame-session',self.root,
                             '--output',self.root+'/unused.json','--timeout','604800'])
        launch = ('nohup sh -c '+shlex.quote(command+' > '+shlex.quote(self.root+'/model.log')+
                  ' 2>&1; code=$?; printf "%s" "$code" > '+shlex.quote(self.root+'/exit-code'))+
                  ' </dev/null >/dev/null 2>&1 &')
        self.shell(launch)
        try:
            for _ in range(300):
                code = self.read('exit-code')
                if code is not None:
                    raise RuntimeError('远端模型启动失败 exit='+code+' '+(self.read('model.log') or '')[-1500:])
                ready = self.read('ready.json')
                if ready is not None:
                    self.selected_runtime = json.loads(ready)
                    return
                time.sleep(2)
            raise TimeoutError('远端模型加载超时')
        except BaseException:
            self.call(['touch',self.root+'/close'])
            raise

    def shell(self, command):
        result = subprocess.run(self.ssh+[command],capture_output=True,text=True,timeout=60)
        if result.returncode:
            raise RuntimeError('SSH: '+result.stderr[-1000:])
        return result.stdout

    def call(self, args):
        return self.shell(shlex.join(args))

    def read(self, relative):
        path=self.root+'/'+relative
        return self._read(path)

    def _read(self,path):
        # Retrying a read never re-submits a video or re-loads the model.
        for attempt in range(3):
            try:
                value=self.shell('if test -f '+shlex.quote(path)+'; then cat '+shlex.quote(path)+'; else printf __MISSING__; fi')
                return None if value=='__MISSING__' else value
            except (RuntimeError, subprocess.TimeoutExpired):
                if attempt == 2:
                    raise
                time.sleep(2 ** attempt)

    def copy(self, source, destination):
        result=subprocess.run(self.scp+[str(source),str(destination)],capture_output=True,text=True,timeout=1800)
        if result.returncode:
            raise RuntimeError('SCP: '+result.stderr[-1000:])

    def select(self, video, output, run, options, context, ranges, progress):
        job='requests/'+uuid.uuid4().hex
        remote=self.root+'/'+job
        self.call(['mkdir','-p',remote])
        atomic_write_json(run/'remote-session.json',{'host':self.host,'session':self.root,'job':job, 'runtime':getattr(self, 'selected_runtime', {})})
        self.copy(video,self.host+':'+remote+'/video.mp4')
        config={k:options[k] for k in ('requirement','interval','fine_fps','top_k','fine_limit','coarse_limit','allow_fallback')}
        config.update(dish_context=context, review_uncertain=context is not None,time_ranges=ranges)
        atomic_write_json(run/'remote-request.json',config)
        self.copy(run/'remote-request.json',self.host+':'+remote+'/request.tmp')
        self.call(['mv',remote+'/request.tmp',remote+'/request.json'])
        seen=0
        deadline=time.monotonic()+6*3600
        while True:
            log=self.read(job+'/stderr.log') or ''
            for line in log[seen:].splitlines():
                progress.line(line)
            seen=len(log)
            done=self.read(job+'/done.json')
            if done is not None:
                done=json.loads(done);break
            code=self.read('exit-code')
            if code is not None:
                raise RuntimeError('远端模型已退出: '+code)
            if time.monotonic()>deadline:
                raise TimeoutError('远端视频选图超时')
            time.sleep(5)
        (run/'stderr.log').write_text(log)
        (run/'stdout.log').write_text(self.read(job+'/stdout.log') or '')
        if done['exit_code'] not in (0,2):
            return done['exit_code']
        output.mkdir(exist_ok=False)
        self.copy(self.host+':'+remote+'/result/result.json',output/'remote-result.json')
        result=json.loads((output/'remote-result.json').read_text())
        if result.get('input') != remote+'/video.mp4' or result.get('metadata') != remote+'/result/result.json':
            raise ValueError('远端返回了其他任务的结果')
        if result.get('image'):
            if result['image'] != remote+'/result/best_frame.png':
                raise ValueError('远端候选图片路径异常')
            self.copy(self.host+':'+remote+'/result/best_frame.png',output/'best_frame.png')
            result['image']=str(output/'best_frame.png')
        result.update(input=str(video),metadata=str(output/'result.json'))
        atomic_write_json(output/'result.json',result,backup=False)
        return done['exit_code']

    def close(self):
        self.call(['touch',self.root+'/close'])
        ready = self.read('ready.json')
        # If unwinding a failed in-flight request, terminate only this session's
        # known supervisor; its normal signal handler cleans up the model.
        jobs = self.call(['find', self.root+'/requests', '-name', 'request.json']).splitlines()
        busy = any(self._read(str(Path(job).parent/'done.json')) is None for job in jobs)
        if busy and ready:
            pid = json.loads(ready).get('supervisor_pid')
            if type(pid) is int and pid > 1:
                self.call(['kill', '-TERM', str(pid)])
        for _ in range(40):
            if self.read('exit-code') is not None:
                return
            time.sleep(2)
        raise RuntimeError('远端会话尚未退出，需检查 '+self.root)


def session(options):
    key=(options['remote_host'],options.get('remote_control_path'),options.get('remote_acceleration',False),options.get('remote_engine','auto'))
    if key in _SESSIONS and _SESSIONS[key].read('fatal.json') is not None:
        # Wait for supervisor cleanup and GPU lock release before replacement.
        _SESSIONS[key].close()
        _SESSIONS.pop(key)
    if key in _SESSIONS and _SESSIONS[key].read('exit-code') is not None:
        # The supervisor has finished cleanup; never reuse a dead session.
        _SESSIONS.pop(key)
    if key not in _SESSIONS:
        _SESSIONS[key]=RemoteSelector(options)
    return _SESSIONS[key]


def close_sessions():
    for key,value in list(_SESSIONS.items()):
        try:
            value.close()
        finally:
            _SESSIONS.pop(key,None)
