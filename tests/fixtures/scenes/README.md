# Scene fixtures

Test stages for `nitpix/scene.py` (`tests/test_scene.py`). Everything here is generated and
contains no client material.

Regenerate them with Blender (5.2.3 LTS was used):

```bash
Blender --background --factory-startup --python tools/scenes/make_fixtures.py -- tests/fixtures/scenes
```

| File | What it is |
|---|---|
| `stage.fbx` | Binary FBX 7.4 from Blender, in metres, Y up. `Logo_B` is renamed `Logo_A` after export, so there are two objects with one name |
| `stage_cm_zup.fbx` | The same stage authored in centimetres and exported Z up |
| `stage.obj` / `.mtl` | The same stage as OBJ (no hierarchy, no instances) |
| `uvedge.fbx` | From `tools/scenes/make_uv_fixtures.py`: UVs within and just past the edge tolerance, out-of-range islands, tiled UVs, and an object whose two UV sets differ |
| `edge.obj` / `.mtl` | Written as text: two objects named `Screen`, an object without UVs, a hexagon |
| `truncated.fbx`, `garbage.fbx`, `truncated.obj` | Damaged files that must be refused with a readable error |
| `parts/` | From `tools/scenes/make_parts.py`: one stage exported as separate files. `centre.obj` (positions in the file), `left.fbx` (placed by its node transform), `right.fbx` (centimetres, Z up, under a `Rig` node), `venue.obj` (floor, and a truss without UVs), `a/screen.obj` and `b/screen.obj` (same file name). Five files have an object called `Screen`. `parts/expected.json` has each UV corner's position in metres, Y up, taken from the Blender scene |
| `expected.json` | Values taken from the Blender scene itself: object paths, triangles, material slots, UV sets, and every (position, UV) corner. UVs are exact float32 values; positions are in metres, Y up, to 1e-4 |

The stage contains:
- `Stage/Screens/Centre`, `Left` and `Right`; Centre has a second UV set, `PixelMap`;
- `Floor`;
- `Truss`, with no UVs;
- `Wall`, with two material slots;
- `Panel_1` and `Panel_2`, which share one mesh;
- `Mirror`, with negative scale;
- `Badge`, an octagon;
- `Spill`, with UVs that run beyond 0–1.

Real stage files exported from other tools belong in a separate folder. Each one needs a note of
who made it, the tool and version it came from, and its licence. They must never be client
material.
