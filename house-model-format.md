# house-model/1

An exported model of a house for the iPhone app: storeys to show, rooms to pick for a scan. The app reads
this format and never reads a `.blend`. `tools/house_model_polygons.py` is reference code for the
`polygons` field, the one part an exporter can't simply read from the `.blend`.

## Package

```text
<House>/
  model-house.json      house, storeys and rooms
  storeys/<id>.usdz     one model per storey
```

Everything must be inside the package folder, and `file` paths are relative to it. An importer rejects
absolute paths, `..`, and a package with a missing storey file.

## Coordinates

Meters, Y up, right-handed. A Blender point maps to `model = (x, z, -y)`. Storey models and room data share
this frame and need no further transform. Polygons and seeds are `[x, z]` in the floor plane, at the height
of their storey's `floorY`.

## model-house.json

```json
{
  "format": "house-model/1",
  "house": "HoF",
  "exported": "2026-11-05T15:00:00+00:00",
  "storeys": [
    { "id": "OG1", "floorY": 4.5, "file": "storeys/OG1.usdz" }
  ],
  "rooms": [
    {
      "id": "02-2110-G01-105",
      "collection": "Room_OG1_1_05_Placeholder",
      "storey": "OG1",
      "nr": "1.05",
      "name": "BÜRO (Allg.)",
      "type": "Büroraum",
      "area": 8.75,
      "propCount": 0,
      "seeds": [[-13.45, -51.7]],
      "polygons": [[[-10.4, -52.8], [-14.5, -52.8], [-14.5, -50.6], [-10.4, -50.6]]]
    }
  ]
}
```

`storeys` is sorted bottom to top. A storey may have no rooms (a roof, for example).

| Room field | Required | Meaning |
|---|---|---|
| `id` | yes | Stable identity, unique in the file. Scan assignments refer to it |
| `collection` | yes | The room's collection name, for tracing it back to the `.blend` |
| `storey` | yes | A `storeys[].id` |
| `nr`, `name`, `type`, `area` | no | `room_nr`, `room_name`, `room_type`, `room_area` (m²), `null` if unset |
| `propCount` | yes | Instances in the room's `Props` collection. Above 0 means the room has interior |
| `seeds` | yes | Points inside the room, possibly empty |
| `polygons` | yes | Floor outlines without holes, possibly empty. Winding is unspecified |

Consumers must ignore unknown fields. Any change that older readers can't ignore gets a new format version.

## Blender source

Rooms are the collections with `group = room` anywhere below the `group = interior` collection, named
`Room_<Storey>_<Name>`. Folders between them (`Interior_OG1`) carry no meaning.

| Output | Source |
|---|---|
| `id` | `room_id`, else `room_nr`, else the collection name |
| `storeys` | Storeys of all rooms, plus `Exterior_<Storey>` children of the `group = exterior` collection |
| `floorY` | Lowest instance origin of the storey's `Structure` collections, else of its exterior |
| `propCount` | Collection instances below the room's `group = props` child |
| `seeds` | Objects directly in the room whose name starts with `ProbeSeed` |

Adding a `room_id` to a room that was exported without one changes its `id`. The app therefore matches
assignments by `id`, then by `nr`.

## Polygons

A wall belongs to only one room, so outlines can't be read from a room's own `Structure`. They are computed
per storey:

1. Cut all `Structure` and exterior instances 1 m above `floorY`. `Props` are left out so that furniture
   can't change an outline.
2. Rasterize their LOD0 bounding boxes at 5 cm. Closed door leaves count as walls.
3. Flood-fill from each seed, trace the outer ring of the region and simplify it by 6 cm.

Only trustworthy polygons are written. A room gets them only if it has `area`, every seed is on free floor,
its regions are enclosed and contain no other room's seed, and their total area is within 15 % of `area`.
Otherwise `polygons` is empty.

## Markers

The app marks a room with its `polygons`, else with a pin at its first seed. A room with neither appears only
in the room list.

## Storey models

Each `.usdz` holds the storey's `Structure` and `Exterior_<id>` instances in the shared frame, at the lowest
level of detail each module has. Leave out `Props` and colliders. Storey models must stay light, because a
phone shows them together.
