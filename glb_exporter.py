import maya.cmds as cmds
import maya.api.OpenMaya as om
import maya.api.OpenMayaAnim as oma
import os, struct, sys, subprocess, io, json, math

# =============================================================================
#  ULTIMATE GLB EXPORTER  v1.0
#  - Multi-mesh, multi-material (one primitive per face-group)
#  - Full skeleton: joint hierarchy + skin weights + keyframe animation
#  - ORM: Make ORM texture (pack O/R/M → single PNG)
#         OR Keep Separate (embed each + write Blender node-wiring script)
#  - Non-manifold detection → warn + confirm dialog before fixing
#  - Maya (1.0) / Blender (0.01) / UE (100) scale presets
#  - Hand-written GLB 2.0 packer — no external dependencies except Pillow
# =============================================================================

# ---------------------------------------------------------------------------
# Drive detection  +  paths
# ---------------------------------------------------------------------------
LIB_PATH           = ""
DEFAULT_EXPORT_DIR = ""
SETTINGS_DIR       = ""
SETTINGS_FILE      = ""
ACTIVE_DRIVE       = ""

def _setup_paths(drive):
    """Configure all global paths for a given drive letter string e.g. 'N:'."""
    global LIB_PATH, DEFAULT_EXPORT_DIR, SETTINGS_DIR, SETTINGS_FILE, ACTIVE_DRIVE
    drive = drive.rstrip("/\\")          # normalise — accept "N" or "N:" or "N:/"
    if not drive.endswith(":"): drive += ":"
    ACTIVE_DRIVE       = drive
    LIB_PATH           = drive + "/MayaGLB/PythonPlugins"
    DEFAULT_EXPORT_DIR = drive + "/MayaGLB/Exports"
    SETTINGS_DIR       = drive + "/MayaGLB/Settings"
    SETTINGS_FILE      = SETTINGS_DIR  + "/exporter_settings.json"
    for p in [LIB_PATH, DEFAULT_EXPORT_DIR, SETTINGS_DIR]:
        if not os.path.exists(p):
            try: os.makedirs(p)
            except: pass
    if LIB_PATH not in sys.path:
        sys.path.insert(0, LIB_PATH)
    print(f"[GLB] Drive: {drive}  LIB: {LIB_PATH}  EXPORT: {DEFAULT_EXPORT_DIR}")

def _find_mayaglb_drive():
    """
    Scan every drive letter A-Z and return the first one that already
    has a MayaGLB folder on it.  Returns e.g. 'N:' or None.
    """
    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        candidate = letter + ":/MayaGLB"
        if os.path.isdir(candidate):
            print(f"[GLB] Found existing MayaGLB folder on {letter}:")
            return letter + ":"
    return None

def _show_drive_picker():
    """Show a dialog asking the user which drive to use for MayaGLB."""
    _win = "GLB_DriveSelector"
    if cmds.window(_win, exists=True): cmds.deleteUI(_win)
    cmds.window(_win, title="Select MayaGLB Drive", w=370, sizeable=False)
    cmds.columnLayout(adj=True, rs=8, co=["both", 16])
    cmds.text(l="")
    cmds.text(l="No MayaGLB folder found on any drive.", fn="boldLabelFont", al="left")
    cmds.text(l="Enter the drive letter where MayaGLB should live:", fn="smallPlainLabelFont", al="left")
    cmds.text(l="(The folder will be created automatically.)",        fn="smallPlainLabelFont", al="left")
    field = cmds.textFieldGrp(l="Drive letter:", text="N", cw2=[100, 60])
    warn  = cmds.text(l="", fn="smallPlainLabelFont", al="left")

    def _on_confirm(*a):
        letter = cmds.textFieldGrp(field, q=True, text=True).strip().upper()
        letter = letter.rstrip("/\\:").strip()
        if not letter or len(letter) != 1 or not letter.isalpha():
            cmds.text(warn, e=True, l="  Enter a single drive letter (e.g. N)."); return
        drive = letter + ":"
        if not os.path.exists(drive + "/"):
            cmds.text(warn, e=True, l=f"  {drive}\\ not found — check and try again."); return
        cmds.deleteUI(_win)
        _setup_paths(drive)
        _boot()

    def _on_cancel(*a):
        cmds.deleteUI(_win)
        print("[GLB] Drive picker cancelled.")

    cmds.rowLayout(nc=2, cw2=[155, 155])
    cmds.button(l="Confirm", w=145, bgc=(0.18, 0.42, 0.78), c=_on_confirm)
    cmds.button(l="Cancel",  w=145, c=_on_cancel)
    cmds.setParent("..")
    cmds.text(l="")
    cmds.setParent("..")
    cmds.showWindow(_win)

# On import: scan drives first, fall back to picker if nothing found
_found_drive = _find_mayaglb_drive()
if _found_drive:
    _setup_paths(_found_drive)
else:
    print("[GLB] No existing MayaGLB folder found on any drive — showing drive picker.")
    _show_drive_picker()

# ---------------------------------------------------------------------------
# Settings  save / load
# ---------------------------------------------------------------------------
_SETTINGS_DEFAULTS = {
    "yup":          False,
    "unit_scale":   1.0,
    "export_uvs":   True,
    "export_norms": True,
    "flip_norms":   False,
    "export_vcs":   False,
    "double_sided": True,
    "fix_nm":       True,
    "apply_trs":    False,
    "merge_verts":  False,
    "merge_thresh": 0.001,
    "export_skel":  False,
    "export_anim":  False,
    "anim_interp":  "LINEAR",
    "export_imgs":  True,
    "tex_jpeg":     False,
    "tex_res":      "No limit",
    "tex_srgb":     True,
    "export_mats":  True,
    "unlit":        False,
    "alpha_mode":   "OPAQUE",
    "alpha_cutoff": 0.5,
    "orm_mode":     "make_orm",  # "make_orm" | "keep_separate"
    "export_mode":  1,           # 1 = single file, 2 = batch
    "export_path":  "",
}

def save_settings(data):
    if not SETTINGS_FILE:
        print("[GLB] Settings path not initialised — skipping save."); return
    try:
        with open(SETTINGS_FILE, 'w') as f:
            json.dump(data, f, indent=2)
        print(f"[GLB] Settings saved → {SETTINGS_FILE}")
    except Exception as e:
        print(f"[GLB] Could not save settings: {e}")

def load_settings():
    """Return a dict with all keys, falling back to defaults for anything missing."""
    s = dict(_SETTINGS_DEFAULTS)
    if not SETTINGS_FILE or not os.path.exists(SETTINGS_FILE):
        return s
    try:
        with open(SETTINGS_FILE, 'r') as f:
            saved = json.load(f)
        s.update({k: v for k, v in saved.items() if k in s})
        print(f"[GLB] Settings loaded ← {SETTINGS_FILE}")
    except Exception as e:
        print(f"[GLB] Could not load settings: {e}")
    return s

# glTF constants
FLOAT                = 5126
UNSIGNED_INT         = 5125
UNSIGNED_SHORT       = 5123
ARRAY_BUFFER         = 34962
ELEMENT_ARRAY_BUFFER = 34963

PILLOW_OK = False
Image     = None

# ---------------------------------------------------------------------------
# Utility: file-friendly name from Maya object name
# ---------------------------------------------------------------------------
def _make_file_friendly(name):
    """Convert a Maya object name into a safe filename (no path separators, spaces, pipes)."""
    import re
    # Strip Maya namespace/path separators and take the last token
    name = name.split('|')[-1]   # long name → short name
    name = name.split(':')[-1]   # strip namespace
    # Replace anything that isn't alphanumeric, underscore or hyphen with underscore
    name = re.sub(r'[^\w\-]', '_', name)
    # Collapse consecutive underscores
    name = re.sub(r'_+', '_', name)
    name = name.strip('_')
    return name or "Mesh"

# ---------------------------------------------------------------------------
# Utility: small error popup
# ---------------------------------------------------------------------------
def _show_error_popup(title, message):
    """Display a compact, non-blocking error dialog."""
    win_id = "GLB_ErrorPopup"
    if cmds.window(win_id, exists=True):
        cmds.deleteUI(win_id)
    # Measure approx pixel width needed: ~7px per char, min 360, max 620
    longest = max((len(line) for line in message.splitlines()), default=len(message))
    win_w   = max(360, min(longest * 7 + 60, 620))
    txt_w   = win_w - 28   # account for column margins

    cmds.window(win_id, title=title, w=win_w, sizeable=False, toolbox=True)
    cmds.columnLayout(adj=False, w=win_w, rs=5, co=["both", 14])
    cmds.text(l="")
    cmds.text(l=f"  \u2718  {title}", fn="boldLabelFont", al="left",
              w=txt_w, bgc=(0.45, 0.12, 0.12))
    cmds.separator(h=5, style='in', w=txt_w)
    # Split on explicit newlines only — no automatic word-wrapping that causes double lines
    for line in message.splitlines():
        cmds.text(l=f"  {line}", fn="smallPlainLabelFont", al="left", w=txt_w)
    cmds.text(l="")
    cmds.button(l="OK", h=28, w=txt_w, bgc=(0.35, 0.35, 0.35),
                c=lambda *a: cmds.deleteUI(win_id))
    cmds.text(l="")
    cmds.setParent("..")
    cmds.showWindow(win_id)

