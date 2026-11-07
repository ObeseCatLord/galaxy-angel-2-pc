"""Bounded read-only exact-Zettai factory entry and logical guest return.

The factory's host function can return at a callee/resume/shared epilogue PC.
Completion is observed only through the actual guest epilogue, with a matching
context and stack frame. Entry arguments require Linux x86-64 SysV.
"""
import json
import struct

import gdb

factory_pending = {}
factory_counts = {}
factory_nop_entries = {}
factory_nop_finishes = {}


def factory_context(address):
    return gdb.Value(address).cast(gdb.lookup_type("R5900Context").pointer()).dereference()


def factory_registers(ctx):
    return {str(i): struct.unpack("<I", bytes(gdb.selected_inferior().read_memory(
        int(ctx["r"][i].address), 4)))[0] for i in (2, 4, 5, 6, 7, 16, 28, 29, 31)}


def factory_full_registers(ctx):
    return {str(i): bytes(gdb.selected_inferior().read_memory(
        int(ctx["r"][i].address), 16)).hex() for i in range(32)}


def factory_words(ram, address, count=16):
    if not 0 <= count <= 16:
        raise ValueError("probe read cap exceeded")
    physical = address & 0x1FFFFFF
    if physical + count * 4 > 0x2000000:
        raise ValueError("probe read outside EE RAM")
    return list(struct.unpack("<" + "I" * count, bytes(
        gdb.selected_inferior().read_memory(ram + physical, count * 4))))


class FactoryGuestReturn(gdb.FinishBreakpoint):
    def __init__(self, frame, key, record, nop=False):
        super().__init__(frame, internal=True)
        self.key, self.record, self.nop = key, record, nop

    def stop(self):
        record = self.record
        item = {"kind": "factory-nop-return" if self.nop else "factory-guest-return",
                "entry": record["entry"]}
        try:
            ctx = factory_context(record["context"])
            regs = factory_registers(ctx)
            full = factory_full_registers(ctx)
            preserved = range(32) if self.nop else (16, 28, 29, 31)
            item.update(pc=int(ctx["pc"]), registers=regs,
                        preserved_registers_128=all(full[str(i)] == record["full"][str(i)] for i in preserved),
                        return_matches=int(ctx["pc"]) == record["registers"]["31"])
            if self.nop:
                item["all_gpr_128"] = full
            else:
                result = regs["2"]
                item["full_preserved_after"] = {str(i): full[str(i)] for i in (16, 28, 29, 31)}
                item.update(result=result, constructor_address=record.get("constructor_address"),
                            original_selector=record["registers"]["5"])
                item["pointer_matches_observed_constructor"] = (
                    result == record["constructor_address"] if "constructor_address" in record else None)
                item["result_words"] = factory_words(record["ram"], result) if result else None
        except (gdb.error, ValueError, TypeError, AttributeError, IndexError) as error:
            item["probe_error"] = str(error)
        if not self.nop:
            factory_pending.pop(self.key, None)
        factory_counts[item["kind"]] = factory_counts.get(item["kind"], 0) + 1
        if self.nop:
            entry = hex(record["entry"])
            factory_nop_finishes[entry] = factory_nop_finishes.get(entry, 0) + 1
        trace.write(json.dumps(item) + "\n")
        return False

    def out_of_scope(self):
        trace.write(json.dumps({"kind": "factory-return-unavailable", "entry": self.record["entry"],
                                "mechanism": "nop" if self.nop else "guest-epilogue"}) + "\n")


