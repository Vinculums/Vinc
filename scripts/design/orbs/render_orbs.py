"""Vinc orb kit, rendered offline with Blender (Cycles).

Run:  blender -b -P scripts/design/orbs/render_orbs.py -- [--out DIR] [--size 1024]
      [--samples 256] [--kinds concept,decision,...] [--states idle,active,...]
      [--theme dark|light|both] [--jewel 0..4]

One glass shell and an inner light for every kind, five states on every kind, as the
orb study describes ([[vinc.research.graph-vinc-orb-2026-10-28]] §2). The app draws the
same kit in a shader (`app/src/surfaces/view-orbs3d.ts`); this file makes the still
images: release cards, the site, social previews, press. Colours come from
`scripts/design/tokens.json` (topicJewels, d_bg0, grain_100), never typed here twice.

Kinds:   concept (sphere, drifting cloud) · decision (sphere, warm white cut crystal)
         record (tetrahedron, cloud and small core) · episode (octahedron, cloud and larger core)
         bridge (elongated shard, a line of light) · topic (hollow dotted shell) · area (glowing volume)
States:  idle · active · selected · historical · conflict
Output:  <out>/<theme>/<kind>-<state>.png, transparent background, square.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

import bpy
from mathutils import Vector

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
TOKENS = REPO / "scripts" / "design" / "tokens.json"

KINDS = ["concept", "decision", "record", "episode", "bridge", "topic", "area"]
STATES = ["idle", "active", "selected", "historical", "conflict"]
CONFLICT_RED = (0.93, 0.22, 0.25)
WARM_WHITE = (1.0, 0.97, 0.90)


# ── colours ───────────────────────────────────────────────────────────────────

def hex_rgb(h: str) -> tuple[float, float, float]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def srgb_to_linear(c: tuple[float, float, float]) -> tuple[float, float, float]:
    def f(v: float) -> float:
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    return tuple(f(v) for v in c)


def linear_to_srgb(c):
    def f(v: float) -> float:
        return v * 12.92 if v <= 0.0031308 else 1.055 * (v ** (1 / 2.4)) - 0.055
    return tuple(f(v) for v in c)


def load_palette() -> dict:
    t = json.loads(TOKENS.read_text(encoding="utf-8"))
    prim = t["primitive"]
    return {
        "jewels": {k: [srgb_to_linear(hex_rgb(x)) for x in v] for k, v in t["topicJewels"].items() if k in ("dark", "light")},
        "bg": {"dark": srgb_to_linear(hex_rgb(prim["d_bg0"]["$value"])),
               "light": srgb_to_linear(hex_rgb(prim["grain_100"]["$value"]))},
    }


def mix(a, b, k):
    return tuple(x + (y - x) * k for x, y in zip(a, b))


def desat(c, k):
    l = 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]
    return mix(c, (l, l, l), k)


# ── scene ─────────────────────────────────────────────────────────────────────

def reset_scene(size: int, samples: int, transparent: bool = True) -> bpy.types.Scene:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.samples = samples
    sc.cycles.use_denoising = True
    sc.render.resolution_x = sc.render.resolution_y = size
    sc.render.resolution_percentage = 100
    sc.render.film_transparent = transparent
    sc.render.image_settings.file_format = "PNG"
    sc.render.image_settings.color_mode = "RGBA"
    sc.render.image_settings.color_depth = "16"
    sc.view_settings.view_transform = "Standard"   # token hues as they are; emissions are kept under 1
    sc.cycles.max_bounces = 12
    sc.cycles.transmission_bounces = 12
    sc.cycles.transparent_max_bounces = 12
    sc.cycles.volume_bounces = 2
    sc.cycles.volume_step_rate = 0.5
    prefs = bpy.context.preferences.addons.get("cycles")
    if prefs:
        cp = prefs.preferences
        # ⚠ 우분투 apt 의 blender 는 CUDA·OptiX 커널이 빠져 있어 CPU 로만 돈다(1024px 한 장에 10분).
        # blender.org 의 공식 빌드(~/.local/opt/blender-5.0.1-linux-x64)로 돌릴 것.
        # 근거: [[record:vinc-orb-kit-rendered-offline-with-blender-cycles-2026-11-04]]
        for dev_type in ("OPTIX", "CUDA"):
            try:
                cp.compute_device_type = dev_type
                cp.refresh_devices()
                if any(d.type == dev_type for d in cp.devices):
                    for d in cp.devices:
                        d.use = d.type == dev_type or d.type == "CPU"
                    sc.cycles.device = "GPU"
                    break
            except Exception:
                continue
    # camera: straight on, orthographic so a sphere stays a circle
    cam_data = bpy.data.cameras.new("cam")
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = 3.2
    cam = bpy.data.objects.new("cam", cam_data)
    cam.location = (0, -8, 0)
    cam.rotation_euler = (math.radians(90), 0, 0)
    sc.collection.objects.link(cam)
    sc.camera = cam
    # world: black, a little ambient so the dark body is not a hole
    w = bpy.data.worlds.new("w")
    w.use_nodes = True
    bgn = w.node_tree.nodes["Background"]
    bgn.inputs[0].default_value = (0.02, 0.02, 0.025, 1)
    bgn.inputs[1].default_value = 1.0
    sc.world = w
    # 굴절·반사 광선이 투명 배경으로 빠지면 몸통 가운데가 알파 0 이 된다. 카메라에는 안 보이는
    # 어두운 배경 구가 그 광선을 받는다(측정은 위 Record).
    bpy.ops.mesh.primitive_uv_sphere_add(radius=40, segments=32, ring_count=16)
    bd = bpy.context.active_object
    bd.name = "backdrop"
    bm = bpy.data.materials.new("backdrop")
    bm.use_nodes = True
    bsdf = bm.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (0.012, 0.012, 0.016, 1)
    bsdf.inputs["Roughness"].default_value = 1.0
    bd.data.materials.append(bm)
    bd.visible_shadow = False
    return sc


def add_light(name, loc, energy, color=(1, 1, 1), size=1.5, kind="AREA"):
    ld = bpy.data.lights.new(name, kind)
    ld.energy = energy
    ld.color = color
    if kind == "AREA":
        ld.size = size
        ld.shape = "DISK"
    ob = bpy.data.objects.new(name, ld)
    ob.location = loc
    ob.visible_transmission = False   # 굴절 경로에서 광원 원반이 하얀 얼룩으로 비치지 않게
    bpy.context.scene.collection.objects.link(ob)
    # aim at origin
    d = Vector((0, 0, 0)) - Vector(loc)
    ob.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
    return ob


def lights(hue, state):
    # one sharp highlight (upper left front), one cool fill, one rim from behind in the hue
    add_light("key", (-2.6, -3.0, 3.2), 70, (1, 1, 1), size=0.08)
    fill = add_light("fill", (3.0, -4.0, -0.5), 12, (0.85, 0.9, 1.0), size=1.0)
    fill.visible_glossy = False   # 평면에 두 번째 원반으로 비치지 않게
    rim = mix(hue, (1, 1, 1), 0.35)
    k = 1.6 if state == "selected" else 0.8
    add_light("rim", (1.5, 3.0, 2.0), 50 * k, rim, size=1.2)


# ── materials ─────────────────────────────────────────────────────────────────

def _nodes(mat):
    mat.use_nodes = True
    nt = mat.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    return nt, nt.nodes, nt.links


def glass_material(name, hue, rim, state, rim_k: float = 1.0):
    """Dark tinted body, thin bright rim in the area colour, one highlight (fresnel-mixed)."""
    mat = bpy.data.materials.new(name)
    nt, nodes, links = _nodes(mat)
    out = nodes.new("ShaderNodeOutputMaterial")
    frosted = state == "historical"
    body = nodes.new("ShaderNodeBsdfPrincipled")
    tint = mix(hue, (0.02, 0.025, 0.04), 0.7)
    body.inputs["Base Color"].default_value = (*tint, 1)
    body.inputs["Roughness"].default_value = 0.28 if frosted else 0.04
    body.inputs["IOR"].default_value = 1.45
    body.inputs["Transmission Weight"].default_value = 0.9 if not frosted else 0.6
    body.inputs["Specular IOR Level"].default_value = 0.8
    # rim: layer weight (fresnel) drives an emission in the rim colour
    lw = nodes.new("ShaderNodeLayerWeight")
    lw.inputs["Blend"].default_value = 0.5
    ramp = nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].position = 0.86
    ramp.color_ramp.elements[0].color = (0, 0, 0, 1)
    ramp.color_ramp.elements[1].position = 1.0
    ramp.color_ramp.elements[1].color = (1, 1, 1, 1)
    emis = nodes.new("ShaderNodeEmission")
    emis.inputs["Color"].default_value = (*rim, 1)
    emis.inputs["Strength"].default_value = (1.4 if state == "selected" else 0.6) * (0.5 if frosted else 1.0) * rim_k
    add = nodes.new("ShaderNodeAddShader")
    mul = nodes.new("ShaderNodeMixShader")
    transp = nodes.new("ShaderNodeBsdfTransparent")
    links.new(lw.outputs["Facing"], ramp.inputs["Fac"])
    links.new(ramp.outputs["Color"], mul.inputs["Fac"])
    links.new(transp.outputs[0], mul.inputs[1])
    links.new(emis.outputs[0], mul.inputs[2])
    links.new(body.outputs[0], add.inputs[0])
    links.new(mul.outputs[0], add.inputs[1])
    links.new(add.outputs[0], out.inputs["Surface"])
    return mat


def cloud_material(name, hue, energy, density=1.6, scale=2.0, seed=0.0):
    """Drifting cloud of light inside a sphere: noise-shaped emission volume."""
    mat = bpy.data.materials.new(name)
    nt, nodes, links = _nodes(mat)
    out = nodes.new("ShaderNodeOutputMaterial")
    coord = nodes.new("ShaderNodeTexCoord")
    warp = nodes.new("ShaderNodeTexNoise")
    warp.inputs["Scale"].default_value = scale * 0.85
    warp.inputs["Detail"].default_value = 3.0
    noise = nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = scale
    noise.inputs["Detail"].default_value = 4.0
    noise.inputs["Roughness"].default_value = 0.55
    mapn = nodes.new("ShaderNodeMapping")
    mapn.inputs["Location"].default_value = (seed * 3.0, seed * 1.7, -seed * 2.1)
    links.new(coord.outputs["Object"], mapn.inputs["Vector"])
    links.new(mapn.outputs["Vector"], noise.inputs["Vector"])
    ramp = nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].position = 0.50
    ramp.color_ramp.elements[0].color = (0, 0, 0, 1)
    ramp.color_ramp.elements[1].position = 0.74
    ramp.color_ramp.elements[1].color = (1, 1, 1, 1)
    links.new(noise.outputs["Fac"], ramp.inputs["Fac"])
    # fade toward the shell so the cloud sits inside
    grad = nodes.new("ShaderNodeTexGradient")
    grad.gradient_type = "SPHERICAL"
    links.new(coord.outputs["Object"], grad.inputs["Vector"])
    mulf = nodes.new("ShaderNodeMath")
    mulf.operation = "MULTIPLY"
    links.new(ramp.outputs["Color"], mulf.inputs[0])
    links.new(grad.outputs["Fac"], mulf.inputs[1])
    light = mix(hue, (1, 1, 1), 0.55)
    cramp = nodes.new("ShaderNodeValToRGB")
    cramp.color_ramp.elements[0].position = 0.0
    cramp.color_ramp.elements[0].color = (*mix(hue, (0, 0, 0), 0.3), 1)
    cramp.color_ramp.elements[1].position = 1.0
    cramp.color_ramp.elements[1].color = (*mix(light, (1, 1, 1), 0.2), 1)
    links.new(noise.outputs["Fac"], cramp.inputs["Fac"])
    vol = nodes.new("ShaderNodeVolumePrincipled")
    vol.inputs["Density"].default_value = 0.0
    links.new(cramp.outputs["Color"], vol.inputs["Emission Color"])
    strength = nodes.new("ShaderNodeMath")
    strength.operation = "MULTIPLY"
    strength.inputs[1].default_value = density * (1.7 + energy * 1.3)
    links.new(mulf.outputs[0], strength.inputs[0])
    links.new(strength.outputs[0], vol.inputs["Emission Strength"])
    links.new(vol.outputs[0], out.inputs["Volume"])
    skin = nodes.new("ShaderNodeBsdfTransparent")
    links.new(skin.outputs[0], out.inputs["Surface"])
    return mat


def emission_material(name, color, strength):
    mat = bpy.data.materials.new(name)
    nt, nodes, links = _nodes(mat)
    out = nodes.new("ShaderNodeOutputMaterial")
    e = nodes.new("ShaderNodeEmission")
    e.inputs["Color"].default_value = (*color, 1)
    e.inputs["Strength"].default_value = strength
    links.new(e.outputs[0], out.inputs["Surface"])
    return mat


def crystal_material(name, energy):
    """The decision's cut crystal: warm white glass that glows from the inside."""
    mat = bpy.data.materials.new(name)
    nt, nodes, links = _nodes(mat)
    out = nodes.new("ShaderNodeOutputMaterial")
    g = nodes.new("ShaderNodeBsdfPrincipled")
    g.inputs["Base Color"].default_value = (*WARM_WHITE, 1)
    g.inputs["Roughness"].default_value = 0.02
    g.inputs["IOR"].default_value = 2.1
    g.inputs["Transmission Weight"].default_value = 0.85
    e = nodes.new("ShaderNodeEmission")
    e.inputs["Color"].default_value = (*WARM_WHITE, 1)
    e.inputs["Strength"].default_value = 0.5 + energy * 0.8
    add = nodes.new("ShaderNodeAddShader")
    links.new(g.outputs[0], add.inputs[0])
    links.new(e.outputs[0], add.inputs[1])
    links.new(add.outputs[0], out.inputs["Surface"])
    return mat


