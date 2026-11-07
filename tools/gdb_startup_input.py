"""Read-only baseline probe for builds retaining the original Zettai PAD body.

Use gdb_controller_sdk.py for the later generated SDK-stub profile.
"""
import json
import struct

import gdb

startup_pending = {}
startup_samples = 0
startup_returns = 0


def startup_words(address, count):
    if address % 4 or not 0 <= count <= 32 or not 0 <= address <= 0x2000000 - count * 4:
        raise ValueError("startup probe requires aligned direct EE RAM")
    return list(struct.unpack("<" + "I" * count, bytes(
        gdb.selected_inferior().read_memory(ram + address, count * 4))))


def startup_context(address):
    return gdb.Value(address).cast(gdb.lookup_type("R5900Context").pointer()).dereference()


def startup_regs(ctx):
    return {str(i): int.from_bytes(bytes(gdb.selected_inferior().read_memory(
        int(ctx["r"][i].address), 4)), "little") for i in (2, 4, 5, 6, 28, 29, 31)}


def startup_identity(r):
    return {k: r[k] for k in ("sample_id", "context", "original_sp", "original_ra")}


class StartupPadReturn(gdb.FinishBreakpoint):
    def __init__(self, frame, key, record):
        super().__init__(frame, internal=True)
        self.key, self.record = key, record

    def stop(self):
        global startup_returns
        r = self.record
        item = {"kind": "startup-pad-host-return", **startup_identity(r)}
        try:
            ctx = startup_context(r["context"])
            regs = startup_regs(ctx)
            item.update(pc=int(ctx["pc"]), registers=regs,
                        guest_return_matches=int(ctx["pc"]) == r["original_ra"],
                        sdk_port_words=startup_words(r["sdk_address"], 7))
        except (gdb.error, ValueError, TypeError, AttributeError, IndexError) as error:
            item["probe_error"] = str(error)
        startup_pending.pop(self.key, None)
        startup_returns += 1
        trace.write(json.dumps(item) + "\n")
        return False

    def out_of_scope(self):
        trace.write(json.dumps({"kind": "startup-pad-return-unavailable",
                                **startup_identity(self.record)}) + "\n")


class StartupPadEntry(gdb.Breakpoint):
    def __init__(self):
        target = gdb.lookup_global_symbol("FUN_0024a970_0x24a970")
        if target is None:
            raise ValueError("original controller SDK symbol absent")
        with open(target.symtab.fullname()) as source:
            if "ps2_stubs::scePadGetState(" in source.read():
                raise ValueError("baseline probe requires the original SDK body, not the generated host stub")
        super().__init__("*" + str(int(target.value().address)), internal=True)

    def stop(self):
        global startup_samples
        r = None
        try:
            frame = gdb.newest_frame()
            if frame.architecture().name() != "i386:x86-64":
                raise ValueError("probe requires Linux x86-64 SysV")
            address = int(gdb.parse_and_eval("$rsi"))
            ctx = startup_context(address)
            if int(ctx["pc"]) != 0x24A970:
                return False
            regs = startup_regs(ctx)
            startup_samples += 1
            r = {"sample_id": startup_samples, "context": address,
                 "original_sp": regs["29"], "original_ra": regs["31"]}
            if regs["4"] >= 2 or regs["5"] >= 4:
                raise ValueError("unexpected controller port/slot")
            r["sdk_address"] = 0x825790 + regs["4"] * 112 + regs["5"] * 28
            key = (address, regs["29"], regs["31"])
            if key in startup_pending or len(startup_pending) >= 8:
                raise ValueError("duplicate or exhausted startup sample")
            startup_pending[key] = r
            trace.write(json.dumps({"kind": "startup-pad-entry", **startup_identity(r),
                                    "registers": regs, "sdk_address": r["sdk_address"],
                                    "sdk_port_words": startup_words(r["sdk_address"], 7),
                                    "game_open_words": startup_words(0x7FEA00, 8),
                                    "game_state_words": startup_words(0x7FEA20, 8)}) + "\n")
            StartupPadReturn(frame, key, r)
            if startup_samples >= 8:
                self.enabled = False
                trace.write(json.dumps({"kind": "startup-pad-cap", "limit": 8}) + "\n")
        except (gdb.error, ValueError, TypeError, AttributeError, IndexError) as error:
            self.enabled = False
            trace.write(json.dumps({"kind": "startup-pad-error", "error": str(error),
                                    **(startup_identity(r) if r is not None else {})}) + "\n")
        return False


try:
    startup_pad_hook = StartupPadEntry()
    trace.write(json.dumps({"kind": "startup-pad-hook", "pending": bool(startup_pad_hook.pending),
                            "enabled": bool(startup_pad_hook.enabled)}) + "\n")
except (gdb.error, ValueError, TypeError, AttributeError, OSError) as error:
    trace.write(json.dumps({"kind": "startup-pad-hook", "probe_error": str(error)}) + "\n")


def startup_snapshot(event):
    item = {"kind": "startup-input-snapshot", "entry_samples": startup_samples,
            "host_return_samples": startup_returns,
            "pending": [startup_identity(r) for r in startup_pending.values()]}
    try:
        item["regions"] = {hex(address): startup_words(address, count) for address, count in (
            (0x825790, 28), (0x7FEA00, 8), (0x7FEA20, 8), (0x7FEAA0, 16),
            (0x2CF970, 8), (0x2B6848, 6), (0x827100, 12), (0x828640, 1))}
    except (gdb.error, ValueError, TypeError, AttributeError) as error:
        item["probe_error"] = str(error)
    trace.write(json.dumps(item) + "\n")


gdb.events.stop.connect(startup_snapshot)
