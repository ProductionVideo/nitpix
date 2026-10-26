"""Build the Mapping importer's test scenes and their expected values.

    ~/nitpix-tools/Blender-5.2.3.app/Contents/MacOS/Blender --background --factory-startup \
        --python tools/scenes/make_fixtures.py -- tests/fixtures/scenes

Blender builds a small LED stage and exports it as FBX (metres, Y-up), OBJ, and FBX again as a
centimetre Z-up scene. edge.obj is written as text, for what Blender cannot make (duplicate object names,
UVs only on some faces). The corrupt files are cut or seeded from those. expected.json is
written from the scene data here, not from the importer under test.

The stage: Stage/Screens/{Centre, Left, Right}, Floor, Truss (no UVs), Wall (two material slots),
Panel_1 / Panel_2 (one mesh, two objects), Logo_A / Logo_B (renamed to the same name in the FBX),
Mirror (negative scale), Badge (an octagon), Spill (UVs beyond 0-1). Centre has a second UV set.
"""
import json
import math
import os
import random
import sys

import bmesh
import bpy
from mathutils import Matrix, Vector

OUT = os.path.abspath(sys.argv[sys.argv.index("--") + 1] if "--" in sys.argv else "tests/fixtures/scenes")
os.makedirs(OUT, exist_ok=True)

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.unit_settings.system = "METRIC"
scene.unit_settings.scale_length = 1.0


def material(name):
    return bpy.data.materials.get(name) or bpy.data.materials.new(name)


def grid(name, w, h, nx, ny, uv=(0.0, 0.0, 1.0, 1.0), slots=None):
    """A w x h screen standing in Blender's XZ plane, facing -Y (towards the audience), its
    bottom edge on z=0. UV (0,0) is the bottom-left of the picture and (1,1) the top-right.
    slots: material names; the left half of the faces uses slot 0, the right half slot 1."""
    me = bpy.data.meshes.new(name + "_mesh")
    verts = [(-w / 2 + w * i / nx, 0.0, h * j / ny) for j in range(ny + 1) for i in range(nx + 1)]
    faces = [(j * (nx + 1) + i, j * (nx + 1) + i + 1, (j + 1) * (nx + 1) + i + 1, (j + 1) * (nx + 1) + i)
             for j in range(ny) for i in range(nx)]
    me.from_pydata(verts, [], faces)
    u0, v0, u1, v1 = uv
    layer = me.uv_layers.new(name="UVMap")
    for poly in me.polygons:
        for li in poly.loop_indices:
            x, _, z = me.vertices[me.loops[li].vertex_index].co
            layer.data[li].uv = (u0 + (u1 - u0) * (x + w / 2) / w, v0 + (v1 - v0) * z / h)
    for s in slots or ():
        me.materials.append(material(s))
    if slots and len(slots) > 1:
        for poly in me.polygons:
            poly.material_index = 0 if poly.center.x < 0 else 1
    elif not slots:
        me.materials.append(material("Screen"))
    me.update()
    return me


def obj(name, data, parent=None, loc=(0, 0, 0), rot_z=0.0, scale=(1, 1, 1)):
    o = bpy.data.objects.new(name, data)
    scene.collection.objects.link(o)
    o.parent = parent
    o.location = loc
    o.rotation_euler = (0, 0, math.radians(rot_z))
    o.scale = scale
    return o


stage = obj("Stage", None)
screens = obj("Screens", None, stage)
centre = obj("Centre", grid("Centre", 6.4, 3.6, 8, 4), screens, (0, 0, 0.5))
pix = centre.data.uv_layers.new(name="PixelMap")            # second set: the centre's pixel map
for i, d in enumerate(centre.data.uv_layers["UVMap"].data):
    pix.data[i].uv = (d.uv.x * 0.5, d.uv.y * 0.25 + 0.75)
obj("Left", grid("Left", 3.2, 3.6, 4, 4), screens, (-4.6, -1.0, 0.5), rot_z=-30)
obj("Right", grid("Right", 3.2, 3.6, 4, 4), screens, (4.6, -1.0, 0.5), rot_z=30)

floor = bpy.data.meshes.new("Floor_mesh")
floor.from_pydata([(-6, -6, 0), (6, -6, 0), (6, 2, 0), (-6, 2, 0)], [], [(0, 1, 2, 3)])
floor.uv_layers.new(name="UVMap")
floor.materials.append(material("Floor"))
obj("Floor", floor, stage)

