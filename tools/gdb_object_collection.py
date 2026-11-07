"""Read-only original collection dispatch, indexed/search queries and returns.

Baseline builds may lack the four method symbols. The post-delay caller probe
still records the live descriptor and collection; absent methods are explicit.
"""
import json
import struct
import time

import gdb

collection_counts = {}
collection_pending = {}
collection_sequence = 0


def collection_alias_owners():
    table = gdb.lookup_global_symbol("g_ps2RecompiledFunctionTable").value()
    base = int(gdb.lookup_global_symbol("g_ps2RecompiledFunctionTableBase").value())
    for first, end, owner, symbol in (
            (0x145B9C, 0x145BF8, 0x145B98, "caseD_4_0x145b98"),
            (0x17E3B4, 0x17E400, 0x17E3B0, "caseD_2_0x17e3b0")):
        resolved = gdb.lookup_global_symbol(symbol)
        if resolved is None:
            raise ValueError("preserved collection-era owner symbol absent")
        expected = int(resolved.value().address)
        head = int(table[(owner - base) // 4])
        rows = [{"guest_pc": address, "matches_owner": int(table[(address - base) // 4]) == expected}
                for address in range(first, end, 4)]
        collection_emit({"kind": "collection-alias-owner-check", "owner_entry": owner,
                         "head_matches": head == expected, "aliases": rows})


def collection_emit(item):
    kind = item["kind"]
    collection_counts[kind] = collection_counts.get(kind, 0) + 1
    trace.write(json.dumps(item) + "\n")


def collection_context(address):
    return gdb.Value(address).cast(gdb.lookup_type("R5900Context").pointer()).dereference()


def collection_bytes(address, size):
    if 0 <= address < 0x2000000:
        physical = address
    elif 0x80000000 <= address < 0x82000000:
        physical = address - 0x80000000
    elif 0xA0000000 <= address < 0xA2000000:
        physical = address - 0xA0000000
    else:
        raise ValueError("collection probe requires direct or cached/uncached EE RAM")
    if not 0 <= size <= 512 or physical + size > 0x2000000:
        raise ValueError("collection probe requires bounded EE RAM")
    return bytes(gdb.selected_inferior().read_memory(ram + physical, size))


def collection_words(address, count):
    if address % 4:
        raise ValueError("collection word address must be aligned")
    return list(struct.unpack("<" + "I" * count, collection_bytes(address, count * 4)))


def collection_registers(ctx):
    full = {str(i): bytes(gdb.selected_inferior().read_memory(
        int(ctx["r"][i].address), 16)).hex() for i in range(32)}
    regs = {i: int.from_bytes(bytes.fromhex(value)[:4], "little") for i, value in full.items()}
    return regs, full


def collection_data(obj):
    words = collection_words(obj, 16)
    begin, end = words[6], words[7]
    distance = (end - begin) & 0xFFFFFFFF
    item = {"object_words": words, "begin": begin, "end": end, "distance32": distance,
            "descriptor": words[13], "descriptor_words": collection_words(words[13], 28)}
    if distance < 0x80000000 and distance % 8 == 0 and distance <= 512:
        item["entries"] = collection_words(begin, distance // 4)
    else:
        item["entries_unsampled"] = "collection shape exceeds bounded record sampling"
    return item


def collection_identity(item):
    return {key: item[key] for key in ("sample_id", "context", "original_sp", "original_ra", "target")}


class CollectionReturn(gdb.FinishBreakpoint):
    def __init__(self, frame, key, record):
        super().__init__(frame, internal=True)
        self.key, self.record = key, record

    def stop(self):
        r = self.record
        item = {"kind": "collection-return", **collection_identity(r)}
        try:
            ctx = collection_context(r["context"])
            regs, full = collection_registers(ctx)
            item.update(pc=int(ctx["pc"]), registers=regs, all_gpr128_after=full,
                        return_matches=int(ctx["pc"]) == r["original_ra"],
                        collection_after=collection_data(r["object"]),
                        stack_after=collection_bytes(r["original_sp"] - 0x40, 0x50).hex())
        except (gdb.error, ValueError, TypeError, AttributeError, IndexError) as error:
            item["probe_error"] = str(error)
        collection_pending.pop(self.key, None)
        collection_emit(item)
        return False

    def out_of_scope(self):
        collection_pending.pop(self.key, None)
        collection_emit({"kind": "collection-return-unavailable", **collection_identity(self.record)})


class CollectionEntry(gdb.Breakpoint):
    def __init__(self, symbol, target):
        resolved = gdb.lookup_global_symbol(symbol)
        if resolved is None:
            raise LookupError(symbol)
        super().__init__("*" + str(int(resolved.value().address)), internal=True)
        self.target, self.samples = target, 0

    def stop(self):
        global collection_sequence
        r = None
        key = None
        pending_inserted = False
        try:
            frame = gdb.newest_frame()
            if frame.architecture().name() != "i386:x86-64":
                raise ValueError("collection method entry requires Linux x86-64 SysV")
            address = int(gdb.parse_and_eval("$rsi"))
            ctx = collection_context(address)
            if int(ctx["pc"]) != self.target:
                return False
            regs, full = collection_registers(ctx)
            collection_sequence += 1
            self.samples += 1
            r = {"sample_id": collection_sequence, "context": address, "target": self.target,
                 "original_sp": regs["29"], "original_ra": regs["31"], "object": regs["4"],
                 "entry_pc": int(ctx["pc"]), "registers": regs, "all_gpr128_before": full,
                 "monotonic_time": time.monotonic(), "collection_before": collection_data(regs["4"]),
                 "stack_before": collection_bytes(regs["29"] - 0x40, 0x50).hex()}
            key = (address, regs["29"], regs["31"], self.target)
            if key in collection_pending or len(collection_pending) >= 16:
                raise ValueError("duplicate or exhausted collection return sample")
            collection_pending[key] = r
            pending_inserted = True
            collection_emit({"kind": "collection-entry", **r})
            CollectionReturn(frame, key, r)
            if self.samples >= 8:
                self.enabled = False
                collection_emit({"kind": "collection-method-cap", "target": self.target, "limit": 8})
        except (gdb.error, ValueError, TypeError, AttributeError, IndexError) as error:
            if pending_inserted:
                collection_pending.pop(key, None)
            self.enabled = False
            collection_emit({"kind": "collection-probe-error", "target": self.target,
                             "error": str(error), **(collection_identity(r) if r is not None else {})})
        return False


class CollectionDispatch(gdb.Breakpoint):
    def __init__(self):
        caller = gdb.lookup_global_symbol("FUN_001532e0_0x1532e0")
        if caller is None:
            raise ValueError("original collection caller symbol absent")
        path = caller.symtab.fullname()
        with open(path) as source:
            line = next(i for i, text in enumerate(source, 1)
                        if "dispatchGuestBranch" in text and "0x15335Cu" in text)
        super().__init__(f"{path}:{line}", internal=True)
        self.samples = 0

    def stop(self):
        try:
            address = int(gdb.newest_frame().read_var("ctx"))
            ctx = collection_context(address)
            if int(ctx["pc"]) != 0x1037B8:
                return False
            regs, full = collection_registers(ctx)
            collection_emit({"kind": "collection-dispatch", "context": address,
                             "pc": int(ctx["pc"]), "registers": regs, "all_gpr128": full,
                             "collection": collection_data(regs["4"]),
                             "pair_address": regs["3"], "target": regs["2"],
                             "root_pointer_word": collection_words(0x2B6640, 1)[0],
                             "coverage": "source-line after original JALR delay, before dispatch"})
            self.samples += 1
            if self.samples >= 4:
                self.enabled = False
                collection_emit({"kind": "collection-dispatch-cap", "limit": 4})
        except (gdb.error, ValueError, TypeError, AttributeError, IndexError) as error:
            self.enabled = False
            collection_emit({"kind": "collection-dispatch-error", "error": str(error)})
        return False


collection_hooks = []
try:
    collection_alias_owners()
except (gdb.error, ValueError, TypeError, AttributeError, IndexError) as error:
    collection_emit({"kind": "collection-alias-owner-error", "error": str(error)})
try:
    collection_dispatch = CollectionDispatch()
    collection_emit({"kind": "collection-dispatch-hook", "pending": bool(collection_dispatch.pending)})
except (gdb.error, ValueError, TypeError, AttributeError, OSError, StopIteration) as error:
    collection_emit({"kind": "collection-dispatch-hook", "probe_error": str(error)})

for symbol, target in (
        ("entry_1034e0_0x103550", 0x1034E0),
        ("entry_1037b8_0x1037f8", 0x1037B8),
        ("entry_1037f8_0x103838", 0x1037F8),
        ("entry_103870_0x103888", 0x103870)):
    item = {"kind": "collection-method-hook", "symbol": symbol, "target": target}
    try:
        hook = CollectionEntry(symbol, target)
        collection_hooks.append(hook)
        item["pending"] = bool(hook.pending)
    except LookupError:
        item["compiled_absent"] = True
    except (gdb.error, ValueError, TypeError, AttributeError) as error:
        item["probe_error"] = str(error)
    collection_emit(item)


def collection_summary(event):
    collection_emit({"kind": "collection-stop-coverage", "counts": dict(collection_counts),
                     "pending": [collection_identity(r) for r in collection_pending.values()]})


gdb.events.stop.connect(collection_summary)
