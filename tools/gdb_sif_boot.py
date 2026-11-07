"""Read-only Zettai SIF boot probes, sourced after stopping at Runtime::run."""
import json
import os
import signal
import struct
import threading

import gdb

runtime = gdb.newest_frame().read_var("this").dereference()
ram = int(runtime["m_memory"]["m_rdram"])
trace = open(os.environ["GA2_SIF_TRACE"], "w", buffering=1)
sample_seconds = int(os.environ.get("GA2_SIF_SAMPLE_SECONDS", "10"))
if not 1 <= sample_seconds <= 45:
    raise ValueError("SIF diagnostic duration must be between one and 45 seconds")
events = 0
frame_probe_counts = {}


def words(address, count):
    physical = address & 0x1FFFFFF
    if physical + count * 4 > 0x2000000:
        return []
    data = bytes(gdb.selected_inferior().read_memory(ram + physical, count * 4))
    return list(struct.unpack("<" + "I" * count, data))


class SifCall(gdb.Breakpoint):
    def stop(self):
        global events
        if events >= 96:
            return False
        try:
            frame = gdb.newest_frame()
            name = frame.name()
            if "223a38" in name or "20a398" in name:
                frame_probe_counts[name] = frame_probe_counts.get(name, 0) + 1
                if frame_probe_counts[name] > 4:
                    return False
            ctx = frame.read_var("ctx").dereference()
            regs = [struct.unpack("<I", bytes(gdb.selected_inferior().read_memory(
                int(ctx["r"][i].address), 4)))[0] for i in (4, 5, 6, 7, 29, 31, 28)]
            item = {"host_function": frame.name(), "guest_pc": int(ctx["pc"]),
                    "a0": regs[0], "a1": regs[1], "a2": regs[2], "a3": regs[3],
                    "sp": regs[4], "ra": regs[5], "gp": regs[6], "rpc_ready": words(0x830B80, 1)[0],
                    "command_data": words(0x8309D8, 8), "system_handlers": words(0x830A00, 12)}
            item["startup_saved_ra"] = words(0x1FFFFD8, 2)
            guest_runtime = frame.read_var("runtime").dereference()
            item["guest_heap"] = {
                "base": int(guest_runtime["m_guestHeapBase"]),
                "end": int(guest_runtime["m_guestHeapEnd"]),
                "limit": int(guest_runtime["m_guestHeapLimit"])}
            if any(name_part in name for name_part in ("22f8e8", "23fd00", "241bd0")):
                item["later_initializer_packets"] = {
                    hex(address): words(address, 4) for address in
                    (0x7FE170, 0x7FE180, 0x7FE1D0, 0x7FE1E0, 0x7FE1F0)}
            item["rpc_register_pack"] = [struct.unpack("<I", bytes(gdb.selected_inferior().read_memory(
                int(ctx["r"][i].address), 4)))[0] for i in (8, 9, 10, 11)]
            item["rpc_stack_pack"] = words(regs[4] + 0x10, 5)
            if "Deci2Call" in frame.name() or "270aa8" in frame.name():
                item["deci2_state"] = words(0x8305D0, 7)
                if "Deci2Call" in frame.name():
                    item["deci2_args"] = words(regs[1], 4)
            if "SetDma" in frame.name() and 0 < regs[1] <= 32:
                item["descriptors"] = []
                for index in range(regs[1]):
                    descriptor = words(regs[0] + index * 16, 4)
                    item["descriptors"].append({"words": descriptor,
                        "packet": words(descriptor[0], min((descriptor[2] + 3) // 4, 28))})
            events += 1
            trace.write(json.dumps(item) + "\n")
        except (gdb.error, ValueError, IndexError):
            pass
        return False


class SavedReturnWrite(gdb.Breakpoint):
    """Observe the reached startup stack slot; never change guest RAM."""
    def __init__(self):
        super().__init__(f"*(unsigned long long *)({ram + 0x1FFFFD8})",
                         gdb.BP_WATCHPOINT, wp_class=gdb.WP_WRITE, internal=True)
        self.writes = 0

    def stop(self):
        self.writes += 1
        item = {"kind": "startup-saved-ra-write",
            "saved_ra": words(0x1FFFFD8, 2),
            "host_stack": gdb.execute("bt 8", to_string=True)}
        frame = gdb.newest_frame()
        while frame:
            if frame.name() == "EeScheduler::run":
                try:
                    scheduler = frame.read_var("this").dereference()
                    item["owner_thread"] = int(scheduler["m_currentThreadId"])
                    owner = frame.read_var("running").dereference()
                    base = owner["context"]
                    item["parent_pc"] = int(base["pc"])
                    item["parent_sp"] = struct.unpack("<I", bytes(
                        gdb.selected_inferior().read_memory(int(base["r"][29].address), 4)))[0]
                    vector = owner["invocations"]["_M_impl"]
                    if vector["_M_start"] != vector["_M_finish"]:
                        invocation = (vector["_M_finish"] - 1).dereference()
                        item["invocation_kind"] = int(invocation["kind"])
                        item["invocation_tag"] = int(invocation["tag"])
                        item["invocation_depth"] = int(vector["_M_finish"] - vector["_M_start"])
                        active = invocation["context"]
                        item["invocation_sp"] = struct.unpack("<I", bytes(
                            gdb.selected_inferior().read_memory(int(active["r"][29].address), 4)))[0]
                except (gdb.error, ValueError):
                    item["owner_details"] = "optimized-out-or-unavailable"
                break
            frame = frame.older()
        trace.write(json.dumps(item) + "\n")
        if self.writes >= 32:
            self.enabled = False
        return False


class IsolatedStackOverlap(gdb.Breakpoint):
    """Capture the idle callback and last published waits at ownership rejection."""
    def stop(self):
        self.enabled = False
        item = {"kind": "isolated-stack-overlap",
                "host_stack": gdb.execute("bt 12", to_string=True)}
        try:
            item["sdk_kernel_table_pointer"] = words(0x2D25E0, 1)
            scheduler = gdb.newest_frame().read_var("this").dereference()
            item["owner_thread"] = int(scheduler["m_currentThreadId"])
            pending = scheduler["m_pendingInvocations"]["_M_impl"]
            first = pending["_M_start"]["_M_cur"]
            if first != pending["_M_finish"]["_M_cur"]:
                invocation = first.dereference()
                ctx = invocation["context"]
                item["pending"] = {"kind": int(invocation["kind"]),
                    "tag": int(invocation["tag"]), "pc": int(ctx["pc"]),
                    "registers": {str(index): struct.unpack("<I", bytes(
                        gdb.selected_inferior().read_memory(int(ctx["r"][index].address), 4)))[0]
                        for index in (4, 5, 6, 7, 28, 29, 31)}}
            vector = scheduler["m_snapshot"]["threads"]["_M_impl"]
            begin, end = vector["_M_start"], vector["_M_finish"]
            count = int(end - begin)
            if not 0 <= count <= 128:
                raise ValueError("unexpected snapshot size")
            item["published_threads"] = [{field: int((begin + index).dereference()[field])
                for field in ("id", "pc", "sp", "ra", "stack", "stackSize", "status",
                              "waitReason", "waitId", "invocationDepth")}
                for index in range(count)]
        except (gdb.error, ValueError, IndexError) as error:
            item["probe_error"] = str(error)
        trace.write(json.dumps(item) + "\n")
        return False


gdb.execute("disable 1")
probe_only = os.environ.get("GA2_SIF_PROBE_ONLY") == "1"
hooks = []
if not probe_only:
    saved_return_watch = SavedReturnWrite()
    stack_source = gdb.lookup_global_symbol("EeScheduler::validateIsolatedInvocationStack").symtab.fullname()
    with open(stack_source) as source:
        overlap_line = next(index for index, line in enumerate(source, 1)
                            if 'throw std::runtime_error("EE callback stack overlaps declared thread stack")' in line)
    overlap_probe = IsolatedStackOverlap(f"{stack_source}:{overlap_line}")
    hooks = [SifCall("ps2_stubs::" + name) for name in
             ("sceSifSetReg", "sceSifSetDma", "sceSifSendCmd", "sceSifInitRpc")]
    hooks += [SifCall("ps2_syscalls::" + name) for name in
              ("SifInitRpc", "SifBindRpc", "SifCallRpc")]
    hooks += [SifCall("ps2_syscalls::Deci2Call"), SifCall("FUN_00270aa8_0x270aa8")]
    hooks += [SifCall("FUN_00223a38_0x223a38"), SifCall("FUN_0020a398_0x20a398")]
    hooks += [SifCall(name) for name in (
        "entry_222608_0x222640", "entry_222640_0x222660",
        "entry_2226b8_0x2226c0", "entry_2226c0_0x2226c8",
        "entry_222f98_0x222fd0", "entry_222fd0_0x222ff0",
        "entry_223048_0x223050", "entry_223050_0x223058")]
    hooks += [SifCall(name) for name in (
        "entry_23fd00_0x23fdb8", "entry_240328_0x240358",
        "entry_240698_0x2406c0", "entry_241bd0_0x241c08",
        "entry_241cb0_0x241ce0", "entry_00241df0_0x241df0",
        "FUN_0022f8e8_0x22f8e8")]
    for symbol in ("FUN_00112b28_0x112b28", "entry_00112d70_0x112d70"):
        startup_source = gdb.lookup_global_symbol(symbol).symtab.fullname()
        with open(startup_source) as source:
            restored_ra_line = next(index for index, line in enumerate(source, 1)
                                    if "ctx->pc = 0x112DB8u;" in line)
        hooks.append(SifCall(f"{startup_source}:{restored_ra_line}"))
trace.write(json.dumps({"kind": "sif-probe-mode", "base_hooks_enabled": not probe_only,
                        "base_hook_count": len(hooks), "saved_return_watch": not probe_only,
                        "sample_seconds": sample_seconds}) + "\n")
ownership_probe = os.environ.get("GA2_KERNEL_OWNERSHIP_PROBE")
idle_irq_probe = os.environ.get("GA2_IDLE_IRQ_PROBE")
if idle_irq_probe:
    with open(idle_irq_probe) as source:
        exec(compile(source.read(), idle_irq_probe, "exec"))
if ownership_probe:
    with open(ownership_probe) as source:
        exec(compile(source.read(), ownership_probe, "exec"))
threading.Timer(sample_seconds, lambda: os.kill(os.getpid(), signal.SIGINT)).start()
