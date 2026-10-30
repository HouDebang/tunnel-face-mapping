# -*- coding: utf-8 -*-
"""
build_robot.py
==============================================================================
Tracked Photogrammetry Robot  (TPR-900)
Procedural Blender asset builder.

Run (headless):
    blender -b -P build_robot.py
    blender -b -P build_robot.py -- --no-render
    blender -b -P build_robot.py -- --quick

Coordinate system (robot local / world):
    +X = robot right,  +Y = robot forward,  +Z = up,  ground = Z 0.
All design values are millimetres.  The Blender scene uses metres
(1 Blender unit = 1 m, unit scale 1.0), conversion happens in M().

The script creates three scenes:
    01_Robot_Asset      clean editable asset, origin at robot centre
    02_Product_Studio   circular infinity cove, 6 product cameras
    03_Tunnel_Preview   tunnel context scene with linked robot
==============================================================================
"""

import bpy
import bmesh
import math
import os
import sys
import time
from mathutils import Vector, Matrix, Euler, Quaternion

# ----------------------------------------------------------------------------
# paths
# ----------------------------------------------------------------------------
try:
    ROOT = os.path.dirname(os.path.abspath(__file__))
except NameError:
    ROOT = os.getcwd()

PREVIEW_DIR = os.path.join(ROOT, "Preview")
os.makedirs(PREVIEW_DIR, exist_ok=True)

BLEND_PATH = os.path.join(ROOT, "Tracked_Photogrammetry_Robot.blend")
GLB_PATH = os.path.join(ROOT, "Tracked_Photogrammetry_Robot.glb")
FBX_PATH = os.path.join(ROOT, "Tracked_Photogrammetry_Robot.fbx")

MM = 0.001


def M(v):
    """millimetres -> metres"""
    return v * MM


def V(x, y, z):
    """millimetre vector -> metre Vector"""
    return Vector((x * MM, y * MM, z * MM))


# ----------------------------------------------------------------------------
# DESIGN PARAMETERS  (all millimetres)  -- edit these to re-proportion the robot
# ----------------------------------------------------------------------------
P = {
    # ---- running gear -----------------------------------------------------
    "track_len": 900,          # overall length incl. grousers
    "track_w": 172,            # rubber belt width
    "track_band_h": 200,       # belt band height (grousers add 10 top+bottom)
    "belt_t": 30,              # belt thickness
    "belt_center_r": 85,       # belt centreline radius at ends
    "wheel_r": 70,             # drive / idler wheel radius (belt inner surface)
    "wheel_cy": 340,           # drive / idler centre Y (+ front, - rear)
    "wheel_cz": 110,           # wheel centre height
    "wheel_w": 120,            # wheel width
    "track_cx": 300,           # track centre X (+ left/right)
    "track_inner_x": 214,      # inner belt face
    "track_outer_x": 386,      # outer belt face
    "grouser_h": 10,           # grouser radial height
    "grouser_spacing": 46,     # along the belt
    "road_r": 38,
    "road_w": 58,
    "road_cz": 78,
    "road_ys": (-220, -110, 0, 110, 220),
    "return_roller_r": 20,
    "return_roller_ys": (-120, 120),

    # ---- chassis ----------------------------------------------------------
    "low_w": 400,
    "low_l": 760,
    "low_z0": 60,
    "low_z1": 250,
    "up_w": 520,
    "up_l": 660,
    "up_z0": 250,
    "up_z1": 470,
    "plat_w": 600,
    "plat_l": 640,
    "plat_z0": 470,
    "plat_z1": 484,

    # ---- camera support ---------------------------------------------------
    "pillar_x": 235,
    "pillar_y": 120,
    "pillar_low": 100,
    "pillar_up": 82,
    "pillar_z0": 484,
    "pillar_z1": 1240,
    "pillar_split_z": 900,
    "beam_x": 330,
    "beam_y0": 60,
    "beam_y1": 180,
    "beam_z0": 1240,
    "beam_z1": 1340,

    # ---- photogrammetry head ---------------------------------------------
    "cam_z": 1290,
    "cam_x": 215,
    "cam_w": 150,
    "cam_h": 120,
    "cam_d": 160,
    "cam_mount_y": 180,
    "cam_yaw": 20.0,           # side camera outward yaw (deg)
    "led_x": 300,
    "led_y0": 70,
    "led_y1": 170,
    "led_z0": 1360,
    "led_z1": 1400,

    # ---- materials / render ----------------------------------------------
    "render_w": 1920,
    "render_h": 1440,
    "samples": 128,
}

# ============================================================================
#  MESH BUILDER
# ============================================================================


class MB:
    """Small bmesh helper that accumulates primitives with material slots.

    All primitive sizes/locations are given in millimetres and converted to
    metres with ``scale`` (default MM).  Scenes modelled directly in metres
    pass scale=1.0.
    """

    def __init__(self, name, scale=MM):
        self.name = name
        self.bm = bmesh.new()
        self.mats = []
        self.s = scale

    # -- material -----------------------------------------------------------
    def _mi(self, mat):
        if mat is None:
            return 0
        if mat not in self.mats:
            self.mats.append(mat)
        return self.mats.index(mat)

    def _finish_piece(self, before, mat):
        faces = [f for f in self.bm.faces if f not in before]
        idx = self._mi(mat)
        for f in faces:
            f.material_index = idx
        return faces

    # -- primitives ---------------------------------------------------------
    def add_cube(self, size, loc=(0, 0, 0), rot=None, bevel=0.0, seg=2, mat=None):
        before = set(self.bm.faces)
        ret = bmesh.ops.create_cube(self.bm, size=1.0)
        vs = ret["verts"]
        sx, sy, sz = [v * self.s for v in size]
        m4 = Matrix.Translation(Vector(loc) * self.s)
        if rot is not None:
            if hasattr(rot, "to_matrix"):
                m4 = m4 @ rot.to_matrix().to_4x4()
            else:
                m4 = m4 @ rot
        m4 = m4 @ Matrix.Diagonal((sx, sy, sz, 1.0))
        bmesh.ops.transform(self.bm, matrix=m4, verts=vs)
        if bevel > 0:
            edges = set()
            for v in vs:
                edges.update(v.link_edges)
            bmesh.ops.bevel(self.bm, geom=list(vs) + list(edges),
                            offset=bevel * self.s, offset_type="OFFSET",
                            segments=seg, profile=0.5, affect="EDGES",
                            clamp_overlap=True)
        self._finish_piece(before, mat)
        return vs

    def add_cyl(self, r, depth, loc=(0, 0, 0), axis="Z", seg=32,
                bevel=0.0, mat=None, r2=None):
        before = set(self.bm.faces)
        ret = bmesh.ops.create_cone(
            self.bm, cap_ends=True, cap_tris=False, segments=seg,
            radius1=r * self.s, radius2=(r if r2 is None else r2) * self.s,
            depth=depth * self.s)
        vs = ret["verts"]
        rot = Matrix.Identity(4)
        if axis == "X":
            rot = Matrix.Rotation(math.radians(90), 4, "Y")
        elif axis == "Y":
            rot = Matrix.Rotation(math.radians(90), 4, "X")
        m4 = Matrix.Translation(Vector(loc) * self.s) @ rot
        bmesh.ops.transform(self.bm, matrix=m4, verts=vs)
        if bevel > 0:
            edges = set()
            for v in vs:
                edges.update(v.link_edges)
            bmesh.ops.bevel(self.bm, geom=list(vs) + list(edges),
                            offset=bevel * self.s, offset_type="OFFSET",
                            segments=2, profile=0.5, affect="EDGES",
                            clamp_overlap=True)
        self._finish_piece(before, mat)
        return vs

    def add_ring(self, r_out, r_in, depth, loc=(0, 0, 0), axis="Z",
                 seg=48, mat=None, bevel=0.0):
        """Hollow cylindrical ring / washer / lens hood."""
        before = set(self.bm.faces)
        bm = self.bm
        r_out *= self.s
        r_in *= self.s
        depth *= self.s
        vs = []
        for lz in (-1, 1):
            for i in range(seg):
                a = 2.0 * math.pi * i / seg
                for rr in (r_out, r_in):
                    vs.append(bm.verts.new((rr * math.cos(a),
                                            rr * math.sin(a),
                                            lz * depth * 0.5)))
        for i in range(seg):
            j = (i + 1) % seg
            o0 = i * 2
            i0 = i * 2 + 1
            o1 = j * 2
            i1 = j * 2 + 1
            top = seg * 2
            # outer wall
            bm.faces.new((vs[o0], vs[o1], vs[top + o1], vs[top + o0]))
            # inner wall
            bm.faces.new((vs[i0], vs[top + i0], vs[top + i1], vs[i1]))
            # bottom annulus
            bm.faces.new((vs[o0], vs[i0], vs[i1], vs[o1]))
            # top annulus
            bm.faces.new((vs[top + o0], vs[top + o1], vs[top + i1], vs[top + i0]))
        rot = Matrix.Identity(4)
        if axis == "X":
            rot = Matrix.Rotation(math.radians(90), 4, "Y")
        elif axis == "Y":
            rot = Matrix.Rotation(math.radians(90), 4, "X")
        bmesh.ops.transform(bm, matrix=Matrix.Translation(Vector(loc) * self.s) @ rot,
                            verts=vs)
        if bevel > 0:
            edges = set()
            for v in vs:
                edges.update(v.link_edges)
            bmesh.ops.bevel(bm, geom=list(vs) + list(edges),
                            offset=bevel * self.s,
                            offset_type="OFFSET", segments=2, profile=0.5,
                            affect="EDGES", clamp_overlap=True)
        self._finish_piece(before, mat)

    def add_raw(self, verts, faces, mat=None, matrix=None):
        before = set(self.bm.faces)
        bm = self.bm
        if self.s != 1.0:
            verts = [(x * self.s, y * self.s, z * self.s) for x, y, z in verts]
        nv = [bm.verts.new(v) for v in verts]
        for f in faces:
            try:
                bm.faces.new([nv[i] for i in f])
            except ValueError:
                pass
        if matrix is not None:
            bmesh.ops.transform(bm, matrix=matrix, verts=nv)
        self._finish_piece(before, mat)

    # -- output -------------------------------------------------------------
    def finish(self, collection, name=None, default_mat=None):
        bmesh.ops.recalc_face_normals(self.bm, faces=self.bm.faces)
        me = bpy.data.meshes.new(name or self.name)
        self.bm.to_mesh(me)
        self.bm.free()
        if default_mat is not None and default_mat not in self.mats:
            self.mats.insert(0, default_mat)
        for m in self.mats:
            me.materials.append(m)
        for p in me.polygons:
            p.use_smooth = True
        ob = bpy.data.objects.new(name or self.name, me)
        collection.objects.link(ob)
        return ob


def set_parent_keep(child, parent):
    bpy.context.view_layer.update()
    mw = child.matrix_world.copy()
    child.parent = parent
    child.matrix_world = mw


def new_empty(name, loc_mm, collection, parent=None, size=0.08,
              display="PLAIN_AXES"):
    ob = bpy.data.objects.new(name, None)
    collection.objects.link(ob)
    ob.empty_display_type = display
    ob.empty_display_size = size
    ob.location = V(*loc_mm)
    if parent is not None:
        set_parent_keep(ob, parent)
    return ob


def place(ob, loc_mm=(0, 0, 0), rot=(0, 0, 0), parent=None):
    """Set object transform from mm / degrees, optionally parent-keep."""
    ob.location = V(*loc_mm)
    ob.rotation_euler = Euler([math.radians(a) for a in rot], "XYZ")
    if parent is not None:
        set_parent_keep(ob, parent)
    return ob


def shade_smooth(ob, angle=35.0):
    try:
        with bpy.context.temp_override(object=ob, active_object=ob,
                                       selected_objects=[ob],
                                       selected_editable_objects=[ob]):
            bpy.ops.object.shade_smooth_by_angle(angle=math.radians(angle))
    except Exception:
        for p in ob.data.polygons:
            p.use_smooth = True


def apply_modifiers(ob):
    dg = bpy.context.evaluated_depsgraph_get()
    me = bpy.data.meshes.new_from_object(ob.evaluated_get(dg))
    old = ob.data
    ob.modifiers.clear()
    ob.data = me
    if old.users == 0:
        bpy.data.meshes.remove(old)


def boolean_cut(ob, cutter, name=None):
    mod = ob.modifiers.new(name or "cut", "BOOLEAN")
    mod.operation = "DIFFERENCE"
    mod.object = cutter
    mod.solver = "EXACT"
    apply_modifiers(ob)
    bpy.data.objects.remove(cutter, do_unlink=True)


def join_objects(objs, name, collection=None):
    objs = [o for o in objs if o is not None]
    if not objs:
        return None
    if len(objs) == 1:
        objs[0].name = name
        return objs[0]
    with bpy.context.temp_override(active_object=objs[0], object=objs[0],
                                   selected_objects=objs,
                                   selected_editable_objects=objs):
        bpy.ops.object.join()
    ob = objs[0]
    ob.name = name
    return ob


# ============================================================================
#  MATERIALS
# ============================================================================


def _set(bsdf, key, value):
    if key in bsdf.inputs:
        bsdf.inputs[key].default_value = value


def make_mat(name, base, metallic=0.0, roughness=0.5, coat=0.0,
             coat_rough=0.08, transmission=0.0, ior=1.45,
             emission=None, emission_strength=0.0, bump=None,
             coat_tint=None):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    _set(bsdf, "Base Color", (*base, 1.0))
    _set(bsdf, "Metallic", metallic)
    _set(bsdf, "Roughness", roughness)
    _set(bsdf, "Coat Weight", coat)
    _set(bsdf, "Coat Roughness", coat_rough)
    _set(bsdf, "Transmission Weight", transmission)
    _set(bsdf, "IOR", ior)
    if coat_tint is not None:
        _set(bsdf, "Coat Tint", (*coat_tint, 1.0))
    if emission is not None:
        _set(bsdf, "Emission Color", (*emission, 1.0))
        _set(bsdf, "Emission Strength", emission_strength)
    if bump is not None:
        scale, strength, detail = bump
        tex = nt.nodes.new("ShaderNodeTexNoise")
        tex.location = (-520, -220)
        tex.inputs["Scale"].default_value = scale
        tex.inputs["Detail"].default_value = detail
        tex.inputs["Roughness"].default_value = 0.55
        bmp = nt.nodes.new("ShaderNodeBump")
        bmp.location = (-260, -220)
        bmp.inputs["Strength"].default_value = strength
        bmp.inputs["Distance"].default_value = 0.0025
        nt.links.new(tex.outputs["Fac"], bmp.inputs["Height"])
        nt.links.new(bmp.outputs["Normal"], bsdf.inputs["Normal"])
    m.diffuse_color = (*base, 1.0)
    return m


