"""Run the engine without uv: a private virtual environment with the pinned packages.

uv is the preferred runner: `uv run qrart.py` reads the script's dependency block and qrart.py.lock.
Without uv, qrart.py calls ensure() before any third-party import. When the running Python does not
already hold exactly the versions pinned in requirements.lock.txt (exported from qrart.py.lock), it
builds a virtual environment in the user's cache folder once, installs the pinned wheels with pip
(hash-checked, binary only, nothing resolved beyond the file), checks the result, and runs qrart.py
again with that environment's Python. The system Python and the user's projects stay untouched.

Standard library only: this module runs before any package is known to exist.

After changing qrart.py.lock, export the pins again (the self-test checks that they agree):
    uv export --script qrart.py --format requirements-txt --no-header --quiet -o requirements.lock.txt
"""
import hashlib
import os
import re
import shutil
import subprocess
import sys
import sysconfig
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REQS = os.path.join(HERE, 'requirements.lock.txt')
PY_MIN, PY_END = (3, 12), (3, 15)  # same range as requires-python in qrart.py
READY = '.qrcode-ready'  # written only after the installed versions were checked
LOCK_WAIT_S = 1800


def _canon(name):
    return re.sub(r'[-_.]+', '-', name).lower()


def pinned():
    """{package: (version, platform-conditional)} for every pin in requirements.lock.txt."""
    pins = {}
    with open(REQS, encoding='utf-8') as f:
        for line in f:
            m = re.match(r'([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;\\]+)\s*(;)?', line)
            if m:
                pins[_canon(m.group(1))] = (m.group(2), bool(m.group(3)))
    return pins


def matches_lock():
    """True when this interpreter holds exactly the pinned versions (uv run, or the private env).
    A platform-conditional pin may be absent, since its platform may not be this one."""
    from importlib import metadata
    pins = pinned()
    if not pins:
        return False
    for name, (version, conditional) in pins.items():
        try:
            if metadata.version(name) != version:
                return False
        except metadata.PackageNotFoundError:
            if not conditional:
                return False
    return True


def cache_dir():
    if os.name == 'nt':
        # not %LOCALAPPDATA%: the Microsoft Store Python silently redirects writes there into its own
        # package folder, and a virtual environment created there cannot start (no pyvenv.cfg found)
        base = os.path.join(os.path.expanduser('~'), '.cache')
    elif sys.platform == 'darwin':
        base = os.path.join(os.path.expanduser('~'), 'Library', 'Caches')
    else:
        base = os.environ.get('XDG_CACHE_HOME') or os.path.join(os.path.expanduser('~'), '.cache')
    return os.path.join(base, 'qrcode-skill')


