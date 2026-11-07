"""Read-only bounded return-state observations for the standalone IOP probe.

Source after loading ga2_iop_probe in GDB. Locate the return statement in the
pinned source; no breakpoint changes guest memory or readiness flags.
"""
import json
from pathlib import Path
import gdb


class IopCallReturn(gdb.Breakpoint):
    def __init__(self):
        source = (Path(__file__).resolve().parents[1] / "third_party/PS2Recomp/"
                  "ps2xIOP/src/emulator/iop_emulator.cpp")
        matches = [n for n, line in enumerate(source.read_text().splitlines(), 1)
                   if line.strip() == "return cpu.gpr[2];"]
        if len(matches) != 1:
            raise RuntimeError("IOP probe return statement no longer unique")
        super().__init__(str(source) + ":" + str(matches[0]), internal=True)
        self.count = 0

    def stop(self):
        self.count += 1
        try:
            frame = gdb.newest_frame()
            cpu = frame.read_var("cpu")
            pc = int(cpu["pc"])
            item = {
                "ordinal": self.count,
                "pc": pc,
                "stopped": bool(cpu["stopped"]),
                "returned_to_sentinel": pc == 0x1FFFFF04,
            }
            try:
                item["v0"] = int(cpu["gpr"]["_M_elems"][2])
            except gdb.error:
                item["v0_unavailable"] = True
            try:
                state = frame.read_var("this").dereference()
                item["interrupt_mask"] = int(state["memory"]["m_interruptMask"])
                printer = gdb.default_visualizer(state["intrman"]["m_handlers"])
                children = iter(printer.children()) if printer else iter(())
                handlers = []
                for _ in range(16):
                    try:
                        _, irq = next(children)
                        _, handler = next(children)
                    except StopIteration:
                        break
                    handlers.append({"irq": int(irq), "function": int(handler["function"]),
                                     "gp": int(handler["gp"])})
                item["interrupt_handlers"] = handlers
            except (gdb.error, ValueError, KeyError, TypeError) as error:
                item["interrupt_state_unavailable"] = str(error)
            print("GA2_IOP_RETURN " + json.dumps(item))
        except (gdb.error, ValueError) as error:
            print("GA2_IOP_RETURN unavailable=" + str(error))
        if self.count >= 32:
            self.enabled = False
        return False


IopCallReturn()


class CdRead0(gdb.Breakpoint):
    def __init__(self):
        super().__init__("ps2x::iop::detail::IopCdvd::Impl::dispatchImport", internal=True)
        self.count = 0

    def stop(self):
        try:
            frame = gdb.newest_frame()
            cpu = frame.read_var("cpu")
            # Optimized inline copies have unreliable ordinal location data.
            # These PCs are the inventoried original CDVDFSV import stubs when
            # loaded first at 10000. Inspect only that proven diagnostic image.
            if int(cpu["pc"]) not in (0x14B78, 0x14B80):
                return False
            self.count += 1
            regs = cpu["gpr"]["_M_elems"]
            memory = frame.read_var("this").dereference()["memory"]
            ram = int(memory["m_ram"]["_M_impl"]["_M_start"])
            inferior = gdb.selected_inferior()

            def read(address, size):
                offset = address & 0x1FFFFFFF
                if offset + size > 2 * 1024 * 1024:
                    raise ValueError("outside IOP RAM")
                return bytes(inferior.read_memory(ram + offset, size))

            item = {"call": self.count, "pc": int(cpu["pc"]),
                    "import": 62 if int(cpu["pc"]) == 0x14B78 else 63}
            for name, index in (("lsn", 4), ("sectors", 5), ("destination", 6),
                                ("mode_address", 7), ("sp", 29), ("ra", 31)):
                item[name] = int(regs[index])
            item["mode_bytes"] = list(read(item["mode_address"], 4))
            item["stack_arguments"] = [int.from_bytes(read(item["sp"] + n, 4), "little")
                                       for n in (16, 20)]
            print("GA2_CD_READ0 " + json.dumps(item))
        except (gdb.error, ValueError, KeyError) as error:
            print("GA2_CD_READ0 unavailable=" + str(error))
        if self.count >= 16:
            self.enabled = False
        return False


CdRead0()
