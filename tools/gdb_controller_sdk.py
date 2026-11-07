"""Read-only SDK configuration, real backend packets and original mask queries.

Samples configuration and neutral/pressed/released input separately so early
neutral frames do not exhaust the evidence budget before a physical test key.
"""
import json
import struct
import time

import gdb

controller_pending = {}
controller_counts = {}
controller_quotas = {}
controller_sequence = 0
controller_pressed = False
controller_saved = (*range(16, 24), 28, 29, 30, 31)


def controller_emit(item):
    controller_counts[item["kind"]] = controller_counts.get(item["kind"], 0) + 1
    trace.write(json.dumps(item) + "\n")


def controller_bytes(address, size):
    if not 0 <= size <= 128 or not 0 <= address <= 0x2000000 - size:
        raise ValueError("controller probe requires bounded direct EE RAM")
    return bytes(gdb.selected_inferior().read_memory(ram + address, size))


def controller_words(address, count):
    if address % 4:
        raise ValueError("controller probe requires aligned words")
    return list(struct.unpack("<" + "I" * count, controller_bytes(address, count * 4)))


def controller_context(address):
    return gdb.Value(address).cast(gdb.lookup_type("R5900Context").pointer()).dereference()


def controller_registers(ctx):
    full = {str(i): bytes(gdb.selected_inferior().read_memory(int(ctx["r"][i].address), 16)).hex()
            for i in (*controller_saved, 2, 4, 5, 6, 7)}
    return ({i: int.from_bytes(bytes.fromhex(value)[:4], "little") for i, value in full.items()}, full)


def controller_identity(r):
    return {key: r[key] for key in ("sample_id", "context", "original_sp", "original_ra", "mechanism")}


def controller_take(category, limit):
    count = controller_quotas.get(category, 0)
    if count >= limit:
        return False
    controller_quotas[category] = count + 1
    return True


def controller_backend(port, slot):
    ports = gdb.lookup_static_symbol("ps2_stubs::(anonymous namespace)::g_padPorts")
    override = gdb.lookup_static_symbol("ps2_stubs::(anonymous namespace)::g_padOverrideEnabled")
    if ports is None or override is None:
        raise ValueError("host controller state symbols unavailable")
    if port not in (0, 1) or slot != 0:
        raise ValueError("unsupported host controller port/slot")
    state = ports.value()[port]
    return {"override_enabled": bool(override.value()),
            **{key: bool(state[key]) for key in ("open", "analogMode", "pressureEnabled", "lastUsedBackend", "lastUsedOverride", "lastReadOk")},
            **{key: int(state[key]) for key in ("reqState", "readCount", "lastReadDataAddr")}}


class ControllerFinish(gdb.FinishBreakpoint):
    def __init__(self, frame, key, record):
        super().__init__(frame, internal=True)
        self.key, self.record = key, record

    def stop(self):
        global controller_pressed
        r = self.record
        item = {"kind": "controller-return", **controller_identity(r)}
        publish = True
        try:
            ctx = controller_context(r["context"])
            regs, full = controller_registers(ctx)
            item.update(pc=int(ctx["pc"]), registers=regs, full_after=full,
                        return_matches=int(ctx["pc"]) == r["original_ra"])
            if r["mechanism"] == "read":
                packet = controller_bytes(r["registers"]["6"], 32)
                pressed = packet[2:4] != b"\xff\xff"
                category = "pressed" if pressed else "released" if controller_pressed else "neutral"
                if pressed:
                    controller_pressed = True
                item.update(packet=packet.hex(), category=category,
                            window_after=controller_bytes(r["registers"]["6"] - 4, 40).hex(),
                            backend=controller_backend(r["registers"]["4"], r["registers"]["5"]))
                publish = controller_take("read-" + category, 8 if pressed else 2)
            elif r["mechanism"] == "mask":
                item.update(input_words_after=[controller_words(a, 1)[0] for a in r["input_addresses"]],
                            object_after=controller_words(r["registers"]["4"], 4))
        except (gdb.error, ValueError, TypeError, AttributeError, IndexError) as error:
            item["probe_error"] = str(error)
            publish = True
            for hook in controller_hooks:
                if hook.mechanism == r["mechanism"]:
                    hook.enabled = False
        controller_pending.pop(self.key, None)
        if publish:
            controller_emit({"kind": "controller-entry", **r})
            controller_emit(item)
        return False

    def out_of_scope(self):
        controller_emit({"kind": "controller-return-unavailable", **controller_identity(self.record)})


