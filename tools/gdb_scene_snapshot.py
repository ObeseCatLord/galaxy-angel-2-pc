"""Read-only sparse scene snapshots at the original input producer entry.

Executed by gdb_sif_boot's optional probe hook; requires Linux x86-64 SysV.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(os.environ["GA2_KERNEL_OWNERSHIP_PROBE"]).parent))
from scene_snapshot import SCENE_ELF_SHA256, physical_range, scene_snapshot

if os.environ.get("GA2_SCENE_ELF_SHA256") != SCENE_ELF_SHA256:
    raise ValueError("scene observer requires the verified original Zettai build")

scene_started = time.monotonic()


def scene_gdb_words(address, count):
    try:
        return list(struct.unpack("<" + "I" * count, bytes(
            gdb.selected_inferior().read_memory(ram + address, count * 4))))
    except gdb.error as error:
        raise OSError(str(error)) from error


class SceneEntry(gdb.Breakpoint):
    def __init__(self):
        symbol = gdb.lookup_global_symbol("FUN_00122cd8_0x122cd8")
        if symbol is None:
            raise ValueError("original Zettai input producer symbol absent")
        super().__init__("*" + str(int(symbol.value().address)), internal=True)
        self.deadlines = [2, 5, 10, 15, 20, 25, 30, 35, 40]

    def stop(self):
        elapsed = time.monotonic() - scene_started
        if not self.deadlines or elapsed < self.deadlines[0]:
            return False
        deadline = self.deadlines.pop(0)
        item = {"kind": "scene-snapshot", "seconds": round(elapsed, 3),
                "deadline": deadline, "consistency": "stopped-native"}
        try:
            frame = gdb.newest_frame()
            if frame.architecture().name() != "i386:x86-64":
                raise ValueError("scene entry requires Linux x86-64 SysV")
            ctx = gdb.Value(int(frame.read_register("rsi"))).cast(
                gdb.lookup_type("R5900Context").pointer()).dereference()
            item["guest_pc"] = int(ctx["pc"])
            if item["guest_pc"] != 0x122CD8:
                raise ValueError("scene entry does not match original input producer")
            item["snapshot"] = scene_snapshot(scene_gdb_words)
        except (gdb.error, ValueError, TypeError, IndexError) as error:
            item["probe_error"] = str(error)
        trace.write(json.dumps(item) + "\n")
        if not self.deadlines:
            self.enabled = False
        return False


scene_hook = SceneEntry()


def scene_context():
    frame = gdb.newest_frame()
    if frame.architecture().name() != "i386:x86-64":
        raise ValueError("scene entry requires Linux x86-64 SysV")
    return gdb.Value(int(frame.read_register("rsi"))).cast(
        gdb.lookup_type("R5900Context").pointer()).dereference()


def scene_register(ctx, index, size=4):
    return int.from_bytes(bytes(gdb.selected_inferior().read_memory(
        int(ctx["r"][index].address), size)), "little")


class ReadinessReturn(gdb.FinishBreakpoint):
    def __init__(self, record, ctx):
        self.record, self.context = record, int(ctx.address)
        super().__init__(gdb.newest_frame(), internal=True)

    def stop(self):
        item = {"kind": "scene-readiness-return", "sample_id": self.record["sample_id"]}
        try:
            ctx = gdb.Value(self.context).cast(gdb.lookup_type("R5900Context").pointer()).dereference()
            item.update(guest_pc=int(ctx["pc"]), v0_low64=scene_register(ctx, 2, 8),
                        return_matches=int(ctx["pc"]) == self.record["ra"],
                        expected_from_entry=self.record["expected_from_entry"])
        except (gdb.error, ValueError, TypeError) as error:
            item["probe_error"] = str(error)
        trace.write(json.dumps(item) + "\n")
        readiness_pending.discard(self.record["sample_id"])
        return False

    def out_of_scope(self):
        trace.write(json.dumps({"kind": "scene-readiness-return-unavailable",
                               "sample_id": self.record["sample_id"]}) + "\n")
        readiness_pending.discard(self.record["sample_id"])


readiness_pending = set()


class ReadinessEntry(gdb.Breakpoint):
    def __init__(self):
        symbol = gdb.lookup_global_symbol("FUN_00121ce0_0x121ce0")
        if symbol is None:
            raise ValueError("original readiness method absent")
        super().__init__("*" + str(int(symbol.value().address)), internal=True)
        self.attempts, self.entries = 0, 0

    def stop(self):
        if self.attempts >= 6:
            return False
        item = {"kind": "scene-readiness-entry"}
        attempt_started = False
        try:
            ctx = scene_context()
            if int(ctx["pc"]) != 0x121CE0 or scene_register(ctx, 31) != 0x13BCD0:
                return False
            self.attempts += 1
            attempt_started = True
            item.update(sample_id=self.attempts, seconds=round(time.monotonic() - scene_started, 3),
                        guest_pc=int(ctx["pc"]), ra=scene_register(ctx, 31))
            child, handle = scene_register(ctx, 17), scene_register(ctx, 5)
            physical_range(child, 32)
            child_words = scene_gdb_words(physical_range(child, 32), 32)
            handle_words = scene_gdb_words(physical_range(handle, 16), 16) if handle else []
            selector = child_words[17]
            item.update(child=child,
                        phase=child_words[26], substate=child_words[27], selector=selector,
                        selected_handle=child_words[4 + selector] if selector < 6 else None,
                        a1=handle, handle_words=handle_words,
                        expected_from_entry=int(bool(handle and handle_words[2] == 4 and handle_words[4] & 0x4)))
            ReadinessReturn(item, ctx)
            readiness_pending.add(self.attempts)
            self.entries += 1
        except (gdb.error, OSError, ValueError, TypeError, IndexError) as error:
            if not attempt_started:
                self.attempts += 1
            item["sample_id"] = self.attempts
            item["probe_error"] = str(error)
        trace.write(json.dumps(item) + "\n")
        if self.attempts >= 6:
            self.enabled = False
        return False


readiness_hook = ReadinessEntry()


def scene_stop_coverage(event):
    trace.write(json.dumps({"kind": "scene-stop-coverage",
                           "remaining_deadlines": scene_hook.deadlines,
                           "readiness_attempts": readiness_hook.attempts,
                           "readiness_entries": readiness_hook.entries,
                           "readiness_pending": sorted(readiness_pending)}) + "\n")


gdb.events.stop.connect(scene_stop_coverage)
