"""Read-only original Zettai idle IRQ / semaphore continuation evidence."""


class IdleIrqProbe(gdb.Breakpoint):
    def __init__(self, location, point):
        super().__init__(location)
        self.point = point
        self.samples = 0

    def stop(self):
        self.samples += 1
        item = {"kind": "idle-irq-proof", "point": self.point}
        try:
            frame = gdb.newest_frame()
            ctx = frame.read_var("ctx").dereference()
            item["guest_pc"] = int(ctx["pc"])
            item["registers"] = {str(index): struct.unpack("<I", bytes(
                gdb.selected_inferior().read_memory(int(ctx["r"][index].address), 4)))[0]
                for index in (4, 28, 29, 31)}
            item["counter_words"] = words(0x7FE0B8, 2)
            item["last_tick_words"] = words(0x7FE0C0, 2)
            item["wait_flag_interval_semaphore"] = words(0x2CF770, 4)
        except (gdb.error, ValueError, IndexError) as error:
            item["probe_error"] = str(error)
        trace.write(json.dumps(item) + "\n")
        if self.samples >= 16:
            self.enabled = False
            trace.write(json.dumps({"kind": "idle-irq-probe-cap",
                "point": self.point, "limit": 16}) + "\n")
        return False


idle_irq_hooks = [IdleIrqProbe("ps2_syscalls::iSignalSema", "signal-wrapper")]
for symbol, instruction, point in (
    ("FUN_002377f8_0x2377f8", "ctx->pc = 0x2377f8u;", "irq-entry"),
    ("FUN_002377f8_0x2377f8", "ctx->pc = 0x23781Cu;", "after-counter-store"),
    ("FUN_00237748_0x237748", "ctx->pc = 0x237770u;", "main-after-wait")):
    source_path = gdb.lookup_global_symbol(symbol).symtab.fullname()
    with open(source_path) as source:
        line = [index for index, text in enumerate(source, 1) if instruction in text][-1]
    idle_irq_hooks.append(IdleIrqProbe(f"{source_path}:{line}", point))
for hook in idle_irq_hooks:
    trace.write(json.dumps({"kind": "idle-irq-hook", "point": hook.point,
        "pending": bool(hook.pending), "enabled": bool(hook.enabled)}) + "\n")