def glow_volume_material(name, hue, strength):
    """Area: a glowing glass volume. Fresnel rim dense only at the silhouette, soft core."""
    mat = bpy.data.materials.new(name)
    nt, nodes, links = _nodes(mat)
    out = nodes.new("ShaderNodeOutputMaterial")
    lw = nodes.new("ShaderNodeLayerWeight")
    lw.inputs["Blend"].default_value = 0.85
    pw = nodes.new("ShaderNodeMath")
    pw.operation = "POWER"
    pw.inputs[1].default_value = 3.0
    links.new(lw.outputs["Fresnel"], pw.inputs[0])
    e = nodes.new("ShaderNodeEmission")
    e.inputs["Color"].default_value = (*mix(hue, (1, 1, 1), 0.25), 1)
    links.new(pw.outputs[0], e.inputs["Strength"])
    t = nodes.new("ShaderNodeBsdfTransparent")
    add = nodes.new("ShaderNodeAddShader")
    links.new(e.outputs[0], add.inputs[0])
    links.new(t.outputs[0], add.inputs[1])
    links.new(add.outputs[0], out.inputs["Surface"])
    # core glow as a volume
    coord = nodes.new("ShaderNodeTexCoord")
    grad = nodes.new("ShaderNodeTexGradient")
    grad.gradient_type = "SPHERICAL"
    links.new(coord.outputs["Object"], grad.inputs["Vector"])
    pw2 = nodes.new("ShaderNodeMath")
    pw2.operation = "POWER"
    pw2.inputs[1].default_value = 2.5
    links.new(grad.outputs["Fac"], pw2.inputs[0])
    vol = nodes.new("ShaderNodeVolumePrincipled")
    vol.inputs["Density"].default_value = 0.0
    vol.inputs["Emission Color"].default_value = (*hue, 1)
    sm = nodes.new("ShaderNodeMath")
    sm.operation = "MULTIPLY"
    sm.inputs[1].default_value = strength
    links.new(pw2.outputs[0], sm.inputs[0])
    links.new(sm.outputs[0], vol.inputs["Emission Strength"])
    links.new(vol.outputs[0], out.inputs["Volume"])
    e.inputs["Strength"].default_value = strength * 2.0
    return mat


