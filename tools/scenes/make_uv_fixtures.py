"""Build uvedge.fbx: UVs at and beyond the image's edge, for the Mapping diagnostics and the
view's UV_EDGE tolerance (nitpix/scene.py). Synthetic; ranges like those of real stage models.

    ~/nitpix-tools/Blender-5.2.3.app/Contents/MacOS/Blender --background --factory-startup \
        --python tools/scenes/make_uv_fixtures.py -- tests/fixtures/scenes

Five 2 x 1 m objects standing in a row, facing the audience (-Y in Blender, +Z in the file), in metres, Y up:
- Noise:   UVs -8e-5 .. 1 + 8e-5: export noise, inside the tolerance (all media, no warning);
- Beyond:  UVs -4e-4 .. 1 + 4e-4: just past it (a thin neutral border, warned);
- Islands: four 0.5 m quads; two map inside 0-1, two are islands at U 1.2-1.7 and -0.8 to -0.3;
- Tiles:   one linear map over U -1.69 .. 2.74, V -0.475 .. 1.476 (most of it outside, as tiled
           or world-scaled UVs are);
- Sets:    UV 1 tiled 0-4 x 0-2, UV 2 ("Fit") 0-1.
"""
import os
import sys

import bpy

OUT = os.path.abspath(sys.argv[sys.argv.index("--") + 1] if "--" in sys.argv else "tests/fixtures/scenes")
os.makedirs(OUT, exist_ok=True)
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.unit_settings.system = "METRIC"
scene.unit_settings.scale_length = 1.0
mat = bpy.data.materials.new("Screen")


def quads(name, cells, sets):
    """cells: (x0, x1) per quad, 0..1 m tall in Blender's XZ plane. sets: {uv name: [(u0, v0, u1, v1) per quad]}."""
    me = bpy.data.meshes.new(name + "_mesh")
    verts, faces = [], []
    for x0, x1 in cells:
        b = len(verts)
        verts += [(x0, 0.0, 0.0), (x1, 0.0, 0.0), (x1, 0.0, 1.0), (x0, 0.0, 1.0)]
        faces.append((b, b + 1, b + 2, b + 3))
    me.from_pydata(verts, [], faces)
    for uvname, rects in sets.items():
        layer = me.uv_layers.new(name=uvname)
        for poly, (u0, v0, u1, v1) in zip(me.polygons, rects):
            for li, corner in zip(poly.loop_indices, ((u0, v0), (u1, v0), (u1, v1), (u0, v1))):
                layer.data[li].uv = corner
    me.materials.append(mat)
    me.update()
    o = bpy.data.objects.new(name, me)
    scene.collection.objects.link(o)
    return o


def strip(name, x, rect, n=4, more=None):
    """A 2 x 1 m strip of n quads at x, its UVs one linear map over `rect`."""
    u0, v0, u1, v1 = rect
    cells = [(x - 1 + 2 * i / n, x - 1 + 2 * (i + 1) / n) for i in range(n)]
    lin = lambda r: [(r[0] + (r[2] - r[0]) * i / n, r[1], r[0] + (r[2] - r[0]) * (i + 1) / n, r[3]) for i in range(n)]
    sets = {"UVMap": lin(rect)}
    for k, r in (more or {}).items():
        sets[k] = lin(r)
    return quads(name, cells, sets)


e = 8e-5
strip("Noise", -6.0, (-e, -e, 1 + e, 1 + e))
e = 4e-4
strip("Beyond", -3.0, (-e, -e, 1 + e, 1 + e))
quads("Islands", [(-1.0, -0.5), (-0.5, 0.0), (0.0, 0.5), (0.5, 1.0)],
      {"UVMap": [(0.0, 0.0, 0.5, 1.0), (0.5, 0.0, 1.0, 1.0), (1.2, 0.0, 1.7, 1.0), (-0.8, 0.0, -0.3, 1.0)]})
strip("Tiles", 3.0, (-1.69, -0.475, 2.74, 1.476))
strip("Sets", 6.0, (0.0, 0.0, 4.0, 2.0), more={"Fit": (0.0, 0.0, 1.0, 1.0)})
bpy.context.view_layer.update()
bpy.ops.export_scene.fbx(filepath=os.path.join(OUT, "uvedge.fbx"), use_selection=False, object_types={"MESH"},
                         axis_forward="-Z", axis_up="Y", apply_unit_scale=True, global_scale=1.0,
                         mesh_smooth_type="OFF", use_mesh_modifiers=False, add_leaf_bones=False, bake_anim=False)