def make_rock_mat(name, base_a, base_b, scale=6.0, rough=0.95):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    tex = nt.nodes.new("ShaderNodeTexNoise")
    tex.location = (-700, 200)
    tex.inputs["Scale"].default_value = scale
    tex.inputs["Detail"].default_value = 12.0
    tex.inputs["Roughness"].default_value = 0.72
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    ramp.location = (-430, 220)
    ramp.color_ramp.elements[0].position = 0.30
    ramp.color_ramp.elements[0].color = (*base_a, 1.0)
    ramp.color_ramp.elements[1].position = 0.72
    ramp.color_ramp.elements[1].color = (*base_b, 1.0)
    nt.links.new(tex.outputs["Fac"], ramp.inputs["Fac"])
    nt.links.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
    _set(bsdf, "Roughness", rough)
    _set(bsdf, "Metallic", 0.0)
    # micro bump
    tex2 = nt.nodes.new("ShaderNodeTexNoise")
    tex2.location = (-700, -220)
    tex2.inputs["Scale"].default_value = 55.0
    tex2.inputs["Detail"].default_value = 10.0
    bmp = nt.nodes.new("ShaderNodeBump")
    bmp.location = (-300, -220)
    bmp.inputs["Strength"].default_value = 0.55
    bmp.inputs["Distance"].default_value = 0.01
    nt.links.new(tex2.outputs["Fac"], bmp.inputs["Height"])
    nt.links.new(bmp.outputs["Normal"], bsdf.inputs["Normal"])
    m.diffuse_color = (*base_a, 1.0)
    return m


def build_materials():
    mats = {}
    mats["graphite"] = make_mat(
        "MAT_Body_Graphite", (0.075, 0.080, 0.088), metallic=0.32,
        roughness=0.40, coat=0.22, coat_rough=0.18,
        bump=(90.0, 0.06, 4.0))
    mats["graphite_dark"] = make_mat(
        "MAT_Body_Graphite_Dark", (0.034, 0.036, 0.041), metallic=0.25,
        roughness=0.52, coat=0.10)
    mats["panel"] = make_mat(
        "MAT_Panel_Graphite", (0.105, 0.110, 0.120), metallic=0.15,
        roughness=0.55, bump=(140.0, 0.05, 4.0))
    mats["yellow"] = make_mat(
        "MAT_Engineering_Yellow", (0.780, 0.470, 0.018), metallic=0.05,
        roughness=0.38, coat=0.25, coat_rough=0.12)
    mats["aluminium"] = make_mat(
        "MAT_Aluminium", (0.520, 0.545, 0.575), metallic=1.0,
        roughness=0.33, bump=(160.0, 0.035, 3.0))
    mats["aluminium_dark"] = make_mat(
        "MAT_Aluminium_Dark", (0.235, 0.250, 0.270), metallic=1.0,
        roughness=0.38, bump=(140.0, 0.05, 3.0))
    mats["steel"] = make_mat(
        "MAT_Steel_Dark", (0.300, 0.310, 0.330), metallic=1.0,
        roughness=0.30)
    mats["black_anod"] = make_mat(
        "MAT_Black_Anodized", (0.018, 0.019, 0.021), metallic=0.85,
        roughness=0.34, bump=(220.0, 0.03, 3.0))
    mats["rubber"] = make_mat(
        "MAT_Rubber_Track", (0.017, 0.017, 0.019), metallic=0.0,
        roughness=0.88, bump=(55.0, 0.30, 8.0))
    mats["rubber_soft"] = make_mat(
        "MAT_Rubber_Bumper", (0.022, 0.022, 0.024), metallic=0.0,
        roughness=0.95, bump=(40.0, 0.22, 6.0))
    mats["camera"] = make_mat(
        "MAT_Camera_Housing", (0.055, 0.060, 0.068), metallic=0.35,
        roughness=0.36, coat=0.25, coat_rough=0.15,
        bump=(120.0, 0.04, 3.0))
    mats["camera_front"] = make_mat(
        "MAT_Camera_FrontPlate", (0.022, 0.024, 0.028), metallic=0.55,
        roughness=0.28)
    mats["lens_glass"] = make_mat(
        "MAT_Lens_Glass", (0.016, 0.020, 0.058), metallic=0.10,
        roughness=0.025, transmission=0.55, ior=1.58, coat=1.0,
        coat_rough=0.02, coat_tint=(0.42, 0.38, 1.0))
    mats["lens_inner"] = make_mat(
        "MAT_Lens_Inner", (0.004, 0.004, 0.006), metallic=0.0,
        roughness=0.55)
    mats["led"] = make_mat(
        "MAT_LED_Emissive", (1.0, 0.96, 0.88), metallic=0.0,
        roughness=0.25, emission=(1.0, 0.93, 0.80), emission_strength=9.0)
    mats["led_diffuser"] = make_mat(
        "MAT_LED_Diffuser", (0.82, 0.83, 0.80), metallic=0.0,
        roughness=0.45, emission=(1.0, 0.94, 0.82), emission_strength=2.2)
    mats["ind_green"] = make_mat(
        "MAT_Indicator_Green", (0.05, 0.5, 0.12), metallic=0.0,
        roughness=0.2, emission=(0.10, 1.0, 0.28), emission_strength=7.0)
    mats["ind_amber"] = make_mat(
        "MAT_Indicator_Amber", (0.6, 0.28, 0.01), metallic=0.0,
        roughness=0.2, emission=(1.0, 0.46, 0.02), emission_strength=7.0)
    mats["ind_red"] = make_mat(
        "MAT_Indicator_Red", (0.5, 0.02, 0.01), metallic=0.0,
        roughness=0.25, emission=(1.0, 0.03, 0.01), emission_strength=5.0)
    mats["screen"] = make_mat(
        "MAT_Display_Screen", (0.008, 0.012, 0.014), metallic=0.0,
        roughness=0.12, emission=(0.05, 0.30, 0.34), emission_strength=1.4,
        coat=0.8, coat_rough=0.05)
    mats["cable"] = make_mat(
        "MAT_Cable_Black", (0.014, 0.014, 0.016), metallic=0.0,
        roughness=0.62)
    mats["cable_sleeve"] = make_mat(
        "MAT_Cable_Sleeve", (0.020, 0.020, 0.022), metallic=0.0,
        roughness=0.78, bump=(160.0, 0.18, 4.0))
    mats["label_white"] = make_mat(
        "MAT_Label_White", (0.780, 0.780, 0.760), metallic=0.0,
        roughness=0.45)
    mats["label_yellow"] = make_mat(
        "MAT_Label_Yellow", (0.850, 0.560, 0.020), metallic=0.0,
        roughness=0.42)
    mats["label_dark"] = make_mat(
        "MAT_Label_Plate", (0.030, 0.032, 0.035), metallic=0.6,
        roughness=0.38)
    mats["studio"] = make_mat(
        "MAT_Studio_Cove", (0.160, 0.163, 0.170), metallic=0.0,
        roughness=0.66)
    mats["studio_floor"] = make_mat(
        "MAT_Studio_Floor", (0.130, 0.132, 0.138), metallic=0.0,
        roughness=0.58)
    mats["rock"] = make_rock_mat(
        "MAT_Tunnel_Rock", (0.045, 0.040, 0.035), (0.115, 0.105, 0.090),
        scale=4.0)
    mats["rock_face"] = make_rock_mat(
        "MAT_Tunnel_Face_Rock", (0.070, 0.060, 0.048), (0.170, 0.150, 0.120),
        scale=2.2)
    mats["concrete"] = make_mat(
        "MAT_Tunnel_Concrete", (0.150, 0.148, 0.140), metallic=0.0,
        roughness=0.88, bump=(45.0, 0.20, 6.0))
    mats["tunnel_floor"] = make_mat(
        "MAT_Tunnel_Floor", (0.075, 0.074, 0.070), metallic=0.0,
        roughness=0.80, bump=(28.0, 0.18, 6.0))
    mats["work_light"] = make_mat(
        "MAT_Work_Light", (1.0, 0.85, 0.65), metallic=0.0,
        roughness=0.3, emission=(1.0, 0.72, 0.40), emission_strength=35.0)
    return mats

# ============================================================================
#  SMALL DETAIL HELPERS
# ============================================================================


def add_bolt(mb, loc, axis="Z", r=5.0, h=4.5, mat=None, washer_mat=None,
             sign=1):
    """Hex bolt head sitting on a surface. loc = surface point."""
    d = {"X": (1, 0, 0), "Y": (0, 1, 0), "Z": (0, 0, 1)}[axis]
    d = (d[0] * sign, d[1] * sign, d[2] * sign)
    wl = (loc[0] + d[0] * 0.8, loc[1] + d[1] * 0.8, loc[2] + d[2] * 0.8)
    hl = (loc[0] + d[0] * (1.6 + h * 0.5),
          loc[1] + d[1] * (1.6 + h * 0.5),
          loc[2] + d[2] * (1.6 + h * 0.5))
    mb.add_cyl(r * 1.55, 1.6, loc=wl, axis=axis, seg=20,
               mat=washer_mat, bevel=0.3)
    mb.add_cyl(r, h, loc=hl, axis=axis, seg=6, mat=mat, bevel=0.4)


def add_bolt_grid(mb, xs, ys, z, axis="Z", r=5.0, h=4.5, mat=None):
    for x in xs:
        for y in ys:
            add_bolt(mb, (x, y, z), axis=axis, r=r, h=h, mat=mat)


def add_text(text, size_mm, loc_mm, normal, collection, mat,
             name="Label_Text", parent=None, extrude_mm=1.2):
    """Create a real mesh text object oriented by its surface normal."""
    cu = bpy.data.curves.new(name + "_curve", "FONT")
    cu.body = text
    cu.size = size_mm * MM
    cu.extrude = extrude_mm * MM
    cu.align_x = "CENTER"
    cu.align_y = "CENTER"
    cu.resolution_u = 3
    tmp = bpy.data.objects.new(name + "_tmp", cu)
    bpy.context.scene.collection.objects.link(tmp)
    q = Vector(normal).normalized().to_track_quat("Z", "Y")
    tmp.rotation_mode = "QUATERNION"
    tmp.rotation_quaternion = q
    n = Vector(normal).normalized()
    tmp.location = V(*loc_mm) + n * M(1.4)
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    me = bpy.data.meshes.new_from_object(tmp.evaluated_get(dg))
    me.materials.append(mat)
    for p in me.polygons:
        p.use_smooth = False
    ob = bpy.data.objects.new(name, me)
    collection.objects.link(ob)
    ob.matrix_world = tmp.matrix_world.copy()
    bpy.data.objects.remove(tmp, do_unlink=True)
    bpy.data.curves.remove(cu)
    if parent is not None:
        set_parent_keep(ob, parent)
    return ob


def add_label(text, size_mm, loc_mm, normal, collection, mats,
              plate_size=None, name="Label", parent=None,
              text_mat=None):
    """Dark plate + white/yellow text."""
    if plate_size is not None:
        w, h, t = plate_size
        mb = MB(name + "_plate")
        mb.add_cube((w, h, t), bevel=min(w, h) * 0.06, seg=2,
                    mat=mats["label_dark"])
        plate = mb.finish(collection, name + "_plate")
        n = Vector(normal).normalized()
        # plate local Z is its normal
        q = n.to_track_quat("Z", "Y")
        plate.rotation_mode = "QUATERNION"
        plate.rotation_quaternion = q
        plate.location = V(*loc_mm)
        if parent is not None:
            set_parent_keep(plate, parent)
    add_text(text, size_mm, loc_mm, normal, collection,
             text_mat or mats["label_white"], name=name + "_text",
             parent=parent)


# ============================================================================
#  COLLECTIONS / SCENE ROOTS
# ============================================================================


def make_collection(name, parent):
    c = bpy.data.collections.new(name)
    parent.children.link(c)
    return c


def build_collections(scene):
    robot = bpy.data.collections.new("Robot_Root")
    scene.collection.children.link(robot)

    C = {}
    C["root"] = robot
    chassis = make_collection("01_Chassis", robot)
    C["chassis"] = chassis
    for key, nm in (("main_body", "Main_Body"),
                    ("battery", "Battery_Compartment"),
                    ("control", "Control_Compartment"),
                    ("body_details", "Body_Details")):
        C[key] = make_collection(nm, chassis)

    for side, prefix in (("L", "02_Left_Track"), ("R", "03_Right_Track")):
        tr = make_collection(prefix, robot)
        C["track_" + side] = tr
        for key, nm in (("belt", "Track_Belt"), ("drive", "Drive_Wheel"),
                        ("idler", "Idler_Wheel"), ("road", "Road_Wheels"),
                        ("frame", "Track_Frame")):
            C[key + "_" + side] = make_collection(nm, tr)

    sup = make_collection("04_Camera_Support", robot)
    C["support"] = sup
    for key, nm in (("pillar_l", "Left_Pillar"), ("pillar_r", "Right_Pillar"),
                    ("beam", "Cross_Beam"), ("brackets", "Mounting_Brackets")):
        C[key] = make_collection(nm, sup)

    photo = make_collection("05_Photogrammetry", robot)
    C["photo"] = photo
    for key, nm in (("cam_l", "Camera_Left"), ("cam_c", "Camera_Center"),
                    ("cam_r", "Camera_Right"), ("led_l", "LED_Left"),
                    ("led_r", "LED_Right"), ("cabling", "Cabling")):
        C[key] = make_collection(nm, photo)

    C["electrical"] = make_collection("06_Electrical", robot)
    C["details"] = make_collection("07_Details", robot)

    # --- control empties ---------------------------------------------------
    R = {}
    R["robot"] = new_empty("Robot_Root", (0, 0, 0), robot, size=0.18,
                           display="PLAIN_AXES")
    R["chassis"] = new_empty("Chassis_Root", (0, 0, 0), robot,
                             parent=R["robot"], size=0.12,
                             display="CUBE")
    R["track_l"] = new_empty("Left_Track_Root",
                             (-P["track_cx"], 0, 0), robot,
                             parent=R["robot"], size=0.10,
                             display="CUBE")
    R["track_r"] = new_empty("Right_Track_Root",
                             (P["track_cx"], 0, 0), robot,
                             parent=R["robot"], size=0.10,
                             display="CUBE")
    R["support"] = new_empty("Camera_Support_Root",
                             (0, 0, P["plat_z1"]), robot,
                             parent=R["robot"], size=0.12,
                             display="CUBE")
    R["array"] = new_empty("Camera_Array_Root",
                           (0, P["cam_mount_y"], P["cam_z"]), robot,
                           parent=R["support"], size=0.10,
                           display="ARROWS")
    R["electrical"] = new_empty("Electrical_Root", (0, 0, 0), robot,
                                parent=R["robot"], size=0.08)
    R["details"] = new_empty("Details_Root", (0, 0, 0), robot,
                             parent=R["robot"], size=0.08)
    bpy.context.view_layer.update()
    return C, R


# ============================================================================
#  MODULE B -- CHASSIS
# ============================================================================