# ── geometry ──────────────────────────────────────────────────────────────────

def smooth(ob):
    for p in ob.data.polygons:
        p.use_smooth = True


def sphere(name, r, mat, loc=(0, 0, 0)):
    bpy.ops.mesh.primitive_uv_sphere_add(radius=r, segments=96, ring_count=48, location=loc)
    ob = bpy.context.active_object
    ob.name = name
    ob.pass_index = 1
    smooth(ob)
    ob.data.materials.append(mat)
    return ob


def tetra(name, r, mat):
    # regular tetrahedron inscribed in a sphere of radius r (sized to read like the sphere)
    s = r * 1.32
    verts = [Vector(v).normalized() * s for v in ((1, 1, 1), (1, -1, -1), (-1, 1, -1), (-1, -1, 1))]
    faces = [(0, 1, 2), (0, 3, 1), (0, 2, 3), (1, 3, 2)]
    return mesh_from(name, verts, faces, mat)


def octa(name, r, mat):
    s = r * 1.18
    verts = [Vector(v) * s for v in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))]
    faces = [(0, 2, 4), (2, 1, 4), (1, 3, 4), (3, 0, 4), (2, 0, 5), (1, 2, 5), (3, 1, 5), (0, 3, 5)]
    return mesh_from(name, verts, faces, mat)


