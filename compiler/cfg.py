"""Control-flow graph: basic blocks and edges, built separately for each function.

Leaders (first instruction of a block): the first instruction of a function or of the
top-level code, every LABEL, and every instruction that follows a jump or a RETURN.
Edge kinds: next (fall through), true / false (the two sides of IF_FALSE), jump (GOTO).
"""
import re


class ControlFlowGraph:
    def build(self, instructions):
        regions, cur, name = [], [], "<globals>"
        for i, ins in enumerate(instructions):
            if ins.startswith("FUNC "):
                if cur: regions.append((name, cur))
                name, cur = ins[5:].rstrip(":"), [(i, ins)]
            elif ins.startswith("END_FUNC"):
                regions.append((name, cur)); name, cur = "<globals>", []
            else:
                cur.append((i, ins))
        if cur:
            regions.append((name, cur))

        blocks, edges, entries, n = [], [], {}, 0
        for fname, code in regions:
            leaders = {0}
            for k, (_, ins) in enumerate(code):
                if ins.startswith("LABEL "):
                    leaders.add(k)
                if ins.startswith(("GOTO ", "IF_FALSE ", "RETURN")) and k + 1 < len(code):
                    leaders.add(k + 1)
            starts = sorted(leaders)
            fblocks = []
            for j, s in enumerate(starts):
                e = starts[j + 1] if j + 1 < len(starts) else len(code)
                n += 1
                fblocks.append({"id": f"B{n}", "function": fname, "start": code[s][0],
                                "indices": [code[k][0] for k in range(s, e)],
                                "instructions": [code[k][1] for k in range(s, e)]})
            entries[fname] = fblocks[0]["id"] if fblocks else None
            label_block = {}
            for b in fblocks:
                for ins in b["instructions"]:
                    m = re.match(r"LABEL (\w+):", ins)
                    if m: label_block[m.group(1)] = b["id"]
            for j, b in enumerate(fblocks):
                last = b["instructions"][-1] if b["instructions"] else ""
                nxt = fblocks[j + 1]["id"] if j + 1 < len(fblocks) else None
                if last.startswith("GOTO "):
                    t = last.split()[-1]
                    if t in label_block: edges.append((b["id"], label_block[t], "jump"))
                elif last.startswith("IF_FALSE "):
                    t = last.split()[-1]
                    if t in label_block: edges.append((b["id"], label_block[t], "false"))
                    if nxt: edges.append((b["id"], nxt, "true"))
                elif not last.startswith("RETURN") and nxt:
                    edges.append((b["id"], nxt, "next"))
            blocks += fblocks
        return {"blocks": blocks, "edges": edges, "entries": entries}