def build_chassis(C, R, MATS):
    root = R["chassis"]

    # ---- lower structural frame ------------------------------------------
    mb = MB("Chassis_LowerFrame")
    mb.add_cube((P["low_w"], P["low_l"], P["low_z1"] - P["low_z0"]),
                bevel=14, seg=3, mat=MATS["graphite_dark"])
    # longitudinal stiffening ribs visible under the upper shell
    for x in (-150, 150):
        mb.add_cube((18, 700, 26), loc=(x, 0, 82), bevel=4,
                    mat=MATS["steel"])
    ob = mb.finish(C["main_body"], "Chassis_LowerFrame")
    place(ob, (0, 0, (P["low_z0"] + P["low_z1"]) / 2), parent=root)
    shade_smooth(ob)

    # ---- skid plate -------------------------------------------------------
    mb = MB("Chassis_SkidPlate")
    mb.add_cube((330, 660, 10), bevel=5, mat=MATS["steel"])
    for x in (-130, 0, 130):
        for y in (-280, 0, 280):
            add_bolt(mb, (x, y, -5), axis="Z", r=6, h=4,
                     mat=MATS["black_anod"], washer_mat=MATS["steel"])
    ob = mb.finish(C["main_body"], "Chassis_SkidPlate")
    place(ob, (0, 0, 55), parent=root)
    shade_smooth(ob)

    # ---- upper shell ------------------------------------------------------
    mb = MB("Chassis_UpperShell")
    mb.add_cube((P["up_w"], P["up_l"], P["up_z1"] - P["up_z0"]),
                bevel=22, seg=4, mat=MATS["graphite"])
    # recessed panel line around the shell (thin dark gasket)
    ob = mb.finish(C["main_body"], "Chassis_UpperShell")
    place(ob, (0, 0, (P["up_z0"] + P["up_z1"]) / 2), parent=root)
    shade_smooth(ob)

    mb = MB("Chassis_ShellSeam")
    for yy in (P["up_l"] / 2 + 0.6, -P["up_l"] / 2 - 0.6):
        mb.add_cube((P["up_w"] + 1.2, 3.2, 5.0), loc=(0, yy, 0),
                    bevel=1.0, mat=MATS["graphite_dark"])
    for xx in (P["up_w"] / 2 + 0.6, -P["up_w"] / 2 - 0.6):
        mb.add_cube((3.2, P["up_l"] + 1.2, 5.0), loc=(xx, 0, 0),
                    bevel=1.0, mat=MATS["graphite_dark"])
    ob = mb.finish(C["main_body"], "Chassis_ShellSeam")
    place(ob, (0, 0, 360), parent=root)
    shade_smooth(ob)

    # ---- top platform -----------------------------------------------------
    mb = MB("Chassis_TopPlatform")
    mb.add_cube((P["plat_w"], P["plat_l"], P["plat_z1"] - P["plat_z0"]),
                bevel=6, seg=3, mat=MATS["panel"])
    ob = mb.finish(C["main_body"], "Chassis_TopPlatform")
    place(ob, (0, 0, (P["plat_z0"] + P["plat_z1"]) / 2), parent=root)
    shade_smooth(ob)

    # platform edge rail (aluminium)
    mb = MB("Chassis_PlatformRail")
    for xx in (P["plat_w"] / 2 - 8, -P["plat_w"] / 2 + 8):
        mb.add_cube((16, P["plat_l"] - 16, 10), loc=(xx, 0, 0),
                    bevel=2.5, mat=MATS["aluminium_dark"])
    for yy in (P["plat_l"] / 2 - 8, -P["plat_l"] / 2 + 8):
        mb.add_cube((P["plat_w"] - 16, 16, 10), loc=(0, yy, 0),
                    bevel=2.5, mat=MATS["aluminium_dark"])
    ob = mb.finish(C["main_body"], "Chassis_PlatformRail")
    place(ob, (0, 0, P["plat_z1"] + 5), parent=root)
    shade_smooth(ob)

    # platform support gussets
    mb = MB("Chassis_PlatformGussets")
    for xx in (-278, 278):
        for yy in (-230, 0, 230):
            mb.add_cube((44, 90, 78), loc=(xx, yy, 0), bevel=5,
                        mat=MATS["steel"])
    ob = mb.finish(C["main_body"], "Chassis_PlatformGussets")
    place(ob, (0, 0, P["plat_z0"] - 39), parent=root)
    shade_smooth(ob)

    # ---- side running boards above the tracks -----------------------------
    mb = MB("Chassis_SideSteps")
    for sx in (-1, 1):
        for yy in (-230, 0, 230):
            mb.add_cube((70, 120, 16),
                        loc=(sx * 295, yy, 0), bevel=4,
                        mat=MATS["aluminium_dark"])
    ob = mb.finish(C["main_body"], "Chassis_SideSteps")
    place(ob, (0, 0, 250), parent=root)
    shade_smooth(ob)

    # structural mounts bridging lower frame and track frames
    mb = MB("Chassis_TrackMounts")
    for sx in (-1, 1):
        for yy in (-200, 200):
            mb.add_cube((24, 90, 120), loc=(sx * 209, yy, 120), bevel=4,
                        mat=MATS["steel"])
    ob = mb.finish(C["main_body"], "Chassis_TrackMounts")
    place(ob, (0, 0, 0), parent=root)
    shade_smooth(ob)

    # ---- bumpers ----------------------------------------------------------
    mb = MB("Chassis_Bumpers")
    for yy in (P["low_l"] / 2 + 14, -P["low_l"] / 2 - 14):
        for xx in (-105, 105):
            mb.add_cube((170, 28, 82), loc=(xx, yy, 0), bevel=9,
                        mat=MATS["rubber_soft"])
    ob = mb.finish(C["body_details"], "Chassis_Bumpers")
    place(ob, (0, 0, 130), parent=root)
    shade_smooth(ob)

    # bumper mounting bolts
    mb = MB("Chassis_BumperBolts")
    for yy in (P["low_l"] / 2 + 1, -P["low_l"] / 2 - 1):
        for xx in (-160, -50, 50, 160):
            add_bolt(mb, (xx, yy, 130), axis="Y" if yy > 0 else "Y",
                     r=5, h=4, mat=MATS["black_anod"],
                     washer_mat=MATS["steel"])
    ob = mb.finish(C["body_details"], "Chassis_BumperBolts")
    place(ob, (0, 0, 0), parent=root)
    shade_smooth(ob)

    # ---- side details: vents, hatch, stripe ------------------------------
    for side, sx in (("L", -1), ("R", 1)):
        # recessed grille plate
        mb = MB("Chassis_SideVent_" + side)
        mb.add_cube((5, 250, 120), loc=(0, 0, 0), bevel=2,
                    mat=MATS["graphite_dark"])
        for i in range(7):
            mb.add_cube((8, 238, 7), loc=(3.5, 0, -48 + i * 16),
                        bevel=1.5, mat=MATS["panel"])
        ob = mb.finish(C["body_details"], "Chassis_SideVent_" + side)
        place(ob, (sx * (P["up_w"] / 2 + 1.5), 0, 372), parent=root)
        shade_smooth(ob)

        # service hatch
        mb = MB("Chassis_ServiceHatch_" + side)
        mb.add_cube((6, 200, 145), loc=(0, 0, 0), bevel=4,
                    mat=MATS["panel"])
        mb.add_cube((2.5, 160, 100), loc=(3.5, 0, 8), bevel=2,
                    mat=MATS["graphite_dark"])
        mb.add_cube((14, 60, 16), loc=(8, 0, -42), bevel=3,
                    mat=MATS["black_anod"])
        for yy in (-85, 85):
            for zz in (-60, 60):
                add_bolt(mb, (3.5, yy, zz), axis="X", r=4.5, h=3.5,
                         mat=MATS["black_anod"], washer_mat=MATS["steel"])
        ob = mb.finish(C["body_details"], "Chassis_ServiceHatch_" + side)
        place(ob, (sx * (P["up_w"] / 2 + 3), -225, 365), parent=root)
        shade_smooth(ob)

        # yellow identity stripe
        mb = MB("Chassis_SideStripe_" + side)
        mb.add_cube((3, 420, 12), loc=(0, 0, 0), bevel=1.2,
                    mat=MATS["yellow"])
        ob = mb.finish(C["body_details"], "Chassis_SideStripe_" + side)
        place(ob, (sx * (P["up_w"] / 2 + 2.2), 40, 300), parent=root)
        shade_smooth(ob)

    # ---- front work lights ------------------------------------------------
    for side, sx in (("L", -1), ("R", 1)):
        mb = MB("Chassis_WorkLight_" + side)
        mb.add_cyl(42, 26, loc=(0, 0, 0), axis="Y", seg=40, bevel=3,
                   mat=MATS["black_anod"])
        mb.add_cyl(33, 4, loc=(0, 14, 0), axis="Y", seg=40,
                   mat=MATS["lens_glass"])
        mb.add_cyl(36, 3, loc=(0, 16, 0), axis="Y", seg=40,
                   mat=MATS["black_anod"])
        # protective cross bars
        for zz in (-14, 0, 14):
            mb.add_cube((70, 6, 4), loc=(0, 20, zz), bevel=1,
                        mat=MATS["steel"])
        ob = mb.finish(C["body_details"], "Chassis_WorkLight_" + side)
        place(ob, (sx * 130, P["low_l"] / 2 + 16, 205), parent=root)
        shade_smooth(ob)

    # ---- front nameplate --------------------------------------------------
    add_label("TPR-900", 26, (0, P["up_l"] / 2 + 1.5, 276), (0, 1, 0),
              C["body_details"], MATS,
              plate_size=(210, 52, 3), name="Chassis_NamePlate",
              parent=root)
    add_text("TRACKED PHOTOGRAMMETRY ROBOT", 10,
             (0, P["up_l"] / 2 + 1.5, 258), (0, 1, 0),
             C["body_details"], MATS["label_white"],
             name="Chassis_NamePlate_Sub", parent=root)

    # ---- rear panel fasteners + tail lights ------------------------------
    mb = MB("Chassis_RearDetails")
    for xx in (-170, 170):
        mb.add_cube((52, 10, 34), loc=(xx, 0, 0), bevel=4,
                    mat=MATS["ind_red"])
    ob = mb.finish(C["body_details"], "Chassis_TailLights")
    place(ob, (0, -P["low_l"] / 2 - 4, 210), parent=root)
    shade_smooth(ob)

    # ---- platform details: rails, lifting eyes, handles -------------------
    mb = MB("Chassis_PlatformRails")
    for xx in (-150, 150):
        mb.add_cube((46, 580, 12), loc=(xx, 0, 0), bevel=2.5,
                    mat=MATS["aluminium"])
        for yy in (-250, 0, 250):
            mb.add_cube((52, 20, 4), loc=(xx, yy, 8), bevel=1,
                        mat=MATS["aluminium_dark"])
    for yy in (-250, 250):
        mb.add_cube((470, 30, 10), loc=(0, yy, 0), bevel=2,
                    mat=MATS["aluminium_dark"])
    ob = mb.finish(C["body_details"], "Chassis_PlatformRails")
    place(ob, (0, 0, P["plat_z1"] + 6), parent=root)
    shade_smooth(ob)

    mb = MB("Chassis_LiftingEyes")
    for xx in (-268, 268):
        for yy in (-288, 288):
            mb.add_cyl(13, 18, loc=(xx, yy, 10), axis="Z", seg=6,
                       mat=MATS["steel"])
            mb.add_ring(20, 9, 8, loc=(xx, yy, 26), axis="X", seg=24,
                        mat=MATS["steel"])
    ob = mb.finish(C["body_details"], "Chassis_LiftingEyes")
    place(ob, (0, 0, P["plat_z1"]), parent=root)
    shade_smooth(ob)

    mb = MB("Chassis_Handles")
    # U-shaped rear maintenance handles
    for xx in (-105, 105):
        mb.add_cube((22, 18, 90), loc=(xx, 0, 0), bevel=4,
                    mat=MATS["black_anod"])
    ob = mb.finish(C["body_details"], "Chassis_HandleMounts")
    place(ob, (0, -P["up_l"] / 2 - 12, 330), parent=root)
    shade_smooth(ob)
    for xx in (-105, 105):
        add_cable("Chassis_Handle_" + ("L" if xx < 0 else "R"),
                  [(xx - 55, -P["up_l"] / 2 - 12, 385),
                   (xx - 55, -P["up_l"] / 2 - 38, 375),
                   (xx, -P["up_l"] / 2 - 46, 370),
                   (xx + 55, -P["up_l"] / 2 - 38, 375),
                   (xx + 55, -P["up_l"] / 2 - 12, 385)],
                  9, C["body_details"], MATS["black_anod"], parent=root,
                  resolution=6)

    # ---- chassis fasteners ------------------------------------------------
    mb = MB("Chassis_Fasteners")
    for xx in (-240, -80, 80, 240):
        for yy in (-290, 290):
            add_bolt(mb, (xx, yy, P["plat_z1"]), axis="Z", r=6, h=4.5,
                     mat=MATS["black_anod"], washer_mat=MATS["steel"])
    for yy in (-300, -150, 0, 150, 300):
        for xx in (-250, 250):
            add_bolt(mb, (xx, yy, P["plat_z1"]), axis="Z", r=5.5, h=4,
                     mat=MATS["black_anod"], washer_mat=MATS["steel"])
    ob = mb.finish(C["body_details"], "Chassis_Fasteners")
    place(ob, (0, 0, 0), parent=root)
    shade_smooth(ob)

    # ======================================================================
    # battery compartment (rear)
    # ======================================================================
    yb = -P["up_l"] / 2
    mb = MB("Battery_Drawer")
    mb.add_cube((340, 26, 150), bevel=7, mat=MATS["panel"])
    mb.add_cube((300, 4, 112), loc=(0, -14, 2), bevel=3,
                mat=MATS["graphite_dark"])
    # vent slots
    for i in range(5):
        mb.add_cube((240, 3, 5), loc=(0, -16, -44 + i * 9), bevel=1,
                    mat=MATS["black_anod"])
    ob = mb.finish(C["battery"], "Battery_Drawer")
    place(ob, (0, yb - 10, 365), parent=root)
    shade_smooth(ob)

    mb = MB("Battery_Handle")
    mb.add_cube((150, 20, 26), bevel=5, mat=MATS["black_anod"])
    mb.add_cube((110, 10, 16), loc=(0, -8, 0), bevel=3,
                mat=MATS["steel"])
    ob = mb.finish(C["battery"], "Battery_Handle")
    place(ob, (0, yb - 26, 315), parent=root)
    shade_smooth(ob)

    mb = MB("Battery_Latches")
    for xx in (-120, 120):
        mb.add_cube((46, 26, 34), loc=(xx, 0, 0), bevel=4,
                    mat=MATS["steel"])
        mb.add_cube((30, 10, 18), loc=(xx, -14, 4), bevel=3,
                    mat=MATS["black_anod"])
    ob = mb.finish(C["battery"], "Battery_Latches")
    place(ob, (0, yb - 20, 424), parent=root)
    shade_smooth(ob)

    add_label("BATTERY 48V", 16, (0, yb - 24, 448), (0, -1, 0),
              C["battery"], MATS, plate_size=(180, 30, 3),
              name="Battery_Label", parent=root,
              text_mat=MATS["label_yellow"])

    # ======================================================================
    # control compartment (front)
    # ======================================================================
    yf = P["up_l"] / 2
    mb = MB("Control_Panel")
    mb.add_cube((330, 10, 132), bevel=6, mat=MATS["panel"])
    mb.add_cube((300, 3, 104), loc=(0, 5, 0), bevel=2,
                mat=MATS["graphite_dark"])
    ob = mb.finish(C["control"], "Control_Panel")
    place(ob, (0, yf + 3, 366), parent=root)
    shade_smooth(ob)

    mb = MB("Control_Display")
    mb.add_cube((128, 6, 72), bevel=4, mat=MATS["black_anod"])
    mb.add_cube((112, 2, 56), loc=(0, 4, 0), bevel=2,
                mat=MATS["screen"])
    ob = mb.finish(C["control"], "Control_Display")
    place(ob, (-78, yf + 10, 386), parent=root)
    shade_smooth(ob)

    mb = MB("Control_Buttons")
    for i, zz in enumerate((412, 388, 364)):
        for j, xx in enumerate((52, 96, 140)):
            mat = (MATS["ind_green"] if (i + j) % 3 == 0 else
                   MATS["black_anod"])
            mb.add_cyl(9, 5, loc=(xx, 0, zz), axis="Y", seg=20,
                       mat=mat, bevel=0.8)
    mb.add_cyl(15, 8, loc=(52, 0, 330), axis="Y", seg=28,
               mat=MATS["black_anod"])
    mb.add_cyl(10, 4, loc=(52, 5, 330), axis="Y", seg=28,
               mat=MATS["yellow"])
    ob = mb.finish(C["control"], "Control_Buttons")
    place(ob, (0, yf + 10, 366), parent=root)
    shade_smooth(ob)

    mb = MB("Control_Hinges")
    for zz in (330, 402):
        mb.add_cube((34, 14, 18), loc=(0, 0, zz), bevel=3,
                    mat=MATS["steel"])
    ob = mb.finish(C["control"], "Control_Hinges")
    place(ob, (-172, yf + 6, 366), parent=root)
    shade_smooth(ob)

    add_label("CONTROL  /  PC-01", 12, (0, yf + 9, 310), (0, 1, 0),
              C["control"], MATS, plate_size=(220, 26, 3),
              name="Control_Label", parent=root)

    # ---- electrical bulkhead fittings on the shell -----------------------
    mb = MB("Chassis_Bulkhead")
    for xx in (-200, 200):
        mb.add_cyl(16, 8, loc=(xx, 0, 0), axis="Y", seg=24,
                   mat=MATS["black_anod"])
        mb.add_cyl(10, 12, loc=(xx, 4, 0), axis="Y", seg=20,
                   mat=MATS["steel"])
    ob = mb.finish(C["electrical"], "Chassis_Bulkhead")
    place(ob, (0, yf + 2, 255), parent=root)
    shade_smooth(ob)

    return True

