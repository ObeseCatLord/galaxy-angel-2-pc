"""Read-only exact-Zettai 113C78 mask query and its leaf guest return.

Records original descriptor and indexed inputs, raw 128-bit GPRs, object words,
and correlated return evidence. No guest state or input is synthesized.
"""
import json
import struct

import gdb

mask_pending = {}
mask_counts = {}
mask_sample_id = 0


def mask_context(address):
    return gdb.Value(address).cast(gdb.lookup_type("R5900Context").pointer()).dereference()


def mask_full(ctx):
    return {str(i): bytes(gdb.selected_inferior().read_memory(
        int(ctx["r"][i].address), 16)).hex() for i in range(32)}


def mask_regs(full):
    return {i: int.from_bytes(bytes.fromhex(raw)[:4], "little") for i, raw in full.items()}


def mask_words(ram, address, count):
    if 0 <= address < 0x2000000:
        physical = address
    elif 0x80000000 <= address < 0x82000000:
        physical = address - 0x80000000
    elif 0xA0000000 <= address < 0xA2000000:
        physical = address - 0xA0000000
    else:
        raise ValueError("probe requires direct or cached/uncached EE RAM")
    if address % 4:
        raise ValueError("probe requires aligned word addresses")
    if not 0 <= count <= 26 or physical + count * 4 > 0x2000000:
        raise ValueError("probe read outside bounded EE RAM")
    return list(struct.unpack("<" + "I" * count, bytes(
        gdb.selected_inferior().read_memory(ram + physical, count * 4))))


def mask_identity(record):
    return {"sample_id": record["sample_id"], "context": record["context"],
            "original_sp": record["sp"], "original_ra": record["ra"]}


def mask_emit(item):
    mask_counts[item["kind"]] = mask_counts.get(item["kind"], 0) + 1
    trace.write(json.dumps(item) + "\n")


class MaskReturn(gdb.FinishBreakpoint):
    def __init__(self, frame, key, record):
        super().__init__(frame, internal=True)
        self.key, self.record = key, record

    def stop(self):
        r = self.record
        item = {"kind": "object-mask-return", **mask_identity(r)}
        try:
            ctx = mask_context(r["context"])
            full = mask_full(ctx)
            regs = mask_regs(full)
            item.update(pc=int(ctx["pc"]), registers=regs, all_gpr128_after=full,
                        object_after=mask_words(r["ram"], r["object"], 4),
                        inputs_after=[mask_words(r["ram"], a, 1)[0] for a in r["input_addresses"]],
                        return_matches=int(ctx["pc"]) == r["ra"])
        except (gdb.error, ValueError, TypeError, AttributeError, IndexError) as error:
            item["probe_error"] = str(error)
        mask_pending.pop(self.key, None)
        mask_emit(item)
        return False

    def out_of_scope(self):
        mask_emit({"kind": "object-mask-return-unavailable", **mask_identity(self.record)})


class MaskEntry(gdb.Breakpoint):
    def __init__(self):
        symbol = "entry_113c78_0x113ca8"
        target = gdb.lookup_global_symbol(symbol)
        if target is None:
            raise ValueError("probe symbol absent: " + symbol)
        super().__init__("*" + str(int(target.value().address)), internal=True)
        self.samples = 0

    def stop(self):
        global mask_sample_id
        r = None
        try:
            frame = gdb.newest_frame()
            if frame.architecture().name() != "i386:x86-64":
                raise ValueError("probe requires Linux x86-64 SysV")
            ram, context = int(gdb.parse_and_eval("$rdi")), int(gdb.parse_and_eval("$rsi"))
            ctx = mask_context(context)
            if int(ctx["pc"]) != 0x113C78:
                return False
            mask_sample_id += 1
            r = {"sample_id": mask_sample_id, "ram": ram, "context": context, "sp": None, "ra": None}
            r["sp"] = int.from_bytes(bytes(gdb.selected_inferior().read_memory(int(ctx["r"][29].address), 4)), "little")
            r["ra"] = int.from_bytes(bytes(gdb.selected_inferior().read_memory(int(ctx["r"][31].address), 4)), "little")
            key = (context, r["sp"], r["ra"])
            if key in mask_pending or len(mask_pending) >= 8:
                raise ValueError("duplicate or exhausted pending mask sample")
            mask_pending[key] = r
            full = mask_full(ctx)
            regs = mask_regs(full)
            r["object"] = regs["4"]
            before = mask_words(ram, r["object"], 4)
            offset = (before[1] << 2) & 0xFFFFFFFF
            r["input_addresses"] = [(base + offset) & 0xFFFFFFFF for base in (0x2B6848, 0x2B6850)]
            inputs = [mask_words(ram, a, 1)[0] for a in r["input_addresses"]]
            mask_emit({"kind": "object-mask-entry", **mask_identity(r), "registers": regs,
                       "all_gpr128_before": full, "object_before": before,
                       "index": before[1], "input_addresses": r["input_addresses"], "inputs_before": inputs,
                       "descriptor_words": mask_words(ram, before[0], 26)})
            MaskReturn(frame, key, r)
            self.samples += 1
            if self.samples >= 8:
                self.enabled = False
                mask_emit({"kind": "object-mask-cap", "limit": 8})
        except (gdb.error, ValueError, TypeError, AttributeError, IndexError) as error:
            self.enabled = False
            mask_emit({"kind": "object-mask-error", "error": str(error),
                       **(mask_identity(r) if r is not None else {})})
        return False


try:
    mask_hook = MaskEntry()
    mask_emit({"kind": "object-mask-hook", "pending": bool(mask_hook.pending),
               "enabled": bool(mask_hook.enabled), "limit": 8})
except (gdb.error, ValueError, TypeError, AttributeError) as error:
    mask_emit({"kind": "object-mask-hook", "probe_error": str(error)})


def mask_summary(event):
    trace.write(json.dumps({"kind": "object-mask-stop-coverage", "counts": mask_counts,
                            "pending": [mask_identity(r) for r in mask_pending.values()]}) + "\n")


gdb.events.stop.connect(mask_summary)
