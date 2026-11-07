"""Bounded, read-only destination inventory; sourced by gdb_sif_boot.py."""

ownership_counts = {}


class KernelOwnershipProbe(gdb.Breakpoint):
    def __init__(self, symbol, mechanism):
        super().__init__(symbol)
        self.mechanism = mechanism

    def stop(self):
        count = ownership_counts.get(self.mechanism, 0)
        if count >= 256:
            if count == 256:
                trace.write(json.dumps({"kind": "kernel-ownership-truncated",
                    "mechanism": self.mechanism, "limit": 256}) + "\n")
                ownership_counts[self.mechanism] = 257
            return False
        ownership_counts[self.mechanism] = count + 1
        item = {"kind": "kernel-ownership-writer", "mechanism": self.mechanism}
        try:
            frame = gdb.newest_frame()
            if self.mechanism == "syscall-copy" or self.mechanism.startswith("guest-copy-"):
                ctx = frame.read_var("ctx").dereference()
                regs = {index: struct.unpack("<I", bytes(
                    gdb.selected_inferior().read_memory(int(ctx["r"][index].address), 4)))[0]
                    for index in (4, 5, 6, 31)}
                item.update(destination=regs[4], source=regs[5], size=regs[6],
                            guest_pc=int(ctx["pc"]), guest_ra=regs[31])
                if self.mechanism.startswith("guest-copy-"):
                    item["effective_size"] = regs[6] // 4 * 4
            elif self.mechanism == "syscall-mirror-registration":
                raw = int(frame.read_var("syscallNumber")) & 0xFFFFFFFF
                signed = raw if raw < 0x80000000 else raw - 0x100000000
                destination = 0x11F80 + signed * 4
                item.update(syscall_number=raw, handler=int(frame.read_var("handler")),
                            destination=destination, size=4,
                            mirror_valid=0 <= destination <= 0x7FFFC)
            else:
                item.update(destination=int(frame.read_var("address")),
                            size=int(frame.read_var("size")))
            address, size = item["destination"], item["size"]
            if 0 <= address < 0x20000000:
                physical = address & 0x1FFFFFF
            elif 0x80000000 <= address < 0xC0000000:
                physical = address & 0x1FFFFFF
            elif 0x20000000 <= address < 0x40000000:
                physical = address & 0x1FFFFFF
            else:
                physical = None
            item["physical_destination"] = physical
            size = item.get("effective_size", size)
            item["candidate_overlap"] = physical is not None and size > 0 and (
                physical < 0x70000 and physical + size > 0x60000)
        except (gdb.error, ValueError, IndexError) as error:
            item["probe_error"] = str(error)
        trace.write(json.dumps(item) + "\n")
        return False


ownership_hooks = [KernelOwnershipProbe(symbol, mechanism) for symbol, mechanism in (
    ("ps2_syscalls::Copy", "syscall-copy"),
    ("PS2Runtime::setEeSyscallOverride", "syscall-mirror-registration"),
    ("PS2IopHostAdapter::writeGuest", "iop-ee-copy"),
    ("PS2IopHostAdapter::zeroGuest", "iop-ee-zero"),
    ("entry_00278e10_0x278e10", "guest-copy-278e10"),
    ("entry_00279478_0x279478", "guest-copy-279478"),
    ("entry_0027b288_0x27b288", "guest-copy-27b288"))]
for hook in ownership_hooks:
    trace.write(json.dumps({"kind": "kernel-ownership-hook",
        "mechanism": hook.mechanism, "pending": bool(hook.pending),
        "enabled": bool(hook.enabled), "limit": 256}) + "\n")