# ============================================================================
#  CABLES
# ============================================================================


def add_cable(name, points_mm, radius_mm, collection, mat, parent=None,
              resolution=10, bevel_res=2):
    cu = bpy.data.curves.new(name + "_curve", "CURVE")
    cu.dimensions = "3D"
    cu.resolution_u = resolution
    cu.bevel_depth = M(radius_mm)
    cu.bevel_resolution = bevel_res
    cu.fill_mode = "FULL"
    sp = cu.splines.new("BEZIER")
    sp.bezier_points.add(len(points_mm) - 1)
    for i, pt in enumerate(points_mm):
        bp = sp.bezier_points[i]
        bp.co = V(*pt)
        bp.handle_left_type = "AUTO"
        bp.handle_right_type = "AUTO"
    tmp = bpy.data.objects.new(name + "_tmp", cu)
    collection.objects.link(tmp)
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    me = bpy.data.meshes.new_from_object(tmp.evaluated_get(dg))
    bpy.data.objects.remove(tmp, do_unlink=True)
    bpy.data.curves.remove(cu)
    me.materials.append(mat)
    for p in me.polygons:
        p.use_smooth = True
    ob = bpy.data.objects.new(name, me)
    collection.objects.link(ob)
    if parent is not None:
        set_parent_keep(ob, parent)
    return ob


# ============================================================================
#  MODULE A -- TRACK RUNNING GEAR
# ============================================================================


def stadium_path(cy, cz, r, n_arc=32, n_straight=10):
    """CCW (in YZ) stadium centreline: front arc, top, rear arc, bottom."""
    pts = []
    for i in range(n_arc):
        a = -math.pi / 2 + math.pi * i / n_arc
        pts.append((cy + r * math.cos(a), cz + r * math.sin(a)))
    for i in range(1, n_straight + 1):
        pts.append((cy + (-2 * cy) * i / n_straight, cz + r))
    for i in range(n_arc):
        a = math.pi / 2 + math.pi * i / n_arc
        pts.append((-cy + r * math.cos(a), cz + r * math.sin(a)))
    for i in range(1, n_straight + 1):
        pts.append((-cy + (2 * cy) * i / n_straight, cz - r))
    return pts


def _path_frames(pts):
    """Return (point, tangent, outward normal, cumulative length) per sample."""
    n = len(pts)
    out = []
    total = 0.0
    for i in range(n):
        y0, z0 = pts[(i - 1) % n]
        y1, z1 = pts[(i + 1) % n]
        ty, tz = y1 - y0, z1 - z0
        ln = math.hypot(ty, tz)
        ty /= ln
        tz /= ln
        ny, nz = tz, -ty
        p = pts[i]
        if i > 0:
            total += math.hypot(p[0] - pts[i - 1][0], p[1] - pts[i - 1][1])
        out.append((p, (ty, tz), (ny, nz), total))
    total += math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1])
    return out, total


def _frame_matrix(p, t, n, loc_extra=0.0):
    """4x4 placing local X across track, local Y along path, local Z outward."""
    ty, tz = t
    ny, nz = n
    R = Matrix(((1.0, 0.0, 0.0),
                (0.0, ty, ny),
                (0.0, tz, nz))).to_4x4()
    pos = Vector((0.0, p[0] + ny * loc_extra,
                  p[1] + nz * loc_extra)) * MM
    return Matrix.Translation(pos) @ R


def build_belt(side, sx, C, R, MATS):
    w = P["track_w"]
    half_t = P["belt_t"] * 0.5
    cy = P["wheel_cy"]
    cz = P["wheel_cz"]
    rc = P["belt_center_r"]
    pts = stadium_path(cy, cz, rc, n_arc=36, n_straight=10)
    frames, total = _path_frames(pts)

    mb = MB("Track_Belt_" + side)
    n = len(pts)
    verts = []
    for (y, z), (ty, tz), (ny, nz), _ in frames:
        for dx in (-w * 0.5, w * 0.5):
            for off in (-half_t, half_t):
                verts.append((dx, y + ny * off, z + nz * off))
    faces = []
    for i in range(n):
        j = (i + 1) % n
        a = i * 4
        b = j * 4
        faces.append((a + 0, b + 0, b + 1, a + 1))      # inner face
        faces.append((a + 3, a + 2, b + 2, b + 3))      # outer face
        faces.append((a + 0, a + 3, b + 3, b + 0))      # side -X
        faces.append((a + 1, b + 1, b + 2, a + 2))      # side +X
    mb.add_raw(verts, faces, mat=MATS["rubber"])
    bmesh.ops.bevel(mb.bm, geom=list(mb.bm.verts) + list(mb.bm.edges),
                    offset=M(3.0), offset_type="OFFSET", segments=2,
                    profile=0.5, affect="EDGES", clamp_overlap=True)

    # ---- grousers ---------------------------------------------------------
    spacing = P["grouser_spacing"]
    count = int(total / spacing)
    for k in range(count):
        s = k * spacing
        # find segment
        idx = 0
        for i in range(1, len(frames)):
            if frames[i][3] >= s:
                idx = i - 1
                break
        p0, t0, n0, s0 = frames[idx]
        p1, t1, n1, s1 = frames[(idx + 1) % len(frames)]
        f = 0.0 if s1 <= s0 else (s - s0) / (s1 - s0)
        p = (p0[0] + (p1[0] - p0[0]) * f, p0[1] + (p1[1] - p0[1]) * f)
        ty = t0[0] + (t1[0] - t0[0]) * f
        tz = t0[1] + (t1[1] - t0[1]) * f
        ln = math.hypot(ty, tz)
        t = (ty / ln, tz / ln)
        nrm = (t[1], -t[0])
        Rm = _frame_matrix(p, t, nrm, half_t + P["grouser_h"] * 0.5)
        mb.add_cube((58, 24, P["grouser_h"]), loc=(-51, 0, 0), rot=Rm,
                    bevel=2.2, mat=MATS["rubber"])
        mb.add_cube((58, 24, P["grouser_h"]), loc=(51, 0, 0), rot=Rm,
                    bevel=2.2, mat=MATS["rubber"])
        Rm2 = _frame_matrix(p, t, nrm, half_t + 4.0)
        mb.add_cube((38, 28, 8.0), rot=Rm2, bevel=1.8, mat=MATS["rubber"])

    # ---- inner guide lugs (straight runs only) ---------------------------
    lug_spacing = 92.0
    for k in range(int(total / lug_spacing)):
        s = k * lug_spacing + 23.0
        idx = 0
        for i in range(1, len(frames)):
            if frames[i][3] >= s:
                idx = i - 1
                break
        p0, t0, n0, s0 = frames[idx]
        p1, t1, n1, s1 = frames[(idx + 1) % len(frames)]
        if abs(p0[0]) > 250:
            continue
        f = 0.0 if s1 <= s0 else (s - s0) / (s1 - s0)
        p = (p0[0] + (p1[0] - p0[0]) * f, p0[1] + (p1[1] - p0[1]) * f)
        ty = t0[0] + (t1[0] - t0[0]) * f
        tz = t0[1] + (t1[1] - t0[1]) * f
        ln = math.hypot(ty, tz)
        t = (ty / ln, tz / ln)
        nrm = (t[1], -t[0])
        Rm = _frame_matrix(p, t, nrm, -(half_t + 4.0))
        mb.add_cube((30, 26, 8.0), rot=Rm, bevel=1.8, mat=MATS["rubber"])

    ob = mb.finish(C["belt_" + side], "Track_Belt_" + side)
    place(ob, (sx * P["track_cx"], 0, 0.4), parent=R["track_" + side.lower()])
    shade_smooth(ob, 40)
    return ob


def build_drive_wheel(side, sx, C, R, MATS):
    mb = MB("Drive_Wheel_" + side)
    # rubber tyre ring
    mb.add_ring(P["wheel_r"], P["wheel_r"] - 14, 124, axis="X",
                seg=48, mat=MATS["rubber"], bevel=3)
    # steel body
    mb.add_cyl(P["wheel_r"] - 13, 116, axis="X", seg=48, bevel=3,
               mat=MATS["steel"])
    # recessed face + spokes
    for dx in (-59, 59):
        mb.add_cyl(56, 4, loc=(dx, 0, 0), axis="X", seg=40,
                   mat=MATS["aluminium_dark"])
        mb.add_cyl(30, 8, loc=(dx * 1.02, 0, 0), axis="X", seg=32,
                   mat=MATS["black_anod"])
    for i in range(6):
        a = 2 * math.pi * i / 6 + math.pi / 6
        for dx in (-62, 62):
            mb.add_cube((6, 16, 52),
                        loc=(dx, math.cos(a) * 33, math.sin(a) * 33),
                        rot=Euler((a - math.pi / 2, 0, 0)),
                        bevel=2, mat=MATS["aluminium_dark"])
    # hub cap
    mb.add_cyl(26, 136, axis="X", seg=32, bevel=3,
               mat=MATS["black_anod"])
    mb.add_cyl(17, 146, axis="X", seg=6, bevel=2, mat=MATS["steel"])
    # ring of bolts on the outer face
    for i in range(10):
        a = 2 * math.pi * i / 10
        for dx in (-66, 66):
            add_bolt(mb, (dx, math.cos(a) * 44, math.sin(a) * 44),
                     axis="X", r=4.5, h=3.5, mat=MATS["black_anod"],
                     washer_mat=MATS["steel"])
    ob = mb.finish(C["drive_" + side], "Drive_Wheel_" + side)
    place(ob, (sx * P["track_cx"], P["wheel_cy"], P["wheel_cz"]),
          parent=R["track_" + side.lower()])
    shade_smooth(ob)
    return ob


def build_idler(side, sx, C, R, MATS):
    mb = MB("Idler_Wheel_" + side)
    mb.add_ring(P["wheel_r"], P["wheel_r"] - 12, 118, axis="X",
                seg=48, mat=MATS["rubber"], bevel=3)
    mb.add_cyl(P["wheel_r"] - 11, 110, axis="X", seg=48, bevel=3,
               mat=MATS["steel"])
    for dx in (-56, 56):
        mb.add_cyl(48, 5, loc=(dx, 0, 0), axis="X", seg=40,
                   mat=MATS["aluminium_dark"])
        mb.add_cyl(22, 7, loc=(dx * 1.02, 0, 0), axis="X", seg=32,
                   mat=MATS["black_anod"])
    # 5 lightening holes suggested by recessed dark discs
    for i in range(5):
        a = 2 * math.pi * i / 5 + 0.3
        for dx in (-57, 57):
            mb.add_cyl(14, 4, loc=(dx, math.cos(a) * 30, math.sin(a) * 30),
                       axis="X", seg=24, mat=MATS["graphite_dark"])
    mb.add_cyl(22, 132, axis="X", seg=32, bevel=2,
               mat=MATS["black_anod"])
    mb.add_cyl(12, 140, axis="X", seg=6, bevel=2, mat=MATS["steel"])
    for i in range(6):
        a = 2 * math.pi * i / 6
        add_bolt(mb, (63, math.cos(a) * 38, math.sin(a) * 38), axis="X",
                 r=4.0, h=3.0, mat=MATS["black_anod"],
                 washer_mat=MATS["steel"])
    ob = mb.finish(C["idler_" + side], "Idler_Wheel_" + side)
    place(ob, (sx * P["track_cx"], -P["wheel_cy"], P["wheel_cz"]),
          parent=R["track_" + side.lower()])
    shade_smooth(ob)
    return ob


def build_road_wheels(side, sx, C, R, MATS):
    root = R["track_" + side.lower()]
    for i, yy in enumerate(P["road_ys"]):
        mb = MB("Road_Wheel_%s_%02d" % (side, i + 1))
        for dx in (-40, 40):
            sgn = 1 if dx > 0 else -1
            mb.add_cyl(P["road_r"], 46, loc=(dx, 0, 0), axis="X",
                       seg=40, bevel=4, mat=MATS["rubber"])
            mb.add_cyl(20, 50, loc=(dx, 0, 0), axis="X", seg=28,
                       mat=MATS["steel"])
            mb.add_cyl(13, 8, loc=(dx + sgn * 27, 0, 0), axis="X",
                       seg=24, mat=MATS["black_anod"])
            for k in range(5):
                a = 2 * math.pi * k / 5
                add_bolt(mb, (dx + sgn * 25, math.cos(a) * 8,
                              math.sin(a) * 8),
                         axis="X", sign=sgn, r=2.6, h=2.0,
                         mat=MATS["black_anod"])
        mb.add_cyl(18, 92, axis="X", seg=28, mat=MATS["aluminium_dark"])
        mb.add_cyl(12, 100, axis="X", seg=6, mat=MATS["steel"])
        ob = mb.finish(C["road_" + side], "Road_Wheel_%s_%02d" % (side, i + 1))
        place(ob, (sx * P["track_cx"], yy, P["road_cz"]), parent=root)
        shade_smooth(ob)


