"""Bounded read-only EE/IOP wait snapshot while the native inferior is stopped."""
import json
import struct

import gdb


def bounded_map(value, limit=128):
    printer = gdb.default_visualizer(value)
    if printer is None:
        raise ValueError("map pretty-printer unavailable")
    children = iter(printer.children())
    result = []
    for _ in range(limit):
        try:
            _, key = next(children)
        except StopIteration:
            return result, False
        _, item = next(children)
        result.append((int(key), item))
    return result, next(children, None) is not None


def register(ctx, index):
    return struct.unpack("<I", bytes(gdb.selected_inferior().read_memory(
        int(ctx["r"][index].address), 4)))[0]


found = set()
for thread in gdb.selected_inferior().threads():
    thread.switch()
    frame = gdb.newest_frame()
    while frame:
        name = frame.name() or ""
        mechanism = None
        if name.startswith("EeScheduler::") and "ee" not in found:
            mechanism = "ee"
        elif "IopEmulator::Impl::" in name and "iop" not in found:
            mechanism = "iop"
        if mechanism:
            record = {"kind": "live-stopped-waits", "mechanism": mechanism,
                      "host_thread": thread.num, "host_function": name}
            try:
                state = frame.read_var("this").dereference()
                if mechanism == "ee":
                    record["current_thread"] = int(state["m_currentThreadId"])
                    record["ee_cycle"] = int(state["m_eeCycle"])
                    record["vsync_tick"] = int(state["m_vsyncTick"])
                    rows, record["threads_truncated"] = bounded_map(state["m_threads"])
                    record["threads"] = []
                    for key, owner in rows:
                        vector = owner["invocations"]["_M_impl"]
                        count = int(vector["_M_finish"] - vector["_M_start"])
                        if not 0 <= count <= 128:
                            raise ValueError("unexpected invocation depth")
                        ctx = owner["context"] if count == 0 else (vector["_M_finish"] - 1).dereference()["context"]
                        item = {field: int(owner[field]) for field in
                                ("id", "stack", "stackSize", "currentPriority", "status")}
                        item.update(pc=int(ctx["pc"]), sp=register(ctx, 29), ra=register(ctx, 31),
                                    gp=register(ctx, 28), invocation_depth=count,
                                    wait_reason=int(owner["wait"]["reason"]))
                        item["wait_payload"] = str(owner["wait"]["payload"])
                        item["registers"] = {str(index): register(ctx, index) for index in (4, 5, 16, 17, 18)}
                        if key == 1:
                            ram = int(state["m_rdram"])
                            sp = item["sp"] & 0x1FFFFFF
                            if sp + 256 <= 0x2000000:
                                item["stack_words"] = list(struct.unpack("<64I", bytes(
                                    gdb.selected_inferior().read_memory(ram + sp, 256))))
                        record["threads"].append(item)
                    for field in ("m_semaphores", "m_eventFlags"):
                        rows, truncated = bounded_map(state[field])
                        record[field] = [{"key": key, "value": str(value)} for key, value in rows]
                        record[field + "_truncated"] = truncated
                else:
                    record["reset_required"] = bool(state["resetRequired"])
                    record["total_cycles"] = int(state["totalCycles"])
                    current = state["kernel"]["m_currentThread"]
                    record["current_thread"] = int(current.dereference()["id"]) if int(current) else None
                    rows, record["threads_truncated"] = bounded_map(state["kernel"]["m_threads"])
                    record["threads"] = []
                    for key, owner in rows:
                        item = {field: int(owner[field]) for field in
                                ("id", "state", "entry", "stackBase", "stackSize", "priority",
                                 "wakeCycle", "waitId", "waitBits", "waitMode", "wakeupCount")}
                        cpu = owner["cpu"]
                        item["pc"] = int(cpu["pc"])
                        regs = cpu["gpr"]["_M_elems"]
                        item["registers"] = {str(index): int(regs[index]) for index in (2, 4, 5, 6, 7, 28, 29, 31)}
                        record["threads"].append(item)
                    for field in ("m_semaphores", "m_eventFlags"):
                        rows, truncated = bounded_map(state["kernel"][field])
                        record[field] = [{"key": key, "value": str(value)} for key, value in rows]
                        record[field + "_truncated"] = truncated
                    rows, record["servers_truncated"] = bounded_map(state["rpc"]["m_servers"])
                    record["servers"] = [{"sid": key, **{field: int(value[field]) for field in
                        ("serverData", "function", "gp", "buffer", "queue")}} for key, value in rows]
                    rows, record["modules_truncated"] = bounded_map(state["modules"])
                    record["modules"] = [{"id": key, "name": str(value["name"]),
                        **{field: int(value[field]) for field in ("base", "size", "entry", "gp")}}
                        for key, value in rows]
                found.add(mechanism)
            except (gdb.error, ValueError, IndexError, StopIteration) as error:
                record["probe_error"] = str(error)
            print("GA2_WAITS " + json.dumps(record))
        frame = frame.older()
print("GA2_WAITS " + json.dumps({"kind": "snapshot-coverage", "found": sorted(found)}))

record = {"kind": "rpc-history"}
try:
    history = gdb.parse_and_eval("g_sif_rpc_debug_history")
    next_sequence = int(gdb.parse_and_eval("g_sif_rpc_debug_next_seq"))
    capacity = history.type.sizeof // history[0].type.sizeof
    record["next_sequence"] = next_sequence
    record["events"] = []
    for index in range(max(0, next_sequence - min(capacity, 32)), next_sequence):
        event = history[index % capacity]
        item = {field: int(event[field]) for field in
            ("seq", "pc", "ra", "threadId", "sid", "rpcNum", "clientPtr", "sendBuf",
             "sendSize", "recvBuf", "recvSize", "mode", "endFunc", "endParam", "semaId", "flags", "result")}
        item["op"] = event["op"].string(length=64).split("\0", 1)[0]
        record["events"].append(item)
except (gdb.error, ValueError, IndexError) as error:
    record["probe_error"] = str(error)
print("GA2_WAITS " + json.dumps(record))
