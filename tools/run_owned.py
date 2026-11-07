#!/usr/bin/env python3
"""Bounded run of a native GA2 binary on a task-owned Xvfb display.

usage: run_owned.py <binary> <out-dir> <seconds> [key-schedule-json]
Key schedule: [[seconds, keysym], ...]; Circle is "c" or "Return", Start is "p" (Alt+Return is fullscreen); mouse1/mouse2/mouse3/wheelup/wheeldown act at the window centre; pointer actions at window pixels: "move:X,Y", "click:X,Y", "rclick:X,Y", "mclick:X,Y", "bclick:X,Y" and "fclick:X,Y" (the back and forward buttons), "dclick:X,Y", "wheel:N@X,Y", "hold:B:X,Y:MS", "drag:B:X1,Y1,X2,Y2[,STEPS[,STEP_MS]]", "press:B:X,Y", "release:B" (B = l, m or r); "keyhold:KEYSYM:MS" holds a key up to 8 s, "keydown:KEYSYM" and "keyup:KEYSYM" hold one across other actions (Ctrl+click).
Linux/X11 only (a Windows build has no equivalent harness yet; test it by hand under Wine or Windows).
Screenshots every GA2_SHOT_EVERY seconds (default 10); GA2_XVFB_SIZE=WxH sets the display size (default 1280x720; a 4x window needs more). Audio goes to a
task-owned PulseAudio null sink (recorded to audio.wav when GA2_RECORD_AUDIO=1).
Everything the harness starts is cleaned up on exit.
"""
import faulthandler, hashlib, json, os, signal, subprocess, sys, time
from pathlib import Path

faulthandler.register(signal.SIGUSR1)  # kill -USR1 <harness pid>: dump its stacks
# SIGTERM (timeout, kill) still runs the cleanup below: game, Xvfb, audio sink.
signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE / 'tools'))
import ga2  # noqa: E402
PRIVATE = ga2.DEFAULT_PRIVATE
from x11_input import tap_key  # noqa: E402

binary, out, seconds = Path(sys.argv[1]).resolve(), Path(sys.argv[2]), float(sys.argv[3])
keys = json.loads(Path(sys.argv[4]).read_text()) if len(sys.argv) > 4 else []
shot_every = float(os.environ.get('GA2_SHOT_EVERY', '10'))
# ga2_mugen_1 -> mugen-1; GA2_DISC overrides.
_inferred = binary.name[4:].replace('_', '-') if binary.name.startswith('ga2_') else 'zettai'
disc = os.environ.get('GA2_DISC', _inferred if _inferred in ga2.DISCS else 'zettai')
out.mkdir(parents=True, exist_ok=False)
record = dict(binary=str(binary), binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
              disc=disc, seconds=seconds, inputs=[], frames=[])


def save():
    (out / 'run-record.json').write_text(json.dumps(record, indent=1) + '\n')


def route_streams(session, sink, moved):
    """miniaudio opens the default device by name, which ignores PULSE_SINK;
    move every stream of the game's session onto the task-owned sink so runs
    are recorded and never play on the user's speakers."""
    try:
        listing = subprocess.run(['pactl', '-f', 'json', 'list', 'sink-inputs'], capture_output=True, timeout=5).stdout
        for entry in json.loads(listing or b'[]'):
            pid = entry.get('properties', {}).get('application.process.id')
            if not pid or entry.get('index') in moved:
                continue
            try:
                if int(Path(f'/proc/{int(pid)}/stat').read_text().rsplit(')', 1)[1].split()[3]) != session:
                    continue
            except (OSError, ValueError, IndexError):
                continue
            subprocess.run(['pactl', 'move-sink-input', str(entry['index']), sink], capture_output=True, timeout=5)
            moved.add(entry['index'])
    except (subprocess.SubprocessError, ValueError, OSError):
        pass


