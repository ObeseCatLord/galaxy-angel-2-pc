#!/usr/bin/env python3
"""Bounded native boot capture on a private Xvfb display, without desktop input."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import select
import socket
import struct
import subprocess
import time

from ga2 import disc_mount_arguments, BUILD, DEFAULT_PRIVATE, DISCS, manifest
from x11_input import tap_key
from scene_snapshot import scene_snapshot


def pine_probe(path, addresses=(), scene=False):
    started = time.monotonic()
    deadline = started + 2
    usable = True

    def remaining_timeout():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise OSError("complete PINE probe deadline exceeded")
        return remaining

    def exact(stream, count):
        result = b""
        while len(result) < count:
            stream.settimeout(remaining_timeout())
            data = stream.recv(count - len(result))
            if not data:
                raise OSError("PINE closed the connection")
            result += data
        return result

    result = {}
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stream:
            stream.settimeout(remaining_timeout())
            stream.connect(str(path))
            requests = [("vm_status", b"\x0f"),
                        ("entry_word", b"\x02" + struct.pack("<I", 0x100008))]
            requests += [(f"ram_{address:08x}", b"\x02" + struct.pack("<I", address))
                         for address in addresses]
            def request(payload):
                nonlocal usable
                if not usable:
                    raise OSError("PINE connection invalidated after failed request")
                try:
                    stream.settimeout(remaining_timeout())
                    stream.sendall(struct.pack("<I", len(payload) + 4) + payload)
                    size = struct.unpack("<I", exact(stream, 4))[0]
                    if not 5 <= size <= 128:
                        raise OSError("unexpected PINE reply size")
                    reply = exact(stream, size - 4)
                    if reply[0] != 0 or len(reply) != 5:
                        raise OSError("PINE rejected the probe")
                    return struct.unpack("<I", reply[1:])[0]
                except OSError:
                    usable = False
                    stream.close()
                    raise
            for name, payload in requests:
                result[name] = request(payload)
            if scene and result["entry_word"] == 0x70000C28:
                result["scene_consistency"] = "sequential-reference-reads"
                result["scene_snapshot"] = scene_snapshot(lambda address, count: [
                    request(b"\x02" + struct.pack("<I", address + offset * 4))
                    for offset in range(count)])
            elif scene:
                result["scene_unsampled"] = "original Zettai entry word not loaded"
    except OSError as error:
        usable = False
        result["error"] = str(error)
    finished = time.monotonic()
    result["probe_elapsed_seconds"] = round(finished - started, 6)
    result["probe_started_monotonic"] = started
    result["probe_finished_monotonic"] = finished
    result["probe_deadline_seconds"] = 2
    result["transport_usable_at_end"] = usable
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("disc", choices=DISCS)
    parser.add_argument("--private-root", type=Path, default=DEFAULT_PRIVATE)
    parser.add_argument("--seconds", type=int, default=20)
    parser.add_argument("--reference", action="store_true", help="capture isolated PCSX2 instead of native")
    parser.add_argument("--slowboot", action="store_true", help="use reference BIOS boot sequence")
    parser.add_argument("--pine-socket", type=Path, help="read-only status/entry probe from isolated reference PINE")
    parser.add_argument("--scene-snapshot", action="store_true",
                        help="bounded original Zettai object reads through reference PINE")
    parser.add_argument("--read-at", action="append", type=lambda value: int(value, 0), default=[],
                        help="read an additional aligned EE RAM word through reference PINE")
    parser.add_argument("--key-at", action="append", default=[], metavar="SECONDS:KEY",
                        help="tap a test key in the named game window on the private display")
    parser.add_argument("--key-hold-ms", type=int, default=100,
                        help="test key hold duration, 10..2000 ms (default: 100)")
    parser.add_argument("--timer-watch", action="store_true", help="GDB queue provenance probe for native Zettai")
    parser.add_argument("--debug-sample", action="store_true", help="stop native execution at the selected sample time and sample stacks")
    parser.add_argument("--debug-sample-seconds", type=int, default=10,
                        help="time of the stopped native debug sample (default: ten seconds)")
    parser.add_argument("--sif-watch", action="store_true", help="trace exact-build native Zettai SIF initialization for ten seconds")
    args = parser.parse_args()
    if args.scene_snapshot and (not args.reference or not args.pine_socket or args.disc != "zettai"):
        parser.error("scene snapshot requires reference Zettai and isolated PINE")
    if not 1 <= args.seconds <= 60:
        parser.error("capture duration must be between 1 and 60 seconds")
    if not 10 <= args.key_hold_ms <= 2000:
        parser.error("key hold duration must be between 10 and 2000 ms")
    if args.read_at and (not args.reference or not args.pine_socket or
                        any(not (0 <= address <= 0x1FFFFFC or
                                 0x80000000 <= address <= 0x81FFFFFC or
                                 0xA0000000 <= address <= 0xA1FFFFFC) or address % 4
                            for address in args.read_at)):
        parser.error("--read-at requires reference PINE and aligned addresses within 32 MiB EE RAM")
    if args.timer_watch and (args.reference or args.disc != "zettai"):
        parser.error("timer watch is scoped to the verified native Zettai build")
    if args.debug_sample and (args.reference or args.timer_watch or args.seconds < 15):
        parser.error("debug sampling requires a native capture of at least fifteen seconds without timer watch")
    if args.debug_sample and not 1 <= args.debug_sample_seconds < args.seconds:
        parser.error("debug sample time must be positive and before the capture ends")
    if args.sif_watch and (args.reference or args.timer_watch or args.debug_sample or args.disc != "zettai" or args.seconds < 15):
        parser.error("SIF watch requires native Zettai, at least fifteen seconds, and no other debugger mode")
    keys = []
    for item in args.key_at:
        try:
            second, key = item.split(":", 1)
            second = float(second)
            if not 0 <= second < args.seconds or not key.isascii() or not key:
                raise ValueError()
            keys.append((second, key))
        except ValueError:
            parser.error("--key-at requires a time within the capture and an ASCII key name")
    keys.sort()
    private = args.private_root.resolve()
    build_manifest, boot = manifest(private, args.disc)
    if args.reference:
        references = list((private / "tooling").glob("pcsx2-*.AppImage"))
        if len(references) != 1:
            parser.error("expected one local PCSX2 AppImage under private tooling")
        binary = references[0]
        image = next((private / "discs" / args.disc).glob("*.iso"))
        command = [str(binary), "--appimage-extract-and-run", "-datapath",
                   str(private / "reference/pcsx2"), "-batch",
                   "-slowboot" if args.slowboot else "-fastboot", "-nofullscreen", str(image)]
    else:
        binary = BUILD / "ga2/bin" / f"ga2_{args.disc.replace('-', '_')}"
        command = [str(binary), str(boot), *map(str, disc_mount_arguments(private, args.disc))]
    if not binary.is_file():
        parser.error("native target is not built")
    mode = "reference" if args.reference else "native"
    capture = private / "captures" / f"{mode}-{args.disc}" / time.strftime("%Y%m%d-%H%M%S")
    capture.mkdir(parents=True)
    if args.reference:
        command[2:2] = ["-logfile", str(capture / "pcsx2.log")]
    elif args.timer_watch or args.sif_watch:
        script = "gdb_sif_boot.py" if args.sif_watch else "gdb_timer_queue.py"
        command = ["gdb", "--batch", "-ex", "set pagination off", "-ex", "set confirm off",
                   "-ex", "set disable-randomization off", "-ex", "break PS2Runtime::run", "-ex", "run", "-ex",
                   f"source {Path(__file__).with_name(script)}", "-ex", "continue",
                   "-ex", f"source {Path(__file__).with_name('gdb_snapshot.py')}",
                   "-ex", "quit", "--args", *command]
    elif args.debug_sample:
        command = ["gdb", "--batch", "-ex", "set pagination off", "-ex", "set confirm off",
                   "-ex", "set disable-randomization off", "-ex",
                   f"python import os, signal, threading; ga2_sample_timer = threading.Timer({args.debug_sample_seconds}, lambda: os.kill(os.getpid(), signal.SIGINT)); ga2_sample_timer.daemon = True; ga2_sample_timer.start()",
                   "-ex", "run", "-ex", "thread apply all bt 7", "-ex",
                   f"source {Path(__file__).with_name('gdb_snapshot.py')}",
                   "-ex", "quit", "--args", *command]
    read_fd, write_fd = os.pipe()
    server = None
    guest = None
    env = dict(os.environ)
    env["GA2_SCENE_ELF_SHA256"] = build_manifest["boot"]["sha256"]
    env["QT_QPA_PLATFORM"] = "xcb"
    if args.timer_watch:
        env["GA2_TIMER_TRACE"] = str(capture / "queue-trace.jsonl")
    if args.sif_watch:
        env["GA2_SIF_TRACE"] = str(capture / "sif-trace.jsonl")
    profile_lock = None
    try:
        if args.reference:
            profile_lock = (private / "reference/pcsx2/capture.lock").open("a")
            try:
                fcntl.flock(profile_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("another capture owns the isolated reference profile") from None
        with (capture / "xvfb.log").open("w") as server_log:
            server = subprocess.Popen(["Xvfb", "-displayfd", str(write_fd), "-screen", "0",
                                       "1280x720x24", "-nolisten", "tcp"],
                                      pass_fds=(write_fd,), stdout=server_log, stderr=subprocess.STDOUT)
            os.close(write_fd)
            write_fd = -1
            with os.fdopen(read_fd) as stream:
                if not select.select([stream], [], [], 15)[0]:
                    raise RuntimeError("Xvfb display allocation timed out")
                env["DISPLAY"] = ":" + stream.readline().strip()
            if env["DISPLAY"] == ":":
                raise RuntimeError("Xvfb did not allocate a display")
            with (capture / "native.log").open("w") as guest_log:
                guest = subprocess.Popen(command, cwd=capture,
                                         env=env, stdout=guest_log, stderr=subprocess.STDOUT)
                start = time.monotonic()
                frames = []
                probes = []
                sent_keys = []
                while time.monotonic() - start < args.seconds:
                    time.sleep(1)
                    elapsed = time.monotonic() - start
                    if guest.poll() is not None:
                        break
                    while keys and elapsed >= keys[0][0]:
                        _, key = keys.pop(0)
                        try:
                            title = tap_key(env["DISPLAY"], key, guest.pid, args.key_hold_ms)
                            sent_keys.append({"seconds": round(elapsed, 3), "key": key,
                                              "hold_ms": args.key_hold_ms, "window": title})
                        except (RuntimeError, ValueError) as error:
                            sent_keys.append({"seconds": round(elapsed, 3), "key": key, "error": str(error)})
                    if len(frames) == 0 or elapsed >= 5 and len(frames) == 1 or elapsed >= 15 and len(frames) == 2:
                        filename = f"frame-{elapsed:.1f}s.png"
                        subprocess.run(["import", "-window", "root", str(capture / filename)],
                                       env=env, check=True, timeout=10)
                        frames.append(filename)
                        if args.pine_socket:
                            probes.append({"seconds": round(elapsed, 3), **pine_probe(args.pine_socket, args.read_at, args.scene_snapshot)})
                exited = guest.poll()
                if exited is None:
                    filename = f"frame-{time.monotonic() - start:.1f}s.png"
                    subprocess.run(["import", "-window", "root", str(capture / filename)],
                                   env=env, check=True, timeout=10)
                    frames.append(filename)
                    if args.pine_socket:
                        probes.append({"seconds": round(time.monotonic() - start, 3), **pine_probe(args.pine_socket, args.read_at, args.scene_snapshot)})
                    guest.terminate()
                    try:
                        guest.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        guest.kill()
                        guest.wait(timeout=5)
                report = {"disc_id": args.disc, "mode": mode, "seconds": round(time.monotonic() - start, 3),
                          "verified_extracted_boot_elf_sha256": build_manifest["boot"]["sha256"],
                          "natural_exit_code": None if args.timer_watch or args.debug_sample or args.sif_watch else exited,
                          "debugger_exit_code": exited if args.timer_watch or args.debug_sample or args.sif_watch else None,
                          "bounded_stop": exited is None or args.timer_watch or args.debug_sample or args.sif_watch,
                          "frames": frames, "audio_backend": "runtime-default" if mode == "native" else "reference-config",
                          "reference_pine": probes,
                          "input": sent_keys,
                          "gameplay_status": "requires-visual-verification"}
                (capture / "capture.json").write_text(json.dumps(report, indent=2) + "\n")
                print(capture)
                print(json.dumps(report))
    finally:
        if write_fd >= 0:
            os.close(write_fd)
        if guest and guest.poll() is None:
            guest.kill()
            guest.wait(timeout=5)
        if server and server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
        if profile_lock:
            profile_lock.close()


if __name__ == "__main__":
    main()
