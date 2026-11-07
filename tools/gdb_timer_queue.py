"""Read-only debugger probes for Zettai's exact-build timer queue."""
import json
import os
import struct

import gdb

HEAD = 0x2D3588
RAM_SIZE = 32 * 1024 * 1024
trace = open(os.environ["GA2_TIMER_TRACE"], "w", buffering=1)
ram = None
events = 0


def read32(address):
    return struct.unpack("<I", bytes(gdb.selected_inferior().read_memory(address, 4)))[0]


def context():
    frame = gdb.newest_frame()
    while frame:
        try:
            value = frame.read_var("ctx")
            if not value.is_optimized_out and int(value):
                value = value.dereference()
                result = {"guest_pc": int(value["pc"])}
                for name, index in (("v0", 2), ("v1", 3), ("a0", 4), ("a1", 5), ("a2", 6),
                                    ("a3", 7), ("s0", 16), ("s1", 17), ("s2", 18), ("gp", 28),
                                    ("sp", 29), ("ra", 31)):
                    result[name] = read32(int(value["r"][index].address))
                return result
        except (gdb.error, ValueError):
            pass
        frame = frame.older()
    return {"guest_context": "not available in stopped frames"}


def emit(kind):
    global events
    events += 1
    head = read32(ram + HEAD)
    record = None
    if 0 < head <= RAM_SIZE - 64:
        record = bytes(gdb.selected_inferior().read_memory(ram + head, 64)).hex()
    item = {"event": kind, "sequence": events, "head": head, "record_hex": record,
            "current_break": read32(ram + 0x2D2520),
            "heap_fill_base": read32(ram + 0x2B6440), **context()}
    frame = gdb.newest_frame()
    while frame:
        try:
            runtime = frame.read_var("runtime")
            if not runtime.is_optimized_out and int(runtime):
                item["runtime_heap_limit"] = int(runtime.dereference()["m_guestHeapLimit"])
                break
        except (gdb.error, ValueError):
            pass
        frame = frame.older()
    frames = []
    frame = gdb.newest_frame()
    for _ in range(5):
        if not frame:
            break
        frames.append(frame.name())
        frame = frame.older()
    item["host_frames"] = frames
    trace.write(json.dumps(item) + "\n")
    print("GA2_QUEUE " + json.dumps(item))
    return head == 0xCBCBCBCB or events >= 32


class QueueWrite(gdb.Breakpoint):
    def __init__(self):
        super().__init__(f"*(unsigned int*){ram + HEAD}", gdb.BP_WATCHPOINT, wp_class=gdb.WP_WRITE)

    def stop(self):
        return emit("head-write")


class TimerConsumer(gdb.Breakpoint):
    def stop(self):
        return emit("consumer-entry") if ram is not None and context().get("guest_pc") == 0x279BD8 else False


class HeapInit(gdb.Breakpoint):
    def stop(self):
        return emit("heap-init") if context().get("guest_pc") in (0x101018, 0x101094) else False


class HeapSyscall(gdb.Breakpoint):
    def stop(self):
        return emit("heap-syscall-entry")


class HeapGrowth(gdb.Breakpoint):
    def stop(self):
        return emit("sbrk-entry") if context().get("guest_pc") == 0x26FE20 else False


class HeapCeiling(gdb.Breakpoint):
    def stop(self):
        return emit("sbrk-ceiling-comparison")


class StackSetup(gdb.Breakpoint):
    def stop(self):
        return emit("stack-setup-entry")


# Source after GDB stops at PS2Runtime::run, outside a stop callback.
value = gdb.newest_frame().read_var("this").dereference()
ram = int(value["m_memory"]["m_rdram"])
print("GA2_QUEUE loaded_head=" + hex(read32(ram + HEAD)))
gdb.execute("disable 1")
queue_watchpoint = QueueWrite()
timer_consumer = TimerConsumer("FUN_00279bd8_0x279bd8")
heap_init = HeapInit("FUN_00101018_0x101018")
heap_syscall = HeapSyscall("ps2_syscalls::SetupHeap")
heap_growth = HeapGrowth("FUN_0026fe20_0x26fe20")
heap_ceiling = HeapCeiling("FUN_0026fe20_0x26fe20.cpp:137")
stack_setup = StackSetup("ps2_syscalls::SetupThread")
heap_end = HeapCeiling("ps2_syscalls::EndOfHeap")