def process_tree_cpu(root):
    """CPU seconds (user + system) of a process and everything it started: in launcher
    mode the engine is a child, and the launcher alone would record ~0."""
    ticks_per_second = os.sysconf('SC_CLK_TCK')
    parents, cpu = {}, {}
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        try:
            fields = (entry / 'stat').read_text().rsplit(')', 1)[1].split()
            parents[int(entry.name)] = int(fields[1])
            cpu[int(entry.name)] = int(fields[11]) + int(fields[12])
        except (OSError, ValueError, IndexError):
            continue  # exited while scanning
    members, frontier = {root}, [root]
    while frontier:
        parent = frontier.pop()
        for pid, ppid in parents.items():
            if ppid == parent and pid not in members:
                members.add(pid)
                frontier.append(pid)
    if root not in cpu:
        return None
    return sum(cpu.get(pid, 0) for pid in members) / ticks_per_second


launcher = binary.name == 'ga2'
if launcher:
    # The player-facing launcher: GA2_START=<entry>, GA2_DATA_DIR=<its data dir>.
    data_dir = Path(os.environ.get('GA2_DATA_DIR', PRIVATE / 'launcher-data'))
    record['start'] = os.environ.get('GA2_START', '')
    command = [str(binary), '--data-dir', str(data_dir)]
    if record['start']:
        command += ['--start', record['start']]  # otherwise the menu runs
else:
    _, boot = ga2.manifest(PRIVATE, disc)
    mount = list(map(str, ga2.disc_set_arguments(PRIVATE, disc)))
    command = [str(binary), str(boot), *mount]
# Optional wrapper, e.g. GA2_WRAP='gprofng collect app -o prof.er' for profiling.
if os.environ.get('GA2_WRAP'):
    import shlex
    command = shlex.split(os.environ['GA2_WRAP']) + command