truss = grid("Truss", 12.0, 0.4, 6, 1)                       # geometry with no UVs at all
truss.uv_layers.remove(truss.uv_layers["UVMap"])
obj("Truss", truss, stage, (0, 0.5, 4.6))

obj("Wall", grid("Wall", 4.0, 2.0, 4, 2, slots=["LED_A", "LED_B"]), stage, (0, 3.0, 0.5))

panel = grid("Panel", 1.0, 1.0, 2, 2)                        # one mesh used by two objects
obj("Panel_1", panel, stage, (-2.0, -3.0, 0.0))
obj("Panel_2", panel, stage, (2.0, -3.0, 0.0))

obj("Logo_A", grid("Logo_A", 1.0, 0.5, 1, 1), stage, (-1.0, -2.0, 4.0))
obj("Logo_B", grid("Logo_B", 1.0, 0.5, 1, 1, uv=(0.0, 0.0, 0.5, 1.0)), stage, (1.0, -2.0, 4.0))

obj("Mirror", grid("Mirror", 1.6, 0.9, 2, 2), stage, (-5.0, -4.0, 0.5), scale=(-1, 1, 1))

bm = bmesh.new()                                              # one eight-sided face
ring = [bm.verts.new((0.5 * math.cos(a), 0.0, 0.5 + 0.5 * math.sin(a))) for a in (2 * math.pi * k / 8 for k in range(8))]
bm.faces.new(ring)
badge = bpy.data.meshes.new("Badge_mesh")
bm.to_mesh(badge)
bm.free()
uvl = badge.uv_layers.new(name="UVMap")
for li, loop in enumerate(badge.loops):
    co = badge.vertices[loop.vertex_index].co
    uvl.data[li].uv = (co.x + 0.5, co.z)
badge.materials.append(material("Screen"))
obj("Badge", badge, stage, (5.0, -4.0, 0.0))

obj("Spill", grid("Spill", 2.0, 1.0, 2, 1, uv=(-0.1, -0.1, 1.1, 1.1)), stage, (0, -5.0, 0.0))

bpy.context.view_layer.update()


# ---- expected values ----------------------------------------------------------------------
def path_of(o):
    names = []
    while o is not None:
        names.append(o.name)
        o = o.parent
    return "/".join(reversed(names))


# Blender's FBX/OBJ export with forward -Z, up Y maps Blender (x, y, z) to (x, z, -y).
Y_UP = Matrix(((1, 0, 0, 0), (0, 0, 1, 0), (0, -1, 0, 0), (0, 0, 0, 1)))

expected = {"objects": {}}
for o in scene.objects:
    if o.type != "MESH":
        continue
    me = o.data
    world = Y_UP @ o.matrix_world
    sets = []
    for layer in me.uv_layers:
        corners = set()
        for li, loop in enumerate(me.loops):
            p = world @ me.vertices[loop.vertex_index].co
            uv = layer.data[li].uv
            corners.add(tuple(round(c, 4) for c in (p.x, p.y, p.z)) + (uv.x, uv.y))   # UVs exact (float32)
        sets.append(sorted(corners))
    pts = [world @ v.co for v in me.vertices]
    tris = sum(len(p.vertices) - 2 for p in me.polygons)
    uvs = [d.uv for layer in me.uv_layers[:1] for d in layer.data]
    expected["objects"][path_of(o)] = {
        "triangles": tris,
        "slots": [m.name for m in me.materials],
        "uv_sets": len(me.uv_layers),
        "mesh": me.name,
        "outside_0_1": any(not (-1e-6 <= c <= 1 + 1e-6) for uv in uvs for c in uv),
        "corners": sets,           # per UV set: distinct (x, y, z, u, v); x y z in metres, Y up, to 1e-4
        "bounds": [[round(min(getattr(p, a) for p in pts), 4) for a in "xyz"],
                   [round(max(getattr(p, a) for p in pts), 4) for a in "xyz"]],
    }

# ---- exports ------------------------------------------------------------------------------
fbx = os.path.join(OUT, "stage.fbx")
bpy.ops.export_scene.fbx(filepath=fbx, use_selection=False, object_types={"EMPTY", "MESH"},
                         axis_forward="-Z", axis_up="Y", apply_unit_scale=True, global_scale=1.0,
                         mesh_smooth_type="OFF", use_mesh_modifiers=False, add_leaf_bones=False, bake_anim=False)
bpy.ops.wm.obj_export(filepath=os.path.join(OUT, "stage.obj"), export_selected_objects=False,
                      forward_axis="NEGATIVE_Z", up_axis="Y", export_uv=True, export_normals=False,
                      export_materials=True, export_triangulated_mesh=False, apply_modifiers=False)