def shard(name, r, mat):
    # elongated asymmetric bipyramid: long axis about 2.9 times the short ones, apex off centre
    s = r * 0.62
    verts = [Vector((1.85, 0, 0)) * s, Vector((-1.05, 0, 0)) * s,
             Vector((0.15, 0.72, 0)) * s, Vector((0.15, -0.72, 0)) * s,
             Vector((0.15, 0, 0.74)) * s, Vector((0.15, 0, -0.74)) * s]
    faces = [(0, 2, 4), (0, 4, 3), (0, 3, 5), (0, 5, 2), (1, 4, 2), (1, 3, 4), (1, 5, 3), (1, 2, 5)]
    return mesh_from(name, verts, faces, mat)


def mesh_from(name, verts, faces, mat):
    me = bpy.data.meshes.new(name)
    me.from_pydata([tuple(v) for v in verts], [], faces)
    me.update()
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    ob.pass_index = 1
    ob.data.materials.append(mat)
    bevel = ob.modifiers.new("edge", "BEVEL")
    bevel.width = 0.012
    bevel.segments = 3
    return ob


def lock_tilt(ob, kind):
    # camera-locked tilts from the shader's polyLock, so stills match the app
    if kind == "record":
        ob.rotation_euler = (0.62, 0.75, 0.0)
    elif kind == "episode":
        ob.rotation_euler = (0.45, 0.62, 0.0)
    else:
        ob.rotation_euler = (0.55, 0.35, 0.38)


