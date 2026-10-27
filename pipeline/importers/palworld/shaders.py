"""Cooked material shaders (raw/shaders/<material package path>/ from `PalExtract shader`).

The game ships only compiled shaders. Each material (and each instance with static switches or base-property overrides)
has its own shader map whose uniform expression set says, per constant-buffer slot, which parameter expression feeds it.
That tells which parameters a compiled variant actually reads, i.e. which static-switch branches it was built with, and maps
cb2[N] in the disassembled shaders (*.asm) to parameter names.
"""
import base64
import json
import os
import re
import struct

# EPreshaderOpcode as cooked by this build (UE 5.1 order without Modulo; checked against the dumped programs:
# Parameter/Parameter/AppendVector for float2 slots, Max(SoftEdge, 1e-5) then Rcp for its reciprocal).
OPS = ["Nop", "ConstantZero", "Constant", "Parameter", "Add", "Sub", "Mul", "Div", "Fmod", "Min", "Max", "Clamp", "Sin", "Cos",
       "Tan", "Asin", "Acos", "Atan", "Atan2", "Dot", "Cross", "Sqrt", "Rcp", "Length", "Normalize", "Saturate", "Abs", "Floor",
       "Ceil", "Round", "Trunc", "Sign", "Frac", "Fractional", "Log2", "Log10", "ComponentSwizzle", "AppendVector"]
BINARY = {"Add": "+", "Sub": "-", "Mul": "*", "Div": "/"}
FUNC2 = {"Fmod", "Min", "Max", "Atan2", "Dot", "Cross"}


def evaluate(ops: bytes, nums: list) -> str:
    """Symbolic evaluation of one preshader program -> expression string over parameter names."""
    st, i = [], 0
    while i < len(ops):
        name = OPS[ops[i]] if ops[i] < len(OPS) else f"op{ops[i]}"
        i += 1
        if name == "Parameter":
            st.append(nums[struct.unpack_from("<H", ops, i)[0]]["ParameterInfo"]["Name"]); i += 2
        elif name == "Constant":
            n = ops[i]; i += 1  # value type: 1..4 float components
            vals = struct.unpack_from(f"<{n}f", ops, i); i += 4 * n
            st.append(f"{vals[0]:g}" if n == 1 else f"({', '.join(f'{v:g}' for v in vals)})")
        elif name == "ConstantZero":
            st.append("0")
        elif name == "ComponentSwizzle":
            n = ops[i]; idx = ops[i + 1: i + 5]; i += 5
            st.append(f"{st.pop()}.{''.join('xyzw'[c] for c in idx[:n])}")
        elif name == "AppendVector":
            b, a = st.pop(), st.pop(); st.append(f"append({a}, {b})")
        elif name in BINARY:
            b, a = st.pop(), st.pop(); st.append(f"({a} {BINARY[name]} {b})")
        elif name in FUNC2:
            b, a = st.pop(), st.pop(); st.append(f"{name.lower()}({a}, {b})")
        elif name == "Clamp":
            c, b, a = st.pop(), st.pop(), st.pop(); st.append(f"clamp({a}, {b}, {c})")
        else:
            st.append(f"{name.lower()}({st.pop()})")
    return st[-1] if len(st) == 1 else " ; ".join(st)


def decode(folder: str, res: int = 0) -> list[dict]:
    d = json.load(open(os.path.join(folder, "resources.json")))[res]
    u = d["LoadedShaderMap"]["Content"]["MaterialCompilationOutput"]["UniformExpressionSet"]
    data = base64.b64decode(u["UniformPreshaderData"]["Data"])
    nums = u["UniformNumericParameters"]
    out = []
    for pre in u["UniformPreshaders"]:
        ops = data[pre["OpcodeOffset"]: pre["OpcodeOffset"] + pre["OpcodeSize"]]
        try:
            expr = evaluate(ops, nums)
        except Exception as e:  # unknown opcode layout: show the bytes
            expr = f"?? {ops.hex()} ({e})"
        for f in u["UniformPreshaderFields"][pre["FieldIndex"]: pre["FieldIndex"] + pre["NumFields"]]:
            o = f["BufferOffset"]
            width = {"Float1": 1, "Float2": 2, "Float3": 3, "Float4": 4}.get(f["Type"], 4)
            out.append({"slot": f"cb2[{o // 4}].{'xyzw'[o % 4: o % 4 + width]}", "type": f["Type"], "expr": expr})
    return out




def used_params(root: str, material_path: str, res: int = 0) -> set[str] | None:
    """Names of the material parameters the compiled shader of `material_path` reads (None when it was not dumped)."""
    folder = os.path.join(root, "shaders", *material_path.split("/"))
    path = os.path.join(folder, "resources.json")
    if not os.path.isfile(path):
        return None
    resources = json.load(open(path))
    if not resources or len(resources) <= res or not (resources[res] or {}).get("LoadedShaderMap"):
        return None  # no own shader map (an instance without static permutation uses its parent's)
    names: set[str] = set()
    for e in decode(folder, res):
        names |= set(re.findall(r"[A-Za-z][A-Za-z0-9_ ]*[A-Za-z0-9_]", e["expr"])) - {"append", "max", "min", "rcp", "clamp", "e"}
    return names