class FactoryHook(gdb.Breakpoint):
    def __init__(self, symbol, entry, mechanism):
        target = gdb.lookup_global_symbol(symbol)
        if target is None:
            raise ValueError("function symbol absent: " + symbol)
        super().__init__("*" + str(int(target.value().address)), internal=True)
        self.entry, self.mechanism, self.samples = entry, mechanism, 0

    def stop(self):
        try:
            frame = gdb.newest_frame()
            if frame.architecture().name() != "i386:x86-64":
                raise ValueError("probe requires Linux x86-64 SysV")
            ram, context = int(gdb.parse_and_eval("$rdi")), int(gdb.parse_and_eval("$rsi"))
            ctx = factory_context(context)
            if int(ctx["pc"]) != self.entry:
                return False  # Continuation entries are not a new factory call.
            regs = factory_registers(ctx)
            if self.mechanism in ("epilogue", "case1-constructor"):
                key = (context, (regs["29"] + 16) & 0xFFFFFFFF)
                record = factory_pending.get(key)
                if record is None:
                    return False
                if self.mechanism == "epilogue":
                    FactoryGuestReturn(frame, key, record)
                else:
                    if record["registers"]["5"] != 1 or regs["31"] != 0x13A2DC:
                        return False
                    record["constructor_address"] = regs["4"]
            else:
                key = (context, regs["29"])
                record = {"entry": self.entry, "ram": ram, "context": context,
                          "registers": regs, "full": factory_full_registers(ctx)}
                if self.mechanism == "factory":
                    if key in factory_pending or len(factory_pending) >= 8:
                        raise ValueError("duplicate or exhausted pending factory sample")
                    factory_pending[key] = record
                else:
                    FactoryGuestReturn(frame, key, record, nop=True)
                    entry = hex(self.entry)
                    factory_nop_entries[entry] = factory_nop_entries.get(entry, 0) + 1
            item = {"kind": "factory-observation", "mechanism": self.mechanism,
                    "entry": self.entry, "registers": regs}
            if self.mechanism == "factory":
                item["control_words"] = factory_words(ram, regs["4"])
                item["full_preserved_before"] = {str(i): record["full"][str(i)] for i in (16, 28, 29, 31)}
                item["original_switch_table"] = factory_words(ram, 0x2DD270, 11)
            elif self.mechanism == "nop":
                item["all_gpr_128"] = record["full"]
            trace.write(json.dumps(item) + "\n")
            self.samples += 1
            if self.samples >= 8:
                self.enabled = False
                trace.write(json.dumps({"kind": "factory-hook-cap", "entry": self.entry, "limit": 8}) + "\n")
        except (gdb.error, ValueError, TypeError, AttributeError, IndexError) as error:
            self.enabled = False
            trace.write(json.dumps({"kind": "factory-probe-error", "entry": self.entry,
                                    "error": str(error)}) + "\n")
        return False


factory_hooks = []
for symbol, entry, mechanism in (
        ("entry_13a290_0x13a420", 0x13A290, "factory"),
        ("entry_0013a420_0x13a420", 0x13A420, "epilogue"),
        ("FUN_0013c4c8_0x13c4c8", 0x13C4C8, "case1-constructor"),
        ("entry_13a4b8_0x13a4c0", 0x13A4B8, "nop"),
        ("entry_13a4c0_0x13a4c8", 0x13A4C0, "nop"),
        ("entry_13a4c8_0x13a4d0", 0x13A4C8, "nop"),
        ("entry_13a4d0_0x13a4d8", 0x13A4D0, "nop")):
    item = {"kind": "factory-hook", "entry": entry, "mechanism": mechanism, "symbol": symbol}
    try:
        hook = FactoryHook(symbol, entry, mechanism)
        factory_hooks.append(hook)
        item.update(pending=bool(hook.pending), enabled=bool(hook.enabled), limit=8)
    except (gdb.error, ValueError, TypeError, AttributeError) as error:
        item["probe_error"] = str(error)
    trace.write(json.dumps(item) + "\n")


def factory_summary(event):
    trace.write(json.dumps({"kind": "factory-stop-coverage", "counts": factory_counts,
                            "pending_factory": [{"entry": r["entry"], "sp": r["registers"]["29"],
                                         "selector": r["registers"]["5"]} for r in factory_pending.values()],
                            "nop_entry_samples": factory_nop_entries,
                            "nop_finish_samples": factory_nop_finishes,
                            "nop_unmatched_entry_samples": {entry: count - factory_nop_finishes.get(entry, 0)
                                                            for entry, count in factory_nop_entries.items()}}) + "\n")


gdb.events.stop.connect(factory_summary)