# ---------------------------------------------------------------------------
# Utility: small success popup
# ---------------------------------------------------------------------------
def _show_success_popup(mesh_name, export_path):
    """Display a compact success dialog showing the exported filename."""
    win_id = "GLB_SuccessPopup"
    if cmds.window(win_id, exists=True):
        cmds.deleteUI(win_id)
    basename = os.path.basename(export_path)
    cmds.window(win_id, title="Export Successful", w=380, sizeable=False, toolbox=True)
    cmds.columnLayout(adj=True, rs=6, co=["both", 14])
    cmds.text(l="")
    cmds.text(l="  ✔  Export Successful", fn="boldLabelFont", al="left", bgc=(0.10, 0.38, 0.18))
    cmds.separator(h=6, style='in')
    cmds.text(l=f"  Mesh:  {mesh_name}", fn="smallPlainLabelFont", al="left")
    cmds.text(l=f"  File:   {basename}",  fn="smallPlainLabelFont", al="left")
    cmds.text(l="")
    cmds.button(l="OK", h=28, bgc=(0.35, 0.35, 0.35),
                c=lambda *a: cmds.deleteUI(win_id))
    cmds.text(l="")
    cmds.setParent("..")
    cmds.showWindow(win_id)

# ---------------------------------------------------------------------------
# Pillow installer
# ---------------------------------------------------------------------------
def _cleanup_pip_artifacts(lib_path):
    """Remove pip installer junk from lib_path — .dist-info, bin/, __pycache__, etc."""
    import shutil
    removed = []
    for item in os.listdir(lib_path):
        full = os.path.join(lib_path, item)
        # Remove pip metadata folders and installer noise
        if (item.endswith('.dist-info') or
                item.endswith('.data') or
                item in ('bin', 'scripts', 'Scripts', '__pycache__')):
            try:
                shutil.rmtree(full) if os.path.isdir(full) else os.remove(full)
                removed.append(item)
            except Exception as e:
                print(f"[GLB] Cleanup warning — could not remove {item}: {e}")
    if removed:
        print(f"[GLB] Cleaned up installer artifacts: {', '.join(removed)}")
    else:
        print("[GLB] Nothing to clean up.")

def ensure_libraries(lib_path):
    if lib_path and lib_path not in sys.path:
        sys.path.insert(0, lib_path)
    try:
        from PIL import Image
        print("[GLB] Pillow already installed — ready.")
        return True
    except ImportError:
        print(f"[GLB] Pillow not found — installing to {lib_path} ...")
        try:
            import maya.mel as mel
            main_pb = mel.eval('$tmp = $gMainProgressBar')
            cmds.progressBar(main_pb, edit=True, beginProgress=True,
                             isInterruptable=False,
                             status=f'Installing Pillow to {lib_path} ...',
                             maxValue=100)
        except: main_pb = None
        try:
            _maya_bin = os.path.dirname(sys.executable)
            _candidates = [
                os.path.join(_maya_bin, "mayapy.exe"),
                os.path.join(_maya_bin, "mayapy"),
                os.path.join(_maya_bin, "python.exe"),
                os.path.join(_maya_bin, "python"),
            ]
            _python_exe = next((c for c in _candidates if os.path.exists(c)), None)
            if _python_exe is None:
                print(f"[GLB] Could not find mayapy next to {sys.executable}"); return False
            print(f"[GLB] Using: {_python_exe}")
            result = subprocess.run(
                [_python_exe, "-m", "pip", "install", "--target", lib_path, "Pillow"],
                capture_output=True, text=True)
            if result.returncode == 0:
                print("[GLB] Pillow installed successfully!")
                if lib_path not in sys.path: sys.path.insert(0, lib_path)
                import importlib; importlib.invalidate_caches()
                # Clean up pip installer artifacts — keep only importable packages
                _cleanup_pip_artifacts(lib_path)
                return True
            else:
                print(f"[GLB] Pillow install FAILED:\n{result.stderr}"); return False
        except Exception as e:
            print(f"[GLB] Pillow install EXCEPTION: {e}"); return False
        finally:
            try:
                if main_pb: cmds.progressBar(main_pb, edit=True, endProgress=True)
            except: pass

# ---------------------------------------------------------------------------
# GLB packer
# ---------------------------------------------------------------------------
def _pad4(data, pad_byte=b'\x00'):
    r = len(data) % 4
    return data + pad_byte * ((4 - r) % 4)

def pack_glb(gltf_dict, bin_blob):
    json_bytes = _pad4(json.dumps(gltf_dict, separators=(',', ':')).encode('utf-8'), b' ')
    bin_blob   = _pad4(bin_blob)
    json_chunk = struct.pack('<II', len(json_bytes), 0x4E4F534A) + json_bytes
    bin_chunk  = struct.pack('<II', len(bin_blob),   0x004E4942) + bin_blob
    header     = struct.pack('<III', 0x46546C67, 2, 12 + len(json_chunk) + len(bin_chunk))
    return header + json_chunk + bin_chunk

# ---------------------------------------------------------------------------
# Non-manifold helpers
# ---------------------------------------------------------------------------
def check_non_manifold(mesh_transform):
    shape = (cmds.listRelatives(mesh_transform, shapes=True, type='mesh') or [None])[0]
    if not shape: return [], []
    nm_e = cmds.polyInfo(shape, nonManifoldEdges=True)    or []
    nm_v = cmds.polyInfo(shape, nonManifoldVertices=True) or []
    return nm_e, nm_v

def fix_non_manifold(mesh_transform):
    cmds.polyClean(mesh_transform, cleanEdges=1, cleanVertices=1,
                   cleanFaces=0, constructionHistory=False)
    print(f"[GLB] Non-manifold cleaned: {mesh_transform}")

# ---------------------------------------------------------------------------
# ORM helpers
# ---------------------------------------------------------------------------
def pack_orm_textures(o_path, r_path, m_path):
    ref_size = (1024, 1024)
    for p in [o_path, r_path, m_path]:
        if p and os.path.exists(p):
            ref_size = Image.open(p).size; break
    def ch(path, default):
        if path and os.path.exists(path):
            return Image.open(path).convert('L').resize(ref_size, Image.LANCZOS)
        return Image.new('L', ref_size, default)
    return Image.merge('RGB', (ch(o_path, 255), ch(r_path, 128), ch(m_path, 0)))

def write_blender_node_script(export_path, mat_name,
                               color_name, normal_name,
                               ao_name, rough_name, metal_name):
    script_path = os.path.splitext(export_path)[0] + "_blender_nodes.py"
    L = [
        "# Auto-generated by Maya Ultimate GLB Exporter v1.0",
        "# Run in Blender's Script Editor after importing the GLB",
        "# to wire the separate ORM textures onto the material.",
        "import bpy",
        "",
        f'mat = bpy.data.materials.get("{mat_name}") or bpy.data.materials.new("{mat_name}")',
        "mat.use_nodes = True",
        "nodes = mat.node_tree.nodes",
        "links = mat.node_tree.links",
        "nodes.clear()",
        "",
        'out  = nodes.new("ShaderNodeOutputMaterial"); out.location  = (400, 0)',
        'bsdf = nodes.new("ShaderNodeBsdfPrincipled"); bsdf.location = (0, 0)',
        'links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])',
        "",
    ]

    def tex_node(var, label, img_name, loc, color_space="sRGB"):
        if not img_name: return []
        return [
            f'{var} = nodes.new("ShaderNodeTexImage")',
            f'{var}.label = "{label}"',
            f'{var}.location = {loc}',
            f'{var}.image = bpy.data.images.get("{img_name}")',
            f'{var}.image.colorspace_settings.name = "{color_space}"' if color_space != "sRGB" else "",
        ]

    if color_name:
        L += tex_node("n_col", "Base Color", color_name, "(-700, 300)")
        L += ['links.new(n_col.outputs["Color"], bsdf.inputs["Base Color"])', ""]

    if normal_name:
        L += tex_node("n_nrm", "Normal Map", normal_name, "(-900, -100)", "Non-Color")
        L += [
            'n_nmap = nodes.new("ShaderNodeNormalMap"); n_nmap.location = (-600, -100)',
            'links.new(n_nrm.outputs["Color"], n_nmap.inputs["Color"])',
            'links.new(n_nmap.outputs["Normal"], bsdf.inputs["Normal"])',
            "",
        ]

    if ao_name:
        L += tex_node("n_ao", "Occlusion", ao_name, "(-700, 0)", "Non-Color")
        if color_name:
            L += [
                'n_mix = nodes.new("ShaderNodeMixRGB"); n_mix.blend_type = "MULTIPLY"',
                'n_mix.location = (-350, 200); n_mix.inputs["Fac"].default_value = 1.0',
                'links.new(n_col.outputs["Color"], n_mix.inputs["Color1"])',
                'links.new(n_ao.outputs["Color"],  n_mix.inputs["Color2"])',
                'links.new(n_mix.outputs["Color"], bsdf.inputs["Base Color"])',
                "",
            ]

    if rough_name:
        L += tex_node("n_rgh", "Roughness", rough_name, "(-700, -300)", "Non-Color")
        L += ['links.new(n_rgh.outputs["Color"], bsdf.inputs["Roughness"])', ""]

    if metal_name:
        L += tex_node("n_mtl", "Metallic", metal_name, "(-700, -550)", "Non-Color")
        L += ['links.new(n_mtl.outputs["Color"], bsdf.inputs["Metallic"])', ""]

    L.append(f'print("Nodes wired for {mat_name}")')

    with open(script_path, 'w') as f:
        f.write('\n'.join(l for l in L if l is not None))
    print(f"[GLB] Blender node script: {script_path}")