def build_return_rollers(side, sx, C, R, MATS):
    root = R["track_" + side.lower()]
    for i, yy in enumerate(P["return_roller_ys"]):
        mb = MB("Return_Roller_%s_%02d" % (side, i + 1))
        for dx in (-35, 35):
            mb.add_cyl(P["return_roller_r"], 26, loc=(dx, 0, 0),
                       axis="X", seg=28, bevel=2, mat=MATS["rubber"])
            mb.add_cyl(10, 30, loc=(dx, 0, 0), axis="X", seg=20,
                       mat=MATS["steel"])
        mb.add_cyl(9, 80, axis="X", seg=20, mat=MATS["black_anod"])
        ob = mb.finish(C["road_" + side],
                       "Return_Roller_%s_%02d" % (side, i + 1))
        place(ob, (sx * P["track_cx"], yy, 158), parent=root)
        shade_smooth(ob)


def build_track_frame(side, sx, C, R, MATS):
    root = R["track_" + side.lower()]
    col = C["frame_" + side]
    cy = P["wheel_cy"]
    cz = P["wheel_cz"]
    r = P["wheel_r"] - 6
    pts = stadium_path(cy, cz, r, n_arc=36, n_straight=10)
    n = len(pts)
    verts = []
    for x in (-8, 8):
        for y, z in pts:
            verts.append((x, y, z))
    faces = []
    faces.append(tuple(range(0, n)))
    faces.append(tuple(range(2 * n - 1, n - 1, -1)))
    for i in range(n):
        j = (i + 1) % n
        faces.append((i, j, n + j, n + i))
    mb = MB("Track_Frame_" + side)
    mb.add_raw(verts, faces, mat=MATS["steel"])
    plate = mb.finish(col, "Track_Frame_" + side)
    place(plate, (sx * 226, 0, 0), parent=root)
    shade_smooth(plate, 30)

    # lightening holes
    for yy in (-180, 0, 180):
        mb = MB("cut")
        mb.add_cyl(28, 44, axis="X", seg=32, mat=MATS["steel"])
        cutter = mb.finish(col, "cut")
        place(cutter, (sx * 226, yy, cz))
        boolean_cut(plate, cutter)

    # raised rings / bolts / axle bosses
    mb = MB("Track_FrameDetails_" + side)
    xo = sx * 8.0
    for yy in (-180, 0, 180):
        mb.add_ring(34, 27, 5, loc=(xo + sx * 2.5, yy, cz), axis="X",
                    seg=32, mat=MATS["aluminium_dark"])
        for k in range(6):
            a = 2 * math.pi * k / 6
            add_bolt(mb, (xo + sx * 4.0,
                          yy + math.cos(a) * 31,
                          cz + math.sin(a) * 31),
                     axis="X", r=3.6, h=2.6, sign=sx,
                     mat=MATS["black_anod"], washer_mat=MATS["steel"])
    for yy in (P["wheel_cy"], -P["wheel_cy"]):
        mb.add_cyl(17, 28, loc=(sx * 15, yy, cz), axis="X", seg=24,
                   mat=MATS["aluminium_dark"], bevel=2)
        mb.add_cyl(9, 34, loc=(sx * 15, yy, cz), axis="X", seg=6,
                   mat=MATS["steel"])
    for yy in P["road_ys"]:
        mb.add_cyl(11, 20, loc=(sx * 12, yy, P["road_cz"]), axis="X",
                   seg=18, mat=MATS["aluminium_dark"])
    # frame to chassis web plates
    for yy in (-250, 250):
        mb.add_cube((30, 70, 60), loc=(sx * 2, yy, 45), bevel=4,
                    mat=MATS["steel"])
    ob = mb.finish(col, "Track_FrameDetails_" + side)
    place(ob, (sx * 226, 0, 0), parent=root)
    shade_smooth(ob)

    # idler tensioner
    mb = MB("Track_Tensioner_" + side)
    mb.add_cube((46, 60, 46), loc=(0, 0, 0), bevel=5,
                mat=MATS["steel"])
    mb.add_cyl(8, 90, loc=(0, -28, 0), axis="Y", seg=20,
               mat=MATS["aluminium"])
    mb.add_cyl(14, 12, loc=(0, -72, 0), axis="Y", seg=6,
               mat=MATS["black_anod"])
    add_bolt(mb, (0, 0, 24), axis="Z", r=5, h=4,
             mat=MATS["black_anod"], washer_mat=MATS["steel"])
    ob = mb.finish(col, "Track_Tensioner_" + side)
    place(ob, (sx * 226, -378, cz), parent=root)
    shade_smooth(ob)

    # suspension arms (visual)
    mb = MB("Track_Suspension_" + side)
    for yy in P["road_ys"]:
        mb.add_cube((14, 20, 60), loc=(sx * 12, yy, 40),
                    rot=Euler((0, math.radians(12 * sx), 0)),
                    bevel=3, mat=MATS["steel"])
        mb.add_cyl(12, 26, loc=(sx * 18, yy, P["road_cz"]),
                   axis="X", seg=16, mat=MATS["black_anod"])
    ob = mb.finish(col, "Track_Suspension_" + side)
    place(ob, (sx * 226, 0, 0), parent=root)
    shade_smooth(ob)


def build_fender(side, sx, C, R, MATS):
    root = R["track_" + side.lower()]
    col = C["frame_" + side]
    mb = MB("Track_Fender_" + side)
    mb.add_cube((184, 800, 12), bevel=6, seg=3, mat=MATS["graphite"])
    # outer yellow rail
    mb.add_cube((5, 740, 8), loc=(sx * 90, 0, 6), bevel=1.5,
                mat=MATS["yellow"])
    # inner vertical lip
    mb.add_cube((4, 740, 16), loc=(-sx * 91, 0, -2), bevel=1.5,
                mat=MATS["graphite_dark"])
    # top ribs
    for yy in (-260, 0, 260):
        mb.add_cube((150, 12, 4), loc=(0, yy, 7), bevel=1,
                    mat=MATS["aluminium_dark"])
    ob = mb.finish(col, "Track_Fender_" + side)
    place(ob, (sx * 296, 0, 228), parent=root)
    shade_smooth(ob)

    # fender to body brackets
    mb = MB("Track_FenderBracket_" + side)
    for yy in (-210, 210):
        mb.add_cube((64, 100, 16), loc=(-sx * 48, yy, 8), bevel=4,
                    mat=MATS["steel"])
        mb.add_cube((16, 100, 22), loc=(-sx * 76, yy, -6), bevel=3,
                    mat=MATS["steel"])
        for yy2 in (-32, 32):
            add_bolt(mb, (-sx * 48, yy + yy2, 16), axis="Z", r=5, h=4,
                     mat=MATS["black_anod"], washer_mat=MATS["steel"])
    ob = mb.finish(col, "Track_FenderBracket_" + side)
    place(ob, (sx * 296, 0, 228), parent=root)
    shade_smooth(ob)


def build_track(side, sx, C, R, MATS):
    build_belt(side, sx, C, R, MATS)
    build_drive_wheel(side, sx, C, R, MATS)
    build_idler(side, sx, C, R, MATS)
    build_road_wheels(side, sx, C, R, MATS)
    build_return_rollers(side, sx, C, R, MATS)
    build_track_frame(side, sx, C, R, MATS)
    build_fender(side, sx, C, R, MATS)

# ============================================================================
#  MODULE C -- CAMERA SUPPORT GANTRY
# ============================================================================


def _tslot_strips(mb, sx_len, sy_len, sz_len, mat):
    """Four shallow dark strips suggesting aluminium T-slot profiles."""
    mb.add_cube((sx_len * 0.12, 1.2, sz_len * 0.96),
                loc=(0, -sy_len / 2 - 0.5, 0), mat=mat)
    mb.add_cube((sx_len * 0.12, 1.2, sz_len * 0.96),
                loc=(0, sy_len / 2 + 0.5, 0), mat=mat)
    mb.add_cube((1.2, sy_len * 0.12, sz_len * 0.96),
                loc=(-sx_len / 2 - 0.5, 0, 0), mat=mat)
    mb.add_cube((1.2, sy_len * 0.12, sz_len * 0.96),
                loc=(sx_len / 2 + 0.5, 0, 0), mat=mat)


def build_support(C, R, MATS):
    root = R["support"]
    px = P["pillar_x"]
    py = P["pillar_y"]
    z0 = P["pillar_z0"]
    z_split = P["pillar_split_z"]
    z1 = P["pillar_z1"]

    for side, sx in (("L", -1), ("R", 1)):
        col = C["pillar_l" if side == "L" else "pillar_r"]
        xx = sx * px

        # base plate
        mb = MB("Support_BasePlate_" + side)
        mb.add_cube((150, 150, 14), bevel=4, mat=MATS["steel"])
        for bx in (-58, 58):
            for by in (-58, 58):
                add_bolt(mb, (bx, by, 7), axis="Z", r=6, h=5,
                         mat=MATS["black_anod"], washer_mat=MATS["steel"])
        ob = mb.finish(col, "Support_BasePlate_" + side)
        place(ob, (xx, py, z0 + 7), parent=root)
        shade_smooth(ob)

        # lower pillar (100 x 100)
        h_low = z_split - (z0 + 14)
        mb = MB("Support_PillarLower_" + side)
        mb.add_cube((P["pillar_low"], P["pillar_low"], h_low),
                    bevel=3, seg=2, mat=MATS["aluminium_dark"])
        _tslot_strips(mb, P["pillar_low"], P["pillar_low"], h_low,
                      MATS["graphite_dark"])
        ob = mb.finish(col, "Support_PillarLower_" + side)
        place(ob, (xx, py, z0 + 14 + h_low / 2), parent=root)
        shade_smooth(ob)

        # clamp collar at the height-adjust joint
        mb = MB("Support_ClampCollar_" + side)
        mb.add_cube((112, 112, 34), bevel=4, mat=MATS["steel"])
        for by in (-38, 38):
            add_bolt(mb, (0, by, 17), axis="Z", r=6, h=5,
                     mat=MATS["black_anod"], washer_mat=MATS["steel"])
        for bx in (-38, 38):
            add_bolt(mb, (bx, 0, 17), axis="Z", r=6, h=5,
                     mat=MATS["black_anod"], washer_mat=MATS["steel"])
        ob = mb.finish(col, "Support_ClampCollar_" + side)
        place(ob, (xx, py, z_split + 17), parent=root)
        shade_smooth(ob)

        # upper pillar (82 x 82) - telescoping section
        h_up = z1 - z_split
        mb = MB("Support_PillarUpper_" + side)
        mb.add_cube((P["pillar_up"], P["pillar_up"], h_up),
                    bevel=3, seg=2, mat=MATS["aluminium_dark"])
        _tslot_strips(mb, P["pillar_up"], P["pillar_up"], h_up,
                      MATS["graphite_dark"])
        # height scale ticks on front face
        for i in range(9):
            mb.add_cube((26, 1.2, 3),
                        loc=(0, -P["pillar_up"] / 2 - 0.8,
                             -h_up / 2 + 30 + i * (h_up - 60) / 8),
                        mat=MATS["label_white"])
        ob = mb.finish(col, "Support_PillarUpper_" + side)
        place(ob, (xx, py, z_split + h_up / 2), parent=root)
        shade_smooth(ob)

        # top mounting plate under beam
        mb = MB("Support_TopPlate_" + side)
        mb.add_cube((150, 150, 14), bevel=4, mat=MATS["steel"])
        for bx in (-58, 58):
            for by in (-58, 58):
                add_bolt(mb, (bx, by, 7), axis="Z", r=6, h=5,
                         mat=MATS["black_anod"], washer_mat=MATS["steel"])
        ob = mb.finish(col, "Support_TopPlate_" + side)
        place(ob, (xx, py, z1 - 7), parent=root)
        shade_smooth(ob)

        # gussets between pillar and beam
        mb = MB("Support_BeamGusset_" + side)
        for by in (-1, 1):
            mb.add_cube((18, 70, 80),
                        loc=(0, by * 70, 30),
                        rot=Euler((math.radians(by * 30), 0, 0)),
                        bevel=3, mat=MATS["steel"])
        ob = mb.finish(col, "Support_BeamGusset_" + side)
        place(ob, (xx, py, z1), parent=root)
        shade_smooth(ob)

        # rear diagonal brace
        p0 = Vector((xx, py - 50, 1000))
        p1 = Vector((xx, -250, z0 + 16))
        d = p1 - p0
        L = d.length
        mid = (p0 + p1) * 0.5
        ang = math.atan2(-d.y, d.z)
        mb = MB("Support_Brace_" + side)
        mb.add_cube((44, 36, L), bevel=4, mat=MATS["aluminium_dark"])
        for end, n in ((p0, 1), (p1, -1)):
            mb.add_cube((86, 70, 18), loc=(0, 0, n * (L / 2 - 6)),
                        bevel=4, mat=MATS["steel"])
            for bx in (-30, 30):
                for by in (-22, 22):
                    add_bolt(mb, (bx, by, n * (L / 2 + 2)),
                             axis="Z", r=5, h=4, sign=n,
                             mat=MATS["black_anod"],
                             washer_mat=MATS["steel"])
        ob = mb.finish(col, "Support_Brace_" + side)
        ob.location = V(mid.x, mid.y, mid.z)
        ob.rotation_euler = Euler((ang, 0, 0), "XYZ")
        set_parent_keep(ob, root)
        shade_smooth(ob)

    # ---- cross beam -------------------------------------------------------
    mb = MB("Support_CrossBeam")
    mb.add_cube((P["beam_x"] * 2, P["beam_y1"] - P["beam_y0"],
                 P["beam_z1"] - P["beam_z0"]),
                bevel=7, seg=3, mat=MATS["aluminium_dark"])
    # horizontal T-slots on front / back
    for yy in (P["beam_y1"] - P["beam_y0"]) / 2, -(P["beam_y1"] - P["beam_y0"]) / 2:
        for zz in (-28, 28):
            mb.add_cube((P["beam_x"] * 1.9, 1.5, 9),
                        loc=(0, yy + (1.0 if yy > 0 else -1.0), zz),
                        mat=MATS["graphite_dark"])
    # end caps
    for xx in (-P["beam_x"] - 5, P["beam_x"] + 5):
        mb.add_cube((10, 130, 110), loc=(xx, 0, 0), bevel=3,
                    mat=MATS["steel"])
    # top cable tray
    mb.add_cube((600, 46, 8), loc=(0, -20, 54), bevel=2,
                mat=MATS["black_anod"])
    for xx in (-290, 290):
        mb.add_cube((8, 46, 34), loc=(xx, -20, 70), bevel=2,
                    mat=MATS["black_anod"])
    # lower stiffening web
    mb.add_cube((600, 30, 24), loc=(0, 0, -56), bevel=3,
                mat=MATS["steel"])
    ob = mb.finish(C["beam"], "Support_CrossBeam")
    place(ob, (0, (P["beam_y0"] + P["beam_y1"]) / 2,
               (P["beam_z0"] + P["beam_z1"]) / 2), parent=root)
    shade_smooth(ob)

    # camera mounting plates on beam front
    mb = MB("Support_CameraPlates")
    for xx in (-P["cam_x"], 0, P["cam_x"]):
        mb.add_cube((170, 12, 170), loc=(xx, 0, 0), bevel=4,
                    mat=MATS["steel"])
        for bx in (-65, 65):
            for bz in (-65, 65):
                add_bolt(mb, (xx + bx, 6, bz), axis="Y", r=6, h=4.5,
                         mat=MATS["black_anod"], washer_mat=MATS["steel"])
    ob = mb.finish(C["brackets"], "Support_CameraPlates")
    place(ob, (0, P["cam_mount_y"] + 6, P["cam_z"]), parent=root)
    shade_smooth(ob)

    # LED risers on beam top
    mb = MB("Support_LEDRisers")
    for sx in (-1, 1):
        mb.add_cube((90, 90, 22), loc=(sx * P["led_x"], 0, 0), bevel=4,
                    mat=MATS["steel"])
        for bx in (-28, 28):
            add_bolt(mb, (sx * P["led_x"] + bx, 0, 11), axis="Z",
                     r=5, h=4, mat=MATS["black_anod"],
                     washer_mat=MATS["steel"])
    ob = mb.finish(C["brackets"], "Support_LEDRisers")
    place(ob, (0, (P["led_y0"] + P["led_y1"]) / 2,
               P["beam_z1"] + 11), parent=root)
    shade_smooth(ob)

    # cable clips on the rear face of the beam
    mb = MB("Support_CableClips")
    for xx in (-250, -120, 120, 250):
        mb.add_cube((22, 18, 16), loc=(xx, 0, 0), bevel=3,
                    mat=MATS["black_anod"])
    ob = mb.finish(C["brackets"], "Support_CableClips")
    place(ob, (0, P["beam_y0"] + 8, P["beam_z1"] - 30), parent=root)
    shade_smooth(ob)


