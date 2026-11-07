"""Read-only observations of original IOMAN devctl calls and invalid event waits."""


class IomanDevctlReturn(gdb.FinishBreakpoint):
    def __init__(self, frame, cpu_address, iop_ram, output, size):
        super().__init__(frame, internal=True)
        self.cpu_address, self.iop_ram = cpu_address, iop_ram
        self.output, self.size = output, size

    def stop(self):
        item = {"kind": "ioman-devctl-return", "output": self.output, "size": self.size}
        try:
            cpu = gdb.Value(self.cpu_address).cast(
                gdb.lookup_type("ps2x::iop::detail::IopCpuState").pointer()).dereference()
            item.update(v0=int(cpu["gpr"]["_M_elems"][2]), pc=int(cpu["pc"]),
                        stopped=bool(cpu["stopped"]), yielded=bool(cpu["yielded"]),
                        execution_fault=bool(cpu["executionFault"]))
            physical = self.output & 0x1FFFFFFF
            size = min(self.size, 16)
            if physical + size <= 0x200000:
                item["output_bytes"] = list(bytes(gdb.selected_inferior().read_memory(self.iop_ram + physical, size)))
        except (gdb.error, ValueError, IndexError, AttributeError, TypeError) as error:
            item["probe_error"] = str(error)
        trace.write(json.dumps(item) + "\n")
        return False

    def out_of_scope(self):
        trace.write(json.dumps({"kind": "ioman-devctl-return-unavailable", "output": self.output}) + "\n")


class IomanDevctlProbe(gdb.Breakpoint):
    def __init__(self, symbol, mechanism):
        target = gdb.lookup_global_symbol(symbol)
        if target is None:
            raise ValueError("probe function symbol absent: " + symbol)
        super().__init__("*" + str(int(target.value().address)))
        self.mechanism = mechanism
        self.samples = 0

    def stop(self):
        try:
            frame = gdb.newest_frame()
            # Exact out-of-line x86-64 SysV function entry, before argument
            # registers are reused. Symbol-name breakpoints also hit inline
            # locations whose DWARF has no ordinal/cpu variables.
            ordinal = int(gdb.parse_and_eval("$rsi")) & 0xFFFF
            cpu = gdb.parse_and_eval("$rdx").cast(
                gdb.lookup_type("ps2x::iop::detail::IopCpuState").pointer()).dereference()
            regs = cpu["gpr"]["_M_elems"]
            if self.mechanism == "devctl" and ordinal != 31:
                return False
            if self.mechanism == "fileio-event-wait" and (ordinal != 10 or int(cpu["pc"]) != 0x37B68):
                return False
            owner_type = "ps2x::iop::detail::" + ("IopIoman" if self.mechanism == "devctl" else "IopKernel")
            state = gdb.parse_and_eval("$rdi").cast(gdb.lookup_type(owner_type).pointer()).dereference()
            memory = state["m_memory"]
            iop_ram = int(memory["m_ram"]["_M_impl"]["_M_start"])

            def read(address, size):
                physical = address & 0x1FFFFFFF
                if physical + size > 0x200000:
                    raise ValueError("IOP range out of bounds")
                return bytes(gdb.selected_inferior().read_memory(iop_ram + physical, size))

            item = {"kind": "ioman-devctl-proof", "mechanism": self.mechanism,
                "pc": int(cpu["pc"]), "registers": {str(index): int(regs[index]) for index in (2, 4, 5, 6, 7, 28, 29, 31)}}
            if self.mechanism == "devctl":
                item["path"] = read(int(regs[4]), 64).split(b"\0", 1)[0].decode("ascii", "replace")
                item["stack_arguments"] = list(struct.unpack("<2I", read(int(regs[29]) + 16, 8)))
                IomanDevctlReturn(frame, int(gdb.parse_and_eval("$rdx")), iop_ram, *item["stack_arguments"])
                item["original_cdvd_lock_event"] = struct.unpack("<I", read(0x21434, 4))[0]
                vector = state["m_devices"]["_M_impl"]
                start, end = vector["_M_start"], vector["_M_finish"]
                count = int(end - start)
                if not 0 <= count <= 16:
                    raise ValueError("unexpected device count")
                item["devices"] = []
                for index in range(count):
                    device = (start + index).dereference()
                    address = int(device["address"])
                    fields = list(struct.unpack("<5I", read(address, 20)))
                    item["devices"].append({"address": address, "gp": int(device["gp"]),
                        "fields": fields, "name": read(fields[0], 64).split(b"\0", 1)[0].decode("ascii", "replace"),
                        "devctl_handler": struct.unpack("<I", read(fields[4] + 23 * 4, 4))[0]})
            else:
                item["event_id"] = int(regs[4])
            self.samples += 1
            trace.write(json.dumps(item) + "\n")
            if self.samples >= 8:
                self.enabled = False
                trace.write(json.dumps({"kind": "ioman-devctl-cap", "mechanism": self.mechanism, "limit": 8}) + "\n")
        except (gdb.error, ValueError, IndexError, AttributeError, TypeError) as error:
            trace.write(json.dumps({"kind": "ioman-devctl-error", "mechanism": self.mechanism,
                "error": str(error)}) + "\n")
            self.enabled = False
        return False


ioman_hooks = [IomanDevctlProbe("ps2x::iop::detail::IopIoman::dispatchImport", "devctl"),
               IomanDevctlProbe("ps2x::iop::detail::IopKernel::dispatchEventImport", "fileio-event-wait")]