# ---------------------------------------------------------------------------
# Shader reader
# ---------------------------------------------------------------------------
def get_shader_data_for_sg(sg_name):
    data = {
        'color_path': None, 'normal_path': None,
        'occlusion_path': None, 'roughness_path': None, 'metallic_path': None,
        'factor': [1.0, 1.0, 1.0, 1.0],
        'metallic_val': 0.0, 'roughness_val': 0.5,
    }
    try:
        shader_conn = cmds.listConnections(sg_name + ".surfaceShader") or []
        if not shader_conn: return data
        shader = shader_conn[0]
        print(f"[GLB] Shader: '{shader}'  type={cmds.nodeType(shader)}")

        # ── Base colour ──────────────────────────────────────────────────
        for attr in ["baseColor", "color", "diffuseColor", "base_color"]:
            if not cmds.attributeQuery(attr, n=shader, ex=True): continue
            full = shader + "." + attr
            fn   = cmds.listConnections(full, type='file') or []
            if fn:
                data['color_path'] = cmds.getAttr(fn[0] + ".fileTextureName")
            else:
                raw = cmds.getAttr(full)
                if isinstance(raw, (list, tuple)) and len(raw) > 0:
                    rgb = raw[0] if isinstance(raw[0], (list, tuple)) else raw
                else:
                    rgb = (1.0, 1.0, 1.0)
                data['factor'] = [
                    float(min(max(float(rgb[0]), 0.0), 1.0)),
                    float(min(max(float(rgb[1]), 0.0), 1.0)),
                    float(min(max(float(rgb[2]), 0.0), 1.0)), 1.0]
            break

        # ── Normal map — handles aiNormalMap, bump2d, and direct file ───
        for attr in ["normalCamera", "normalMap", "normal"]:
            if not cmds.attributeQuery(attr, n=shader, ex=True): continue
            # Walk all upstream connections, not just specific types
            upstream = cmds.listConnections(shader + "." + attr, source=True, destination=False) or []
            for node in upstream:
                node_type = cmds.nodeType(node)
                # aiNormalMap: texture connected to .input
                if node_type == 'aiNormalMap':
                    nf = cmds.listConnections(node + ".input", type='file') or []
                    if nf:
                        data['normal_path'] = cmds.getAttr(nf[0] + ".fileTextureName")
                        print(f"[GLB] Normal via aiNormalMap: {data['normal_path']}")
                        break
                # Standard bump2d: texture connected to .bumpValue
                elif node_type == 'bump2d':
                    nf = cmds.listConnections(node + ".bumpValue", type='file') or []
                    if nf:
                        data['normal_path'] = cmds.getAttr(nf[0] + ".fileTextureName")
                        print(f"[GLB] Normal via bump2d: {data['normal_path']}")
                        break
                # Direct file node
                elif node_type == 'file':
                    data['normal_path'] = cmds.getAttr(node + ".fileTextureName")
                    print(f"[GLB] Normal direct file: {data['normal_path']}")
                    break
            if data['normal_path']:
                break

        # ── Roughness ────────────────────────────────────────────────────
        for attr in ["specularRoughness", "roughness"]:
            if not cmds.attributeQuery(attr, n=shader, ex=True): continue
            f2 = cmds.listConnections(shader + "." + attr, type='file') or []
            if f2:
                data['roughness_path'] = cmds.getAttr(f2[0] + ".fileTextureName")
                print(f"[GLB] Roughness: {data['roughness_path']}")
                break
            else:
                try: data['roughness_val'] = float(cmds.getAttr(shader + "." + attr))
                except: pass

        # ── Metallic ─────────────────────────────────────────────────────
        for attr in ["metalness", "metallic"]:
            if not cmds.attributeQuery(attr, n=shader, ex=True): continue
            f2 = cmds.listConnections(shader + "." + attr, type='file') or []
            if f2:
                data['metallic_path'] = cmds.getAttr(f2[0] + ".fileTextureName")
                print(f"[GLB] Metallic: {data['metallic_path']}")
                break
            else:
                try: data['metallic_val'] = float(cmds.getAttr(shader + "." + attr))
                except: pass

        # ── Occlusion ────────────────────────────────────────────────────
        for attr in ["ambientOcclusion", "occlusion", "ao"]:
            if not cmds.attributeQuery(attr, n=shader, ex=True): continue
            f2 = cmds.listConnections(shader + "." + attr, type='file') or []
            if f2:
                data['occlusion_path'] = cmds.getAttr(f2[0] + ".fileTextureName")
                print(f"[GLB] Occlusion: {data['occlusion_path']}")
                break

    except Exception as e:
        import traceback
        print(f"[GLB] get_shader_data_for_sg ERROR: {e}"); traceback.print_exc()
    return data

# ---------------------------------------------------------------------------
# Per-face-material geometry extraction
# ---------------------------------------------------------------------------
def extract_geometry_by_material(mesh_transform, unit_scale=1.0):
    shape = (cmds.listRelatives(mesh_transform, shapes=True, type='mesh') or [None])[0]
    if not shape: return []

    sel_list = om.MSelectionList()
    sel_list.add(mesh_transform)
    dag_path = sel_list.getDagPath(0)
    m_fn     = om.MFnMesh(dag_path)

    # --- Build face_to_sg using OpenMaya shading engine iteration ---
    # getConnectedShaders returns per-face shader index — no string matching needed
    face_to_sg = {}
    sg_order   = []

    shaders_mobjs, face_shader_idx = m_fn.getConnectedShaders(0)

    sg_names = []
    for mob in shaders_mobjs:
        fn = om.MFnDependencyNode(mob)
        name = fn.name()
        sg_names.append(name)
        if name not in sg_order:
            sg_order.append(name)

    for fi, si in enumerate(face_shader_idx):
        sg = sg_names[si] if 0 <= si < len(sg_names) else (sg_names[0] if sg_names else None)
        if sg:
            face_to_sg[fi] = sg

    sgs = sg_order if sg_order else (cmds.listConnections(shape, type='shadingEngine') or [])
    if not sgs: return []

    # Fallback: any unassigned faces go to first SG
    for fi in range(m_fn.numPolygons):
        if fi not in face_to_sg:
            face_to_sg[fi] = sgs[0]

    raw_pts          = m_fn.getPoints(om.MSpace.kWorld)
    raw_nrms         = m_fn.getNormals(om.MSpace.kWorld)
    u_arr, v_arr     = m_fn.getUVs()
    fv_counts, fv_verts = m_fn.getVertices()
    _, fv_nrm_ids    = m_fn.getNormalIds()
    _, fv_uv_ids     = m_fn.getAssignedUVs()
    tri_counts, tri_vis = m_fn.getTriangles()

    sg_geom = {sg: {'positions':[], 'normals':[], 'uvs':[],
                    'indices':[], 'vert_ids':[], 'next_idx':0} for sg in sgs}

    fv_off = tri_off = 0
    for fi in range(m_fn.numPolygons):
        fvc        = fv_counts[fi]
        face_verts = [fv_verts[fv_off+lv] for lv in range(fvc)]
        ntris      = tri_counts[fi]
        sg         = face_to_sg.get(fi, sgs[0])
        g          = sg_geom[sg]

        for t in range(ntris):
            for v in range(3):
                gvi = tri_vis[tri_off + t*3 + v]
                try:    lv = face_verts.index(gvi)
                except: lv = 0
                fvi = fv_off + lv

                rp = raw_pts[gvi]
                g['positions'].append((rp.x * unit_scale, rp.y * unit_scale, rp.z * unit_scale))
                rn = raw_nrms[fv_nrm_ids[fvi]]
                g['normals'].append((rn.x, rn.y, rn.z))
                ui = fv_uv_ids[fvi]
                g['uvs'].append((u_arr[ui], 1.0 - v_arr[ui]) if ui >= 0 else (0.0, 0.0))
                g['indices'].append(g['next_idx'])
                g['vert_ids'].append(gvi)
                g['next_idx'] += 1

        fv_off  += fvc
        tri_off += ntris * 3

    return [{'sg': sg, **sg_geom[sg]} for sg in sgs if sg_geom[sg]['positions']]

# ---------------------------------------------------------------------------
# Skeleton helpers
# ---------------------------------------------------------------------------
def collect_joint_hierarchy(root_joint):
    joints = []
    def _walk(j):
        joints.append(j)
        for child in (cmds.listRelatives(j, children=True, type='joint') or []):
            _walk(child)
    _walk(root_joint)
    return joints

def get_skin_cluster(mesh_transform):
    shape = (cmds.listRelatives(mesh_transform, shapes=True, type='mesh') or [None])[0]
    if not shape: return None
    for node in (cmds.listHistory(shape) or []):
        if cmds.nodeType(node) == 'skinCluster':
            return node
    return None

def _mat4_col_major(mm):
    """MMatrix → 16-float column-major list (glTF format)."""
    return [mm[r*4+c] for c in range(4) for r in range(4)]

def get_inverse_bind_matrices(joints, unit_scale, yup):
    ibms = []
    for j in joints:
        sel = om.MSelectionList(); sel.add(j)
        dag = sel.getDagPath(0)
        mm  = dag.inclusiveMatrix()
        mm.setElement(0, 3, mm.getElement(0, 3) * unit_scale)
        mm.setElement(1, 3, mm.getElement(1, 3) * unit_scale)
        mm.setElement(2, 3, mm.getElement(2, 3) * unit_scale)
        if yup:
            for c in range(4):
                y = mm.getElement(1, c); z = mm.getElement(2, c)
                mm.setElement(1, c,  z); mm.setElement(2, c, -y)
            for r in range(4):
                y = mm.getElement(r, 1); z = mm.getElement(r, 2)
                mm.setElement(r, 1,  z); mm.setElement(r, 2, -y)
        ibms.append(_mat4_col_major(mm.inverse()))
    return ibms

def extract_skin_weights(skin_cluster, mesh_transform, joints):
    shape      = cmds.listRelatives(mesh_transform, shapes=True, type='mesh')[0]
    joint_idx  = {j: i for i, j in enumerate(joints)}
    num_verts  = cmds.polyEvaluate(mesh_transform, vertex=True)

    all_j = []; all_w = []
    for vi in range(num_verts):
        comp     = f"{shape}.vtx[{vi}]"
        raw_jnts = cmds.skinPercent(skin_cluster, comp, query=True, transform=None) or []
        raw_wts  = cmds.skinPercent(skin_cluster, comp, query=True, value=True)     or []
        pairs    = sorted(zip(raw_wts, raw_jnts), reverse=True)[:4]
        while len(pairs) < 4: pairs.append((0.0, joints[0]))
        total    = sum(p[0] for p in pairs) or 1.0
        all_j.append([joint_idx.get(p[1], 0) for p in pairs])
        all_w.append([p[0] / total            for p in pairs])
    return all_j, all_w

