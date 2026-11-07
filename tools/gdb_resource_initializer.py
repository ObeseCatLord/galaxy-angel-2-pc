"""Bounded read-only Zettai resource callback and actual guest epilogue.

Records raw packet/register/table evidence. Source-backed expectations are
checked separately; live tables alone cannot establish original behavior.
"""
import json
import struct

import gdb

resource_pending = {}
resource_counts = {}
resource_sample_id = 0
resource_saved = (16, 17, 18, 19, 20, 28, 29, 31)


def resource_context(address):
    return gdb.Value(address).cast(gdb.lookup_type("R5900Context").pointer()).dereference()


def resource_regs(ctx):
    return {str(i): struct.unpack("<I", bytes(gdb.selected_inferior().read_memory(
        int(ctx["r"][i].address), 4)))[0] for i in (2, 4, 5, 6, 7, 16, 17, 18, 19, 20, 28, 29, 31)}


def resource_full(ctx):
    return {str(i): bytes(gdb.selected_inferior().read_memory(
        int(ctx["r"][i].address), 16)).hex() for i in resource_saved}


def resource_words(ram, address, count):
    physical = address & 0x1FFFFFF
    if not 0 <= count <= 35 or physical + count * 4 > 0x2000000:
        raise ValueError("probe read outside bounded EE RAM")
    return list(struct.unpack("<" + "I" * count, bytes(
        gdb.selected_inferior().read_memory(ram + physical, count * 4))))


def resource_emit(item):
    resource_counts[item["kind"]] = resource_counts.get(item["kind"], 0) + 1
    trace.write(json.dumps(item) + "\n")


def resource_identity(record):
    return {"sample_id": record["sample_id"], "context": record["context"],
            "original_sp": record["registers"]["29"]}


class ResourceReturn(gdb.FinishBreakpoint):
    def __init__(self, frame, key, record, helper=False):
        super().__init__(frame, internal=True)
        self.key, self.record, self.helper = key, record, helper

    def stop(self):
        r = self.record
        item = {"kind": "resource-helper-return" if self.helper else "resource-guest-return",
                **resource_identity(r)}
        try:
            ctx = resource_context(r["context"])
            regs = resource_regs(ctx)
            item.update(pc=int(ctx["pc"]), registers=regs)
            if self.helper:
                item["input_word"] = r["helper_input"]
                item["return_matches"] = int(ctx["pc"]) == 0x23EDE8
                item["result_matches"] = regs["2"] == r["helper_input"]
            else:
                item.update(full_preserved_after=resource_full(ctx),
                            packet_after=resource_words(r["ram"], r["output"], 24),
                            return_matches=int(ctx["pc"]) == r["registers"]["31"])
        except (gdb.error, ValueError, TypeError, AttributeError, IndexError) as error:
            item["probe_error"] = str(error)
        if not self.helper:
            resource_pending.pop(self.key, None)
        resource_emit(item)
        return False

    def out_of_scope(self):
        resource_emit({"kind": "resource-return-unavailable", "helper": self.helper,
                       **resource_identity(self.record)})


class ResourceHook(gdb.Breakpoint):
    def __init__(self, symbol, address, mechanism):
        target = gdb.lookup_global_symbol(symbol)
        if target is None:
            raise ValueError("probe symbol absent: " + symbol)
        super().__init__("*" + str(int(target.value().address)), internal=True)
        self.address, self.mechanism, self.samples = address, mechanism, 0

    def stop(self):
        global resource_sample_id
        r = None
        try:
            frame = gdb.newest_frame()
            if frame.architecture().name() != "i386:x86-64":
                raise ValueError("probe requires Linux x86-64 SysV")
            ram, context = int(gdb.parse_and_eval("$rdi")), int(gdb.parse_and_eval("$rsi"))
            ctx = resource_context(context)
            if int(ctx["pc"]) != self.address:
                return False
            regs = resource_regs(ctx)
            key = (context, regs["29"] if self.mechanism == "entry" else (regs["29"] + 64) & 0xFFFFFFFF)
            item = {"kind": "resource-" + self.mechanism, "registers": regs}
            if self.mechanism == "entry":
                if key in resource_pending or len(resource_pending) >= 8:
                    raise ValueError("duplicate or exhausted pending resource sample")
                resource_sample_id += 1
                r = {"sample_id": resource_sample_id, "context": context, "ram": ram, "registers": regs,
                     "output": regs["4"]}
                resource_pending[key] = r
                r["full"] = resource_full(ctx)
                active = resource_words(ram, 0x7FB780, 1)[0]
                item.update(full_preserved_before=r["full"],
                            packet_before=resource_words(ram, regs["4"], 24),
                            live_switch_table=resource_words(ram, 0x2EC930, 35),
                            active_descriptor=active,
                            active_slot74=resource_words(ram, active + 0x74, 1)[0])
            else:
                r = resource_pending.get(key)
                if r is None:
                    return False
                if self.mechanism == "helper":
                    if regs["31"] != 0x23EDE8:
                        return False
                    r["helper_input"] = resource_words(ram, 0x7FD880, 1)[0]
                    item["input_word"] = r["helper_input"]
                    ResourceReturn(frame, key, r, helper=True)
                elif self.mechanism == "epilogue":
                    ResourceReturn(frame, key, r)
                elif self.mechanism == "packet":
                    item["descriptor_mode"] = resource_words(ram, 0x2CF7C8, 1)[0]
            item.update(resource_identity(r))
            resource_emit(item)
            self.samples += 1
            if self.samples >= 8:
                self.enabled = False
                resource_emit({"kind": "resource-hook-cap", "address": self.address, "limit": 8})
        except (gdb.error, ValueError, TypeError, AttributeError, IndexError) as error:
            self.enabled = False
            resource_emit({"kind": "resource-probe-error", "address": self.address, "error": str(error),
                           **(resource_identity(r) if r is not None else {})})
        return False


resource_hooks = []
for symbol, address, mechanism in (
        ("entry_23ed20_0x23ed9c", 0x23ED20, "entry"),
        ("entry_0023ed9c_0x23ed9c", 0x23ED9C, "classification"),
        ("FUN_002318f8_0x2318f8", 0x2318F8, "helper"),
        ("entry_0023ee1c_0x23ee1c", 0x23EE1C, "packet"),
        ("entry_0023ef2c_0x23ef2c", 0x23EF2C, "epilogue")):
    item = {"kind": "resource-hook", "symbol": symbol, "address": address, "mechanism": mechanism}
    try:
        hook = ResourceHook(symbol, address, mechanism)
        resource_hooks.append(hook)
        item.update(pending=bool(hook.pending), enabled=bool(hook.enabled), limit=8)
    except (gdb.error, ValueError, TypeError, AttributeError) as error:
        item["probe_error"] = str(error)
    resource_emit(item)


def resource_summary(event):
    trace.write(json.dumps({"kind": "resource-stop-coverage", "counts": resource_counts,
                            "pending": [{**resource_identity(r), "ra": r["registers"]["31"],
                                         "selector": r["registers"]["5"]} for r in resource_pending.values()]}) + "\n")


gdb.events.stop.connect(resource_summary)