def dotted_shell(name, r, mat, rings=9, dot=0.04):
    """Topic: a hollow lat/lon lattice of small dots."""
    me = bpy.data.meshes.new(name)
    verts = []
    for i in range(rings + 1):
        lat = -math.pi / 2 + math.pi * i / rings
        cnt = max(1, int(math.cos(lat) * rings * 2 + 0.5))
        for j in range(cnt):
            lon = -math.pi + 2 * math.pi * (j + 0.5) / cnt
            verts.append((r * math.cos(lat) * math.cos(lon), r * math.cos(lat) * math.sin(lon), r * math.sin(lat)))
    import bmesh
    bm = bmesh.new()
    for v in verts:
        bmesh.ops.create_icosphere(bm, subdivisions=2, radius=dot, matrix=__import__("mathutils").Matrix.Translation(v))
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    ob.pass_index = 1
    smooth(ob)
    ob.data.materials.append(mat)
    return ob


def halo(name, r, color, strength):
    """Soft under-glow: a disc behind the node with a radial falloff, never in front of the glass."""
    mat = bpy.data.materials.new(name)
    nt, nodes, links = _nodes(mat)
    out = nodes.new("ShaderNodeOutputMaterial")
    coord = nodes.new("ShaderNodeTexCoord")
    grad = nodes.new("ShaderNodeTexGradient")
    grad.gradient_type = "SPHERICAL"
    links.new(coord.outputs["Object"], grad.inputs["Vector"])
    pw = nodes.new("ShaderNodeMath")
    pw.operation = "POWER"
    pw.inputs[1].default_value = 2.2
    links.new(grad.outputs["Fac"], pw.inputs[0])
    e = nodes.new("ShaderNodeEmission")
    e.inputs["Color"].default_value = (*color, 1)
    e.inputs["Strength"].default_value = strength
    t = nodes.new("ShaderNodeBsdfTransparent")
    lp = nodes.new("ShaderNodeLightPath")
    cam = nodes.new("ShaderNodeMath")
    cam.operation = "MULTIPLY"
    links.new(pw.outputs[0], cam.inputs[0])
    links.new(lp.outputs["Is Camera Ray"], cam.inputs[1])
    mixs = nodes.new("ShaderNodeMixShader")
    links.new(cam.outputs[0], mixs.inputs["Fac"])
    links.new(t.outputs[0], mixs.inputs[1])
    links.new(e.outputs[0], mixs.inputs[2])
    links.new(mixs.outputs[0], out.inputs["Surface"])
    mat.blend_method = "BLEND"
    bpy.ops.mesh.primitive_plane_add(size=2.0, location=(0, 1.6, 0), rotation=(math.radians(90), 0, 0))
    ob = bpy.context.active_object
    ob.name = name
    ob.scale = (r, r, r)
    ob.pass_index = 0
    ob.data.materials.append(mat)
    ob.visible_shadow = False
    ob.visible_glossy = False
    ob.visible_transmission = False
    ob.visible_diffuse = False
    return ob


