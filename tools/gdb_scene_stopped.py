"""Bounded read-only scene/GS state from a stopped native runtime stack."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(os.environ["GA2_WAIT_SNAPSHOT_PROBE"]).parent))
from scene_snapshot import SCENE_ELF_SHA256, scene_snapshot


def scalar_fields(value):
    return {field.name: int(value[field.name]) for field in value.type.fields()}


def stopped_scene_words(address, count):
    try:
        return list(struct.unpack("<" + "I" * count, bytes(
            gdb.selected_inferior().read_memory(scene_ram + address, count * 4))))
    except gdb.error as error:
        raise OSError(str(error)) from error


scene_runtime = None
for thread in gdb.selected_inferior().threads():
    thread.switch()
    frame = gdb.newest_frame()
    while frame:
        try:
            candidate = frame.read_var("this" if (frame.name() or "").startswith("PS2Runtime::") else "runtime")
            if not candidate.is_optimized_out and candidate.type.target().name == "PS2Runtime" and int(candidate):
                scene_runtime = candidate.dereference()
                break
        except (gdb.error, ValueError, TypeError):
            pass
        frame = frame.older()
    if scene_runtime is not None:
        break

scene_result = {"kind": "stopped-scene-state", "consistency": "stopped-native"}
if os.environ.get("GA2_SCENE_ELF_SHA256") != SCENE_ELF_SHA256:
    scene_result["probe_error"] = "scene observer requires the verified original Zettai build"
elif scene_runtime is None:
    scene_result["probe_error"] = "runtime unavailable in stopped stack"
else:
    try:
        scene_ram = int(scene_runtime["m_memory"]["m_rdram"])
        if stopped_scene_words(0x100008, 1)[0] != 0x70000C28:
            raise ValueError("original Zettai entry word not loaded; layout unavailable")
        scene_result["snapshot"] = scene_snapshot(stopped_scene_words)
        gs = scene_runtime["m_gs"]
        scene_result["gs"] = {name: int(gs[name]) for name in (
            "m_nativeImageUploadCount", "m_nativePackedGIFPacketCount", "m_debugHistoryCount",
            "m_lastDisplayBaseBytes", "m_hostPresentationWidth", "m_hostPresentationHeight",
            "m_hostPresentationDisplayFbp", "m_hostPresentationSourceFbp", "m_hasHostPresentationFrame",
            "m_debugHistoryPaused", "m_trxdir")}
        scene_result["gs"]["contexts"] = [{name: scalar_fields(gs["m_ctx"][index][name])
            for name in ("frame", "tex0", "scissor")} for index in range(2)]
        scene_result["gs"]["prim"] = scalar_fields(gs["m_prim"])
    except (gdb.error, OSError, ValueError, TypeError, IndexError) as error:
        scene_result["probe_error"] = str(error)
print("GA2_SCENE " + json.dumps(scene_result))
