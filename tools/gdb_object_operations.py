"""Read-only exact-Zettai object operation entries and their return effects.

Source through GA2_KERNEL_OWNERSHIP_PROBE during --sif-watch. Function entry
arguments use Linux x86-64 SysV, before the native prologue changes registers.
"""
import json
import struct

import gdb


def object_registers(context):
    return {str(index): struct.unpack("<I", bytes(gdb.selected_inferior().read_memory(
        int(context["r"][index].address), 4)))[0] for index in (2, 4, 5, 6, 7, 28, 29, 31)}


def object_preserved_registers(context):
    return {str(index): bytes(gdb.selected_inferior().read_memory(
        int(context["r"][index].address), 16)).hex() for index in (28, 29, 31)}


def object_words(ram, address):
    physical = address & 0x1FFFFFF
    if physical + 64 > 0x2000000:
        raise ValueError("object range outside EE RAM")
    return list(struct.unpack("<16I", bytes(gdb.selected_inferior().read_memory(ram + physical, 64))))


class ObjectOperationReturn(gdb.FinishBreakpoint):
    def __init__(self, frame, ram, context, entry, registers, full_registers, before):
        super().__init__(frame, internal=True)
        self.ram, self.context, self.entry = ram, context, entry
        self.registers, self.before = registers, before
        self.full_registers = full_registers

    def stop(self):
        item = {"kind": "object-operation-return", "entry": self.entry,
                "object": self.registers["4"]}
        try:
            ctx = gdb.Value(self.context).cast(gdb.lookup_type("R5900Context").pointer()).dereference()
            after = object_words(self.ram, self.registers["4"])
            registers = object_registers(ctx)
            full_registers = object_preserved_registers(ctx)
            expected = self.before.copy()
            operation = {0x27DB70: 1, 0x27DB88: 2, 0x27DBA0: 3}[self.entry]
            expected[1] = operation
            expected[3] = self.registers["5"] if operation == 3 else self.registers["6"]
            if operation != 3:
                expected[2] = self.registers["5"]
            if operation == 1:
                expected[4] = self.registers["7"]
            item.update(pc=int(ctx["pc"]), registers=registers, after=after,
                        effects_match=after == expected,
                        return_matches=int(ctx["pc"]) == self.registers["31"],
                        full_registers=full_registers,
                        preserved_sp_gp_ra_128=full_registers == self.full_registers,
                        v0_matches=registers["2"] == operation)
        except (gdb.error, ValueError, IndexError, TypeError, AttributeError) as error:
            item["probe_error"] = str(error)
        trace.write(json.dumps(item) + "\n")
        return False

    def out_of_scope(self):
        trace.write(json.dumps({"kind": "object-operation-return-unavailable",
                                "entry": self.entry}) + "\n")


class ObjectOperationEntry(gdb.Breakpoint):
    def __init__(self, symbol, entry):
        target = gdb.lookup_global_symbol(symbol)
        if target is None:
            raise ValueError("function symbol absent: " + symbol)
        super().__init__("*" + str(int(target.value().address)), internal=True)
        self.entry, self.samples = entry, 0

    def stop(self):
        try:
            frame = gdb.newest_frame()
            if frame.architecture().name() != "i386:x86-64":
                raise ValueError("probe requires Linux x86-64 SysV entry ABI")
            ram = int(gdb.parse_and_eval("$rdi"))
            context = int(gdb.parse_and_eval("$rsi"))
            ctx = gdb.Value(context).cast(gdb.lookup_type("R5900Context").pointer()).dereference()
            registers = object_registers(ctx)
            full_registers = object_preserved_registers(ctx)
            before = object_words(ram, registers["4"])
            ObjectOperationReturn(frame, ram, context, self.entry, registers, full_registers, before)
            trace.write(json.dumps({"kind": "object-operation-entry", "entry": self.entry,
                                    "pc": int(ctx["pc"]), "registers": registers,
                                    "full_registers": full_registers, "before": before}) + "\n")
            self.samples += 1
            if self.samples >= 8:
                self.enabled = False
                trace.write(json.dumps({"kind": "object-operation-cap", "entry": self.entry,
                                        "limit": 8}) + "\n")
        except (gdb.error, ValueError, IndexError, TypeError, AttributeError) as error:
            self.enabled = False
            trace.write(json.dumps({"kind": "object-operation-error", "entry": self.entry,
                                    "error": str(error)}) + "\n")
        return False


object_operation_hooks = []
for symbol, entry in (("entry_27db70_0x27db88", 0x27DB70),
                      ("entry_27db88_0x27dba0", 0x27DB88),
                      ("entry_27dba0_0x27dbc0", 0x27DBA0)):
    item = {"kind": "object-operation-hook", "entry": entry, "symbol": symbol}
    try:
        hook = ObjectOperationEntry(symbol, entry)
        object_operation_hooks.append(hook)
        item.update(pending=bool(hook.pending), enabled=bool(hook.enabled), limit=8)
    except (gdb.error, ValueError, TypeError, AttributeError) as error:
        item["probe_error"] = str(error)
    trace.write(json.dumps(item) + "\n")