record['command'] = command
xvfb = game = module = None
try:
    import socket

    def x_ready(number):
        # Xvfb occasionally reports a display that never accepts clients.
        for _ in range(30):
            try:
                with socket.socket(socket.AF_UNIX) as probe:
                    probe.connect(f'/tmp/.X11-unix/X{number}')
                return True
            except OSError:
                time.sleep(0.1)
        return False

    for attempt in range(3):
        read_fd, write_fd = os.pipe()
        xvfb = subprocess.Popen(['Xvfb', '-displayfd', str(write_fd), '-screen', '0', os.environ.get('GA2_XVFB_SIZE', '1280x720') + 'x24', '-nolisten', 'tcp'],
                                pass_fds=(write_fd,), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.close(write_fd)
        display = os.read(read_fd, 64).decode().strip()
        os.close(read_fd)
        if display and xvfb.poll() is None and x_ready(display):
            break
        xvfb.kill()
        xvfb.wait()
    else:
        raise SystemExit('Xvfb did not start a usable display')
    env = dict(os.environ, DISPLAY=':' + display)
    sink = 'ga2_run_' + str(os.getpid())
    module = int(subprocess.check_output(['pactl', 'load-module', 'module-null-sink', 'sink_name=' + sink], timeout=15).strip())
    env['PULSE_SINK'] = sink
    # A separate client identity: the user's stream-restore settings for the
    # game (e.g. a muted ga2_zettai) neither apply to nor change from harness runs.
    env['PULSE_PROP'] = 'application.name=ga2-harness'
    record.update(display=env['DISPLAY'], sink=sink)
    env.setdefault('GA2_PERF', '1')
    recorder = None
    video = None
    if os.environ.get('GA2_RECORD_VIDEO') == '1':
        # Region of the 1280x720 display that holds the game window (centered 640x448).
        video = subprocess.Popen(['ffmpeg', '-loglevel', 'error', '-y', '-f', 'x11grab', '-framerate', '15',
                                  '-video_size', '1280x720', '-i', env['DISPLAY'],
                                  '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '28', str(out / 'video.mp4')],
                                 env=env, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if os.environ.get('GA2_RECORD_AUDIO') == '1':
        recorder = subprocess.Popen(['parecord', '--device=' + sink + '.monitor', '--file-format=wav',
                                     str(out / 'audio.wav')], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    with (out / 'game.log').open('w') as log:
        for launch in range(3):
            origin = time.monotonic()
            # Own process group: the launcher's engine child is torn down with it.
            game = subprocess.Popen(command, cwd=out, env=env, stdout=log, stderr=subprocess.STDOUT,
                                    start_new_session=True)
            # The window system occasionally refuses the very first client;
            # relaunch when the game exits early without opening its display.
            while game.poll() is None and time.monotonic() - origin < 4.0:
                time.sleep(0.2)
            log.flush()
            if game.poll() is None or 'Failed to open display' not in (out / 'game.log').read_text(errors='replace'):
                break
            record.setdefault('display_retries', 0)
            record['display_retries'] += 1
            time.sleep(1.0)
        record['pid'] = game.pid
        save()
        next_shot = 2.0
        keys = sorted(keys)
        cpu0 = None
        moved_streams, next_route = set(), 0.0
        # GA2_LIVE_KEYS=<file>: lines appended while running are sent at once
        # (any schedule key, or "shot" for a screenshot now).
        live_path = os.environ.get('GA2_LIVE_KEYS')
        live_pos = 0
        while game.poll() is None and time.monotonic() - origin < seconds:
            elapsed = time.monotonic() - origin
            if elapsed >= next_route:
                route_streams(game.pid, sink, moved_streams)  # start_new_session: sid == pid
                record['routed_streams'] = len(moved_streams)
                next_route = elapsed + 0.5
            if live_path and Path(live_path).exists():
                with open(live_path) as live:
                    live.seek(live_pos)
                    lines = live.readlines()
                    live_pos = live.tell()
                for line in (l.strip() for l in lines):
                    if line == 'shot':
                        name = f'live-{int(elapsed):05d}.png'
                        subprocess.run(['import', '-window', 'root', str(out / name)], env=env, timeout=10)
                        record['frames'].append(dict(file=name, at=elapsed))
                    elif line:
                        try:
                            tap_key(env['DISPLAY'], line, game.pid, 150)
                            record['inputs'].append(dict(at=elapsed, key=line, sent=elapsed, live=True))
                        except Exception as error:
                            record['inputs'].append(dict(at=elapsed, key=line, error=str(error)))
                        time.sleep(0.25)
            if keys and elapsed >= keys[0][0]:
                at, key = keys.pop(0)
                try:
                    tap_key(env['DISPLAY'], key, game.pid, 150)
                    record['inputs'].append(dict(at=at, key=key, sent=elapsed))
                except Exception as error:
                    record['inputs'].append(dict(at=at, key=key, error=str(error)))
            if elapsed >= next_shot:
                name = f'frame-{int(elapsed):05d}.png'
                try:
                    subprocess.run(['import', '-window', 'root', str(out / name)], env=env, timeout=10)
                except subprocess.TimeoutExpired:
                    name = None  # a busy host must not end the run
                cpu = process_tree_cpu(game.pid)
                record['frames'].append(dict(file=name, at=elapsed, cpu_seconds=cpu))
                save()
                next_shot += shot_every
            time.sleep(0.1)
        record['exit_code'] = game.poll()
finally:
    if game and game.poll() is None:
        os.killpg(game.pid, signal.SIGTERM)
        try:
            game.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(game.pid, signal.SIGKILL)
            game.wait(timeout=5)
        record['terminated_by_harness'] = True
    if game:
        try:
            os.killpg(game.pid, signal.SIGKILL)  # stragglers (an engine whose launcher exited)
        except ProcessLookupError:
            pass
    if launcher and record['start'] and (data_dir / 'logs' / f"{record['start']}.log").exists():
        (out / 'engine.log').write_bytes((data_dir / 'logs' / f"{record['start']}.log").read_bytes())
    if 'video' in globals() and video and video.poll() is None:
        try:
            video.communicate(b'q', timeout=20)
        except Exception:
            video.kill()
    if 'recorder' in globals() and recorder and recorder.poll() is None:
        recorder.terminate()
        recorder.wait(timeout=5)
    if module is not None:
        record['sink_unload'] = subprocess.run(['pactl', 'unload-module', str(module)], capture_output=True, timeout=10).returncode
    if xvfb:
        xvfb.terminate()
        try:
            xvfb.wait(timeout=5)
        except subprocess.TimeoutExpired:
            xvfb.kill()
    save()
