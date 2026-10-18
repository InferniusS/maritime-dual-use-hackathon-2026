"""STEP assembly export through an isolated optional OpenCascade interpreter."""
import json,os,subprocess,tempfile
from pathlib import Path

def cad_python():return os.environ.get('PIPEFORGE_CAD_PYTHON','/workspace/pipeforge-cad-venv/bin/python')
def available():
    try:return Path(cad_python()).is_file() and subprocess.run([cad_python(),'-c','import OCP'],capture_output=True,timeout=15).returncode==0
    except (OSError,subprocess.TimeoutExpired):return False

def export_step(model):
    if not available():raise ValueError('Optional STEP kernel unavailable. Run scripts/setup-cad.sh; the main application remains usable.')
    with tempfile.TemporaryDirectory(prefix='pipeforge-step-') as directory:
        path=Path(directory)/'assembly.step'
        result=subprocess.run([cad_python(),str(Path(__file__).with_name('step_worker.py')),str(path)],input=model.model_dump_json(),text=True,capture_output=True,timeout=60)
        if result.returncode:raise ValueError('STEP kernel export failed: '+result.stderr[-1200:])
        return path.read_text()

def inspect_step(text):
    with tempfile.TemporaryDirectory(prefix='pipeforge-check-') as directory:
        path=Path(directory)/'assembly.step';path.write_text(text)
        result=subprocess.run([cad_python(),str(Path(__file__).with_name('step_worker.py')),str(path),'inspect'],capture_output=True,text=True,timeout=60)
        if result.returncode:raise ValueError(result.stderr[-1200:])
        return json.loads(result.stdout.strip().splitlines()[-1])
