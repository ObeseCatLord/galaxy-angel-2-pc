"""Read-only exact-Zettai descriptor query and its existing case returns.

The switch header transfers to preexisting case functions. A header host return
is not guest completion; only the matched case's actual return is sampled.
"""
import json
import struct

import gdb

query_pending = {}
query_entry_count = 0
query_return_count = 0
query_cases = {
    0x23F128: ("caseD_1_0x23f128", 0),
    0x23F130: ("caseD_2_0x23f130", 1),
    0x23F138: ("caseD_12_0x23f138", 2),
    0x23F140: ("caseD_3_0x23f140", 3),
    0x23F148: ("caseD_13_0x23f148", 4),
    0x23F150: ("caseD_23_0x23f150", 5),
    0x23F158: ("caseD_4_0x23f158", -1),
}


def query_context(address):
    return gdb.Value(address).cast(gdb.lookup_type("R5900Context").pointer()).dereference()


def query_registers(ctx):
    return {str(i): struct.unpack("<I", bytes(gdb.selected_inferior().read_memory(
        int(ctx["r"][i].address), 4)))[0] for i in (2, 4, 5, 6, 7, 28, 29, 31)}


def query_full_preserved(ctx):
    return {str(i): bytes(gdb.selected_inferior().read_memory(
        int(ctx["r"][i].address), 16)).hex() for i in (28, 29, 31)}


def query_words(ram, address, count):
    physical = address & 0x1FFFFFF
    if not 0 <= count <= 35 or physical + count * 4 > 0x2000000:
        raise ValueError("probe read outside bounded EE RAM")
    return list(struct.unpack("<" + "I" * count, bytes(
        gdb.selected_inferior().read_memory(ram + physical, count * 4))))


class QueryReturn(gdb.FinishBreakpoint):
    def __init__(self, frame, key, record, case):
        super().__init__(frame, internal=True)
        self.key, self.record, self.case = key, record, case

    def stop(self):
        global query_return_count
        record = self.record
        item = {"kind": "descriptor-query-return", "selector": record["selector"], "case": self.case}
        try:
            ctx = query_context(record["context"])
            registers = query_registers(ctx)
            after = query_full_preserved(ctx)
            output = query_words(record["ram"], record["output"], 3)
            expected = record["before"].copy()
            expected[0] = 0x6DB0
            item.update(pc=int(ctx["pc"]), registers=registers, output_words=output,
                        full_preserved_after=after, preserved_128=after == record["full"],
                        return_matches=int(ctx["pc"]) == record["registers"]["31"],
                        result_matches=registers["2"] == (query_cases[self.case][1] & 0xFFFFFFFF),
                        selected_case_matches=self.case == record["expected_case"],
                        output_matches=output == expected)
        except (gdb.error, ValueError, TypeError, AttributeError, IndexError) as error:
            item["probe_error"] = str(error)
        query_pending.pop(self.key, None)
        query_return_count += 1  # Sample count; errors/mismatches remain separate.
        trace.write(json.dumps(item) + "\n")
        return False

    def out_of_scope(self):
        trace.write(json.dumps({"kind": "descriptor-query-return-unavailable",
                                "selector": self.record["selector"], "case": self.case}) + "\n")


class QueryHook(gdb.Breakpoint):
    def __init__(self, symbol, address, initial=False):
        target = gdb.lookup_global_symbol(symbol)
        if target is None:
            raise ValueError("probe symbol absent: " + symbol)
        super().__init__("*" + str(int(target.value().address)), internal=True)
        self.address, self.initial, self.samples = address, initial, 0

    def stop(self):
        global query_entry_count
        try:
            frame = gdb.newest_frame()
            if frame.architecture().name() != "i386:x86-64":
                raise ValueError("probe requires Linux x86-64 SysV entry ABI")
            ram, context = int(gdb.parse_and_eval("$rdi")), int(gdb.parse_and_eval("$rsi"))
            ctx = query_context(context)
            if int(ctx["pc"]) != self.address:
                return False
            registers = query_registers(ctx)
            key = (context, registers["29"], registers["31"])
            if self.initial:
                if key in query_pending or len(query_pending) >= 8:
                    raise ValueError("duplicate or exhausted pending query sample")
                selector = registers["4"]
                index = (selector - 1) & 0xFFFFFFFF
                table = query_words(ram, 0x2EC9C0, 35)
                case = table[index] if index < 35 else 0x23F158
                if case not in query_cases:
                    raise ValueError("original table contains an unexpected case")
                record = {"ram": ram, "context": context, "selector": selector,
                          "registers": registers, "full": query_full_preserved(ctx),
                          "output": registers["5"], "before": query_words(ram, registers["5"], 3),
                          "expected_case": case}
                query_pending[key] = record
                query_entry_count += 1
                active = query_words(ram, 0x7FB780, 1)[0]
                trace.write(json.dumps({"kind": "descriptor-query-entry", "registers": registers,
                                        "selector": selector, "expected_case": case,
                                        "live_switch_table": table,
                                        "full_preserved_before": record["full"], "output_before": record["before"],
                                        "active_descriptor": active,
                                        "active_slot20": query_words(ram, active + 0x20, 1)[0]}) + "\n")
            else:
                record = query_pending.get(key)
                if record is None:
                    return False
                QueryReturn(frame, key, record, self.address)
                trace.write(json.dumps({"kind": "descriptor-query-case", "case": self.address,
                                        "selector": record["selector"], "registers": registers}) + "\n")
            self.samples += 1
            if self.samples >= 8:
                self.enabled = False
                trace.write(json.dumps({"kind": "descriptor-query-cap", "address": self.address, "limit": 8}) + "\n")
        except (gdb.error, ValueError, TypeError, AttributeError, IndexError) as error:
            self.enabled = False
            trace.write(json.dumps({"kind": "descriptor-query-error", "address": self.address,
                                    "error": str(error)}) + "\n")
        return False


query_hooks = []
for symbol, address, initial in [("entry_23f0f8_0x23f128", 0x23F0F8, True)] + [
        (symbol, address, False) for address, (symbol, _) in query_cases.items()]:
    item = {"kind": "descriptor-query-hook", "symbol": symbol, "address": address}
    try:
        hook = QueryHook(symbol, address, initial)
        query_hooks.append(hook)
        item.update(pending=bool(hook.pending), enabled=bool(hook.enabled), limit=8)
    except (gdb.error, ValueError, TypeError, AttributeError) as error:
        item["probe_error"] = str(error)
    trace.write(json.dumps(item) + "\n")


def query_summary(event):
    trace.write(json.dumps({"kind": "descriptor-query-stop-coverage", "entry_samples": query_entry_count,
                            "return_samples": query_return_count,
                            "pending": [{"selector": r["selector"], "sp": r["registers"]["29"],
                                         "ra": r["registers"]["31"]} for r in query_pending.values()]}) + "\n")


gdb.events.stop.connect(query_summary)
