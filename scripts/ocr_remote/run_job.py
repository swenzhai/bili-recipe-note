"""Run an isolated OCR/vision job and release its GPU on completion or cancellation."""
import argparse
from contextlib import contextmanager
import csv
from dataclasses import dataclass
import fcntl
import os
from pathlib import Path
import signal
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
# Headroom above measured peaks (PDF 8.4 GiB, dish 17.1 GiB).
MIN_FREE_MIB = {"pdf": 10 * 1024, "dish": 20 * 1024}


@dataclass(frozen=True)
class ModelProfile:
    name: str
    model: str
    min_free_mib: int
    python: Path
    backend: str
    engine: str = "transformers"


# Preference order, not a measured accuracy guarantee. No model binds to a GPU UUID.
DISH_PROFILES = (
    ModelProfile("27b", "Qwen3.8-27B-NF4", 23 * 1024,
                 ROOT / "envs/dish-quality/bin/python", "qwen38-nf4"),
    ModelProfile("8b", "Qwen3-VL-8B-Instruct", 20 * 1024,
                 Path("/data03/swenz/miniforge3/envs/py310/bin/python"), "qwen3vl-bf16"),
)


class ResourcesBusy(RuntimeError):
    pass


@dataclass(frozen=True)
class GPU:
    uuid: str
    name: str
    used: int
    free: int
    total: int
    utilization: int
    has_process: bool = False


def query_gpus():
    """Fail closed if GPU memory, utilization or compute processes are unknown."""
    def query(fields):
        return subprocess.run([
            "nvidia-smi", fields, "--format=csv,noheader,nounits",
        ], check=True, capture_output=True, text=True, timeout=15).stdout

    try:
        rows = list(csv.reader(query(
            "--query-gpu=uuid,name,memory.used,memory.free,memory.total,utilization.gpu"
        ).splitlines(), skipinitialspace=True))
        occupied = set()
        for row in csv.reader(query("--query-compute-apps=gpu_uuid,pid").splitlines(),
                              skipinitialspace=True):
            if not row:
                continue
            uuid, pid = (value.strip() for value in row)
            if not uuid.startswith("GPU-") or int(pid) <= 0:
                raise ValueError("Unknown compute process identity")
            occupied.add(uuid)
        gpus = []
        for row in rows:
            if not row:
                continue
            uuid, name, used, free, total, utilization = (value.strip() for value in row)
            if not uuid.startswith("GPU-") or any(c not in "0123456789abcdefABCDEF-" for c in uuid[4:]):
                raise ValueError("Unknown GPU UUID")
            gpu = GPU(uuid, name, int(used), int(free), int(total), int(utilization), uuid in occupied)
            if min(gpu.used, gpu.free, gpu.total, gpu.utilization) < 0 or gpu.utilization > 100:
                raise ValueError("Invalid GPU metrics")
            gpus.append(gpu)
        identities = {gpu.uuid for gpu in gpus}
        if len(identities) != len(gpus) or not occupied.issubset(identities):
            raise ValueError("GPU/process inventory changed or is unsupported")
        return gpus
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        raise ResourcesBusy("Cannot verify GPU availability; no model was started: " + str(error)) from error


def eligible(gpu, kind, min_free_mib=None):
    return (not gpu.has_process and gpu.used <= 1024 and gpu.utilization == 0
            and gpu.free >= (MIN_FREE_MIB[kind] if min_free_mib is None else min_free_mib))


@contextmanager
def reserve_gpu(kind, lock_dir, min_free_mib=None):
    # For a given model, prefer the smallest suitable card.
    candidates = sorted((gpu for gpu in query_gpus() if eligible(gpu, kind, min_free_mib)),
                        key=lambda gpu: (gpu.total, -gpu.free, gpu.uuid))
    for candidate in candidates:
        lock = (lock_dir / (candidate.uuid + ".lock")).open("a")
        try:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                continue
            # The same UUID lock is shared by PDF and dish, including model loading.
            # Recheck after locking: another program may have started since discovery.
            current = next((gpu for gpu in query_gpus() if gpu.uuid == candidate.uuid), None)
            if current is None or not eligible(current, kind, min_free_mib):
                continue
            yield current
            return
        finally:
            lock.close()
    raise ResourcesBusy(
        f"No idle GPU with at least {min_free_mib or MIN_FREE_MIB[kind]} MiB free for {kind}. "
        "Cards with compute processes, >1 GiB used, nonzero utilization or an OCR lock are skipped. "
        "Retry later; no model was started."
    )


@contextmanager
def reserve_model(kind, lock_dir, profiles):
    """Try models in quality preference order, keeping the selected GPU locked."""
    from contextlib import ExitStack
    for profile in profiles:
        # Catch only reservation failure; worker failures must not be retried silently.
        with ExitStack() as stack:
            try:
                gpu = stack.enter_context(reserve_gpu(kind, lock_dir, profile.min_free_mib))
            except ResourcesBusy:
                continue
            # If a larger card became idle between attempts, keep the best model
            # that fits the card now reserved, rather than unnecessarily downgrading.
            selected = next(p for p in profiles if p.min_free_mib <= gpu.free)
            yield selected, gpu
            return
    raise ResourcesBusy("No idle GPU can run an available model profile. Retry later; no model was started.")


