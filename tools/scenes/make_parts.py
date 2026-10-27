"""Build the multi-file Mapping stage fixtures: one stage exported as separate model files.

    ~/nitpix-tools/Blender-5.2.3.app/Contents/MacOS/Blender --background --factory-startup \
        --python tools/scenes/make_parts.py -- tests/fixtures/scenes/parts

Each file is exported on its own, at its place in one shared stage (metres, Y up after import):
- centre.obj   a 6.4 x 3.6 m screen, "Screen", positions written into the OBJ
- left.fbx     "Screen", 3.2 x 3.6 m, placed by its node transform (moved left, turned -30°)
- right.fbx    "Rig/Screen", the mirror of left, authored in centimetres and exported Z up
- venue.obj    "Floor" (UVs) and "Truss" (no UVs)
- a/screen.obj and b/screen.obj   two files with one name, each a 1 x 0.5 m "Screen" banner
So the object name "Screen" is in five files, and two files share a file name. expected.json has,
per file and object, the scene-space point (metres, Y up, to 1e-4) of each UV corner, from the
Blender scene itself, not from the importer under test.
"""
import json
import math
import os
import sys

import bpy
from mathutils import Matrix

OUT = os.path.abspath(sys.argv[sys.argv.index("--") + 1] if "--" in sys.argv else "tests/fixtures/scenes/parts")
Y_UP = Matrix(((1, 0, 0, 0), (0, 0, 1, 0), (0, -1, 0, 0), (0, 0, 0, 1)))   # Blender (x, y, z) -> (x, z, -y)
expected = {}


def reset(cm=False):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    s = bpy.context.scene
    s.unit_settings.system = "METRIC"
    s.unit_settings.scale_length = 0.01 if cm else 1.0


def grid(name, w, h, nx=4, ny=2, uv=True):
    """A w x h screen in Blender's XZ plane facing -Y, bottom edge on z=0; UV (0,0) bottom-left."""
    me = bpy.data.meshes.new(name + "_mesh")
    verts = [(-w / 2 + w * i / nx, 0.0, h * j / ny) for j in range(ny + 1) for i in range(nx + 1)]
    faces = [(j * (nx + 1) + i, j * (nx + 1) + i + 1, (j + 1) * (nx + 1) + i + 1, (j + 1) * (nx + 1) + i)
             for j in range(ny) for i in range(nx)]
    me.from_pydata(verts, [], faces)
    if uv:
        layer = me.uv_layers.new(name="UVMap")
        for poly in me.polygons:
            for li in poly.loop_indices:
                x, _, z = me.vertices[me.loops[li].vertex_index].co
                layer.data[li].uv = ((x + w / 2) / w, z / h)
    me.materials.append(bpy.data.materials.get("Screen") or bpy.data.materials.new("Screen"))
    me.update()
    return me


def obj(name, data, parent=None, loc=(0, 0, 0), rot_z=0.0):
    o = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(o)
    o.parent = parent
    o.location = loc
    o.rotation_euler = (0, 0, math.radians(rot_z))
    return o


def path_of(o):
    names = []
    while o is not None:
        names.append(o.name)
        o = o.parent
    return "/".join(reversed(names))


def record(file, scale=1.0):
    """UV corner -> scene point, for every mesh object (world / scale: metres in a cm scene)."""
    bpy.context.view_layer.update()
    out = {}
    for o in bpy.context.scene.objects:
        if o.type != "MESH":
            continue
        world = Y_UP @ o.matrix_world
        corners = {}
        if o.data.uv_layers:
            for li, loop in enumerate(o.data.loops):
                uv = tuple(round(c, 4) + 0.0 for c in o.data.uv_layers[0].data[li].uv)   # + 0.0: no "-0"
                if uv in ((0, 0), (1, 0), (0, 1), (1, 1)):
                    p = world @ o.data.vertices[loop.vertex_index].co
                    corners[f"{uv[0]:g},{uv[1]:g}"] = [round(c / scale, 4) + 0.0 for c in (p.x, p.y, p.z)]
        out[path_of(o)] = {"corners": corners, "uv_sets": len(o.data.uv_layers)}
    expected[file] = out


def export_obj(rel):
    path = os.path.join(OUT, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    bpy.ops.wm.obj_export(filepath=path, export_selected_objects=False, forward_axis="NEGATIVE_Z", up_axis="Y",
                          export_uv=True, export_normals=False, export_materials=False, export_triangulated_mesh=False,
                          apply_modifiers=False)


def export_fbx(rel, **kw):
    args = dict(use_selection=False, object_types={"EMPTY", "MESH"}, axis_forward="-Z", axis_up="Y", apply_unit_scale=True,
                global_scale=1.0, mesh_smooth_type="OFF", use_mesh_modifiers=False, add_leaf_bones=False, bake_anim=False)
    args.update(kw)
    bpy.ops.export_scene.fbx(filepath=os.path.join(OUT, rel), **args)


reset()
obj("Screen", grid("Centre", 6.4, 3.6, 8, 4), loc=(0, 0, 0.5))
record("centre.obj")
export_obj("centre.obj")

reset()
obj("Screen", grid("Left", 3.2, 3.6), loc=(-4.6, -1.0, 0.5), rot_z=-30)
record("left.fbx")
export_fbx("left.fbx")

reset(cm=True)                                  # centimetres, Z up: must arrive the same size and way up
rig = obj("Rig", None)
obj("Screen", grid("Right", 3.2, 3.6), rig, loc=(4.6, -1.0, 0.5), rot_z=30)
rig.scale = (100, 100, 100)
record("right.fbx", scale=100)
export_fbx("right.fbx", axis_forward="Y", axis_up="Z", apply_scale_options="FBX_SCALE_NONE")

reset()
floor = bpy.data.meshes.new("Floor_mesh")
floor.from_pydata([(-6, -6, 0), (6, -6, 0), (6, 2, 0), (-6, 2, 0)], [], [(0, 1, 2, 3)])
lay = floor.uv_layers.new(name="UVMap")
for li, loop in enumerate(floor.loops):
    x, y, _ = floor.vertices[loop.vertex_index].co
    lay.data[li].uv = ((x + 6) / 12, (y + 6) / 8)
obj("Floor", floor)
obj("Truss", grid("Truss", 12.0, 0.4, 6, 1, uv=False), loc=(0, 0.5, 4.6))
record("venue.obj")
export_obj("venue.obj")

for rel, x in (("a/screen.obj", -2.0), ("b/screen.obj", 2.0)):
    reset()
    obj("Screen", grid("Banner", 1.0, 0.5, 2, 1), loc=(x, -2.0, 4.2))
    record(rel)
    export_obj(rel)

with open(os.path.join(OUT, "expected.json"), "w") as fh:
    json.dump(expected, fh, indent=1, sort_keys=True)
print("wrote", OUT)
