"""Floor polygons for house-model/1 rooms (house-model-format.md), for use inside Blender.

Copy of tools/house_model_polygons.py from the app repository (reference code of the format).
Keep both in sync.

Only the polygon step: reading rooms, storeys and metadata is the exporter's job. The exporter
passes one storey at a time and writes the result into each room's `polygons`:

    from .house_model_polygons import floor_polygons
    polygons = floor_polygons(structure, floor_z, {room.name: (seeds, room.get('room_area'))})

structure: the storey's rooms' Structure collections and its exterior; never Props, so that
           furniture can't change an outline
floor_z:   the storey's floor height (Blender Z)
seeds:     world points inside the room (Vectors); area: room_area in m², or None
"""
import numpy as np
from mathutils import Vector

CELL = 0.05             # floor-plan raster size in meters
CUT_HEIGHT = 1.0        # height above the floor at which walls are cut
AREA_TOLERANCE = 0.15   # a polygon must match room_area this closely
SIMPLIFY = 0.06         # polygon simplification tolerance in meters


def model_xz(p):
    """Blender (x, y) to the model's floor plane: model = (x, z, -y), Y up."""
    return [round(p[0], 3) + 0.0, round(-p[1], 3) + 0.0]


def instances(colls):
    for coll in colls:
        for obj in coll.all_objects:
            if obj.instance_type == 'COLLECTION' and obj.instance_collection is not None:
                yield obj


def footprints(colls, cut_z):
    """World XY bounds of every LOD0 mesh crossing the cut plane."""
    boxes = []
    for obj in instances(colls):
        offset = obj.instance_collection.instance_offset
        for mesh in obj.instance_collection.all_objects:
            if mesh.type != 'MESH' or not mesh.name.endswith('_LOD0'):
                continue
            corners = [obj.matrix_world @ (mesh.matrix_world @ Vector(c) - offset) for c in mesh.bound_box]
            zs = [c.z for c in corners]
            if min(zs) <= cut_z <= max(zs):
                boxes.append((min(c.x for c in corners), max(c.x for c in corners),
                              min(c.y for c in corners), max(c.y for c in corners)))
    return boxes


class FloorPlan:
    """Rasterized walls of one storey. Regions are flood-filled lazily from seed points."""

    def __init__(self, boxes):
        b = np.array(boxes)
        self.x0, self.y0 = b[:, 0].min() - 1, b[:, 2].min() - 1
        self.width = int((b[:, 1].max() + 1 - self.x0) / CELL) + 1
        self.height = int((b[:, 3].max() + 1 - self.y0) / CELL) + 1
        self.walls = np.zeros((self.height, self.width), bool)
        for x0, x1, y0, y1 in boxes:
            self.walls[int((y0 - self.y0) / CELL):int((y1 - self.y0) / CELL) + 1,
                       int((x0 - self.x0) / CELL):int((x1 - self.x0) / CELL) + 1] = True
        self.labels = np.zeros((self.height, self.width), np.int32)
        self.regions = {}   # label -> dict(cells, open)

    def region_at(self, x, y):
        """Label of the free region containing a world point, or None inside a wall or off the plan."""
        cx, cy = int((x - self.x0) / CELL), int((y - self.y0) / CELL)
        if not (0 <= cx < self.width and 0 <= cy < self.height) or self.walls[cy, cx]:
            return None
        if not self.labels[cy, cx]:
            self._fill(cy, cx)
        return int(self.labels[cy, cx])

    def _fill(self, sy, sx):
        label = len(self.regions) + 1
        self.labels[sy, sx] = label
        stack, cells, is_open = [(sy, sx)], [], False
        while stack:
            y, x = stack.pop()
            cells.append((y, x))
            if y in (0, self.height - 1) or x in (0, self.width - 1):
                is_open = True      # reached the plan's edge: not enclosed
            for v, u in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
                if 0 <= v < self.height and 0 <= u < self.width and not self.walls[v, u] and not self.labels[v, u]:
                    self.labels[v, u] = label
                    stack.append((v, u))
        self.regions[label] = dict(cells=cells, open=is_open)

    def area(self, label):
        return len(self.regions[label]['cells']) * CELL * CELL

    def outline(self, label):
        """The region's outer ring as simplified world XY points. Inner islands are filled."""
        cells = set(self.regions[label]['cells'])
        following = {}
        for y, x in cells:
            if (y - 1, x) not in cells:
                following.setdefault((x, y), []).append((x + 1, y))
            if (y, x + 1) not in cells:
                following.setdefault((x + 1, y), []).append((x + 1, y + 1))
            if (y + 1, x) not in cells:
                following.setdefault((x + 1, y + 1), []).append((x, y + 1))
            if (y, x - 1) not in cells:
                following.setdefault((x, y + 1), []).append((x, y))
        rings = []
        while following:
            start = next(iter(following))
            ring, here, direction = [start], start, None
            while True:
                exits = following[here]
                chosen = exits[0]
                if direction and len(exits) > 1:
                    # the filled cell is on the left, so turning left stays on the same region
                    left = (-direction[1], direction[0])
                    chosen = next((e for e in exits if (e[0] - here[0], e[1] - here[1]) == left), exits[0])
                exits.remove(chosen)
                if not exits:
                    del following[here]
                direction = (chosen[0] - here[0], chosen[1] - here[1])
                here = chosen
                if here == start:
                    break
                ring.append(here)
            rings.append(ring)
        ring = max(rings, key=lambda r: abs(ring_area(r)))
        return simplify([(self.x0 + x * CELL, self.y0 + y * CELL) for x, y in ring])