def ring(name, r, color):
    """Selected: a thin white ring in the view plane."""
    bpy.ops.mesh.primitive_torus_add(major_radius=r, minor_radius=0.012, major_segments=128, minor_segments=12,
                                     rotation=(math.radians(90), 0, 0))
    ob = bpy.context.active_object
    ob.name = name
    smooth(ob)
    ob.data.materials.append(emission_material(name + "-m", color, 6.0))
    return ob


# ── one orb ───────────────────────────────────────────────────────────────────

def build(kind: str, state: str, hue, theme: str):
    r = 1.0
    energy = {"idle": 0.0, "active": 0.9, "selected": 0.5, "historical": 0.0, "conflict": 0.6}[state]
    if state == "conflict":
        hue = CONFLICT_RED
    if state == "historical":
        hue = desat(hue, 0.65)
    rim = mix(hue, (1, 1, 1), 0.35)
    lights(hue, state)
    halo_k = {"idle": 0.5, "active": 1.6, "selected": 1.0, "historical": 0.18, "conflict": 1.9}[state]
    if kind != "area":
        halo("halo", r * 1.6, hue, halo_k)
    seed = (KINDS.index(kind) + 1) * 0.37

    if kind in ("concept", "decision"):
        sphere("shell", r, glass_material("glass", hue, rim, state))
        if kind == "concept":
            inner = sphere("cloud", r * 0.9, cloud_material("cloud", hue, energy, seed=seed))
        else:
            bpy.ops.mesh.primitive_ico_sphere_add(radius=r * 0.40, subdivisions=1)
            inner = bpy.context.active_object
            inner.name = "crystal"
            inner.rotation_euler = (0.4, 0.2, 0.6)
            inner.data.materials.append(crystal_material("crystal", energy))
            sphere("crystal-haze", r * 0.9, cloud_material("haze", hue, energy, density=0.35, scale=2.5, seed=seed))
    elif kind in ("record", "episode", "bridge"):
        g = glass_material("glass", hue, rim, state, rim_k=0.25)
        ob = {"record": tetra, "episode": octa, "bridge": shard}[kind]("shell", r, g)
        lock_tilt(ob, kind)
        smooth(ob)
        if kind == "bridge":
            # a line of light along the long axis, with a pulse
            bpy.ops.mesh.primitive_cylinder_add(radius=r * 0.045, depth=r * 1.7, rotation=(0, math.radians(90), 0))
            line = bpy.context.active_object
            line.name = "line"
            line.data.materials.append(emission_material("line", mix(hue, (1, 1, 1), 0.55), 2.5 * (1 + energy)))
            line.parent = ob
            pulse = sphere("pulse", r * 0.09, emission_material("pulse", (1, 1, 1), 5.0), loc=(r * 0.55, 0, 0))
            pulse.parent = ob
        else:
            core_r = r * (0.16 if kind == "record" else 0.24)
            # 0.6 을 넘기면 꼭짓점 근처 면이 굴절로 핵의 상을 여러 개 비춰 흰 얼룩이 된다
            core = sphere("core", core_r, emission_material("core", mix(hue, (1, 1, 1), 0.55), 0.6 * (1 + energy)))
            core.parent = ob
            cloud = sphere("cloud", r * (0.40 if kind == "record" else 0.62), cloud_material("cloud", hue, energy, density=1.4, scale=3.0, seed=seed))
            cloud.parent = ob
    elif kind == "topic":
        dotted_shell("shell", r, emission_material("dot", mix(hue, (1, 1, 1), 0.08), 1.1 + energy * 0.8))
    elif kind == "area":
        sphere("volume", r * 1.15, glow_volume_material("area", hue, 1.2 + energy))

    if state == "selected":
        ring("ring", r * 1.22, (1, 1, 1))
    if state == "historical" and kind not in ("topic", "area"):
        # frosted: a faint milky veil inside. 다면체보다 큰 구를 두면 모양 둘레에 검은 원반이 생긴다
        # 껍질 안에 들어가는 반지름만 쓴다.
        fr = {"concept": 0.98, "decision": 0.98, "record": 0.40, "episode": 0.62, "bridge": 0.30}[kind]
        frost = sphere("frost", r * fr, cloud_material("frost", (0.8, 0.8, 0.85), 0.0, density=0.25 / fr, scale=1.2, seed=seed))
        if kind in ("record", "episode", "bridge"):
            frost.parent = bpy.data.objects["shell"]
    return hue