def frame_profiles(engine):
    if engine not in ("auto", "vllm", "transformers"):
        raise ValueError("Unknown frame engine")
    fast = ModelProfile("27b", "Qwen3.8-27B-NF4", 34 * 1024,
                        ROOT / "envs/dish-vllm-validation/bin/python", "qwen38-nf4", "vllm")
    small = ModelProfile("8b", "Qwen3-VL-8B-Instruct", 20 * 1024,
                         ROOT / "envs/dish-vllm-validation/bin/python", "qwen3vl-bf16", "vllm")
    if engine == "vllm":
        return [fast, small]
    if engine == "transformers":
        return list(DISH_PROFILES)
    # With both inference engines installed, prefer the validated faster profile
    # when its VRAM threshold is met; reserve_model handles the final live GPU check.
    return [fast, small]


def profiles_for(kind, requested):
    if kind == "pdf":
        if requested != "auto":
            raise ValueError("--model-profile is only supported for dish")
        return [ModelProfile("pdf", "PaddleOCR-VL-1.6", MIN_FREE_MIB["pdf"],
                             ROOT / "envs/pdf/bin/python", "paddle")]
    profiles = [p for p in frame_profiles(os.environ.get("OCR_FRAME_ENGINE", "transformers"))
                if requested == "auto" or p.name == requested]
    available = []
    for profile in profiles:
        if profile.python.is_file() and (ROOT / "models" / profile.model / "config.json").is_file():
            available.append(profile)
        else:
            print("Model profile unavailable: " + profile.name, file=sys.stderr)
    if not available:
        raise ValueError("Requested model environment or weights are missing")
    return available


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=MIN_FREE_MIB)
    parser.add_argument("--model-profile", choices=("auto", "27b", "8b"), default="auto",
                        help="dish: highest preferred feasible model by default; explicit profile disables fallback")
    parser.add_argument("--timeout", type=int, help="Maximum runtime in seconds (PDF 7200, dish 900)")
    args, worker_args = parser.parse_known_args()
    if args.timeout is not None and args.timeout <= 0:
        parser.error("timeout must be positive")
    if not worker_args:
        parser.error("Provide input and output paths; append --help to see worker arguments")
    try:
        profiles = profiles_for(args.kind, args.model_profile)
    except ValueError as error:
        parser.error(str(error))
    script = ROOT / "scripts" / ("parse_pdf_once.py" if args.kind == "pdf" else "judge_dish_once.py")
    env = os.environ.copy()
    env.update({
        "CUDA_DEVICE_ORDER": "PCI_BUS_ID",
        "PYTHONNOUSERSITE": "1", "PYTHONUNBUFFERED": "1",
        "TMPDIR": str(ROOT / "tmp"), "PIP_CACHE_DIR": str(ROOT / "cache/pip"),
        "HF_HOME": str(ROOT / "cache/huggingface"),
        "MODELSCOPE_CACHE": str(ROOT / "cache/modelscope"),
        "PADDLE_PDX_CACHE_HOME": str(ROOT / "cache/paddlex"),
        "PADDLE_PDX_MODEL_SOURCE": "bos",
        "PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK": "True",
        "XDG_CACHE_HOME": str(ROOT / "cache/xdg"),
        "OMP_NUM_THREADS": "8", "TOKENIZERS_PARALLELISM": "false",
    })
    # Locks live on the host filesystem, not the model directory's NFS mount.
    lock_dir = Path.home() / ".local/state/ocr-on-demand"
    lock_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (lock_dir / (args.kind + ".lock")).open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.exit(75, f"Another {args.kind} job is running. Retry after it finishes.\n")
        try:
            with reserve_model(args.kind, lock_dir, profiles) as (profile, gpu):
                command = [str(profile.python), str(script), *worker_args]
                if args.kind == "dish":
                    command += ["--model", str(ROOT / "models" / profile.model),
                                "--backend", profile.backend]
                env["CUDA_VISIBLE_DEVICES"] = gpu.uuid
                env["OCR_FRAME_ENGINE"] = profile.engine
                print(f"Starting {args.kind} job on {gpu.uuid} ({gpu.name}, {gpu.free} MiB free); "
                      f"model={profile.model}, profile={profile.name}; model exits after this job.", flush=True)
                return run_worker(command, env, args.timeout or (7200 if args.kind == "pdf" else 900))
        except ResourcesBusy as error:
            parser.exit(75, str(error) + "\n")


def run_worker(command, env, timeout):
    child = None
    def cancelled(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, cancelled)
    signal.signal(signal.SIGHUP, cancelled)
    try:
        child = subprocess.Popen(command, env=env, start_new_session=True)
        try:
            code = child.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            print("Job exceeded its time limit; stopping the worker.", file=sys.stderr)
            code = 124
        except KeyboardInterrupt:
            print("Job cancelled; stopping the worker.", file=sys.stderr)
            code = 130
    finally:
        # Keep both locks held until the worker and its process group are stopped.
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        if child is not None:
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                child.wait(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
            # Remove any descendants that outlived the model process.
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    print(f"Job finished (exit {code}); worker process stopped.", flush=True)
    return code if code >= 0 else 128 - code


if __name__ == "__main__":
    sys.exit(main())