# ============================================================================
#  MODULE D -- PHOTOGRAMMETRY CAMERAS
# ============================================================================


def build_camera(name, label, world_x, yaw_deg, C, R, MATS):
    mb = MB(name)
    # rear mounting flange
    mb.add_cyl(56, 10, loc=(0, 17, 0), axis="Y", seg=40, bevel=2,
               mat=MATS["black_anod"])
    for k in range(6):
        a = 2 * math.pi * k / 6
        add_bolt(mb, (math.cos(a) * 47, 22, math.sin(a) * 47),
                 axis="Y", r=5, h=3.5, mat=MATS["black_anod"],
                 washer_mat=MATS["steel"])
    # main housing
    mb.add_cube((P["cam_w"], P["cam_d"], P["cam_h"]),
                loc=(0, 102, 0), bevel=10, seg=4, mat=MATS["camera"])
    # side ribs
    for sx in (-1, 1):
        for zz in (-34, 0, 34):
            mb.add_cube((2.5, 110, 7), loc=(sx * 76, 105, zz),
                        bevel=1, mat=MATS["graphite_dark"])
    # rear cable connector block
    mb.add_cube((64, 30, 34), loc=(0, 50, 46), bevel=4,
                mat=MATS["graphite_dark"])
    for sx in (-1, 1):
        mb.add_cyl(9, 18, loc=(sx * 18, 50, 62), axis="Z", seg=20,
                   mat=MATS["black_anod"])
        mb.add_cyl(12, 6, loc=(sx * 18, 50, 54), axis="Z", seg=6,
                   mat=MATS["steel"])
    # front plate
    mb.add_cube((160, 8, 130), loc=(0, 186, 0), bevel=4,
                mat=MATS["camera_front"])
    for bx in (-66, 66):
        for bz in (-52, 52):
            add_bolt(mb, (bx, 190, bz), axis="Y", r=5, h=3.5,
                     mat=MATS["black_anod"], washer_mat=MATS["steel"])
    # lens mount + barrel
    mb.add_cyl(42, 9, loc=(0, 194, 0), axis="Y", seg=40, bevel=1.5,
               mat=MATS["black_anod"])
    mb.add_cyl(33, 72, loc=(0, 233, 0), axis="Y", seg=40, bevel=2,
               mat=MATS["black_anod"])
    mb.add_cyl(36, 14, loc=(0, 220, 0), axis="Y", seg=48,
               mat=MATS["black_anod"])
    # knurled focus ring
    for k in range(20):
        a = 2 * math.pi * k / 20
        mb.add_cube((3, 10, 6),
                    loc=(math.cos(a) * 36.5, 220, math.sin(a) * 36.5),
                    rot=Euler((0, 0, a)), mat=MATS["steel"])
    mb.add_ring(38, 32, 6, loc=(0, 265, 0), axis="Y", seg=40,
                mat=MATS["steel"])
    # lens hood
    mb.add_ring(46, 34, 24, loc=(0, 279, 0), axis="Y", seg=48,
                mat=MATS["black_anod"], bevel=2)
    # glass
    mb.add_cyl(33, 3, loc=(0, 284, 0), axis="Y", seg=48,
               mat=MATS["lens_glass"])
    mb.add_cyl(30, 7, loc=(0, 277, 0), axis="Y", seg=40,
               mat=MATS["lens_inner"])
    # status indicator
    mb.add_cyl(4, 4, loc=(54, 190, 46), axis="Y", seg=16,
               mat=MATS["ind_green"])
    col_key = {"Camera_Center": "cam_c", "Camera_Left": "cam_l",
               "Camera_Right": "cam_r"}[name]
    ob = mb.finish(C[col_key], name)
    place(ob, (world_x, P["cam_mount_y"], P["cam_z"]),
          rot=(0, 0, yaw_deg), parent=R["array"])
    shade_smooth(ob)

    # label plate + text on top of the housing, parented to the camera
    mw = ob.matrix_world
    loc = mw @ Vector((0, 100 * MM, 61 * MM))
    nrm = (mw.to_3x3() @ Vector((0, 0, 1))).normalized()
    add_label(label, 12, (loc.x / MM, loc.y / MM, loc.z / MM),
              tuple(nrm), C["photo"], MATS, plate_size=(72, 20, 2),
              name=name + "_Label", parent=ob)
    return ob


def build_led(name, sx, C, R, MATS):
    mb = MB(name)
    y0 = P["led_y0"]
    y1 = P["led_y1"]
    z0 = P["led_z0"]
    z1 = P["led_z1"]
    # housing
    mb.add_cube((100, y1 - y0, z1 - z0), bevel=6, seg=3,
                mat=MATS["graphite"])
    # front bezel
    mb.add_cube((92, 8, 30), loc=(0, (y1 - y0) / 2 + 1, 0), bevel=4,
                mat=MATS["black_anod"])
    # diffuser
    mb.add_cube((82, 3, 22), loc=(0, (y1 - y0) / 2 + 5, 0), bevel=2,
                mat=MATS["led_diffuser"])
    # LED array
    for ix in range(4):
        for iz in range(2):
            mb.add_cyl(6, 2.5,
                       loc=(-30 + ix * 20, (y1 - y0) / 2 + 6.5,
                            -10 + iz * 20),
                       axis="Y", seg=16, mat=MATS["led"])
    # rear connector
    mb.add_cyl(10, 14, loc=(0, -(y1 - y0) / 2 - 2, -10), axis="Y",
               seg=20, mat=MATS["black_anod"])
    # side ribs
    for zz in (-14, 0, 14):
        mb.add_cube((2, 70, 4), loc=(sx * 50.5, 0, zz), bevel=1,
                    mat=MATS["graphite_dark"])
    ob = mb.finish(C["led_l" if sx > 0 else "led_r"], name)
    place(ob, (sx * P["led_x"], (y0 + y1) / 2, (z0 + z1) / 2),
          parent=R["array"])
    shade_smooth(ob)
    return ob


def build_photogrammetry(C, R, MATS):
    build_camera("Camera_Center", "CAM-C", 0.0, 0.0, C, R, MATS)
    build_camera("Camera_Left", "CAM-L", -P["cam_x"], P["cam_yaw"], C, R, MATS)
    build_camera("Camera_Right", "CAM-R", P["cam_x"], -P["cam_yaw"], C, R, MATS)
    build_led("LED_Left", -1, C, R, MATS)
    build_led("LED_Right", 1, C, R, MATS)

# ============================================================================
#  MODULE E -- ELECTRICAL + DETAILS + CABLING
# ============================================================================


def build_electrical(C, R, MATS):
    root = R["electrical"]
    zt = P["plat_z1"]

    # ---- junction box -----------------------------------------------------
    mb = MB("Elec_JunctionBox")
    mb.add_cube((240, 120, 100), loc=(0, 0, 50), bevel=8, seg=3,
                mat=MATS["panel"])
    mb.add_cube((250, 130, 10), loc=(0, 0, 103), bevel=4,
                mat=MATS["graphite"])
    mb.add_cube((222, 102, 4), loc=(0, 0, 108), bevel=2,
                mat=MATS["graphite_dark"])
    for xx in (-100, 100):
        for yy in (-45, 45):
            add_bolt(mb, (xx, yy, 110), axis="Z", r=6, h=4.5,
                     mat=MATS["black_anod"], washer_mat=MATS["steel"])
    # latches on the front face
    for xx in (-80, 80):
        mb.add_cube((40, 16, 26), loc=(xx, -66, 55), bevel=3,
                    mat=MATS["steel"])
    # cable glands
    for xx in (-55, 0, 55):
        mb.add_cyl(15, 12, loc=(xx, -66, 30), axis="Y", seg=24,
                   mat=MATS["black_anod"])
        mb.add_cyl(10, 16, loc=(xx, -72, 30), axis="Y", seg=6,
                   mat=MATS["steel"])
    # feet
    for xx in (-100, 100):
        for yy in (-45, 45):
            mb.add_cube((26, 26, 10), loc=(xx, yy, -5), bevel=2,
                        mat=MATS["steel"])
    ob = mb.finish(C["electrical"], "Elec_JunctionBox")
    place(ob, (0, -230, zt), parent=root)
    shade_smooth(ob)

    add_label("JUNCTION BOX  JB-01", 11, (0, -230, zt + 111), (0, 0, 1),
              C["electrical"], MATS, plate_size=(200, 24, 2),
              name="Elec_JB_Label", parent=root)

    # ---- emergency stop ---------------------------------------------------
    mb = MB("Elec_EmergencyStop")
    mb.add_cube((72, 72, 10), loc=(0, 0, 5), bevel=3,
                mat=MATS["yellow"])
    mb.add_cyl(30, 5, loc=(0, 0, 12), axis="Z", seg=32,
               mat=MATS["yellow"])
    mb.add_cyl(16, 18, loc=(0, 0, 24), axis="Z", seg=24,
               mat=MATS["black_anod"])
    mb.add_cyl(25, 12, loc=(0, 0, 37), axis="Z", seg=32, bevel=5,
               mat=MATS["ind_red"])
    mb.add_cyl(20, 3, loc=(0, 0, 44), axis="Z", seg=32,
               mat=MATS["ind_red"])
    ob = mb.finish(C["electrical"], "Elec_EmergencyStop")
    place(ob, (170, -270, zt), parent=root)
    shade_smooth(ob)
    add_label("E-STOP", 10, (170, -312, zt + 2), (0, 0, 1),
              C["electrical"], MATS, plate_size=(70, 20, 2),
              name="Elec_EStop_Label", parent=root)

    # ---- status light tower ----------------------------------------------
    mb = MB("Elec_StatusTower")
    mb.add_cyl(24, 10, loc=(0, 0, 5), axis="Z", seg=32,
               mat=MATS["black_anod"])
    mb.add_cyl(18, 26, loc=(0, 0, 23), axis="Z", seg=32,
               mat=MATS["ind_green"])
    mb.add_cyl(18, 26, loc=(0, 0, 49), axis="Z", seg=32,
               mat=MATS["ind_amber"])
    mb.add_cyl(18, 26, loc=(0, 0, 75), axis="Z", seg=32,
               mat=MATS["ind_red"])
    mb.add_cyl(20, 6, loc=(0, 0, 90), axis="Z", seg=32,
               mat=MATS["black_anod"])
    ob = mb.finish(C["electrical"], "Elec_StatusTower")
    place(ob, (-180, -280, zt), parent=root)
    shade_smooth(ob)

    # ---- antenna + GPS ----------------------------------------------------
    mb = MB("Elec_Antenna")
    mb.add_cyl(22, 8, loc=(0, 0, 4), axis="Z", seg=28,
               mat=MATS["black_anod"])
    mb.add_cyl(6, 190, loc=(0, 0, 100), axis="Z", seg=16,
               mat=MATS["black_anod"])
    mb.add_cyl(2.5, 60, loc=(0, 0, 220), axis="Z", seg=12,
               mat=MATS["steel"])
    ob = mb.finish(C["electrical"], "Elec_Antenna")
    place(ob, (0, -308, zt), parent=root)
    shade_smooth(ob)

    mb = MB("Elec_GPS")
    mb.add_cyl(30, 12, loc=(0, 0, 6), axis="Z", seg=32, bevel=3,
               mat=MATS["graphite_dark"])
    mb.add_cyl(24, 4, loc=(0, 0, 13), axis="Z", seg=32,
               mat=MATS["panel"])
    ob = mb.finish(C["electrical"], "Elec_GPS")
    place(ob, (110, -300, zt), parent=root)
    shade_smooth(ob)

    # ---- short interconnect cables on the platform ----------------------
    add_cable("Elec_Cable_JB_to_Tower",
              [(80, -250, 500), (20, -255, 502), (-60, -270, 502),
               (-140, -278, 498), (-175, -280, 496)],
              4.0, C["electrical"], MATS["cable"], parent=root)
    add_cable("Elec_Cable_JB_to_EStop",
              [(85, -205, 505), (120, -220, 500), (150, -250, 496),
               (168, -268, 494)],
              4.0, C["electrical"], MATS["cable"], parent=root)


def build_details(C, R, MATS):
    root = R["details"]

    # rear reflective warning stripes on the bumper
    mb = MB("Detail_ReflectiveStripes")
    for i, xx in enumerate((-150, -75, 0, 75, 150)):
        mb.add_cube((48, 4, 34), loc=(xx, 0, 0), bevel=2,
                    mat=MATS["yellow"])
    ob = mb.finish(C["details"], "Detail_ReflectiveStripes")
    place(ob, (0, -P["low_l"] / 2 - 30, 130), parent=root)
    shade_smooth(ob)

    # QR-style service label on the right service hatch
    mb = MB("Detail_ServiceQR")
    mb.add_cube((56, 2, 56), loc=(0, 0, 0), bevel=2,
                mat=MATS["label_white"])
    cells = [(0, 0), (1, 0), (2, 0), (4, 0), (5, 0), (6, 0),
             (0, 1), (2, 1), (4, 1), (6, 1),
             (0, 2), (1, 2), (2, 2), (5, 2), (6, 2),
             (0, 3), (3, 3), (4, 3), (6, 3),
             (0, 4), (2, 4), (3, 4), (5, 4), (6, 4),
             (0, 5), (2, 5), (4, 5), (6, 5),
             (0, 6), (1, 6), (2, 6), (4, 6), (5, 6), (6, 6)]
    for cx, cz in cells:
        mb.add_cube((5.4, 1.2, 5.4),
                    loc=(-21 + cx * 7, 1.8, -21 + cz * 7),
                    mat=MATS["label_dark"])
    ob = mb.finish(C["details"], "Detail_ServiceQR")
    place(ob, (-(P["up_w"] / 2 + 7), -225, 355), rot=(0, 0, 90),
          parent=root)
    shade_smooth(ob)

    # tie-down rings on the lower chassis
    mb = MB("Detail_TieDowns")
    for xx in (-170, 170):
        for yy in (-250, 250):
            mb.add_cyl(12, 10, loc=(xx, yy, 0), axis="Z", seg=6,
                       mat=MATS["steel"])
            mb.add_ring(17, 8, 8, loc=(xx, yy, 0), axis="X", seg=20,
                        mat=MATS["steel"])
    ob = mb.finish(C["details"], "Detail_TieDowns")
    place(ob, (0, 0, 72), parent=root)
    shade_smooth(ob)