for hook in ioman_hooks:
    trace.write(json.dumps({"kind": "ioman-devctl-hook", "mechanism": hook.mechanism,
        "pending": bool(hook.pending), "enabled": bool(hook.enabled)}) + "\n")


class CheckedDeviceReturn(gdb.FinishBreakpoint):
    def __init__(self, frame, owner, function):
        super().__init__(frame, internal=True)
        self.owner, self.function = owner, function

    def stop(self):
        item = {"kind": "ioman-checked-return", "function": self.function}
        try:
            state = gdb.Value(self.owner).cast(gdb.lookup_type(
                "ps2x::iop::detail::IopEmulator::Impl").pointer()).dereference()
            item["reset_required"] = bool(state["resetRequired"])
            item["status"] = int(self.return_value) if self.return_value is not None else None
        except (gdb.error, ValueError, IndexError, AttributeError, TypeError) as error:
            item["probe_error"] = str(error)
        trace.write(json.dumps(item) + "\n")
        return False

    def out_of_scope(self):
        trace.write(json.dumps({"kind": "ioman-checked-return-unavailable", "function": self.function}) + "\n")


class CheckedDeviceEntry(gdb.Breakpoint):
    def __init__(self):
        symbol = gdb.lookup_global_symbol(
            "ps2x::iop::detail::IopEmulator::Impl::executeGuestFunctionWithStackArgumentsChecked")
        super().__init__("*" + str(int(symbol.value().address)))
        self.samples = 0

    def stop(self):
        try:
            CheckedDeviceReturn(gdb.newest_frame(), int(gdb.parse_and_eval("$rdi")), int(gdb.parse_and_eval("$rsi")))
            self.samples += 1
            if self.samples >= 8:
                self.enabled = False
                trace.write(json.dumps({"kind": "ioman-checked-cap", "limit": 8}) + "\n")
        except (gdb.error, ValueError, IndexError, AttributeError, TypeError) as error:
            trace.write(json.dumps({"kind": "ioman-checked-error", "error": str(error)}) + "\n")
            self.enabled = False
        return False


checked_device_entry = CheckedDeviceEntry()
trace.write(json.dumps({"kind": "ioman-checked-hook", "pending": bool(checked_device_entry.pending),
    "enabled": bool(checked_device_entry.enabled), "limit": 8}) + "\n")


class McservRegistrationReturn(gdb.FinishBreakpoint):
    def __init__(self, frame, owner):
        super().__init__(frame, internal=True)
        self.owner = owner

    def stop(self):
        item = {"kind": "mcserv-registration-return"}
        try:
            state = gdb.Value(self.owner).cast(gdb.lookup_type(
                "ps2x::iop::detail::IopRpcBridge").pointer()).dereference()
            children = iter(gdb.default_visualizer(state["m_servers"]).children())
            item["server"] = None
            item["servers_truncated"] = False
            for _ in range(128):
                entry = next(children, None)
                if entry is None:
                    break
                _, value = next(children)
                if int(entry[1]) == 0x80000400:
                    item["server"] = {field: int(value[field]) for field in
                        ("sid", "serverData", "function", "gp", "buffer", "queue")}
                    break
            else:
                item["servers_truncated"] = next(children, None) is not None
            kernel = state["m_kernel"]
            current = kernel["m_currentThread"]
            item["current_thread"] = int(current.dereference()["id"]) if int(current) else None
            children = iter(gdb.default_visualizer(kernel["m_threads"]).children())
            item["fileio_thread"] = None
            item["threads_truncated"] = False
            for _ in range(128):
                entry = next(children, None)
                if entry is None:
                    break
                _, value = next(children)
                if int(entry[1]) == 7:
                    item["fileio_thread"] = {field: int(value[field]) for field in
                        ("id", "state", "priority", "waitId", "waitBits")}
                    break
            else:
                item["threads_truncated"] = next(children, None) is not None
        except (gdb.error, ValueError, IndexError, StopIteration, AttributeError, TypeError) as error:
            item["probe_error"] = str(error)
        trace.write(json.dumps(item) + "\n")
        return False

    def out_of_scope(self):
        trace.write(json.dumps({"kind": "mcserv-registration-return-unavailable"}) + "\n")


class McservRegistrationEntry(gdb.Breakpoint):
    def __init__(self):
        symbol = gdb.lookup_global_symbol("ps2x::iop::detail::IopRpcBridge::dispatchSifCmdImport")
        super().__init__("*" + str(int(symbol.value().address)))

    def stop(self):
        try:
            cpu_address = int(gdb.parse_and_eval("$rdx"))
            cpu = gdb.Value(cpu_address).cast(gdb.lookup_type(
                "ps2x::iop::detail::IopCpuState").pointer()).dereference()
            if int(gdb.parse_and_eval("$rsi")) & 0xFFFF != 17 or int(cpu["gpr"]["_M_elems"][5]) != 0x80000400:
                return False
            McservRegistrationReturn(gdb.newest_frame(), int(gdb.parse_and_eval("$rdi")))
            self.enabled = False
            trace.write(json.dumps({"kind": "mcserv-registration-cap", "limit": 1}) + "\n")
        except (gdb.error, ValueError, IndexError, AttributeError, TypeError) as error:
            trace.write(json.dumps({"kind": "mcserv-registration-error", "error": str(error)}) + "\n")
            self.enabled = False
        return False


mcserv_registration_entry = McservRegistrationEntry()
trace.write(json.dumps({"kind": "mcserv-registration-hook", "pending": bool(mcserv_registration_entry.pending),
    "enabled": bool(mcserv_registration_entry.enabled), "limit": 1}) + "\n")
