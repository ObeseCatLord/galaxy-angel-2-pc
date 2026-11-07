"""Bounded diagnostic reads of original Zettai object layouts; no guest writes.

PINE reads are sequential and may span guest updates. GDB reads occur while
stopped. Field offsets describe observed original loads, not inferred classes.
"""

SCENE_ELF_SHA256 = "727b6451e8216e8b091ba6bcdbfe3a2f5b408cbca2f73118715d6f922c106578"


def physical_range(address, count):
    if not isinstance(count, int) or not 0 <= count <= 128 or address % 4:
        raise ValueError("unaligned or oversized scene read")
    if 0 <= address <= 0x2000000:
        physical = address
    elif 0x80000000 <= address <= 0x82000000:
        physical = address - 0x80000000
    elif 0xA0000000 <= address <= 0xA2000000:
        physical = address - 0xA0000000
    else:
        raise ValueError("scene read outside EE RAM")
    if physical + count * 4 > 0x2000000:
        raise ValueError("scene read crosses EE RAM boundary")
    return physical


def scene_snapshot(reader):
    remaining = 1024
    completed = 0
    result = {"layout": "zettai-original-fields-v1",
              "layout_expected_elf_sha256": SCENE_ELF_SHA256, "errors": []}

    def failure(field, error):
        result["errors"].append({"field": field, "error": str(error)})

    def words(address, count):
        nonlocal remaining, completed
        physical = physical_range(address, count)
        if count > remaining:
            raise ValueError("scene read budget exhausted")
        remaining -= count
        data = reader(physical, count)
        if len(data) != count:
            raise ValueError("short scene word read")
        completed += count
        return data

    def branch(name, action):
        try:
            result[name] = action()
        except (OSError, ValueError, IndexError) as error:
            failure(name, error)

    def world(item):
        pointer = words(0x2BCF40, 1)[0]
        item["pointer"] = pointer
        if pointer:
            item["words_ce0_d00"] = words(pointer + 0xCE0, 9)
            item["word_148"] = words(pointer + 0x148, 1)[0]
        return item

    def collection(item):
        holder = words(0x2B6640, 1)[0]
        item["holder"] = holder
        if not holder:
            return item
        obj = words(holder, 1)[0]
        item["object"] = obj
        if not obj:
            return item
        data = words(obj, 16)
        item["words"] = data
        if data[13]:
            item["descriptor_words"] = words(data[13], 28)
        begin, end = data[6:8]
        distance = (end - begin) & 0xFFFFFFFF
        item.update(begin=begin, end=end, distance32=distance)
        if distance % 8 or distance > 512 or (distance and not begin):
            item["entries_unsampled"] = "collection shape outside 64-record bound"
            failure("collection.entries", item["entries_unsampled"])
            return item
        if begin or end:
            physical_range(begin, distance // 4)
            physical_range(end, 0)
        entries = words(begin, distance // 4) if distance else []
        item["entries"] = entries
        children = []
        for pointer, key in zip(entries[::2][:8], entries[1::2][:8]):
            child = {"pointer": pointer, "opaque_key": key}
            try:
                if pointer:
                    child["words"] = words(pointer, 32)
                    descriptor = child["words"][1]
                    if descriptor:
                        child["descriptor_at_4_words"] = words(descriptor, 28)
                    if descriptor == 0x2D4398:
                        child["phase_word_68"] = child["words"][26]
                        child["substate_word_6c"] = child["words"][27]
                        handles = []
                        for offset in range(0x10, 0x28, 4):
                            handle = child["words"][offset // 4]
                            row = {"field_offset": offset, "pointer": handle}
                            try:
                                if handle:
                                    row["words"] = words(handle, 16)
                            except (OSError, ValueError, IndexError) as error:
                                row["error"] = str(error)
                                failure(f"collection.child[{len(children)}].handle[{offset:x}]", error)
                            handles.append(row)
                        child["handles_10_24"] = handles
            except (OSError, ValueError, IndexError) as error:
                child["error"] = str(error)
                failure(f"collection.child[{len(children)}]", error)
            children.append(child)
        item["children_first_eight"] = children
        return item

    result["world"] = {}
    branch("world", lambda: world(result["world"]))
    branch("pad_fsm", lambda: words(0x2CF970, 8))
    branch("mc_reply", lambda: words(0x828640, 4))
    result["collection"] = {}
    branch("collection", lambda: collection(result["collection"]))
    result["requested_words"] = 1024 - remaining
    result["completed_words"] = completed
    result["error_count"] = len(result["errors"])
    result["complete"] = not result["errors"]
    return result
