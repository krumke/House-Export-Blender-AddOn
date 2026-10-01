"""Minimal binary FBX reader (7.x): nodes, Model transforms, geometry vertices, connections."""
import struct, zlib
from math import radians
from mathutils import Matrix, Euler, Vector


def _read_props(f, n):
    out = []
    for _ in range(n):
        t = f.read(1)
        if t == b"Y": out.append(struct.unpack("<h", f.read(2))[0])
        elif t == b"C": out.append(bool(f.read(1)[0]))
        elif t == b"I": out.append(struct.unpack("<i", f.read(4))[0])
        elif t == b"F": out.append(struct.unpack("<f", f.read(4))[0])
        elif t == b"D": out.append(struct.unpack("<d", f.read(8))[0])
        elif t == b"L": out.append(struct.unpack("<q", f.read(8))[0])
        elif t in b"fdlib":
            ln, enc, clen = struct.unpack("<III", f.read(12))
            data = f.read(clen)
            if enc == 1: data = zlib.decompress(data)
            fmt = {"f": "f", "d": "d", "l": "q", "i": "i", "b": "b"}[t.decode()]
            out.append(list(struct.unpack("<%d%s" % (ln, fmt), data)))
        elif t in (b"S", b"R"):
            ln = struct.unpack("<I", f.read(4))[0]
            out.append(f.read(ln))
        else:
            raise ValueError(t)
    return out


def _read_node(f, v64):
    if v64:
        end, nprops, plen = struct.unpack("<QQQ", f.read(24))
    else:
        end, nprops, plen = struct.unpack("<III", f.read(12))
    nlen = f.read(1)[0]
    if end == 0:
        return None
    name = f.read(nlen).decode()
    props = _read_props(f, nprops)
    children = []
    while f.tell() < end:
        ch = _read_node(f, v64)
        if ch is None:
            break
        children.append(ch)
    f.seek(end)
    return (name, props, children)


def read(path):
    with open(path, "rb") as f:
        f.read(23)
        ver = struct.unpack("<I", f.read(4))[0]
        v64 = ver >= 7500
        nodes = []
        while True:
            n = _read_node(f, v64)
            if n is None:
                break
            nodes.append(n)
    return nodes


def find(nodes, name):
    return [n for n in nodes if n[0] == name]


def p70(node):
    out = {}
    for ch in node[2]:
        if ch[0] == "Properties70":
            for p in ch[2]:
                key = p[1][0].decode()
                out[key] = p[1][4:]
    return out


def model_data(path):
    nodes = read(path)
    gs = p70(find(nodes, "GlobalSettings")[0])
    objs = find(nodes, "Objects")[0][2]
    models, geoms = {}, {}
    for n in objs:
        if n[0] == "Model":
            uid = n[1][0]; name = n[1][1].split(b"\x00")[0].decode()
            pr = p70(n)
            t = Vector(pr.get("Lcl Translation", (0, 0, 0)))
            r = pr.get("Lcl Rotation", (0, 0, 0))
            s = pr.get("Lcl Scaling", (1, 1, 1))
            pre = pr.get("PreRotation", (0, 0, 0))
            M = (Matrix.Translation(t) @ Euler([radians(a) for a in pre], "XYZ").to_matrix().to_4x4()
                 @ Euler([radians(a) for a in r], "XYZ").to_matrix().to_4x4() @ Matrix.Diagonal((*s, 1)))
            models[uid] = dict(name=name, local=M, parent=None, geom=None, props=pr)
        elif n[0] == "Geometry":
            uid = n[1][0]
            for ch in n[2]:
                if ch[0] == "Vertices":
                    v = ch[1][0]
                    geoms[uid] = [Vector(v[i:i + 3]) for i in range(0, len(v), 3)]
    for c in find(nodes, "Connections")[0][2]:
        kind = c[1][0].decode() if isinstance(c[1][0], bytes) else c[1][0]
        if kind != "OO": continue
        child, parent = c[1][1], c[1][2]
        if child in models and parent in models:
            models[child]["parent"] = parent
        elif child in geoms and parent in models:
            models[parent]["geom"] = child
    def world(uid):
        m = models[uid]
        return (world(m["parent"]) if m["parent"] else Matrix.Identity(4)) @ m["local"]
    for uid in models:
        models[uid]["world"] = world(uid)
    return gs, models, geoms