class ControllerHook(gdb.Breakpoint):
    def __init__(self, symbol, mechanism):
        target = gdb.lookup_global_symbol(symbol)
        if target is None:
            raise ValueError("controller symbol absent: " + symbol)
        super().__init__("*" + str(int(target.value().address)), internal=True)
        self.mechanism, self.candidates = mechanism, 0

    def stop(self):
        global controller_sequence
        r = None
        try:
            frame = gdb.newest_frame()
            if frame.architecture().name() != "i386:x86-64":
                raise ValueError("controller probe requires Linux x86-64 SysV")
            context = int(gdb.parse_and_eval("$rsi"))
            ctx = controller_context(context)
            regs, full = controller_registers(ctx)
            if self.mechanism == "mask" and int(ctx["pc"]) != 0x113C78:
                return False
            if self.mechanism == "align-caller" and int(ctx["pc"]) != 0x242B50:
                return False
            self.candidates += 1
            if self.candidates >= 8192:
                self.enabled = False
                controller_emit({"kind": "controller-candidate-cap", "mechanism": self.mechanism, "limit": 8192})
            if self.mechanism == "read" and (regs["4"] != 0 or regs["5"] != 0):
                return False
            if self.mechanism not in ("read", "mask") and not controller_take(self.mechanism, 16):
                return False
            controller_sequence += 1
            r = {"sample_id": controller_sequence, "context": context, "original_sp": regs["29"],
                 "original_ra": regs["31"], "mechanism": self.mechanism,
                 "registers": regs, "full_before": full, "entry_pc": int(ctx["pc"]),
                 "monotonic_time": time.monotonic()}
            if self.mechanism == "read":
                r["window_before"] = controller_bytes(regs["6"] - 4, 40).hex()
            if self.mechanism == "mask":
                r["object_before"] = controller_words(regs["4"], 4)
                offset = (r["object_before"][1] << 2) & 0xFFFFFFFF
                r["input_addresses"] = [(a + offset) & 0xFFFFFFFF for a in (0x2B6848, 0x2B6850)]
                r["input_words"] = [controller_words(a, 1)[0] for a in r["input_addresses"]]
                a, b = r["input_words"]
                category = "nonzero" if (a & ~b & regs["5"]) else "zero-after-press" if controller_pressed else "neutral"
                if not controller_take("mask-" + category, 8 if category == "nonzero" else 2):
                    return False
                r["category"] = category
            key = (context, regs["29"], regs["31"], self.mechanism)
            if key in controller_pending or len(controller_pending) >= 32:
                raise ValueError("duplicate or exhausted pending controller sample")
            controller_pending[key] = r
            ControllerFinish(frame, key, r)
        except (gdb.error, ValueError, TypeError, AttributeError, IndexError) as error:
            self.enabled = False
            controller_emit({"kind": "controller-probe-error", "mechanism": self.mechanism,
                             "error": str(error), **(controller_identity(r) if r is not None else {})})
        return False


controller_hooks = []
for symbol, mechanism in (
        ("ps2_stubs::scePadInfoMode", "info-mode"),
        ("ps2_stubs::scePadSetMainMode", "set-mode"),
        ("ps2_stubs::scePadGetState", "state"),
        ("ps2_stubs::scePadGetReqState", "request-state"),
        ("FUN_00242b50_0x242b50", "align-caller"),
        ("FUN_0024b050_0x24b050", "align-wrapper"),
        ("ps2_stubs::scePadSetActAlign", "act-align"),
        ("ps2_stubs::scePadSetActDirect", "act-direct"),
        ("ps2_stubs::scePadRead", "read"),
        ("entry_113c78_0x113ca8", "mask"),
        ("FUN_0024a748_0x24a748", "dma-escape")):
    item = {"kind": "controller-hook", "symbol": symbol, "mechanism": mechanism}
    try:
        hook = ControllerHook(symbol, mechanism)
        controller_hooks.append(hook)
        item.update(pending=bool(hook.pending), enabled=bool(hook.enabled))
    except (gdb.error, ValueError, TypeError, AttributeError) as error:
        item["probe_error"] = str(error)
    controller_emit(item)


def controller_summary(event):
    controller_emit({"kind": "controller-stop-coverage", "counts": dict(controller_counts),
                     "quotas": dict(controller_quotas),
                     "candidates": {h.mechanism: h.candidates for h in controller_hooks},
                     "pending": [controller_identity(r) for r in controller_pending.values()],
                     "input_words": controller_words(0x2B6848, 6),
                     "game_states": controller_words(0x2CF970, 8)})


gdb.events.stop.connect(controller_summary)