def get_fps():
    fps_map = {
        'film':24,'ntsc':30,'pal':25,'game':15,'show':48,'palf':50,
        'ntscf':60,'23.976fps':23.976,'29.97fps':29.97,'59.94fps':59.94,
        '48fps':48,'72fps':72,'2fps':2,'3fps':3,'4fps':4,'5fps':5,
        '6fps':6,'8fps':8,'10fps':10,'12fps':12,'16fps':16,
    }
    return fps_map.get(cmds.currentUnit(q=True, time=True), 24.0)

def extract_animation(joints, unit_scale, yup):
    start = int(cmds.playbackOptions(q=True, minTime=True))
    end   = int(cmds.playbackOptions(q=True, maxTime=True))
    fps   = get_fps()
    times = [(f - start) / fps for f in range(start, end+1)]
    current_frame = cmds.currentTime(q=True)

    anim = {j: {'T':[], 'R':[], 'S':[]} for j in joints}
    for frame in range(start, end+1):
        cmds.currentTime(frame, update=True)
        for j in joints:
            sel = om.MSelectionList(); sel.add(j)
            xfm = om.MFnTransform(sel.getDagPath(0))
            t   = xfm.translation(om.MSpace.kTransform)
            r   = xfm.rotation(om.MSpace.kTransform, asQuaternion=True)
            s   = xfm.scale()

            tx, ty, tz = t.x*unit_scale, t.y*unit_scale, t.z*unit_scale
            if yup:
                tx, ty, tz =  tx,  tz, -ty
                qx, qy, qz, qw = r.x, r.z, -r.y, r.w
            else:
                qx, qy, qz, qw = r.x, r.y, r.z, r.w

            anim[j]['T'].append((tx, ty, tz))
            anim[j]['R'].append((qx, qy, qz, qw))
            anim[j]['S'].append((s[0], s[1], s[2]))

    cmds.currentTime(current_frame, update=True)
    return times, anim