def build_cabling(C, R, MATS):
    root = R["array"]
    cam_y = P["cam_mount_y"]
    cam_z = P["cam_z"]

    # camera harness - service loops over the beam and down the left pillar
    add_cable("Cable_Cam_Center",
              [(18, cam_y + 50, cam_z + 66),
               (18, cam_y + 55, cam_z + 86),
               (18, cam_y + 20, cam_z + 90),
               (18, 110, cam_z + 72),
               (40, 95, cam_z + 64),
               (140, 90, cam_z + 58),
               (210, 80, cam_z + 40),
               (234, 72, cam_z - 30),
               (240, 64, 1100),
               (240, 64, 900),
               (238, 62, 650),
               (232, 50, 540),
               (205, 20, 510),
               (160, -40, 498),
               (110, -110, 506),
               (86, -160, 520)],
              4.2, C["cabling"], MATS["cable"], parent=root)

    add_cable("Cable_Cam_Right",
              [(249, cam_y + 42, cam_z + 66),
               (252, cam_y + 46, cam_z + 86),
               (248, cam_y + 12, cam_z + 92),
               (255, 100, cam_z + 72),
               (248, 85, cam_z + 56),
               (242, 74, cam_z + 20),
               (246, 66, 1150),
               (246, 64, 900),
               (244, 62, 650),
               (238, 52, 540),
               (215, 22, 512),
               (170, -40, 500),
               (118, -112, 508),
               (92, -160, 522)],
              4.2, C["cabling"], MATS["cable"], parent=root)

    add_cable("Cable_Cam_Left",
              [(-249, cam_y + 42, cam_z + 66),
               (-252, cam_y + 46, cam_z + 86),
               (-248, cam_y + 10, cam_z + 92),
               (-250, 100, cam_z + 74),
               (-150, 88, cam_z + 68),
               (0, 86, cam_z + 64),
               (150, 84, cam_z + 58),
               (222, 76, cam_z + 36),
               (232, 70, cam_z - 20),
               (236, 66, 1150),
               (236, 64, 900),
               (234, 62, 650),
               (228, 52, 540),
               (200, 22, 510),
               (155, -40, 498),
               (105, -112, 506),
               (82, -160, 520)],
              4.2, C["cabling"], MATS["cable"], parent=root)

    # LED power leads
    for sx, nm in ((-1, "Left"), (1, "Right")):
        add_cable("Cable_LED_" + nm,
                  [(sx * P["led_x"], P["led_y0"] - 8, 1360),
                   (sx * P["led_x"], 90, 1352),
                   (sx * 280, 80, 1348),
                   (sx * 250, 72, 1330),
                   (sx * 242, 68, 1200),
                   (sx * 242, 66, 900),
                   (sx * 238, 58, 560),
                   (sx * 220, 30, 520),
                   (sx * 190, -10, 505),
                   (sx * 150, -80, 502),
                   (sx * 110, -150, 515)],
                  3.2, C["cabling"], MATS["cable"], parent=root)

    # cable sleeve on the vertical run of the right pillar
    mb = MB("Cable_Sleeve")
    mb.add_cyl(13, 520, loc=(0, 0, 0), axis="Z", seg=20,
               mat=MATS["cable_sleeve"])
    for i in range(18):
        mb.add_cyl(14.5, 5, loc=(0, 0, -250 + i * 29), axis="Z",
                   seg=20, mat=MATS["cable_sleeve"])
    ob = mb.finish(C["cabling"], "Cable_Sleeve")
    place(ob, (241, 64, 900), parent=R["support"])
    shade_smooth(ob)

    # clips
    mb = MB("Cable_Clips")
    for zz in (600, 760, 920, 1080):
        mb.add_cube((26, 22, 14), loc=(0, 0, zz), bevel=3,
                    mat=MATS["black_anod"])
    ob = mb.finish(C["cabling"], "Cable_Clips")
    place(ob, (241, 64, 0), parent=R["support"])
    shade_smooth(ob)

    mb = MB("Cable_Clips_Beam")
    for xx in (-260, -140, 0, 140, 260):
        mb.add_cube((18, 18, 12), loc=(xx, 0, 0), bevel=2,
                    mat=MATS["black_anod"])
    ob = mb.finish(C["cabling"], "Cable_Clips_Beam")
    place(ob, (0, 100, 1346), parent=root)
    shade_smooth(ob)

    # track motor cables
    for sx, nm in ((-1, "Left"), (1, "Right")):
        add_cable("Cable_TrackMotor_" + nm,
                  [(sx * (P["low_w"] / 2 + 2), 250, 200),
                   (sx * (P["low_w"] / 2 + 8), 245, 180),
                   (sx * 214, 240, 150),
                   (sx * 220, 235, 120)],
                  6.0, C["details"], MATS["cable_sleeve"],
                  parent=R["chassis"])
        mb = MB("Cable_Gland_" + nm)
        mb.add_cyl(13, 10, loc=(0, 0, 0), axis="X", seg=20,
                   mat=MATS["black_anod"])
        ob = mb.finish(C["details"], "Cable_Gland_" + nm)
        place(ob, (sx * (P["low_w"] / 2 + 4), 250, 200), parent=R["chassis"])
        shade_smooth(ob)

# ============================================================================
#  SCENES / STUDIO / CAMERAS
# ============================================================================


def reset_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    for s in list(bpy.data.scenes)[1:]:
        bpy.data.scenes.remove(s)
    scene = bpy.context.scene
    scene.name = "01_Robot_Asset"
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.scale_length = 1.0
    scene.unit_settings.length_unit = "MILLIMETERS"
    world = bpy.data.worlds.new("World_Asset")
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    bg.inputs["Color"].default_value = (0.035, 0.038, 0.042, 1.0)
    bg.inputs["Strength"].default_value = 0.6
    scene.world = world
    return scene


def build_cyclorama(collection, mat):
    profile = [(0.0, -0.002), (2.0, -0.002), (4.2, -0.002)]
    for i in range(1, 13):
        a = -math.pi / 2 + (math.pi / 2) * i / 12
        profile.append((4.2 + 1.3 * math.cos(a), 1.3 + 1.3 * math.sin(a)))
    profile += [(5.5, 2.0), (5.5, 4.0), (5.5, 7.0)]
    seg = 96
    verts = [(0.0, 0.0, profile[0][1])]
    ring_starts = [0]
    for r, z in profile[1:]:
        ring_starts.append(len(verts))
        for i in range(seg):
            a = 2 * math.pi * i / seg
            verts.append((r * math.cos(a), r * math.sin(a), z))
    faces = []
    first = ring_starts[1]
    for i in range(seg):
        j = (i + 1) % seg
        faces.append((0, first + i, first + j))
    for k in range(1, len(ring_starts) - 1):
        a0 = ring_starts[k]
        a1 = ring_starts[k + 1]
        for i in range(seg):
            j = (i + 1) % seg
            faces.append((a0 + i, a1 + i, a1 + j, a0 + j))
    mb = MB("Studio_Cyclorama", scale=1.0)
    mb.add_raw(verts, faces, mat=mat)
    ob = mb.finish(collection, "Studio_Cyclorama")
    for p in ob.data.polygons:
        p.use_smooth = True
    return ob


def add_area_light(name, size, energy, color, collection):
    data = bpy.data.lights.new(name, "AREA")
    data.shape = "SQUARE"
    data.size = size
    data.energy = energy
    data.color = color
    ob = bpy.data.objects.new(name, data)
    collection.objects.link(ob)
    ob.visible_camera = False
    return ob


def aim_light(light, pos, target):
    light.location = Vector(pos)
    d = Vector(target) - light.location
    light.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()


def aim_studio_lights(lights, cam_loc_m, target_m):
    tgt = Vector(target_m)
    cam = Vector(cam_loc_m)
    d = Vector((cam.x - tgt.x, cam.y - tgt.y, 0.0))
    if d.length < 1e-5:
        d = Vector((1.0, 0.0, 0.0))
    d.normalize()
    rot = Matrix.Rotation
    key_dir = (rot(math.radians(42), 4, "Z") @ d).to_3d()
    fill_dir = (rot(math.radians(-58), 4, "Z") @ d).to_3d()
    rim_dir = (rot(math.radians(172), 4, "Z") @ d).to_3d()
    aim_light(lights["key"], tgt + key_dir * 3.0 + Vector((0, 0, 2.8)),
              tgt + Vector((0, 0, 0.25)))
    aim_light(lights["fill"], tgt + fill_dir * 3.2 + Vector((0, 0, 1.7)),
              tgt + Vector((0, 0, 0.3)))
    aim_light(lights["rim"], tgt + rim_dir * 3.2 + Vector((0, 0, 2.4)),
              tgt + Vector((0, 0, 0.45)))
    aim_light(lights["top"], tgt + Vector((0.2, 0.0, 4.2)),
              tgt)


def add_camera(name, loc_mm, target_mm, lens, fstop, collection):
    data = bpy.data.cameras.new(name)
    data.lens = lens
    data.sensor_width = 36.0
    data.sensor_fit = "HORIZONTAL"
    data.clip_start = 0.05
    data.clip_end = 120.0
    ob = bpy.data.objects.new(name, data)
    collection.objects.link(ob)
    ob.location = V(*loc_mm)
    d = V(*target_mm) - ob.location
    ob.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
    data.dof.use_dof = True
    data.dof.focus_distance = d.length
    data.dof.aperture_fstop = fstop
    return ob


def build_studio_scene(robot_col, MATS):
    scene = bpy.data.scenes.new("02_Product_Studio")
    scene.collection.children.link(robot_col)
    world = bpy.data.worlds.new("World_Studio")
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    bg.inputs["Color"].default_value = (0.025, 0.027, 0.030, 1.0)
    bg.inputs["Strength"].default_value = 0.45
    scene.world = world

    set_col = bpy.data.collections.new("Studio_Set")
    scene.collection.children.link(set_col)
    build_cyclorama(set_col, MATS["studio"])

    lights_col = bpy.data.collections.new("Studio_Lights")
    scene.collection.children.link(lights_col)
    lights = {
        "key": add_area_light("Light_Key", 3.2, 850.0, (1.0, 0.97, 0.93),
                              lights_col),
        "fill": add_area_light("Light_Fill", 2.6, 260.0, (0.88, 0.93, 1.0),
                               lights_col),
        "rim": add_area_light("Light_Rim", 2.0, 700.0, (0.95, 0.97, 1.0),
                              lights_col),
        "top": add_area_light("Light_Top", 4.0, 260.0, (1.0, 1.0, 1.0),
                              lights_col),
    }

    cam_col = bpy.data.collections.new("Product_Cameras")
    scene.collection.children.link(cam_col)
    views = [
        dict(name="01_Hero_ThreeQuarter", loc=(3100, 3600, 980),
             target=(0, 80, 770), lens=65, fstop=5.6,
             file="01_Hero_ThreeQuarter.png"),
        dict(name="02_Front_Camera_Array", loc=(0, 4800, 1000),
             target=(0, 0, 740), lens=70, fstop=8.0,
             file="02_Front_Camera_Array.png"),
        dict(name="03_Side_Profile", loc=(4700, 0, 900),
             target=(0, 0, 730), lens=70, fstop=8.0,
             file="03_Side_Profile.png"),
        dict(name="04_Rear_ThreeQuarter", loc=(3000, -4300, 1800),
             target=(0, -100, 800), lens=70, fstop=8.0,
             file="04_Rear_ThreeQuarter.png"),
        dict(name="05_Top_Layout", loc=(1250, 1650, 5200),
             target=(0, 0, 950), lens=70, fstop=8.0,
             file="05_Top_Layout.png"),
        dict(name="06_Photogrammetry_Closeup", loc=(1850, 2650, 1600),
             target=(0, 280, 1320), lens=100, fstop=4.0,
             file="06_Photogrammetry_Closeup.png"),
    ]
    for v in views:
        v["camera"] = add_camera(v["name"], v["loc"], v["target"],
                                 v["lens"], v["fstop"], cam_col)
    scene.camera = views[0]["camera"]
    return scene, views, lights


def setup_render(scene, samples, res=(1920, 1440), exposure=0.35,
                 look="AgX - Medium High Contrast"):
    scene.render.engine = "CYCLES"
    scene.cycles.device = "GPU"
    scene.cycles.samples = samples
    scene.cycles.use_denoising = True
    try:
        scene.cycles.denoiser = "OPENIMAGEDENOISE"
    except Exception:
        pass
    scene.cycles.max_bounces = 8
    scene.cycles.diffuse_bounces = 4
    scene.cycles.glossy_bounces = 4
    scene.cycles.transmission_bounces = 8
    scene.cycles.transparent_max_bounces = 8
    scene.cycles.use_adaptive_sampling = True
    scene.cycles.adaptive_threshold = 0.02
    scene.render.resolution_x = res[0]
    scene.render.resolution_y = res[1]
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.render.image_settings.color_depth = "8"
    scene.render.film_transparent = False
    scene.render.use_persistent_data = True
    scene.view_settings.view_transform = "AgX"
    try:
        scene.view_settings.look = look
    except Exception:
        for alt in ("AgX - Medium High Contrast", "AgX - Punchy",
                    "AgX - Medium Low Contrast", "None"):
            try:
                scene.view_settings.look = alt
                break
            except Exception:
                continue
    scene.view_settings.exposure = exposure

# ============================================================================
#  TUNNEL PREVIEW SCENE
# ============================================================================


def tunnel_profile(n_arc=40):
    pts = [(-4.0, 0.0), (-4.0, 2.0)]
    for i in range(1, n_arc):
        a = math.pi - math.pi * i / n_arc
        pts.append((4.0 * math.cos(a), 2.0 + 4.0 * math.sin(a)))
    pts += [(4.0, 2.0), (4.0, 0.0)]
    return pts


def _profile_normals(pts):
    n = len(pts)
    out = []
    for i in range(n):
        x0, z0 = pts[(i - 1) % n]
        x1, z1 = pts[(i + 1) % n]
        tx, tz = x1 - x0, z1 - z0
        ln = math.hypot(tx, tz) or 1.0
        tx /= ln
        tz /= ln
        out.append((-tz, tx))
    return out