# ── two renders, one file ─────────────────────────────────────────────────────
#
# 투명 필름에서는 유리를 지난 굴절 광선이 세계로 빠지며 알파를 0 으로 만든다(측정은 위 Record:
# 몸통 가운데 알파 0, 가장자리 27). 그래서 색은 어두운 배경 구 앞에서 한 번 렌더하고(A),
# 알파는 흰 재질로 덮어 그린 덮임 마스크 C(유리 안은 1)와 배경 구를 뺀 투명 렌더 B 의 알파(후광의
# 부드러운 가장자리)를 합쳐 만든다. B 는 후광만 보면 되므로 샘플이 적다.

def _read(path: Path):
    import numpy as np
    im = bpy.data.images.load(str(path))
    w, h = im.size
    buf = np.empty(w * h * 4, dtype=np.float32)
    im.pixels.foreach_get(buf)
    bpy.data.images.remove(im)
    return buf.reshape(h, w, 4)


def render_composite(sc, path: Path, samples: int, hue):
    import numpy as np
    tmp = path.parent / ".tmp"
    tmp.mkdir(exist_ok=True)
    # A: colour, against the dark backdrop
    sc.render.film_transparent = False
    bpy.data.objects["backdrop"].visible_camera = True
    sc.render.filepath = str(tmp / "colour.png")
    bpy.ops.render.render(write_still=True)
    # B: transparent, halo alpha only (the glass alpha here is wrong, see above)
    sc.render.film_transparent = True
    bpy.data.objects["backdrop"].visible_camera = False
    sc.cycles.samples = max(16, samples // 8)
    sc.cycles.use_denoising = False
    sc.render.filepath = str(tmp / "alpha.png")
    bpy.ops.render.render(write_still=True)
    # C: coverage mask of the solids: every object drawn flat white, halo and backdrop hidden
    vl = sc.view_layers[0]
    white = emission_material("mask-white", (1, 1, 1), 1.0)
    vl.material_override = white
    hidden = []
    for ob in sc.objects:
        if ob.type == "MESH" and ob.pass_index == 0:
            hidden.append(ob)
            ob.hide_render = True
    sc.cycles.samples = 16
    sc.render.filepath = str(tmp / "mask.png")
    bpy.ops.render.render(write_still=True)
    vl.material_override = None
    for ob in hidden:
        ob.hide_render = False
    sc.cycles.samples = samples
    sc.cycles.use_denoising = True
    A = _read(tmp / "colour.png")
    B = _read(tmp / "alpha.png")
    M = _read(tmp / "mask.png")[..., 3]
    alpha = np.maximum(M, B[..., 3])
    # A 는 거의 검은 배경 위의 값이라 전곱 알파에 가깝다. 바깥(후광)은 알파로 나눠 직선 알파로 돌린다.
    k = np.clip(M * 2.0, 0.0, 1.0)[..., None]      # 작은 점·가장자리는 마스크가 반쯤이라 섞는다
    halo_srgb = np.array(linear_to_srgb(mix(hue, (1, 1, 1), 0.2)), dtype=np.float32)
    rgb = A[..., :3] * k + halo_srgb * (1.0 - k)
    rgb[alpha <= 0.002] = 0.0
    outp = np.dstack([rgb, alpha]).astype(np.float32)
    h, w = alpha.shape
    # 읽은 값은 PNG 의 sRGB 부호 그대로다(바이트 이미지의 pixels). 쓰는 쪽도 바이트 이미지라야 두 번 부호화되지 않는다.
    im = bpy.data.images.new("out", w, h, alpha=True, float_buffer=False)
    im.pixels.foreach_set(outp.ravel())
    im.file_format = "PNG"
    im.filepath_raw = str(path)
    im.save()
    bpy.data.images.remove(im)
    if not os.environ.get("ORBS_KEEP_TMP"):
        for f in tmp.iterdir():
            f.unlink()


# ── main ──────────────────────────────────────────────────────────────────────

def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(REPO / "scripts" / "design" / "assets" / "orbs"))
    ap.add_argument("--size", type=int, default=1024)
    ap.add_argument("--samples", type=int, default=256)
    ap.add_argument("--kinds", default=",".join(KINDS))
    ap.add_argument("--states", default=",".join(STATES))
    ap.add_argument("--theme", default="dark", choices=["dark", "light", "both"])
    ap.add_argument("--jewel", type=int, default=1, help="index into topicJewels (0 ruby, 1 sapphire, 2 emerald, 3 amber, 4 amethyst)")
    a = ap.parse_args(argv)
    pal = load_palette()
    themes = ["dark", "light"] if a.theme == "both" else [a.theme]
    kinds = [k for k in a.kinds.split(",") if k]
    states = [s for s in a.states.split(",") if s]
    for k in kinds:
        if k not in KINDS:
            raise SystemExit(f"unknown kind {k}")
    for s in states:
        if s not in STATES:
            raise SystemExit(f"unknown state {s}")
    out = Path(a.out)
    n = 0
    for theme in themes:
        hue = pal["jewels"][theme][a.jewel % 5]
        (out / theme).mkdir(parents=True, exist_ok=True)
        for kind in kinds:
            for state in (states if kind != "area" else ["idle"]):
                sc = reset_scene(a.size, a.samples)
                if theme == "light":
                    sc.world.node_tree.nodes["Background"].inputs[0].default_value = (0.35, 0.35, 0.36, 1)
                final_hue = build(kind, state, hue, theme)
                path = out / theme / f"{kind}-{state}.png"
                render_composite(sc, path, a.samples, final_hue)
                print(f"[orbs] wrote {path}")
                n += 1
    print(f"[orbs] {n} images")
    return 0


if __name__ == "__main__":
    args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    raise SystemExit(main(args))