# ---------------------------------------------------------------------------
# Core GLB builder
# ---------------------------------------------------------------------------
def build_glb(mesh_list, opts=None):
    if opts is None: opts = {}
    orm_mode        = opts.get('orm_mode',        'make_orm')
    yup             = opts.get('yup',             True)
    unit_scale      = opts.get('unit_scale',      1.0)
    export_uvs      = opts.get('export_uvs',      True)
    export_norms    = opts.get('export_norms',    True)
    flip_norms      = opts.get('flip_norms',      False)
    export_vcs      = opts.get('export_vcs',      False)
    double_sided    = opts.get('double_sided',    True)
    apply_trs       = opts.get('apply_trs',       False)
    merge_verts     = opts.get('merge_verts',     False)
    merge_thresh    = opts.get('merge_thresh',    0.001)
    export_mats     = opts.get('export_mats',     True)
    export_imgs     = opts.get('export_imgs',     True)
    tex_jpeg        = opts.get('tex_jpeg',        False)
    max_tex_size    = opts.get('max_tex_size',    None)
    unlit           = opts.get('unlit',           False)
    alpha_mode      = opts.get('alpha_mode',      'OPAQUE')
    alpha_cutoff    = opts.get('alpha_cutoff',    0.5)
    anim_interp     = opts.get('anim_interp',     'LINEAR')
    export_skeleton = opts.get('export_skeleton', False)
    export_anim     = opts.get('export_anim',     False)
    export_path     = opts.get('export_path',     '')

    gltf = {
        "asset":       {"version":"2.0","generator":"Maya Ultimate GLB Exporter v1.0"},
        "scene":       0,
        "scenes":      [{"nodes":[0]}],
        "nodes":       [{"mesh":0}],
        "meshes":      [{"primitives":[]}],
        "accessors":   [],
        "bufferViews": [],
        "buffers":     [{"byteLength":0}],
        "materials":   [],
        "textures":    [],
        "images":      [],
    }

    bin_blob  = b''
    bv_idx = acc_idx = tex_idx = img_idx = 0
    tex_cache = {}

    def add_bv(data, target=None):
        nonlocal bin_blob, bv_idx
        data  = _pad4(data); start = len(bin_blob); bin_blob += data
        bv    = {"buffer":0,"byteOffset":start,"byteLength":len(data)}
        if target: bv["target"] = target
        gltf["bufferViews"].append(bv)
        i = bv_idx; bv_idx += 1; return i

    def add_acc(bv, comp, count, atype, normalized=False):
        nonlocal acc_idx
        a = {"bufferView":bv,"byteOffset":0,"componentType":comp,"count":count,"type":atype}
        if normalized: a["normalized"] = True
        gltf["accessors"].append(a)
        i = acc_idx; acc_idx += 1; return i

    def embed_pil(pil_img, cache_key=None):
        nonlocal bin_blob, bv_idx, tex_idx, img_idx
        if cache_key and cache_key in tex_cache: return tex_cache[cache_key]
        if max_tex_size: pil_img.thumbnail((max_tex_size, max_tex_size), Image.LANCZOS)
        buf = io.BytesIO()
        if tex_jpeg:
            pil_img.convert('RGB').save(buf, format="JPEG", quality=90); mime = "image/jpeg"
        else:
            pil_img.save(buf, format="PNG"); mime = "image/png"
        data  = _pad4(buf.getvalue()); start = len(bin_blob); bin_blob += data
        gltf["bufferViews"].append({"buffer":0,"byteOffset":start,"byteLength":len(data)})
        gltf["images"].append({"bufferView":bv_idx,"mimeType":mime})
        gltf["textures"].append({"source":img_idx})
        ti = tex_idx; bv_idx += 1; img_idx += 1; tex_idx += 1
        if cache_key: tex_cache[cache_key] = ti
        return ti

    def embed_file(path):
        if not path or not os.path.exists(path): return None
        if path in tex_cache: return tex_cache[path]
        return embed_pil(Image.open(path).convert('RGBA'), cache_key=path)

    # ---- Skeleton setup ----
    joint_list        = []
    joint_node_start  = 1

    if export_skeleton:
        sel_joints = [j for j in (cmds.ls(sl=True) or []) if cmds.nodeType(j) == 'joint']
        if not sel_joints:
            sel_joints = cmds.ls(type='joint') or []
        roots = [j for j in sel_joints
                 if not (cmds.listRelatives(j, parent=True, type='joint') or [])]
        for root in roots:
            joint_list += collect_joint_hierarchy(root)
        joint_list = list(dict.fromkeys(joint_list))

    # ---- Process each mesh ----
    temps         = []
    all_vert_ids  = []

    for mesh_transform in mesh_list:
        print(f"\n[GLB] ── Processing: {mesh_transform}")
        tmp = cmds.duplicate(mesh_transform)[0]
        if apply_trs:
            cmds.makeIdentity(tmp, apply=True, t=True, r=True, s=True)
        if merge_verts:
            cmds.polyMergeVertex(tmp, d=merge_thresh, constructionHistory=False)
        cmds.polyTriangulate(tmp)
        temps.append(tmp)

        prim_groups = extract_geometry_by_material(tmp, unit_scale=unit_scale)
        if not prim_groups:
            print(f"[GLB] WARNING: No geometry on {mesh_transform}, skipping."); continue

        for group in prim_groups:
            sg        = group['sg']
            positions = group['positions']
            normals   = group['normals']
            uvs       = group['uvs']
            indices   = group['indices']
            vert_ids  = group['vert_ids']
            vc        = len(positions)

            if yup:
                positions = [( p[0],  p[2], -p[1]) for p in positions]
                normals   = [( n[0],  n[2], -n[1]) for n in normals]

            if flip_norms:
                normals = [(-n[0], -n[1], -n[2]) for n in normals]

            bv_pos  = add_bv(_pad4(b"".join(struct.pack("<fff",*p) for p in positions)), ARRAY_BUFFER)
            bv_ind  = add_bv(_pad4(b"".join(struct.pack("<I",  i)  for i in indices)),   ELEMENT_ARRAY_BUFFER)
            acc_pos = add_acc(bv_pos, FLOAT,        vc,           "VEC3")
            acc_ind = add_acc(bv_ind, UNSIGNED_INT, len(indices), "SCALAR")
            attribs = {"POSITION": acc_pos}

            if export_norms:
                bv_n = add_bv(_pad4(b"".join(struct.pack("<fff",*n) for n in normals)), ARRAY_BUFFER)
                attribs["NORMAL"] = add_acc(bv_n, FLOAT, vc, "VEC3")

            if export_uvs:
                bv_u = add_bv(_pad4(b"".join(struct.pack("<ff", *u) for u in uvs)), ARRAY_BUFFER)
                attribs["TEXCOORD_0"] = add_acc(bv_u, FLOAT, vc, "VEC2")

            if export_vcs:
                try:
                    sel_vc = om.MSelectionList(); sel_vc.add(tmp)
                    mfn_vc = om.MFnMesh(sel_vc.getDagPath(0))
                    col_names = mfn_vc.getColorSetNames()
                    if col_names:
                        colors_raw = mfn_vc.getVertexColors(col_names[0])
                        vc_data = []
                        for vi in vert_ids:
                            c = colors_raw[vi]
                            vc_data.append((
                                min(max(c.r, 0.0), 1.0),
                                min(max(c.g, 0.0), 1.0),
                                min(max(c.b, 0.0), 1.0),
                                min(max(c.a, 0.0), 1.0),
                            ))
                        vc_bytes = _pad4(b"".join(struct.pack("<ffff", *c) for c in vc_data))
                        attribs["COLOR_0"] = add_acc(add_bv(vc_bytes, ARRAY_BUFFER),
                                                     FLOAT, vc, "VEC4")
                        print(f"[GLB] Vertex colours exported for {mesh_transform}")
                    else:
                        print(f"[GLB] No colour set found on {mesh_transform} — skipping COLOR_0")
                except Exception as vc_err:
                    print(f"[GLB] Vertex colour export failed: {vc_err}")

            # ---- Material ----
            mat_idx = 0
            if export_mats:
                md        = get_shader_data_for_sg(sg)
                color_tex = embed_file(md['color_path'])  if export_imgs else None
                norm_tex  = embed_file(md['normal_path']) if export_imgs else None
                orm_tex   = None

                if export_imgs:
                    if orm_mode == 'make_orm':
                        o = md['occlusion_path']
                        r = md['roughness_path']
                        m = md['metallic_path']
                        if any([o, r, m]):
                            orm_img = pack_orm_textures(o, r, m)
                            ck = f"ORM::{o}::{r}::{m}"
                            orm_tex = embed_pil(orm_img, cache_key=ck)
                            print(f"[GLB] ORM packed — O:{o} R:{r} M:{m}")
                        else:
                            print(f"[GLB] ORM skipped — no O/R/M textures found on shader")
                    else:
                        if export_path:
                            write_blender_node_script(
                                export_path,
                                f"M_{mesh_transform}_{sg}",
                                os.path.basename(md['color_path'])     if md['color_path']     else None,
                                os.path.basename(md['normal_path'])    if md['normal_path']    else None,
                                os.path.basename(md['occlusion_path']) if md['occlusion_path'] else None,
                                os.path.basename(md['roughness_path']) if md['roughness_path'] else None,
                                os.path.basename(md['metallic_path'])  if md['metallic_path']  else None,
                            )

                pbr = {
                    "baseColorFactor": md['factor'],
                    "metallicFactor":  md['metallic_val'] if orm_tex is None else 1.0,
                    "roughnessFactor": md['roughness_val'] if orm_tex is None else 1.0,
                }
                if color_tex is not None: pbr["baseColorTexture"]           = {"index": color_tex}
                if orm_tex   is not None: pbr["metallicRoughnessTexture"]   = {"index": orm_tex}

                mat = {
                    "name":                 f"M_{mesh_transform}_{sg}",
                    "doubleSided":          double_sided,
                    "pbrMetallicRoughness": pbr,
                    "alphaMode":            alpha_mode,
                }
                if alpha_mode == "MASK": mat["alphaCutoff"] = alpha_cutoff
                if norm_tex is not None: mat["normalTexture"] = {"index": norm_tex}
                if unlit:
                    mat["extensions"] = {"KHR_materials_unlit": {}}
                    gltf.setdefault("extensionsUsed", [])
                    if "KHR_materials_unlit" not in gltf["extensionsUsed"]:
                        gltf["extensionsUsed"].append("KHR_materials_unlit")

                mat_idx = len(gltf["materials"])
                gltf["materials"].append(mat)

            prim = {"attributes": attribs, "indices": acc_ind}
            if export_mats: prim["material"] = mat_idx
            gltf["meshes"][0]["primitives"].append(prim)
            all_vert_ids.append((mesh_transform, vert_ids))

    for t in temps:
        try: cmds.delete(t)
        except: pass

    if not gltf["meshes"][0]["primitives"]:
        raise RuntimeError("No primitives generated — check mesh selection.")

    # ---- Skeleton & skin weights ----
    if export_skeleton and joint_list:
        print(f"[GLB] Exporting {len(joint_list)} joints")
        joint_node_start = len(gltf["nodes"])

        for j in joint_list:
            sel = om.MSelectionList(); sel.add(j)
            xfm = om.MFnTransform(sel.getDagPath(0))
            t   = xfm.translation(om.MSpace.kTransform)
            r   = xfm.rotation(om.MSpace.kTransform, asQuaternion=True)
            s   = xfm.scale()

            tx, ty, tz = t.x*unit_scale, t.y*unit_scale, t.z*unit_scale
            if yup:
                tx, ty, tz =  tx,  tz, -ty
                qx, qy, qz, qw = r.x, r.z, -r.y, r.w
            else:
                qx, qy, qz, qw = r.x, r.y, r.z, r.w

            child_jnts = cmds.listRelatives(j, children=True, type='joint') or []
            child_idxs = [joint_node_start + joint_list.index(c)
                          for c in child_jnts if c in joint_list]

            node = {"name": j,
                    "translation": [tx, ty, tz],
                    "rotation":    [qx, qy, qz, qw],
                    "scale":       list(s)}
            if child_idxs: node["children"] = child_idxs
            gltf["nodes"].append(node)

        root_joints = [j for j in joint_list
                       if not (cmds.listRelatives(j, parent=True, type='joint') or [])]
        root_idxs   = [joint_node_start + joint_list.index(r)
                       for r in root_joints if r in joint_list]
        if root_idxs:
            gltf["scenes"][0]["nodes"] += root_idxs

        ibms    = get_inverse_bind_matrices(joint_list, unit_scale, yup)
        ibm_bv  = add_bv(_pad4(b"".join(struct.pack("<16f", *m) for m in ibms)))
        ibm_acc = add_acc(ibm_bv, FLOAT, len(joint_list), "MAT4")

        j_node_indices = [joint_node_start + i for i in range(len(joint_list))]
        gltf.setdefault("skins", []).append({
            "name":                "Armature",
            "joints":              j_node_indices,
            "inverseBindMatrices": ibm_acc,
        })
        gltf["nodes"][0]["skin"] = 0

        for prim_i, (mesh_transform, vert_ids) in enumerate(all_vert_ids):
            sc = get_skin_cluster(mesh_transform)
            if not sc:
                print(f"[GLB] No skinCluster on {mesh_transform} — skipping weights"); continue
            all_j_table, all_w_table = extract_skin_weights(sc, mesh_transform, joint_list)
            prim_j = []; prim_w = []
            for vi in vert_ids:
                prim_j.append(all_j_table[vi])
                prim_w.append(all_w_table[vi])
            jb = _pad4(b"".join(struct.pack("<HHHH", *row) for row in prim_j))
            wb = _pad4(b"".join(struct.pack("<ffff", *row) for row in prim_w))
            vc = len(prim_j)
            j_acc = add_acc(add_bv(jb, ARRAY_BUFFER), UNSIGNED_SHORT, vc, "VEC4")
            w_acc = add_acc(add_bv(wb, ARRAY_BUFFER), FLOAT,          vc, "VEC4")
            if prim_i < len(gltf["meshes"][0]["primitives"]):
                gltf["meshes"][0]["primitives"][prim_i]["attributes"]["JOINTS_0"]  = j_acc
                gltf["meshes"][0]["primitives"][prim_i]["attributes"]["WEIGHTS_0"] = w_acc

        # ---- Animation ----
        if export_anim:
            print(f"[GLB] Baking animation...")
            times, anim_data = extract_animation(joint_list, unit_scale, yup)
            t_bv  = add_bv(_pad4(b"".join(struct.pack("<f", t) for t in times)))
            t_acc = add_acc(t_bv, FLOAT, len(times), "SCALAR")

            samplers = []; channels = []; si = 0
            for ji, j in enumerate(joint_list):
                node_idx = joint_node_start + ji
                for path, key, fmt, atype in [
                    ("translation", "T", "<fff",  "VEC3"),
                    ("rotation",    "R", "<ffff", "VEC4"),
                    ("scale",       "S", "<fff",  "VEC3"),
                ]:
                    frames   = anim_data[j][key]
                    data_bv  = add_bv(_pad4(b"".join(struct.pack(fmt, *f) for f in frames)))
                    out_acc  = add_acc(data_bv, FLOAT, len(frames), atype)
                    samplers.append({"input": t_acc, "output": out_acc, "interpolation": anim_interp})
                    channels.append({"sampler": si, "target": {"node": node_idx, "path": path}})
                    si += 1

            gltf["animations"] = [{"name":"Take001","samplers":samplers,"channels":channels}]
            print(f"[GLB] Animation: {len(times)} frames, {si} channels")

    gltf["buffers"][0]["byteLength"] = len(bin_blob)
    for key in ["textures", "images", "materials"]:
        if not gltf.get(key): gltf.pop(key, None)

    return pack_glb(gltf, bin_blob)


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------
class UE_Blender_Final_Exporter:
    def __init__(self):
        self.win = "UE_Final_Exporter_Win"
        if cmds.window(self.win, exists=True): cmds.deleteUI(self.win)
        cmds.window(self.win, title="Ultimate GLB Exporter  v1.0",
                    w=500, sizeable=True,
                    topLeftCorner=[100,100],
                    toolbox=True)
        cmds.scrollLayout("GLB_Scroll", cr=True, hst=0, vst=14,
                          childResizable=True)
        root = cmds.columnLayout("GLB_RootCol", adj=True, rs=0)

        # Title
        cmds.frameLayout(l="", bv=False, mh=6, mw=0, p=root)
        cmds.text(l="  ULTIMATE GLB EXPORTER  v1.0", fn="boldLabelFont",
                  al="center", h=28, bgc=(0.12, 0.12, 0.18))
        cmds.text(l="  Blender / UE  ·  Skeleton + Anim  ·  Multi-material  ·  ORM",
                  fn="smallPlainLabelFont", al="center", h=18, bgc=(0.12, 0.12, 0.18))
        cmds.setParent('..'); cmds.setParent(root)

        # ── Settings (collapsible sub-sections) ──────────────────────────
        sf = cmds.frameLayout(l="  ▸  Settings", cll=True, cl=True, mh=6, mw=6, p=root,
                              cc=self._refresh_win, ec=self._refresh_win)
        self.settings_frame = sf
        cmds.columnLayout(adj=True, rs=2)

        def _sub(label, collapsed=False):
            fl = cmds.frameLayout(l=f"  {label}", cll=True, cl=collapsed,
                                  mh=6, mw=14, bv=True,
                                  cc=self._refresh_win, ec=self._refresh_win)
            cmds.columnLayout(adj=True, rs=5)
            return fl

        def _end_sub():
            cmds.setParent('..')
            cmds.setParent('..')

        CW = [180, 50]

        # ── Transform ──────────────────────────────────────
        _sub("Transform")
        self.yup        = cmds.checkBoxGrp(l="+Y Up (Z-up → Y-up):", v1=False, cw2=CW,
                                            ann="Rotate the mesh so Maya's Z-up axis becomes Y-up in the GLB.\nEnable if the model appears on its side in Blender or Unreal Engine.")
        self.unit_scale = cmds.floatFieldGrp(l="Scale multiplier:", nf=1, v1=1.0, cw2=[150,80],
                                              ann="Multiply all vertex positions by this value.\n1.0 = no change. Use 0.01 to convert Maya cm to metres for Blender/UE.")
        cmds.rowLayout(nc=2, cw2=[100,220])
        cmds.text(l="")
        cmds.button(l="Check Selection Scale", c=self._check_scale,
                    ann="Reports the exported bounding box dimensions of the selected mesh\nusing the current scale multiplier, so you can verify size before exporting.")
        cmds.setParent('..')
        _end_sub()

        # ── Mesh ───────────────────────────────────────────
        _sub("Mesh")
        self.export_uvs    = cmds.checkBoxGrp(l="Export UVs:",               v1=True,  cw2=CW,
                                               ann="Include TEXCOORD_0 UV coordinates in the GLB.\nDisable only if the mesh has no UVs or you want a smaller file with no texturing.")
        self.export_norms  = cmds.checkBoxGrp(l="Export normals:",            v1=True,  cw2=CW,
                                               ann="Include per-vertex normals in the GLB.\nDisabling lets the target app recalculate normals, which can fix shading issues but may look different.")
        self.flip_norms    = cmds.checkBoxGrp(l="Flip normals:",              v1=False, cw2=CW,
                                               ann="Negate all normal vectors on export.\nUse this if the mesh appears inside-out or dark/black in the target application.")
        self.export_vcs    = cmds.checkBoxGrp(l="Export vertex colours:",     v1=False, cw2=CW,
                                               ann="Export the active vertex colour set as the COLOR_0 attribute.\nUseful for baked ambient occlusion or hand-painted colour on meshes with no textures.")
        self.double_sided  = cmds.checkBoxGrp(l="Double sided:",              v1=True,  cw2=CW,
                                               ann="Set the material doubleSided flag in the GLB.\nWhen on, both faces of a polygon are rendered — useful for thin surfaces like leaves or cloth.")
        self.fix_nm        = cmds.checkBoxGrp(l="Check non-manifold geo:",    v1=True,  cw2=CW,
                                               ann="Before exporting, scan the mesh for non-manifold edges and vertices.\nNon-manifold geometry can cause export errors or incorrect results in game engines.")
        self.apply_trs     = cmds.checkBoxGrp(l="Apply transform (freeze):",  v1=False, cw2=CW,
                                               ann="Freeze the mesh's translation, rotation, and scale into the vertex positions before export.\nResults in the mesh sitting at the origin with identity transforms in the GLB.")
        self.merge_verts   = cmds.checkBoxGrp(l="Merge vertices:",            v1=False, cw2=CW,
                                               ann="Run polyMergeVertex before export to weld vertices that are closer than the threshold.\nHelps close gaps and reduce vertex count on meshes with shared edges.")
        self.merge_thresh  = cmds.floatFieldGrp(l="  Merge threshold:", nf=1, v1=0.001, cw2=[150,80],
                                                ann="Maximum distance (in Maya units) between vertices for them to be merged.\nSmaller values only merge near-perfect overlaps; larger values merge a wider area.")
        _end_sub()

        # ── Animation ──────────────────────────────────────
        _sub("Animation", collapsed=True)
        self.export_skel = cmds.checkBoxGrp(l="Export skeleton:",          v1=False, cw2=CW,
                                             cc=self._on_skel_change,
                                             ann="Include joint hierarchy and skin weights in the GLB.\nThe skeleton is read from selected joints, or all joints in the scene if none are selected.")
        self.export_anim = cmds.checkBoxGrp(l="Export animation:",         v1=False, cw2=CW,
                                             en=False,
                                             ann="Bake TRS (translation, rotation, scale) keyframes for every joint\nover the current playback range and embed them as a glTF animation track.")
        self.anim_interp = cmds.optionMenuGrp(l="Interpolation:", cw2=[150,100],
                                               ann="Curve interpolation used between keyframes in the glTF animation.\nLINEAR: straight lines between keys.\nSTEP: no interpolation, snaps at each key.\nCUBICSPLINE: smooth cubic curves (requires tangent data).")
        for m in ["LINEAR","STEP","CUBICSPLINE"]: cmds.menuItem(l=m)
        cmds.rowLayout(nc=2, cw2=[180, 240])
        cmds.text(l="  Playback range:", fn="smallPlainLabelFont",
                  ann="The frame range that will be baked into the animation — taken from Maya's playback range.")
        self.range_text = cmds.text(l="", fn="smallPlainLabelFont")
        cmds.setParent('..')
        self._update_range_text()
        _end_sub()

        # ── Texture ────────────────────────────────────────
        _sub("Texture", collapsed=True)
        self.export_imgs = cmds.checkBoxGrp(l="Embed textures:",            v1=True,  cw2=CW,
                                             ann="Pack texture image data directly inside the GLB binary.\nDisable to export a smaller GLB with no textures (useful for geometry-only checks).")
        self.tex_jpeg    = cmds.checkBoxGrp(l="Convert to JPEG:",           v1=False, cw2=CW,
                                             ann="Re-encode all textures as JPEG before embedding.\nProduces a smaller file but loses the alpha channel and introduces compression artefacts.")
        self.tex_res     = cmds.optionMenuGrp(l="Max texture size:", cw2=[150,100],
                                               ann="Downscale any texture larger than this before embedding.\nUse to cap file size — textures smaller than the limit are left untouched.")
        for r in ["No limit","256","512","1024","2048","4096"]: cmds.menuItem(l=r)
        self.tex_srgb    = cmds.checkBoxGrp(l="Force sRGB colour space:",   v1=True,  cw2=CW,
                                             ann="Tag base colour textures as sRGB in the glTF JSON.\nEnsures correct gamma handling in Blender and Unreal Engine.\nNormal/roughness/metallic maps are always tagged as linear.")
        _end_sub()

        # ── Material ───────────────────────────────────────
        _sub("Material", collapsed=True)
        self.export_mats  = cmds.checkBoxGrp(l="Export materials:",         v1=True,  cw2=CW,
                                               ann="Read shader networks from Maya and write glTF PBR materials.\nDisable for a geometry-only export with no material data.")
        self.unlit         = cmds.checkBoxGrp(l="Unlit (shadeless):",        v1=False, cw2=CW,
                                               ann="Apply the KHR_materials_unlit extension to all materials.\nThe mesh will appear fully bright with no lighting or shadows — useful for UI elements or baked-light assets.")
        self.alpha_mode    = cmds.optionMenuGrp(l="Alpha mode:", cw2=[150,100],
                                                ann="Controls how the material handles transparency.\nOPAQUE: fully solid, alpha ignored.\nMASK: binary cutout using the alpha cutoff value (good for foliage).\nBLEND: smooth transparency (can cause sorting artefacts in real-time engines).")
        for m in ["OPAQUE","MASK","BLEND"]: cmds.menuItem(l=m)
        self.alpha_cutoff  = cmds.floatFieldGrp(l="Alpha cutoff (MASK):", nf=1, v1=0.5, cw2=[150,80],
                                                 ann="Pixels with alpha below this threshold are discarded (fully transparent).\nOnly used when Alpha mode is set to MASK.")

        # ORM: two radio buttons stacked vertically
        cmds.text(l="  ORM:", fn="smallPlainLabelFont", al="left",
                  ann="Controls how Occlusion, Roughness, and Metallic texture channels are handled.")
        self.orm_make = cmds.radioCollection()
        self.orm_rb1  = cmds.radioButton(l="Make ORM texture  (pack O+R+M → 1 PNG)",
                                          sl=True, cc=self._on_orm_mode_change,
                                          ann="Pack the three separate textures into a single ORM image:\nR = Occlusion, G = Roughness, B = Metallic.\nThis is the standard glTF metallicRoughness format expected by most engines.")
        self.orm_rb2  = cmds.radioButton(l="Keep separate  (embed each + Blender script)",
                                          sl=False, cc=self._on_orm_mode_change,
                                          ann="Embed Occlusion, Roughness, and Metallic as individual images.\nAlso writes a Python sidecar script you can run in Blender's Script Editor\nto automatically wire the textures onto the material node tree.")

        self.orm_sep_info  = cmds.frameLayout(l="  Keep Separate — note", bv=True, mh=4, mw=10)
        cmds.columnLayout(adj=True, rs=3)
        cmds.text(l="  O/R/M read from shader, embedded separately.", fn="smallPlainLabelFont", al="left")
        cmds.text(l="  Blender node script written next to GLB.",     fn="smallPlainLabelFont", al="left")
        cmds.setParent('..'); cmds.setParent('..')
        cmds.frameLayout(self.orm_sep_info, e=True, vis=False)
        _end_sub()

        cmds.setParent('..'); cmds.setParent('..')  # leave settings outer frame

        # Mode & path
        cmds.frameLayout(l="", bv=False, mh=8, mw=10, p=root)
        self.mode = cmds.radioButtonGrp(
            l="Mode: ", labelArray2=["Single file (merge all)", "Batch (one per obj)"],
            numberOfRadioButtons=2, sl=1, cc=self.update_ui_path, cw3=[60,165,165],
            ann="Single file: all selected meshes merged into one GLB.\nBatch: each selected mesh exported as its own separate GLB file.")
        self.path_field = cmds.textFieldButtonGrp(
            l="Output path:", bl="Browse", bc=self.browse_path,
            text=os.path.join(DEFAULT_EXPORT_DIR, "MyAsset.glb"), cw=[1,80], adj=2,
            ann="Full path to the output GLB file (single mode), or the folder to export into (batch mode).\nThe filename is auto-generated from the selected mesh name.")
        cmds.setParent('..'); cmds.setParent(root)

        # Status
        cmds.frameLayout(l="", bv=False, mh=4, mw=10, p=root)
        self.status_text = cmds.text(l="  Ready.", al="left",
                                     fn="smallPlainLabelFont", h=20, bgc=(0.18,0.18,0.18))
        cmds.setParent('..'); cmds.setParent(root)

        # Export button
        cmds.frameLayout(l="", bv=False, mh=8, mw=10, p=root)
        self.export_btn = cmds.button(l="EXPORT GLB", h=52,
                                      bgc=(0.18,0.42,0.78), c=self.run_export)
        cmds.setParent('..'); cmds.setParent(root)

        # Credits
        self.credits_frame = cmds.frameLayout(
            l="  ▸  Credits", cll=True, cl=True, mh=8, mw=10, p=root,
            cc=self._refresh_win, ec=self._refresh_win)
        cmds.columnLayout(adj=True, rs=5)
        cmds.text(l="  Ultimate GLB Exporter  v1.0",    fn="boldLabelFont",      al="left")
        cmds.text(l="  Written for Maya 2026  ·  Python 3", fn="smallPlainLabelFont", al="left")
        cmds.separator(h=8, style='in')
        cmds.text(l="  CREDITS",                         fn="boldLabelFont",      al="left")
        cmds.separator(h=6, style='in')
        cmds.text(l="  Connor Henry          -  Main Developer",                al="left", fn="smallPlainLabelFont")
        cmds.text(l="  Claude / Anthropic    -  Debugging / Code Assistance",   al="left", fn="smallPlainLabelFont")
        cmds.text(l="  Jack Clewer           -  Being a Good Teacher",          al="left", fn="smallPlainLabelFont")
        cmds.text(l="  Maya                  -  Being annoying by not having GLB export.", al="left", fn="smallPlainLabelFont")
        cmds.text(l="  ", al="left", fn="smallPlainLabelFont")
        cmds.setParent('..'); cmds.setParent('..')

        # Load saved settings and apply to UI
        self._apply_settings(load_settings())
        cmds.showWindow(self.win)
        cmds.evalDeferred(self._fit_window_height)

    # ── Helpers ───────────────────────────────────────────────────────────
    def _refresh_win(self, *args):
        # Two deferred passes: first lets Maya collapse/expand the frame,
        # second lets it finish recalculating all child heights.
        try:
            cmds.evalDeferred(lambda *a: cmds.evalDeferred(self._fit_window_height))
        except: pass

    def _fit_window_height(self, *args):
        try:
            content_h = cmds.columnLayout("GLB_RootCol", q=True, h=True)
            capped    = max(200, min(content_h + 4, 900))
            cmds.window(self.win, e=True, h=capped, w=500)
        except: pass

    def _set_status(self, msg, colour=(0.18,0.18,0.18)):
        try:
            cmds.text(self.status_text, e=True, l=f"  {msg}", bgc=colour)
        except: pass

    def _update_range_text(self):
        try:
            s = int(cmds.playbackOptions(q=True, minTime=True))
            e = int(cmds.playbackOptions(q=True, maxTime=True))
            fps = get_fps()
            cmds.text(self.range_text, e=True, l=f"frames {s}–{e}  ({(e-s)/fps:.2f}s @ {fps}fps)")
        except: pass

    def _on_skel_change(self, *args):
        has = cmds.checkBoxGrp(self.export_skel, q=True, v1=True)
        cmds.checkBoxGrp(self.export_anim, e=True, en=has)
        if not has: cmds.checkBoxGrp(self.export_anim, e=True, v1=False)
        self._update_range_text()

    def _on_orm_mode_change(self, *args):
        is_sep = cmds.radioButton(self.orm_rb2, q=True, sl=True)
        cmds.frameLayout(self.orm_sep_info, e=True, vis=is_sep)
        self._refresh_win()

    # ── Settings persistence ──────────────────────────────────────────────
    def _apply_settings(self, s):
        """Push a settings dict into every UI control."""
        try:
            cmds.checkBoxGrp(self.yup,          e=True, v1=s['yup'])
            cmds.floatFieldGrp(self.unit_scale,  e=True, v1=s['unit_scale'])
            cmds.checkBoxGrp(self.export_uvs,    e=True, v1=s['export_uvs'])
            cmds.checkBoxGrp(self.export_norms,  e=True, v1=s['export_norms'])
            cmds.checkBoxGrp(self.flip_norms,    e=True, v1=s['flip_norms'])
            cmds.checkBoxGrp(self.export_vcs,    e=True, v1=s['export_vcs'])
            cmds.checkBoxGrp(self.double_sided,  e=True, v1=s['double_sided'])
            cmds.checkBoxGrp(self.fix_nm,        e=True, v1=s['fix_nm'])
            cmds.checkBoxGrp(self.apply_trs,     e=True, v1=s['apply_trs'])
            cmds.checkBoxGrp(self.merge_verts,   e=True, v1=s['merge_verts'])
            cmds.floatFieldGrp(self.merge_thresh,e=True, v1=s['merge_thresh'])
            cmds.checkBoxGrp(self.export_skel,   e=True, v1=s['export_skel'])
            has_skel = s['export_skel']
            cmds.checkBoxGrp(self.export_anim,   e=True, en=has_skel, v1=s['export_anim'] if has_skel else False)
            interp_items = ["LINEAR","STEP","CUBICSPLINE"]
            interp_idx   = interp_items.index(s['anim_interp']) + 1 if s['anim_interp'] in interp_items else 1
            cmds.optionMenuGrp(self.anim_interp, e=True, sl=interp_idx)
            cmds.checkBoxGrp(self.export_imgs,   e=True, v1=s['export_imgs'])
            cmds.checkBoxGrp(self.tex_jpeg,      e=True, v1=s['tex_jpeg'])
            res_items = ["No limit","256","512","1024","2048","4096"]
            res_idx   = res_items.index(s['tex_res']) + 1 if s['tex_res'] in res_items else 1
            cmds.optionMenuGrp(self.tex_res,     e=True, sl=res_idx)
            cmds.checkBoxGrp(self.tex_srgb,      e=True, v1=s['tex_srgb'])
            cmds.checkBoxGrp(self.export_mats,   e=True, v1=s['export_mats'])
            cmds.checkBoxGrp(self.unlit,         e=True, v1=s['unlit'])
            am_items = ["OPAQUE","MASK","BLEND"]
            am_idx   = am_items.index(s['alpha_mode']) + 1 if s['alpha_mode'] in am_items else 1
            cmds.optionMenuGrp(self.alpha_mode,  e=True, sl=am_idx)
            cmds.floatFieldGrp(self.alpha_cutoff,e=True, v1=s['alpha_cutoff'])
            is_sep = (s['orm_mode'] == 'keep_separate')
            cmds.radioButton(self.orm_rb1,       e=True, sl=not is_sep)
            cmds.radioButton(self.orm_rb2,       e=True, sl=is_sep)
            cmds.frameLayout(self.orm_sep_info,  e=True, vis=is_sep)
            cmds.radioButtonGrp(self.mode,       e=True, sl=s['export_mode'])
            # Restore last export path if it still looks valid, else use default
            saved_path = s.get('export_path', '')
            if saved_path and os.path.isdir(os.path.dirname(saved_path)):
                cmds.textFieldButtonGrp(self.path_field, e=True, text=saved_path)
            else:
                self.update_ui_path()
        except Exception as e:
            print(f"[GLB] _apply_settings warning: {e}")
            self.update_ui_path()

    def _collect_settings(self):
        """Read every UI control and return a settings dict ready for saving."""
        tex_res_str = cmds.optionMenuGrp(self.tex_res,    q=True, v=True)
        alpha_str   = cmds.optionMenuGrp(self.alpha_mode, q=True, v=True)
        interp_str  = cmds.optionMenuGrp(self.anim_interp,q=True, v=True)
        is_sep      = cmds.radioButton(self.orm_rb2,      q=True, sl=True)
        return {
            "yup":          cmds.checkBoxGrp(self.yup,          q=True, v1=True),
            "unit_scale":   cmds.floatFieldGrp(self.unit_scale,  q=True, v1=True),
            "export_uvs":   cmds.checkBoxGrp(self.export_uvs,   q=True, v1=True),
            "export_norms": cmds.checkBoxGrp(self.export_norms, q=True, v1=True),
            "flip_norms":   cmds.checkBoxGrp(self.flip_norms,   q=True, v1=True),
            "export_vcs":   cmds.checkBoxGrp(self.export_vcs,   q=True, v1=True),
            "double_sided": cmds.checkBoxGrp(self.double_sided, q=True, v1=True),
            "fix_nm":       cmds.checkBoxGrp(self.fix_nm,       q=True, v1=True),
            "apply_trs":    cmds.checkBoxGrp(self.apply_trs,    q=True, v1=True),
            "merge_verts":  cmds.checkBoxGrp(self.merge_verts,  q=True, v1=True),
            "merge_thresh": cmds.floatFieldGrp(self.merge_thresh,q=True, v1=True),
            "export_skel":  cmds.checkBoxGrp(self.export_skel,  q=True, v1=True),
            "export_anim":  cmds.checkBoxGrp(self.export_anim,  q=True, v1=True),
            "anim_interp":  interp_str,
            "export_imgs":  cmds.checkBoxGrp(self.export_imgs,  q=True, v1=True),
            "tex_jpeg":     cmds.checkBoxGrp(self.tex_jpeg,     q=True, v1=True),
            "tex_res":      tex_res_str,
            "tex_srgb":     cmds.checkBoxGrp(self.tex_srgb,     q=True, v1=True),
            "export_mats":  cmds.checkBoxGrp(self.export_mats,  q=True, v1=True),
            "unlit":        cmds.checkBoxGrp(self.unlit,        q=True, v1=True),
            "alpha_mode":   alpha_str,
            "alpha_cutoff": cmds.floatFieldGrp(self.alpha_cutoff,q=True, v1=True),
            "orm_mode":     "keep_separate" if is_sep else "make_orm",
            "export_mode":  cmds.radioButtonGrp(self.mode,      q=True, sl=True),
            "export_path":  cmds.textFieldButtonGrp(self.path_field, q=True, text=True),
        }

    def _browse_tex(self, field):
        f = cmds.fileDialog2(fm=1, ff="Images (*.png *.jpg *.jpeg *.tif *.tga *.exr)")
        if f: cmds.textFieldButtonGrp(field, e=True, text=f[0])

    def _check_scale(self, *args):
        sel    = cmds.ls(sl=True, type='transform')
        meshes = [o for o in sel if cmds.listRelatives(o, shapes=True, type='mesh')]
        if not meshes:
            _show_error_popup("Scale Check", "Select a mesh first to check scale.")
            return
        all_bb = [cmds.exactWorldBoundingBox(m) for m in meshes]
        w = max(bb[3] for bb in all_bb) - min(bb[0] for bb in all_bb)
        h = max(bb[4] for bb in all_bb) - min(bb[1] for bb in all_bb)
        d = max(bb[5] for bb in all_bb) - min(bb[2] for bb in all_bb)
        sc = cmds.floatFieldGrp(self.unit_scale, q=True, v1=True)
        wo,ho,do = w*sc, h*sc, d*sc
        lbl = "m" if abs(sc-0.01)<0.0001 else "units"
        if ho < 0.05:  verdict,col = "WARNING: Very small — check Maya scale.", (0.55,0.2,0.2)
        elif ho > 500: verdict,col = "WARNING: Very large — check Maya scale.",  (0.55,0.2,0.2)
        else:          verdict,col = "Scale looks reasonable.",                   (0.14,0.38,0.18)
        self._set_status(f"{wo:.2f} x {ho:.2f} x {do:.2f} {lbl}  —  {verdict}", col)

    def update_ui_path(self, *args):
        is_batch = cmds.radioButtonGrp(self.mode, q=True, sl=True) == 2
        if is_batch:
            cmds.textFieldButtonGrp(self.path_field, e=True, text=DEFAULT_EXPORT_DIR)
        else:
            sel  = cmds.ls(sl=True, type='transform')
            if sel:
                # Use the first selected mesh object name, made file-friendly
                name = _make_file_friendly(sel[0])
            else:
                name = "Asset"
            cmds.textFieldButtonGrp(self.path_field, e=True,
                                    text=os.path.join(DEFAULT_EXPORT_DIR, f"{name}.glb"))

    def browse_path(self):
        is_batch = cmds.radioButtonGrp(self.mode, q=True, sl=True) == 2
        f = cmds.fileDialog2(fm=3 if is_batch else 0, dir=DEFAULT_EXPORT_DIR)
        if f: cmds.textFieldButtonGrp(self.path_field, e=True, text=f[0])

    def run_export(self, *args):
        sel    = cmds.ls(sl=True, type='transform')
        meshes = [o for o in sel if cmds.listRelatives(o, shapes=True, type='mesh')]
        if not meshes:
            _show_error_popup("Export Failed", "No mesh selected! Please select at least one mesh transform.")
            self._set_status("No mesh selected!", (0.55,0.2,0.2))
            return

        # Non-manifold check
        if cmds.checkBoxGrp(self.fix_nm, q=True, v1=True):
            nm_found = []
            for m in meshes:
                e, v = check_non_manifold(m)
                if e or v: nm_found.append((m, len(e), len(v)))
            if nm_found:
                details = "\n".join(f"  {m}: {ne} non-manifold edges, {nv} verts"
                                    for m,ne,nv in nm_found)
                resp = cmds.confirmDialog(
                    title="Non-Manifold Geometry Found",
                    message=f"Non-manifold geometry detected:\n\n{details}\n\nFix before export?",
                    button=["Fix & Export", "Export Anyway", "Cancel"],
                    defaultButton="Fix & Export", cancelButton="Cancel",
                    dismissString="Cancel")
                if resp == "Cancel":
                    self._set_status("Export cancelled.", (0.5,0.35,0.1)); return
                if resp == "Fix & Export":
                    for m,_,_ in nm_found: fix_non_manifold(m)
                    self._set_status("Non-manifold geometry fixed.", (0.14,0.38,0.18))

        tex_res_str  = cmds.optionMenuGrp(self.tex_res,    q=True, v=True)
        alpha_str    = cmds.optionMenuGrp(self.alpha_mode, q=True, v=True)
        interp_str   = cmds.optionMenuGrp(self.anim_interp,q=True, v=True)
        is_sep       = cmds.radioButton(self.orm_rb2, q=True, sl=True)

        opts = {
            'orm_mode':        'keep_separate' if is_sep else 'make_orm',
            'yup':             cmds.checkBoxGrp(self.yup,            q=True, v1=True),
            'unit_scale':      cmds.floatFieldGrp(self.unit_scale,    q=True, v1=True),
            'export_uvs':      cmds.checkBoxGrp(self.export_uvs,     q=True, v1=True),
            'export_norms':    cmds.checkBoxGrp(self.export_norms,   q=True, v1=True),
            'flip_norms':      cmds.checkBoxGrp(self.flip_norms,     q=True, v1=True),
            'export_vcs':      cmds.checkBoxGrp(self.export_vcs,     q=True, v1=True),
            'double_sided':    cmds.checkBoxGrp(self.double_sided,   q=True, v1=True),
            'apply_trs':       cmds.checkBoxGrp(self.apply_trs,      q=True, v1=True),
            'merge_verts':     cmds.checkBoxGrp(self.merge_verts,    q=True, v1=True),
            'merge_thresh':    cmds.floatFieldGrp(self.merge_thresh,  q=True, v1=True),
            'export_mats':     cmds.checkBoxGrp(self.export_mats,    q=True, v1=True),
            'export_imgs':     cmds.checkBoxGrp(self.export_imgs,    q=True, v1=True),
            'tex_jpeg':        cmds.checkBoxGrp(self.tex_jpeg,       q=True, v1=True),
            'max_tex_size':    None if tex_res_str == "No limit" else int(tex_res_str),
            'unlit':           cmds.checkBoxGrp(self.unlit,          q=True, v1=True),
            'alpha_mode':      alpha_str,
            'alpha_cutoff':    cmds.floatFieldGrp(self.alpha_cutoff,  q=True, v1=True),
            'anim_interp':     interp_str,
            'export_skeleton': cmds.checkBoxGrp(self.export_skel,    q=True, v1=True),
            'export_anim':     cmds.checkBoxGrp(self.export_anim,    q=True, v1=True),
        }

        cmds.button(self.export_btn, e=True, bgc=(0.18,0.42,0.78), l="Exporting...")
        self._set_status("Exporting...", (0.18,0.35,0.55))
        cmds.refresh()

        path     = cmds.textFieldButtonGrp(self.path_field, q=True, text=True)
        is_batch = cmds.radioButtonGrp(self.mode, q=True, sl=True) == 2

        try:
            if is_batch:
                if not os.path.isdir(path): os.makedirs(path)
                for obj in meshes:
                    safe_name = _make_file_friendly(obj)
                    out = os.path.join(path, f"{safe_name}.glb")
                    opts['export_path'] = out
                    with open(out,'wb') as fh: fh.write(build_glb([obj], opts=opts))
                    print(f"[GLB] Wrote: {out}")
                    _show_success_popup(obj, out)
                msg = f"Batch done — {len(meshes)} file(s) → {path}"
            else:
                opts['export_path'] = path
                with open(path,'wb') as fh: fh.write(build_glb(meshes, opts=opts))
                print(f"[GLB] Wrote: {path}")
                mesh_label = ", ".join(meshes) if len(meshes) <= 3 else f"{meshes[0]} +{len(meshes)-1} more"
                msg = f"Exported {len(meshes)} mesh(es) → {os.path.basename(path)}"
                _show_success_popup(mesh_label, path)

            cmds.button(self.export_btn, e=True, bgc=(0.18,0.62,0.28), l="✔  EXPORT SUCCESSFUL")
            self._set_status(msg, (0.14,0.38,0.18))
            print(f"[GLB] {msg}")
            save_settings(self._collect_settings())
            # Reset button after 3 seconds
            import threading
            def _reset_btn():
                try:
                    cmds.evalDeferred(lambda: cmds.button(self.export_btn, e=True,
                        bgc=(0.18,0.42,0.78), l="EXPORT GLB"))
                except: pass
            threading.Timer(3.0, _reset_btn).start()

        except Exception as e:
            import traceback; traceback.print_exc()
            cmds.button(self.export_btn, e=True, bgc=(0.6,0.18,0.18), l="✘  EXPORT FAILED")
            self._set_status(f"Error: {e}", (0.45,0.12,0.12))
            _show_error_popup("Export Failed", str(e))


def _boot():
    global PILLOW_OK, Image
    import importlib
    PILLOW_OK = ensure_libraries(LIB_PATH)
    if not PILLOW_OK:
        _show_error_popup("Pillow Not Found",
                          "Pillow could not be installed. Check the Script Editor for details.")
        return
    importlib.invalidate_caches()
    for mod in list(sys.modules.keys()):
        if mod == 'PIL' or mod.startswith('PIL.'): del sys.modules[mod]
    try:
        from PIL import Image as _PIL
        globals()['Image'] = _PIL
        print("[GLB] PIL imported successfully.")
    except ImportError as e:
        _show_error_popup("PIL Import Failed",
                          f"PIL import failed: {e} — try restarting Maya once.")
        return
    UE_Blender_Final_Exporter()

if LIB_PATH:
    _boot()