def _pins_tag():
    with open(REQS, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()[:12]


def env_dir():
    """One environment per pin set and interpreter: new pins or another Python get their own."""
    ft = 't' if sysconfig.get_config_var('Py_GIL_DISABLED') else ''
    return os.path.join(cache_dir(), f'env-{_pins_tag()}-py{sys.version_info[0]}{sys.version_info[1]}{ft}')


def env_python(env):
    return os.path.join(env, 'Scripts', 'python.exe') if os.name == 'nt' else os.path.join(env, 'bin', 'python')


def _same_path(a, b):
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def _die(msg):
    print(f'ENGINE SETUP: {msg}', file=sys.stderr)
    sys.exit(3)


def ensure():
    """Return when this interpreter can run the engine; otherwise run the same command again in the
    private environment (building it first) and end with that run's exit status."""
    if not PY_MIN <= sys.version_info[:2] < PY_END:
        _die(f'without uv the engine needs Python {PY_MIN[0]}.{PY_MIN[1]}-{PY_END[0]}.{PY_END[1] - 1}, '
             f'not {sys.version.split()[0]}; install uv (recommended: https://docs.astral.sh/uv/) '
             'or one of those Python versions')
    if matches_lock():
        return
    env = env_dir()
    if _same_path(sys.prefix, env):
        _die(f'the private environment lacks the pinned packages; delete {env} and run again')
    if os.environ.get('UV_RUN_RECURSION_DEPTH'):
        # `uv run qrart.py` always matches (the self-test checks the export); this is uv running some
        # other environment, such as a project's: the private environment still gives the pinned set
        print('note: this uv environment lacks the pinned versions; using the private environment',
              file=sys.stderr)
    py = _prepare(env)
    cmd = [py, '-s', os.path.abspath(sys.argv[0]), *sys.argv[1:]]
    child_env = {k: v for k, v in os.environ.items() if k not in ('PYTHONPATH', 'PYTHONHOME')}
    sys.stdout.flush()
    sys.stderr.flush()
    try:
        if os.name != 'nt':
            os.execve(py, cmd, child_env)  # replaces this process: signals and exit status pass through
        r = subprocess.run(cmd, env=child_env)
    except OSError as e:
        _die(f'cannot start {py} ({e}); delete {env} and run again')
    sys.exit(r.returncode)


def _healthy(env):
    """Ready marker present, and the base Python recorded in pyvenv.cfg still installed."""
    if not (os.path.exists(os.path.join(env, READY)) and os.path.exists(env_python(env))):
        return False
    try:
        with open(os.path.join(env, 'pyvenv.cfg'), encoding='utf-8') as f:
            for line in f:
                key, _, value = line.partition('=')
                if key.strip() == 'home':
                    return os.path.isdir(value.strip())
    except OSError:
        pass
    return False


class _Lock:
    """Exclusive OS lock on a file, held while the environment is built. The OS drops it when the
    holder exits or is killed, so a crashed build never blocks the next run."""

    def __init__(self, path):
        self.path = path
        self.f = None

    def _try(self):
        if os.name == 'nt':
            import msvcrt
            self.f.seek(0)
            msvcrt.locking(self.f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(self.f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def __enter__(self):
        self.f = open(self.path, 'a+b')
        start, told = time.time(), False
        while True:
            try:
                self._try()
                return self
            except OSError:
                if time.time() - start > LOCK_WAIT_S:
                    self.f.close()
                    _die(f'another run has been setting up the environment for {LOCK_WAIT_S // 60} minutes; '
                         f'close it or delete {self.path}')
                if not told:
                    print('another run is setting up the private environment; waiting for it', file=sys.stderr)
                    told = True
                time.sleep(1)

    def __exit__(self, *exc):
        try:
            if os.name == 'nt':
                import msvcrt
                self.f.seek(0)
                msvcrt.locking(self.f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.f.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            self.f.close()


def _run(cmd, failure, show_output=False):
    """Run a setup step in the cache folder (never the user's project); exit with `failure` if it fails."""
    try:
        if show_output:  # pip's progress goes to stderr: stdout stays reserved for the engine's verdicts
            r = subprocess.run(cmd, cwd=cache_dir(), stdout=sys.stderr.fileno())
            detail = ''
        else:
            r = subprocess.run(cmd, cwd=cache_dir(), capture_output=True, text=True)
            detail = f' ({(r.stderr or r.stdout).strip()[:200]})' if r.returncode else ''
    except OSError as e:
        _die(f'{failure} ({e})')
    if r.returncode:
        _die(f'{failure}{detail}; install uv, or delete {os.path.dirname(os.path.dirname(cmd[0]))} and retry')


def _check_owner(path):
    """POSIX: refuse a cache folder owned by someone else (a shared XDG_CACHE_HOME could hold a planted env)."""
    if os.name != 'nt' and os.stat(path).st_uid != os.getuid():
        _die(f'{path} belongs to another user; set XDG_CACHE_HOME to a folder of your own, or install uv')


def _prepare(env):
    py = env_python(env)
    if _healthy(env):
        return py
    try:
        os.makedirs(cache_dir(), exist_ok=True)
        _check_owner(cache_dir())
        with _Lock(env + '.lock'):
            if _healthy(env):  # another run finished it while this one waited
                return py
            if os.path.exists(env):
                shutil.rmtree(env)  # half-built, or its base Python is gone
            print(f'first run without uv: building a private Python environment in {env} '
                  '(about 230 MB, 1-3 minutes)', file=sys.stderr)
            try:
                import venv
                venv.EnvBuilder(with_pip=True, symlinks=os.name != 'nt').create(env)
            except Exception as e:
                _die(f'cannot create a virtual environment ({e}); on Debian/Ubuntu install python3-venv, '
                     'or install uv')
            # -I (isolated): no PYTHONPATH, no user site-packages, no current folder on sys.path, so
            # nothing but the environment's own pip and the hash-checked wheels can run here
            _run([py, '-I', '-c', 'import sys'], "the new environment's Python does not start")
            _run([py, '-I', '-m', 'pip', 'install', '--disable-pip-version-check', '--no-input',
                  '--only-binary=:all:', '--require-hashes', '--no-deps', '-r', REQS],
                 'pip could not install the pinned packages (see above)', show_output=True)
            probe = (f'import sys; sys.path.insert(0, {HERE!r}); import bootstrap; '
                     'sys.exit(0 if bootstrap.matches_lock() else 1)')
            _run([py, '-I', '-c', probe], 'the environment does not hold the pinned packages after pip')
            with open(os.path.join(env, READY), 'w', encoding='utf-8') as f:
                f.write(sys.version + '\n')
        _drop_stale_envs()
        return py
    except OSError as e:
        _die(f'cannot set up {env} ({e})')


def _drop_stale_envs():
    """Remove environments built for older pin sets (about 230 MB each); best effort."""
    root, tag = cache_dir(), _pins_tag()
    try:
        names = os.listdir(root)
    except OSError:
        return
    for name in names:
        if name.startswith('env-') and not name.startswith(f'env-{tag}-') and not name.endswith('.lock'):
            shutil.rmtree(os.path.join(root, name), ignore_errors=True)
