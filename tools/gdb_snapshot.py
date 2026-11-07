"""Sample guest contexts from stopped native threads without modifying them."""
import json
import os
import struct

import gdb

for thread in gdb.selected_inferior().threads():
    thread.switch()
    frame = gdb.newest_frame()
    while frame:
        try:
            ctx = frame.read_var("ctx")
            if not ctx.is_optimized_out and int(ctx):
                ctx = ctx.dereference()
                item = {"thread": thread.num, "guest_pc": int(ctx["pc"]), "host_function": frame.name()}
                for name, index in (("v0", 2), ("a0", 4), ("a1", 5), ("s0", 16), ("gp", 28), ("sp", 29), ("ra", 31)):
                    item[name] = struct.unpack("<I", bytes(gdb.selected_inferior().read_memory(
                        int(ctx["r"][index].address), 4)))[0]
                print("GA2_SAMPLE " + json.dumps(item))
                break
        except (gdb.error, ValueError):
            pass
        frame = frame.older()

wait_probe = os.environ.get("GA2_WAIT_SNAPSHOT_PROBE")
if wait_probe:
    with open(wait_probe) as source:
        exec(compile(source.read(), wait_probe, "exec"))