def build_tunnel_scene(robot_col, MATS):
    scene = bpy.data.scenes.new("03_Tunnel_Preview")
    scene.collection.children.link(robot_col)
    world = bpy.data.worlds.new("World_Tunnel")
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    bg.inputs["Color"].default_value = (0.004, 0.005, 0.007, 1.0)
    bg.inputs["Strength"].default_value = 0.6
    scene.world = world

    col = bpy.data.collections.new("Tunnel")
    scene.collection.children.link(col)

    profile = tunnel_profile(40)
    nprof = len(profile)
    y0, y1 = -12.0, 9.0
    ny = 40
    verts = []
    for iy in range(ny + 1):
        yy = y0 + (y1 - y0) * iy / ny
        for px, pz in profile:
            verts.append((px, yy, pz))
    faces = []
    for iy in range(ny):
        for ip in range(nprof - 1):
            a = iy * nprof + ip
            b = (iy + 1) * nprof + ip
            faces.append((a, b, b + 1, a + 1))
    mb = MB("Tunnel_Lining", scale=1.0)
    mb.add_raw(verts, faces, mat=MATS["concrete"])
    wall = mb.finish(col, "Tunnel_Lining")
    mod = wall.modifiers.new("solid", "SOLIDIFY")
    mod.thickness = 0.30
    mod.offset = 0.0
    tex = bpy.data.textures.new("Tunnel_RockNoise", "CLOUDS")
    tex.noise_scale = 1.1
    tex.noise_depth = 4
    dsp = wall.modifiers.new("rock", "DISPLACE")
    dsp.texture = tex
    dsp.strength = 0.16
    dsp.mid_level = 0.5
    apply_modifiers(wall)
    shade_smooth(wall, 50)

    # ---- rock ribs --------------------------------------------------------
    nrm = _profile_normals(profile)
    mb = MB("Tunnel_Ribs", scale=1.0)
    for ry in (-9.0, -6.0, -3.0, 0.0, 3.0, 6.0):
        vv = []
        for (px, pz), (nx, nz) in zip(profile, nrm):
            for dy in (-0.09, 0.09):
                for off in (0.02, 0.24):
                    vv.append((px + nx * off, ry + dy, pz + nz * off))
        # order per point: (dy-, off0), (dy-, off1), (dy+, off0), (dy+, off1)
        ff = []
        for ip in range(nprof - 1):
            a = ip * 4
            b = (ip + 1) * 4
            ff.append((a + 0, b + 0, b + 2, a + 2))      # inner face
            ff.append((a + 1, a + 3, b + 3, b + 1))      # outer face
            ff.append((a + 0, a + 1, b + 1, b + 0))      # side -y
            ff.append((a + 2, b + 2, b + 3, a + 3))      # side +y
        mb.add_raw(vv, ff, mat=MATS["steel"])
    ob = mb.finish(col, "Tunnel_Ribs")
    shade_smooth(ob, 40)

    # ---- floor ------------------------------------------------------------
    mb = MB("Tunnel_Floor", scale=1.0)
    mb.add_cube((8.0, y1 - y0, 0.06), loc=(0, (y0 + y1) / 2, -0.03),
                mat=MATS["tunnel_floor"])
    for sx in (-1, 1):
        mb.add_cube((0.08, y1 - y0, 0.02), loc=(sx * 3.85, (y0 + y1) / 2, 0.01),
                    mat=MATS["yellow"])
    mb.add_cube((0.30, y1 - y0, 0.02), loc=(2.5, (y0 + y1) / 2, 0.01),
                mat=MATS["graphite_dark"])
    ob = mb.finish(col, "Tunnel_Floor")
    shade_smooth(ob, 30)

    # ---- services ---------------------------------------------------------
    mb = MB("Tunnel_Services", scale=1.0)
    for i, (xx, zz, rr) in enumerate(((3.55, 2.30, 0.07),
                                      (3.55, 2.05, 0.05),
                                      (-3.55, 2.60, 0.06))):
        mb.add_cyl(rr, y1 - y0, loc=(xx, (y0 + y1) / 2, zz), axis="Y",
                   seg=20, mat=MATS["steel"])
    for yy in (-9.0, -5.0, -1.0, 3.0, 7.0):
        for xx, zz in ((3.72, 2.30), (3.72, 2.05), (-3.72, 2.60)):
            mb.add_cube((0.10, 0.10, 0.10), loc=(xx, yy, zz), bevel=0.01,
                        mat=MATS["black_anod"])
    # cable tray on the left wall
    mb.add_cube((0.24, y1 - y0, 0.05), loc=(-3.75, (y0 + y1) / 2, 3.15),
                mat=MATS["black_anod"])
    mb.add_cube((0.04, y1 - y0, 0.14), loc=(-3.88, (y0 + y1) / 2, 3.20),
                mat=MATS["black_anod"])
    ob = mb.finish(col, "Tunnel_Services")
    shade_smooth(ob, 40)

    # ---- work light fixtures ---------------------------------------------
    mb = MB("Tunnel_LightFixtures", scale=1.0)
    for yy in (-6.0, -1.0, 4.0):
        mb.add_cube((0.34, 0.72, 0.14),
                    loc=(3.70, yy, 4.25),
                    rot=Euler((0, math.radians(18), 0)),
                    bevel=0.02, mat=MATS["black_anod"])
        mb.add_cube((0.26, 0.60, 0.04),
                    loc=(3.62, yy, 4.17),
                    rot=Euler((0, math.radians(18), 0)),
                    mat=MATS["work_light"])
    ob = mb.finish(col, "Tunnel_LightFixtures")
    shade_smooth(ob, 40)

    lights = []
    for yy in (-6.0, -1.0, 4.0):
        ld = bpy.data.lights.new("Tunnel_WorkLight_%d" % int(yy + 10),
                                 "AREA")
        ld.shape = "RECTANGLE"
        ld.size = 0.8
        ld.size_y = 0.4
        ld.energy = 380.0
        ld.color = (1.0, 0.74, 0.44)
        lo = bpy.data.objects.new(ld.name, ld)
        col.objects.link(lo)
        lo.location = (3.35, yy, 4.05)
        d = Vector((0.0, yy + 1.2, 0.9)) - lo.location
        lo.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
        lo.visible_camera = False
        lights.append(lo)

    # face work light
    ld = bpy.data.lights.new("Tunnel_FaceLight", "AREA")
    ld.shape = "SQUARE"
    ld.size = 2.4
    ld.energy = 2200.0
    ld.color = (1.0, 0.82, 0.62)
    lo = bpy.data.objects.new(ld.name, ld)
    col.objects.link(lo)
    lo.location = (0.0, 5.2, 3.8)
    d = Vector((0.0, 9.0, 2.0)) - lo.location
    lo.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
    lo.visible_camera = False

    # cool fill from behind camera
    ld = bpy.data.lights.new("Tunnel_Fill", "AREA")
    ld.shape = "SQUARE"
    ld.size = 3.0
    ld.energy = 420.0
    ld.color = (0.62, 0.75, 1.0)
    lo = bpy.data.objects.new(ld.name, ld)
    col.objects.link(lo)
    lo.location = (-2.6, -5.5, 3.4)
    d = Vector((0.0, 0.0, 0.8)) - lo.location
    lo.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
    lo.visible_camera = False

    # ---- rock face --------------------------------------------------------
    nx, nz = 34, 26
    fverts = []
    for iz in range(nz + 1):
        for ix in range(nx + 1):
            fverts.append((-4.0 + 8.0 * ix / nx, y1,
                           0.0 + 6.1 * iz / nz))
    ffaces = []
    for iz in range(nz):
        for ix in range(nx):
            a = iz * (nx + 1) + ix
            b = a + (nx + 1)
            ffaces.append((a, a + 1, b + 1, b))
    mb = MB("Tunnel_Face", scale=1.0)
    mb.add_raw(fverts, ffaces, mat=MATS["rock_face"])
    face = mb.finish(col, "Tunnel_Face")
    tex2 = bpy.data.textures.new("Tunnel_FaceNoise", "CLOUDS")
    tex2.noise_scale = 0.55
    tex2.noise_depth = 5
    dsp = face.modifiers.new("rock", "DISPLACE")
    dsp.texture = tex2
    dsp.strength = 0.55
    dsp.mid_level = 0.45
    apply_modifiers(face)
    shade_smooth(face, 55)

    # ---- haze volume ------------------------------------------------------
    haze_mat = bpy.data.materials.new("MAT_Tunnel_Haze")
    haze_mat.use_nodes = True
    nt = haze_mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    vol = nt.nodes.new("ShaderNodeVolumePrincipled")
    vol.inputs["Color"].default_value = (0.72, 0.74, 0.80, 1.0)
    vol.inputs["Density"].default_value = 0.0045
    vol.inputs["Anisotropy"].default_value = 0.35
    nt.links.new(vol.outputs["Volume"], out.inputs["Volume"])
    mb = MB("Tunnel_Haze", scale=1.0)
    mb.add_cube((7.8, y1 - y0 - 0.2, 5.8),
                loc=(0, (y0 + y1) / 2, 2.9), mat=haze_mat)
    ob = mb.finish(col, "Tunnel_Haze")
    ob.visible_shadow = False

    # ---- camera -----------------------------------------------------------
    cam_col = bpy.data.collections.new("Tunnel_Camera")
    scene.collection.children.link(cam_col)
    cam = add_camera("Tunnel_Camera", (1700, -2600, 1450),
                     (0, 1200, 1000), 35, 5.6, cam_col)
    scene.camera = cam

    setup_render(scene, P["samples"], (1920, 1440), exposure=0.55)
    return scene, cam


# ============================================================================
#  VALIDATION / EXPORT / RENDER
# ============================================================================


def report_dimensions(C, R):
    mn = Vector((1e9, 1e9, 1e9))
    mx = Vector((-1e9, -1e9, -1e9))
    count = 0
    for ob in C["root"].all_objects:
        if ob.type != "MESH":
            continue
        count += 1
        for corner in ob.bound_box:
            w = ob.matrix_world @ Vector(corner)
            mn.x = min(mn.x, w.x)
            mn.y = min(mn.y, w.y)
            mn.z = min(mn.z, w.z)
            mx.x = max(mx.x, w.x)
            mx.y = max(mx.y, w.y)
            mx.z = max(mx.z, w.z)
    size = mx - mn
    print("=" * 70)
    print("ROBOT BOUNDING BOX (m): min=(%.3f, %.3f, %.3f) max=(%.3f, %.3f, %.3f)"
          % (mn.x, mn.y, mn.z, mx.x, mx.y, mx.z))
    print("ROBOT SIZE (mm): L(Y)=%.1f  W(X)=%.1f  H(Z)=%.1f"
          % (size.y * 1000, size.x * 1000, size.z * 1000))
    print("MESH OBJECTS: %d   COLLECTIONS: %d" %
          (count, len(bpy.data.collections)))
    print("=" * 70)
    R["robot"]["asset_name"] = "TPR-900 Tracked Photogrammetry Robot"
    R["robot"]["design_units"] = "millimetres (scene unit = metre)"
    R["robot"]["size_mm"] = "%.1f x %.1f x %.1f" % (
        size.x * 1000, size.y * 1000, size.z * 1000)
    R["robot"]["track_ground_contact_z"] = "0.0"
    return size


def export_assets(C, R):
    bpy.ops.object.select_all(action="DESELECT")
    for ob in C["root"].all_objects:
        try:
            ob.select_set(True)
        except Exception:
            pass
    bpy.context.view_layer.objects.active = R["robot"]
    bpy.ops.export_scene.gltf(
        filepath=GLB_PATH, export_format="GLB", use_selection=True,
        export_apply=True, export_yup=True, export_cameras=False,
        export_lights=False, export_extras=True)
    print("GLB exported:", GLB_PATH)
    try:
        bpy.ops.export_scene.fbx(
            filepath=FBX_PATH, use_selection=True,
            object_types={"EMPTY", "MESH"}, apply_unit_scale=True,
            global_scale=1.0, axis_forward="-Z", axis_up="Y",
            bake_space_transform=False, use_mesh_modifiers=True)
        print("FBX exported:", FBX_PATH)
    except Exception as exc:
        print("FBX export failed:", exc)


def enable_gpu():
    try:
        prefs = bpy.context.preferences.addons["cycles"].preferences
        for dev in ("OPTIX", "CUDA"):
            try:
                prefs.compute_device_type = dev
                prefs.refresh_devices()
                if any(d.type == dev for d in prefs.devices):
                    break
            except Exception:
                continue
        for d in prefs.devices:
            d.use = d.type in ("OPTIX", "CUDA")
        print("CYCLES DEVICES:", [(d.name, d.type, d.use)
                                  for d in prefs.devices])
    except Exception as exc:
        print("GPU setup failed:", exc)


def render_all(studio, views, lights, tunnel, tunnel_cam):
    print("Rendering %d studio views + tunnel ..." % len(views))
    for v in views:
        aim_studio_lights(lights, V(*v["loc"]), V(*v["target"]))
        studio.camera = v["camera"]
        studio.render.filepath = os.path.join(PREVIEW_DIR, v["file"])
        t0 = time.time()
        bpy.ops.render.render(write_still=True, scene=studio.name)
        print("Rendered %s in %.1fs" % (v["file"], time.time() - t0))
    tunnel.camera = tunnel_cam
    tunnel.render.filepath = os.path.join(PREVIEW_DIR,
                                          "07_Tunnel_Context.png")
    t0 = time.time()
    bpy.ops.render.render(write_still=True, scene=tunnel.name)
    print("Rendered tunnel in %.1fs" % (time.time() - t0))


def main():
    t_start = time.time()
    argv = sys.argv
    if "--" in argv:
        argv = argv[argv.index("--") + 1:]
    quick = "--quick" in argv
    no_render = "--no-render" in argv
    samples = 32 if quick else P["samples"]
    res = (960, 720) if quick else (P["render_w"], P["render_h"])

    scene = reset_scene()
    enable_gpu()
    MATS = build_materials()
    C, R = build_collections(scene)
    print("Building chassis ...")
    build_chassis(C, R, MATS)
    print("Building left track ...")
    build_track("L", -1, C, R, MATS)
    print("Building right track ...")
    build_track("R", 1, C, R, MATS)
    print("Building camera support ...")
    build_support(C, R, MATS)
    print("Building photogrammetry head ...")
    build_photogrammetry(C, R, MATS)
    print("Building electrical ...")
    build_electrical(C, R, MATS)
    print("Building details ...")
    build_details(C, R, MATS)
    print("Building cabling ...")
    build_cabling(C, R, MATS)
    bpy.context.view_layer.update()

    report_dimensions(C, R)

    print("Building studio scene ...")
    studio, views, lights = build_studio_scene(C["root"], MATS)
    setup_render(studio, samples, res, exposure=0.0)
    print("Building tunnel scene ...")
    tunnel, tunnel_cam = build_tunnel_scene(C["root"], MATS)
    setup_render(tunnel, samples, (res[0], res[1]), exposure=0.55)

    bpy.ops.wm.save_as_mainfile(filepath=BLEND_PATH)
    print("Saved:", BLEND_PATH)
    export_assets(C, R)
    bpy.ops.wm.save_as_mainfile(filepath=BLEND_PATH)

    if not no_render:
        render_all(studio, views, lights, tunnel, tunnel_cam)
    print("TOTAL TIME %.1fs" % (time.time() - t_start))


if __name__ == "__main__":
    main()