# The same stage authored in centimetres (a 6.4 m screen is 640 units) and exported Z-up, as
# Cinema 4D / 3ds Max stages usually are: it must arrive the same size and the same way up.
scene.unit_settings.scale_length = 0.01
stage.scale = (100, 100, 100)
bpy.context.view_layer.update()
bpy.ops.export_scene.fbx(filepath=os.path.join(OUT, "stage_cm_zup.fbx"), use_selection=False,
                         object_types={"EMPTY", "MESH"}, axis_forward="Y", axis_up="Z",
                         apply_unit_scale=True, global_scale=1.0, apply_scale_options="FBX_SCALE_NONE",
                         mesh_smooth_type="OFF", use_mesh_modifiers=False, add_leaf_bones=False, bake_anim=False)

# FBX objects need unique names in Blender; give Logo_B the same name as Logo_A in the file.
# Same length, so every length prefix in the binary FBX stays right.
with open(fbx, "rb") as fh:
    data = fh.read()
assert data.count(b"Logo_B") >= 1
with open(fbx, "wb") as fh:
    fh.write(data.replace(b"Logo_B\x00\x01Model", b"Logo_A\x00\x01Model"))
expected["fbx_duplicate_names"] = {"Stage/Logo_A": ["Stage/Logo_A", "Stage/Logo_B"]}

# ---- edge.obj, as text -------------------------------------------------------------------
edge = """# nitpix importer edge cases
mtllib edge.mtl
o Screen
v -1 0 0
v 1 0 0
v 1 1 0
v -1 1 0
vt 0 0
vt 1 0
vt 1 1
vt 0 1
usemtl LED
f 1/1 2/2 3/3 4/4
o Screen
v 2 0 0
v 4 0 0
v 4 1 0
v 2 1 0
vt 0.5 0
vt 1 0
vt 1 1
vt 0.5 1
usemtl LED
f 5/5 6/6 7/7 8/8
o Bare
v 0 2 0
v 1 2 0
v 1 3 0
f 9 10 11
o Hex
v 0 -1 0
v 1 -1 0
v 1.5 -0.5 0
v 1 0 0
v 0 0 0
v -0.5 -0.5 0
vt 0.2 0
vt 0.8 0
vt 1 0.5
vt 0.8 1
vt 0.2 1
vt 0 0.5
usemtl LED
f 12/9 13/10 14/11 15/12 16/13 17/14
"""
with open(os.path.join(OUT, "edge.obj"), "w") as fh:
    fh.write(edge)
with open(os.path.join(OUT, "edge.mtl"), "w") as fh:
    fh.write("newmtl LED\nKd 0.5 0.5 0.5\n")
expected["edge_obj"] = {
    # Assimp's OBJ reader merges objects that share a name, so the two "o Screen" blocks arrive
    # as one object (FBX keeps duplicates apart; see fbx_duplicate_names).
    "Screen": {"triangles": 4, "uv": [[0, 0], [1, 0], [1, 1], [0, 1], [0.5, 0], [0.5, 1]]},
    "Bare": {"triangles": 1, "uv": None},
    "Hex": {"triangles": 4, "uv": [[0.2, 0], [0.8, 0], [1, 0.5], [0.8, 1], [0.2, 1], [0, 0.5]]},
}

# ---- corrupt files ---------------------------------------------------------------------------
with open(fbx, "rb") as fh:
    good = fh.read()
with open(os.path.join(OUT, "truncated.fbx"), "wb") as fh:
    fh.write(good[: len(good) * 3 // 5])
rnd = random.Random(7)
with open(os.path.join(OUT, "garbage.fbx"), "wb") as fh:
    fh.write(b"Kaydara FBX Binary  \x00\x1a\x00" + bytes(rnd.randrange(256) for _ in range(4096)))
with open(os.path.join(OUT, "stage.obj")) as fh:
    text = fh.read()
with open(os.path.join(OUT, "truncated.obj"), "w") as fh:
    fh.write(text[: len(text) // 2].rsplit("\n", 1)[0] + "\nf 1/1 99999/2 3\n")

expected["generator"] = {"blender": bpy.app.version_string, "script": "tools/scenes/make_fixtures.py"}
with open(os.path.join(OUT, "expected.json"), "w") as fh:
    json.dump(expected, fh, indent=1, sort_keys=True)
print("fixtures:", sorted(os.listdir(OUT)))