def ring_area(ring):
    return sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(ring, ring[1:] + ring[:1])) / 2


def _reduce(points, tolerance):
    """Douglas-Peucker on an open chain."""
    if len(points) < 3:
        return points
    a, b = np.array(points[0]), np.array(points[-1])
    line = b - a
    length = np.hypot(*line)
    offsets = np.array(points[1:-1]) - a
    distances = np.abs(line[0] * offsets[:, 1] - line[1] * offsets[:, 0]) / length if length else np.hypot(*offsets.T)
    i = int(distances.argmax())
    if distances[i] <= tolerance:
        return [points[0], points[-1]]
    return _reduce(points[:i + 2], tolerance)[:-1] + _reduce(points[i + 1:], tolerance)


def simplify(ring, tolerance=SIMPLIFY):
    origin = np.array(ring[0])
    far = int(np.hypot(*(np.array(ring) - origin).T).argmax())
    first = _reduce(ring[:far + 1], tolerance)
    second = _reduce(ring[far:] + ring[:1], tolerance)
    points = first[:-1] + second[:-1]
    # the chains start at an arbitrary ring point, which may lie in the middle of a wall
    def is_corner(i):
        (ax, ay), (bx, by), (cx, cy) = points[i - 1], points[i], points[(i + 1) % len(points)]
        return abs((bx - ax) * (cy - by) - (by - ay) * (cx - bx)) > 1e-6
    return [p for i, p in enumerate(points) if is_corner(i)]


def floor_polygons(structure, floor_z, rooms):
    """{room key: polygons as [x, z] rings in model coordinates}, for the rooms of one storey.

    A room gets polygons only where they are trustworthy, otherwise [] (the app shows a pin):
    it needs an area to compare with, every seed on free floor, enclosed regions that contain
    no other room's seed, and a total area within AREA_TOLERANCE of its area.
    """
    boxes = footprints(structure, floor_z + CUT_HEIGHT)
    if not boxes:
        return {key: [] for key in rooms}
    plan = FloorPlan(boxes)
    owners = {}
    for key, (seeds, _) in rooms.items():
        for s in seeds:
            label = plan.region_at(s.x, s.y)
            if label is not None:
                owners.setdefault(label, set()).add(key)

    def polygons(key, seeds, area):
        if not area or not seeds:
            return []
        labels = {plan.region_at(s.x, s.y) for s in seeds}
        if None in labels:
            return []
        if any(plan.regions[l]['open'] or owners[l] != {key} for l in labels):
            return []
        if abs(sum(plan.area(l) for l in labels) / area - 1) > AREA_TOLERANCE:
            return []
        return [[model_xz(p) for p in plan.outline(l)] for l in sorted(labels)]

    return {key: polygons(key, seeds, area) for key, (seeds, area) in rooms.items()}
