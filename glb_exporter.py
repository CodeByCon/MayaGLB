"""
MayaGLB Exporter
"""

import maya.cmds as cmds
import maya.api.OpenMaya as om
import maya.api.OpenMayaAnim as oma
import os, struct, sys, subprocess, io, json, math, re, threading, time

# ---------------------------------------------------------------------------
# Drive detection + paths
# ---------------------------------------------------------------------------
LIB_PATH           = ""
DEFAULT_EXPORT_DIR = ""
SETTINGS_DIR       = ""
SETTINGS_FILE      = ""
PRESETS_FILE       = ""
STATE_FILE         = ""
ACTIVE_DRIVE       = ""

def _setup_paths(drive):
    global LIB_PATH, DEFAULT_EXPORT_DIR, SETTINGS_DIR, SETTINGS_FILE, PRESETS_FILE, STATE_FILE, ACTIVE_DRIVE
    drive = drive.rstrip("/\\")
    if not drive.endswith(":"): drive += ":"
    ACTIVE_DRIVE       = drive
    LIB_PATH           = drive + "/MayaGLB/PythonPlugins"
    DEFAULT_EXPORT_DIR = drive + "/MayaGLB/Exports"
    SETTINGS_DIR       = drive + "/MayaGLB/Settings"
    SETTINGS_FILE      = SETTINGS_DIR + "/exporter_settings.json"
    PRESETS_FILE       = SETTINGS_DIR + "/exporter_presets.json"
    STATE_FILE         = SETTINGS_DIR + "/exporter_state.json"
    for p in [LIB_PATH, DEFAULT_EXPORT_DIR, SETTINGS_DIR]:
        if not os.path.exists(p):
            try: os.makedirs(p)
            except: pass
    if LIB_PATH not in sys.path:
        sys.path.insert(0, LIB_PATH)
    print(f"[GLB] Drive: {drive}  LIB: {LIB_PATH}  EXPORT: {DEFAULT_EXPORT_DIR}")

def _find_mayaglb_drive():
    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        candidate = letter + ":/MayaGLB"
        if os.path.isdir(candidate):
            print(f"[GLB] Found existing MayaGLB folder on {letter}:")
            return letter + ":"
    return None

def _show_drive_picker():
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

_found_drive = _find_mayaglb_drive()
if _found_drive:
    _setup_paths(_found_drive)
else:
    print("[GLB] No existing MayaGLB folder found — showing drive picker.")
    _show_drive_picker()

# ---------------------------------------------------------------------------
# Persistent state (install markers, cooldown timers)
# ---------------------------------------------------------------------------
_STATE_DEFAULTS = {
    "shelf_installed":        False,
    "shelf_install_failed_at": 0,
    "pillow_last_attempt":    0,
    "pillow_last_result":     None,   # None = never tried, True/False = last outcome
}

def _load_state():
    s = dict(_STATE_DEFAULTS)
    if not STATE_FILE or not os.path.exists(STATE_FILE): return s
    try:
        with open(STATE_FILE, 'r') as f: saved = json.load(f)
        s.update({k: v for k, v in saved.items() if k in s})
    except Exception as e:
        print(f"[GLB] Could not load state: {e}")
    return s

def _save_state(s):
    if not STATE_FILE: return
    try:
        with open(STATE_FILE, 'w') as f: json.dump(s, f, indent=2)
    except Exception as e:
        print(f"[GLB] Could not save state: {e}")

# ---------------------------------------------------------------------------
# Auto shelf button installer
# ---------------------------------------------------------------------------
_GLB_SHELF_NAME = "MayaGLB Exporter"   # dedicated shelf — no ambiguity

def _shelf_button_exists():
    """Return True only if the shelf AND a valid GLB shelfButton genuinely exist."""
    try:
        if not cmds.shelfLayout(_GLB_SHELF_NAME, exists=True):
            return False
        for btn in (cmds.shelfLayout(_GLB_SHELF_NAME, q=True, childArray=True) or []):
            try:
                if cmds.shelfButton(btn, q=True, exists=True):
                    lbl = cmds.shelfButton(btn, q=True, label=True) or ""
                    if "GLB" in lbl:
                        return True
            except:
                continue
        return False
    except:
        return False

def _notify(message, success=True):
    """Pop a quick heads-up display message in the viewport so the user actually
    sees what happened, instead of it just scrolling by in the Script Editor."""
    try:
        bg = 0x2e7d32 if success else 0x8a2323
        cmds.inViewMessage(amg=message, pos='midCenterTop', fade=True,
                           fadeStayTime=2500, dragKill=True, bkc=bg)
    except Exception:
        # inViewMessage isn't available in every context (e.g. batch mode) —
        # console output below still covers us.
        pass

# The shelf button just re-runs this same file from GitHub so people always
# get the latest build without having to reinstall by hand.
_SHELF_CMD = (
    "python(\"import urllib.request as r; "
    "exec(compile(r.urlopen("
    "'https://raw.githubusercontent.com/CodeByCon/MayaGLB/main/glb_exporter.py'"
    ").read(),'<glb>','exec'))\")"
)

def _install_shelf_button():
    state = _load_state()

    # Already installed and verified on disk/UI — nothing to do, don't touch the shelf.
    if state.get("shelf_installed") and _shelf_button_exists():
        print("[GLB] Shelf button already installed — skipping.")
        return

    # A previous attempt failed — do NOT auto-retry. Retrying automatically is what
    # caused the empty-shelf loop; once it fails, it stays failed until the state
    # file is cleared manually (delete exporter_state.json in the Settings folder).
    if state.get("shelf_install_failed_at"):
        print(f"[GLB] Shelf install previously failed — not retrying automatically. "
              f"Delete {STATE_FILE} to try again.")
        return

    # Hand off to _do_install on the next idle tick — this used to just stop here
    # without ever calling _do_install(), which is the whole reason the shelf
    # button never showed up. evalDeferred also gives Maya's shelf UI a beat to
    # finish loading before we try to touch it.
    try:
        cmds.evalDeferred(_do_install)
    except Exception as e:
        print(f"[GLB] Couldn't queue the shelf install: {e}")
        _notify("GLB shelf install failed to queue — check the Script Editor", success=False)

def _do_install():
    try:
        import maya.mel as mel
        top = mel.eval('$tmp = $gShelfTopLevel')
        if not top or not cmds.shelfTabLayout(top, exists=True):
            raise RuntimeError("Shelf UI ($gShelfTopLevel) is not ready yet.")

        if cmds.shelfLayout(_GLB_SHELF_NAME, exists=True) and not _shelf_button_exists():
            print(f"[GLB] Found stale/incomplete '{_GLB_SHELF_NAME}' shelf — rebuilding.")
            cmds.deleteUI(_GLB_SHELF_NAME)

        if cmds.shelfLayout(_GLB_SHELF_NAME, exists=True):
            shelf_name = _GLB_SHELF_NAME
        else:
            # Capture whatever name Maya actually assigned, it may not match
            # _GLB_SHELF_NAME if that name was already taken somewhere in the UI.
            shelf_name = cmds.shelfLayout(_GLB_SHELF_NAME, parent=top)
            print(f"[GLB] Created shelf: {shelf_name}")
            if shelf_name != _GLB_SHELF_NAME:
                print(f"[GLB] NOTE: requested name '{_GLB_SHELF_NAME}' was taken — "
                      f"Maya assigned '{shelf_name}' instead.")

        # Make sure Maya actually registered it as a tab, and select it.
        try:
            cmds.shelfTabLayout(top, edit=True, selectTab=shelf_name)
        except Exception as tab_err:
            print(f"[GLB] Could not select shelf tab: {tab_err}")

        if _shelf_button_exists():
            print("[GLB] Shelf button already exists — skipping.")
            s = _load_state(); s["shelf_installed"] = True
            s["shelf_install_failed_at"] = 0
            _save_state(s)
            return

        cmds.setParent(shelf_name)          # use the REAL name, not the constant
        cmds.shelfButton(
             label="Export as GLB",
             annotation="Open Ultimate GLB Exporter v2.1",
             image="out_mesh.png",
             imageOverlayLabel="GLB",
             overlayLabelColor=(0.2, 0.9, 0.4),
             overlayLabelBackColor=(0, 0, 0, 0.4),
             style="iconAndTextCentered",
             command=_SHELF_CMD,
             sourceType="mel",
        )

        if not _shelf_button_exists():
            raise RuntimeError("shelfButton() returned but button could not be verified afterwards.")

        mel.eval('saveAllShelves $gShelfTopLevel')
        print(f"[GLB] Shelf button installed and saved on '{shelf_name}' shelf.")
        _notify(f"GLB Exporter added to the '{shelf_name}' shelf", success=True)

        s = _load_state()
        s["shelf_installed"] = True
        s["shelf_install_failed_at"] = 0
        _save_state(s)

    except Exception as e:
        import traceback
        print(f"[GLB] Shelf button install FAILED: {e}")
        traceback.print_exc()
        print(f"[GLB] Will not auto-retry. Delete {STATE_FILE} to try again after fixing the issue above.")
        _notify("GLB shelf install failed — see Script Editor for details", success=False)
        s = _load_state()
        s["shelf_installed"] = False
        s["shelf_install_failed_at"] = time.time()
        _save_state(s)

# ---------------------------------------------------------------------------
# Settings save / load
# ---------------------------------------------------------------------------
_SETTINGS_DEFAULTS = {
    # Transform
    "yup":            False,
    "unit_scale":     1.0,
    # Mesh
    "export_uvs":     True,
    "uv_set_count":   1,
    "export_norms":   True,
    "flip_norms":     False,
    "export_vcs":     False,
    "double_sided":   True,
    "fix_nm":         True,
    "apply_trs":      False,
    "merge_verts":    False,
    "merge_thresh":   0.001,
    # Morph / blend shapes
    "export_morphs":  False,
    # LOD
    "export_lod":     False,
    "lod_mode":       "name",      # "name" | "manual"
    "lod_levels":     4,
    # Collision
    "tag_collision":  True,
    # Skeleton / anim
    "export_skel":    False,
    "export_anim":    False,
    "anim_interp":    "LINEAR",
    # Texture
    "export_imgs":    True,
    "tex_jpeg":       False,
    "tex_res":        "No limit",
    "tex_srgb":       True,
    # Material
    "export_mats":    True,
    "unlit":          False,
    "alpha_mode":     "OPAQUE",
    "alpha_cutoff":   0.5,
    "orm_mode":       "make_orm",
    "export_emissive": False,
    # Thumbnail
    "export_thumb":   False,
    # Export mode
    "export_mode":    1,
    "export_path":    "",
}

def save_settings(data):
    if not SETTINGS_FILE:
        print("[GLB] Settings path not initialised — skipping save."); return
    try:
        with open(SETTINGS_FILE, 'w') as f: json.dump(data, f, indent=2)
        print(f"[GLB] Settings saved → {SETTINGS_FILE}")
    except Exception as e:
        print(f"[GLB] Could not save settings: {e}")

def load_settings():
    s = dict(_SETTINGS_DEFAULTS)
    if not SETTINGS_FILE or not os.path.exists(SETTINGS_FILE): return s
    try:
        with open(SETTINGS_FILE, 'r') as f: saved = json.load(f)
        s.update({k: v for k, v in saved.items() if k in s})
        print(f"[GLB] Settings loaded ← {SETTINGS_FILE}")
    except Exception as e:
        print(f"[GLB] Could not load settings: {e}")
    return s

# ---------------------------------------------------------------------------
# Presets save / load / delete
# ---------------------------------------------------------------------------
def load_presets():
    """Return dict of {preset_name: settings_dict}."""
    if not PRESETS_FILE or not os.path.exists(PRESETS_FILE): return {}
    try:
        with open(PRESETS_FILE, 'r') as f: return json.load(f)
    except: return {}

def save_preset(name, data):
    presets = load_presets()
    presets[name] = data
    try:
        with open(PRESETS_FILE, 'w') as f: json.dump(presets, f, indent=2)
        print(f"[GLB] Preset saved: {name}")
    except Exception as e:
        print(f"[GLB] Could not save preset: {e}")

def delete_preset(name):
    presets = load_presets()
    if name in presets:
        del presets[name]
        try:
            with open(PRESETS_FILE, 'w') as f: json.dump(presets, f, indent=2)
            print(f"[GLB] Preset deleted: {name}")
        except: pass

# ---------------------------------------------------------------------------
# glTF constants
# ---------------------------------------------------------------------------
FLOAT                = 5126
UNSIGNED_INT         = 5125
UNSIGNED_SHORT       = 5123
ARRAY_BUFFER         = 34962
ELEMENT_ARRAY_BUFFER = 34963

PILLOW_OK = False
Image     = None

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------
def _make_file_friendly(name):
    name = name.split('|')[-1]
    name = name.split(':')[-1]
    name = re.sub(r'[^\w\-]', '_', name)
    name = re.sub(r'_+', '_', name)
    return name.strip('_') or "Mesh"

def _show_error_popup(title, message):
    win_id = "GLB_ErrorPopup"
    if cmds.window(win_id, exists=True): cmds.deleteUI(win_id)
    longest  = max((len(line) for line in message.splitlines()), default=len(message))
    win_w    = max(360, min(longest * 7 + 60, 680))
    txt_w    = win_w - 28
    cmds.window(win_id, title=title, w=win_w, sizeable=False, toolbox=True)
    cmds.columnLayout(adj=False, w=win_w, rs=5, co=["both", 14])
    cmds.text(l="")
    cmds.text(l=f"  \u2718  {title}", fn="boldLabelFont", al="left",
              w=txt_w, bgc=(0.45, 0.12, 0.12))
    cmds.separator(h=5, style='in', w=txt_w)
    for line in message.splitlines():
        cmds.text(l=f"  {line}", fn="smallPlainLabelFont", al="left", w=txt_w)
    cmds.text(l="")
    cmds.button(l="OK", h=28, w=txt_w, bgc=(0.35, 0.35, 0.35),
                c=lambda *a: cmds.deleteUI(win_id))
    cmds.text(l="")
    cmds.setParent("..")
    cmds.showWindow(win_id)

def _show_success_popup(mesh_name, export_path):
    win_id = "GLB_SuccessPopup"
    if cmds.window(win_id, exists=True): cmds.deleteUI(win_id)
    basename = os.path.basename(export_path)
    cmds.window(win_id, title="Export Successful", w=400, sizeable=False, toolbox=True)
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
    import shutil
    removed = []
    for item in os.listdir(lib_path):
        full = os.path.join(lib_path, item)
        if (item.endswith('.dist-info') or item.endswith('.data') or
                item in ('bin', 'scripts', 'Scripts', '__pycache__')):
            try:
                shutil.rmtree(full) if os.path.isdir(full) else os.remove(full)
                removed.append(item)
            except Exception as e:
                print(f"[GLB] Cleanup warning: {e}")
    if removed: print(f"[GLB] Cleaned up: {', '.join(removed)}")

_PILLOW_RETRY_COOLDOWN = 300   # seconds between automatic reinstall attempts after a failure

def ensure_libraries(lib_path):
    if lib_path and lib_path not in sys.path:
        sys.path.insert(0, lib_path)
    try:
        from PIL import Image
        print("[GLB] Pillow already installed — ready.")
        s = _load_state(); s["pillow_last_result"] = True; _save_state(s)
        return True
    except ImportError:
        pass

    # Import failed. Check whether we already tried recently — if so, don't
    # spawn another mayapy subprocess, just report the cached failure.
    state = _load_state()
    last_attempt = state.get("pillow_last_attempt", 0) or 0
    if (state.get("pillow_last_result") is False and
            (time.time() - last_attempt) < _PILLOW_RETRY_COOLDOWN):
        remaining = int(_PILLOW_RETRY_COOLDOWN - (time.time() - last_attempt))
        print(f"[GLB] Pillow install failed recently — not retrying for {remaining}s "
              f"(delete {STATE_FILE} to force a retry sooner).")
        return False

    print(f"[GLB] Pillow not found — installing to {lib_path} ...")
    state["pillow_last_attempt"] = time.time()
    _save_state(state)

    try:
        import maya.mel as mel
        main_pb = mel.eval('$tmp = $gMainProgressBar')
        cmds.progressBar(main_pb, edit=True, beginProgress=True,
                         isInterruptable=False,
                         status=f'Installing Pillow to {lib_path} ...',
                         maxValue=100)
    except: main_pb = None
    try:
        _maya_bin  = os.path.dirname(sys.executable)
        _candidates = [
            os.path.join(_maya_bin, "mayapy.exe"),
            os.path.join(_maya_bin, "mayapy"),
            os.path.join(_maya_bin, "python.exe"),
            os.path.join(_maya_bin, "python"),
        ]
        _python_exe = next((c for c in _candidates if os.path.exists(c)), None)
        if _python_exe is None:
            state = _load_state(); state["pillow_last_result"] = False; _save_state(state)
            return False
        _sp_kwargs = {}
        if sys.platform == "win32":
            _sp_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        result = subprocess.run([_python_exe, "-m", "pip", "install", "--target", lib_path, "Pillow"], capture_output=True, text=True, **_sp_kwargs)
        state = _load_state()
        if result.returncode == 0:
            print("[GLB] Pillow installed!")
            if lib_path not in sys.path: sys.path.insert(0, lib_path)
            import importlib; importlib.invalidate_caches()
            _cleanup_pip_artifacts(lib_path)
            state["pillow_last_result"] = True
            _save_state(state)
            return True
        else:
            print(f"[GLB] Pillow install FAILED:\n{result.stderr}")
            state["pillow_last_result"] = False
            _save_state(state)
            return False
    except Exception as e:
        print(f"[GLB] Pillow install EXCEPTION: {e}")
        state = _load_state(); state["pillow_last_result"] = False; _save_state(state)
        return False
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
    cmds.polyClean(mesh_transform, cleanEdges=1, cleanVertices=1, constructionHistory=False)
    print(f"[GLB] Non-manifold cleaned: {mesh_transform}")

# ---------------------------------------------------------------------------
# Collision mesh detection
# ---------------------------------------------------------------------------
_COLLISION_PREFIXES = ("UCX_", "UBX_", "USP_", "UCP_")
_COLLISION_TYPE_MAP  = {
    "UCX_": "convex",
    "UBX_": "box",
    "USP_": "sphere",
    "UCP_": "capsule",
}

def _is_collision_mesh(name):
    short = name.split('|')[-1].split(':')[-1]
    for p in _COLLISION_PREFIXES:
        if short.upper().startswith(p):
            return True, _COLLISION_TYPE_MAP[p], short[len(p):]
    return False, None, None

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


# ---------------------------------------------------------------------------
# Thumbnail capture
# ---------------------------------------------------------------------------
def capture_thumbnail(export_path, size=256):
    thumb_path = os.path.splitext(export_path)[0] + "_thumb.png"
    try:
        cmds.playblast(
            frame=[cmds.currentTime(q=True)],
            format="image",
            completeFilename=thumb_path,
            compression="png",
            widthHeight=[size, size],
            percent=100,
            viewer=False,
            showOrnaments=False,
            offScreen=True,
        )
        print(f"[GLB] Thumbnail saved: {thumb_path}")
        if os.path.exists(thumb_path) and Image:
            return Image.open(thumb_path).convert("RGBA"), thumb_path
    except Exception as e:
        print(f"[GLB] Thumbnail capture failed: {e}")
    return None, None

# ---------------------------------------------------------------------------
# Shader reader
# ---------------------------------------------------------------------------
def get_shader_data_for_sg(sg_name):
    data = {
        'color_path':    None, 'normal_path':  None,
        'occlusion_path':None, 'roughness_path':None, 'metallic_path': None,
        'emissive_path': None,
        'factor':        [1.0, 1.0, 1.0, 1.0],
        'metallic_val':  0.0,  'roughness_val': 0.5,
        'emissive_factor':[0.0, 0.0, 0.0],
    }
    try:
        shader_conn = cmds.listConnections(sg_name + ".surfaceShader") or []
        if not shader_conn: return data
        shader = shader_conn[0]
        print(f"[GLB] Shader: '{shader}'  type={cmds.nodeType(shader)}")

        for attr in ["baseColor", "color", "diffuseColor", "base_color"]:
            if not cmds.attributeQuery(attr, n=shader, ex=True): continue
            full = shader + "." + attr
            fn   = cmds.listConnections(full, type='file') or []
            if fn:
                data['color_path'] = cmds.getAttr(fn[0] + ".fileTextureName")
            else:
                try:
                    raw = cmds.getAttr(full)
                    if isinstance(raw, (list, tuple)):
                        flat = raw[0] if isinstance(raw[0], (list, tuple)) else raw
                        r = float(flat[0]) if len(flat) > 0 else 1.0
                        g = float(flat[1]) if len(flat) > 1 else 1.0
                        b = float(flat[2]) if len(flat) > 2 else 1.0
                    else:
                        r = g = b = 1.0
                    data['factor'] = [
                        min(max(r, 0.0), 1.0),
                        min(max(g, 0.0), 1.0),
                        min(max(b, 0.0), 1.0), 1.0]
                except Exception as ce:
                    print(f"[GLB] Could not read colour factor: {ce}")
            break

        for attr in ["normalCamera", "normalMap", "normal"]:
            if not cmds.attributeQuery(attr, n=shader, ex=True): continue
            upstream = cmds.listConnections(shader + "." + attr, source=True, destination=False) or []
            for node in upstream:
                node_type = cmds.nodeType(node)
                if node_type == 'aiNormalMap':
                    nf = cmds.listConnections(node + ".input", type='file') or []
                    if nf: data['normal_path'] = cmds.getAttr(nf[0] + ".fileTextureName"); break
                elif node_type == 'bump2d':
                    nf = cmds.listConnections(node + ".bumpValue", type='file') or []
                    if nf: data['normal_path'] = cmds.getAttr(nf[0] + ".fileTextureName"); break
                elif node_type == 'file':
                    data['normal_path'] = cmds.getAttr(node + ".fileTextureName"); break
            if data['normal_path']: break

        for attr in ["specularRoughness", "roughness"]:
            if not cmds.attributeQuery(attr, n=shader, ex=True): continue
            f2 = cmds.listConnections(shader + "." + attr, type='file') or []
            if f2:
                data['roughness_path'] = cmds.getAttr(f2[0] + ".fileTextureName")
                print(f"[GLB] Roughness: {data['roughness_path']}"); break
            else:
                try: data['roughness_val'] = float(cmds.getAttr(shader + "." + attr))
                except: pass

        for attr in ["metalness", "metallic"]:
            if not cmds.attributeQuery(attr, n=shader, ex=True): continue
            f2 = cmds.listConnections(shader + "." + attr, type='file') or []
            if f2:
                data['metallic_path'] = cmds.getAttr(f2[0] + ".fileTextureName")
                print(f"[GLB] Metallic: {data['metallic_path']}"); break
            else:
                try: data['metallic_val'] = float(cmds.getAttr(shader + "." + attr))
                except: pass

        for attr in ["ambientOcclusion", "occlusion", "ao"]:
            if not cmds.attributeQuery(attr, n=shader, ex=True): continue
            f2 = cmds.listConnections(shader + "." + attr, type='file') or []
            if f2:
                data['occlusion_path'] = cmds.getAttr(f2[0] + ".fileTextureName")
                print(f"[GLB] Occlusion: {data['occlusion_path']}"); break

        for attr in ["emissionColor", "emissiveColor", "emission", "incandescence"]:
            if not cmds.attributeQuery(attr, n=shader, ex=True): continue
            full = shader + "." + attr
            f2   = cmds.listConnections(full, type='file') or []
            if f2:
                data['emissive_path'] = cmds.getAttr(f2[0] + ".fileTextureName")
                data['emissive_factor'] = [1.0, 1.0, 1.0]
                print(f"[GLB] Emissive: {data['emissive_path']}")
            else:
                try:
                    raw = cmds.getAttr(full)
                    if isinstance(raw, (list, tuple)):
                        flat = raw[0] if isinstance(raw[0], (list, tuple)) else raw
                        er = float(flat[0]) if len(flat) > 0 else 0.0
                        eg = float(flat[1]) if len(flat) > 1 else 0.0
                        eb = float(flat[2]) if len(flat) > 2 else 0.0
                        data['emissive_factor'] = [er, eg, eb]
                except: pass
            break

    except Exception as e:
        import traceback
        print(f"[GLB] get_shader_data_for_sg ERROR: {e}"); traceback.print_exc()
    return data

# ---------------------------------------------------------------------------
# Blend shape / morph target extraction
# ---------------------------------------------------------------------------
def get_blend_shapes(mesh_transform):
    shape = (cmds.listRelatives(mesh_transform, shapes=True, type='mesh') or [None])[0]
    if not shape: return []
    blend_nodes = []
    for node in (cmds.listHistory(shape) or []):
        if cmds.nodeType(node) == 'blendShape':
            blend_nodes.append(node)
    if not blend_nodes: return []

    sel = om.MSelectionList(); sel.add(mesh_transform)
    dag = sel.getDagPath(0)
    base_fn = om.MFnMesh(dag)
    base_pts = base_fn.getPoints(om.MSpace.kObject)
    num_verts = base_fn.numVertices

    targets = []
    for bs_node in blend_nodes:
        aliases = cmds.aliasAttr(bs_node, q=True) or []
        pairs = [(aliases[i], aliases[i+1]) for i in range(0, len(aliases)-1, 2)]
        for alias, attr_name in pairs:
            if not alias: continue
            idx_match = re.search(r'\[(\d+)\]', attr_name)
            if not idx_match: continue
            t_idx = int(idx_match.group(1))
            try:
                old_val = cmds.getAttr(f"{bs_node}.{alias}")
                for a2, _ in pairs:
                    try: cmds.setAttr(f"{bs_node}.{a2}", 0.0)
                    except: pass
                cmds.setAttr(f"{bs_node}.{alias}", 1.0)
                cmds.dgeval(shape)
                target_fn  = om.MFnMesh(dag)
                target_pts = target_fn.getPoints(om.MSpace.kObject)
                deltas = []
                for vi in range(num_verts):
                    bp = base_pts[vi]; tp = target_pts[vi]
                    deltas.append((tp.x - bp.x, tp.y - bp.y, tp.z - bp.z))
                targets.append({'name': alias, 'deltas': deltas})
                for a2, _ in pairs:
                    try: cmds.setAttr(f"{bs_node}.{a2}", 0.0)
                    except: pass
                cmds.setAttr(f"{bs_node}.{alias}", old_val)
                cmds.dgeval(shape)
                print(f"[GLB] Morph target captured: {alias}")
            except Exception as e:
                print(f"[GLB] Morph target '{alias}' failed: {e}")
    return targets

# ---------------------------------------------------------------------------
# LOD helpers
# ---------------------------------------------------------------------------
_LOD_SUFFIXES = ["_LOD0","_LOD1","_LOD2","_LOD3","_LOD4",
                 "_lod0","_lod1","_lod2","_lod3","_lod4",
                 "LOD0","LOD1","LOD2","LOD3","LOD4"]

def find_lod_meshes(mesh_transform):
    base = mesh_transform.split('|')[-1].split(':')[-1]
    root = base
    for suf in _LOD_SUFFIXES:
        if base.upper().endswith(suf.upper()):
            root = base[:-len(suf)]; break

    lod_list = [(0, mesh_transform)]
    for level in range(1, 5):
        for pattern in [f"{root}_LOD{level}", f"{root}_lod{level}",
                        f"{root}LOD{level}",  f"{root}lod{level}"]:
            found = cmds.ls(pattern, type='transform')
            if found and cmds.listRelatives(found[0], shapes=True, type='mesh'):
                lod_list.append((level, found[0])); break

    return sorted(lod_list, key=lambda x: x[0])

# ---------------------------------------------------------------------------
# Per-face-material geometry extraction  (multi-UV)
# ---------------------------------------------------------------------------
def extract_geometry_by_material(mesh_transform, unit_scale=1.0, uv_set_count=1):
    shape = (cmds.listRelatives(mesh_transform, shapes=True, type='mesh') or [None])[0]
    if not shape: return []

    sel_list = om.MSelectionList()
    sel_list.add(mesh_transform.split('|')[-1])
    dag_path = sel_list.getDagPath(0)
    m_fn     = om.MFnMesh(dag_path)

    face_to_sg = {}
    sg_order   = []
    shaders_mobjs, face_shader_idx = m_fn.getConnectedShaders(0)
    sg_names = []
    for mob in shaders_mobjs:
        fn   = om.MFnDependencyNode(mob)
        name = fn.name()
        sg_names.append(name)
        if name not in sg_order: sg_order.append(name)
    for fi, si in enumerate(face_shader_idx):
        sg = sg_names[si] if 0 <= si < len(sg_names) else (sg_names[0] if sg_names else None)
        if sg: face_to_sg[fi] = sg

    sgs = sg_order if sg_order else (cmds.listConnections(shape, type='shadingEngine') or [])
    if not sgs: return []
    for fi in range(m_fn.numPolygons):
        if fi not in face_to_sg: face_to_sg[fi] = sgs[0]

    raw_pts          = m_fn.getPoints(om.MSpace.kWorld)
    raw_nrms         = m_fn.getNormals(om.MSpace.kWorld)
    fv_counts, fv_verts = m_fn.getVertices()
    _, fv_nrm_ids    = m_fn.getNormalIds()
    tri_counts, tri_vis = m_fn.getTriangles()

    all_uv_set_names = m_fn.getUVSetNames()
    uv_sets_to_export = all_uv_set_names[:max(1, min(uv_set_count, 4, len(all_uv_set_names)))]
    uv_data = {}
    for uv_set in uv_sets_to_export:
        try:
            u_arr, v_arr = m_fn.getUVs(uv_set)
            _, fv_uv_ids = m_fn.getAssignedUVs(uv_set)
            uv_data[uv_set] = (u_arr, v_arr, fv_uv_ids)
        except Exception as e:
            print(f"[GLB] UV set '{uv_set}' read error: {e}")

    primary_uv_set = uv_sets_to_export[0] if uv_sets_to_export else None
    _, primary_fv_uv_ids = (m_fn.getAssignedUVs(primary_uv_set)
                             if primary_uv_set else (None, []))

    sg_geom = {sg: {
        'positions': [], 'normals': [], 'indices': [],
        'vert_ids': [], 'next_idx': 0,
        'uvs': {uv: [] for uv in uv_sets_to_export},
    } for sg in sgs}

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
                g['indices'].append(g['next_idx'])
                g['vert_ids'].append(gvi)
                g['next_idx'] += 1

                for uv_set in uv_sets_to_export:
                    if uv_set in uv_data:
                        u_arr, v_arr, fv_uv_ids = uv_data[uv_set]
                        ui = fv_uv_ids[fvi] if fvi < len(fv_uv_ids) else -1
                        g['uvs'][uv_set].append(
                            (u_arr[ui], 1.0 - v_arr[ui]) if ui >= 0 else (0.0, 0.0))

        fv_off  += fvc
        tri_off += ntris * 3

    return [{'sg': sg,
             'positions': sg_geom[sg]['positions'],
             'normals':   sg_geom[sg]['normals'],
             'uvs':       sg_geom[sg]['uvs'],
             'uv_sets':   uv_sets_to_export,
             'indices':   sg_geom[sg]['indices'],
             'vert_ids':  sg_geom[sg]['vert_ids']}
            for sg in sgs if sg_geom[sg]['positions']]

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
        if cmds.nodeType(node) == 'skinCluster': return node
    return None

def _mat4_col_major(mm):
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
    shape     = cmds.listRelatives(mesh_transform, shapes=True, type='mesh')[0]
    joint_idx = {j: i for i, j in enumerate(joints)}
    num_verts = cmds.polyEvaluate(mesh_transform, vertex=True)
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
# Core GLB builder  v2.0
# ---------------------------------------------------------------------------
def build_glb(mesh_list, opts=None):
    if opts is None: opts = {}
    orm_mode        = opts.get('orm_mode',        'make_orm')
    yup             = opts.get('yup',             False)
    unit_scale      = opts.get('unit_scale',      1.0)
    export_uvs      = opts.get('export_uvs',      True)
    uv_set_count    = opts.get('uv_set_count',    1)
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
    export_morphs   = opts.get('export_morphs',   False)
    export_lod      = opts.get('export_lod',      False)
    lod_mode        = opts.get('lod_mode',        'name')
    lod_manual      = opts.get('lod_manual',      [])
    export_emissive = opts.get('export_emissive', False)   # FIX 2: was True
    tex_srgb        = opts.get('tex_srgb',        True)
    export_thumb    = opts.get('export_thumb',    False)
    tag_collision   = opts.get('tag_collision',   True)
    export_path     = opts.get('export_path',     '')

    print(f"[GLB] Settings → orm_mode={orm_mode!r}  "
          f"export_imgs={export_imgs}  export_emissive={export_emissive}  yup={yup}  unit_scale={unit_scale}")

    gltf = {
        "asset":       {"version":"2.0","generator":"Maya Ultimate GLB Exporter v2.0"},
        "scene":       0,
        "scenes":      [{"nodes":[0]}],
        "nodes":       [{"mesh":0}],
        "meshes":      [{"primitives":[], "extras":{}}],
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
    collision_meshes = []

    def add_bv(data, target=None):
        nonlocal bin_blob, bv_idx
        data  = _pad4(data); start = len(bin_blob); bin_blob += data
        bv    = {"buffer":0,"byteOffset":start,"byteLength":len(data)}
        if target: bv["target"] = target
        gltf["bufferViews"].append(bv)
        i = bv_idx; bv_idx += 1; return i

    def add_acc(bv, comp, count, atype, normalized=False, mn=None, mx=None):
        nonlocal acc_idx
        a = {"bufferView":bv,"byteOffset":0,"componentType":comp,"count":count,"type":atype}
        if normalized: a["normalized"] = True
        if mn is not None: a["min"] = mn
        if mx is not None: a["max"] = mx
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
        img = Image.open(path)
        img = img.convert('RGBA') if tex_srgb else img.convert('RGB')
        return embed_pil(img, cache_key=path)

    thumb_tex_idx = None
    if export_thumb and export_path:
        thumb_img, thumb_path = capture_thumbnail(export_path)
        if thumb_img:
            thumb_tex_idx = embed_pil(thumb_img, cache_key="__thumb__")
            gltf["asset"]["extras"] = {"thumbnail": {"index": thumb_tex_idx}}

    joint_list       = []
    joint_node_start = 1

    if export_skeleton:
        sel_joints = [j for j in (cmds.ls(sl=True) or []) if cmds.nodeType(j) == 'joint']
        if not sel_joints:
            sel_joints = cmds.ls(type='joint') or []
        roots = [j for j in sel_joints
                 if not (cmds.listRelatives(j, parent=True, type='joint') or [])]
        for root in roots:
            joint_list += collect_joint_hierarchy(root)
        joint_list = list(dict.fromkeys(joint_list))

    temps        = []
    all_vert_ids = []
    prim_mesh_sg = []

    meshes_with_lod = []
    for mesh_transform in mesh_list:
        is_col, col_type, col_base = _is_collision_mesh(mesh_transform)
        if is_col and tag_collision:
            collision_meshes.append({
                'transform': mesh_transform,
                'type':      col_type,
                'base_name': col_base,
            })
            print(f"[GLB] Collision mesh tagged: {mesh_transform} ({col_type})")
        if export_lod and lod_mode == 'name':
            lod_pairs = find_lod_meshes(mesh_transform)
        elif export_lod and lod_mode == 'manual':
            # lod_manual is [lod1_name, lod2_name, lod3_name, lod4_name]
            # LOD0 is always the base mesh_transform
            lod_pairs = [(0, mesh_transform)]
            for level, name in enumerate(lod_manual, start=1):
                name = (name or '').strip()
                if name and cmds.objExists(name) and cmds.listRelatives(name, shapes=True, type='mesh'):
                    lod_pairs.append((level, name))
                elif name:
                    print(f"[GLB] Manual LOD{level} '{name}' not found — skipped")
        else:
            lod_pairs = [(0, mesh_transform)]
        meshes_with_lod.append((mesh_transform, lod_pairs))

    if collision_meshes and tag_collision:
        gltf["scenes"][0].setdefault("extras", {})
        gltf["scenes"][0]["extras"]["collision_meshes"] = collision_meshes

    for mesh_idx, (mesh_transform, lod_pairs) in enumerate(meshes_with_lod):
        print(f"\n[GLB] ── Processing: {mesh_transform}  ({len(lod_pairs)} LOD levels)")

        cur_mesh_idx = 0
        lod_prim_ranges = {}

        for lod_level, lod_transform in lod_pairs:
            print(f"[GLB]   LOD{lod_level}: {lod_transform}")

            dup_result = cmds.duplicate(lod_transform, returnRootsOnly=True)[0]
            tmp = cmds.rename(dup_result, f"_GLB_tmp_{id(dup_result)}")
            if cmds.listRelatives(tmp, parent=True):
                tmp = cmds.parent(tmp, world=True)[0]
            if apply_trs:
                cmds.makeIdentity(tmp, apply=True, t=True, r=True, s=True)
            if merge_verts:
                cmds.polyMergeVertex(tmp, d=merge_thresh, constructionHistory=False)
            cmds.polyTriangulate(tmp)
            tmp = cmds.ls(tmp, long=False)[0]
            temps.append(tmp)

            prim_groups = extract_geometry_by_material(
                tmp, unit_scale=unit_scale, uv_set_count=uv_set_count)
            if not prim_groups:
                print(f"[GLB] WARNING: No geometry on {lod_transform}, skipping."); continue

            morph_targets = []
            if export_morphs and lod_level == 0:
                morph_targets = get_blend_shapes(lod_transform)
                if morph_targets:
                    print(f"[GLB] {len(morph_targets)} morph target(s) found on {lod_transform}")

            lod_prim_start = len(gltf["meshes"][cur_mesh_idx]["primitives"])

            for group in prim_groups:
                sg        = group['sg']
                positions = group['positions']
                normals   = group['normals']
                uvs_dict  = group['uvs']
                uv_sets   = group['uv_sets']
                indices   = group['indices']
                vert_ids  = group['vert_ids']
                vc        = len(positions)

                if yup:
                    positions = [( p[0],  p[2], -p[1]) for p in positions]
                    normals   = [( n[0],  n[2], -n[1]) for n in normals]

                if flip_norms:
                    normals = [(-n[0], -n[1], -n[2]) for n in normals]

                px = [p[0] for p in positions]; py = [p[1] for p in positions]; pz = [p[2] for p in positions]
                pos_min = [min(px), min(py), min(pz)]
                pos_max = [max(px), max(py), max(pz)]

                bv_pos  = add_bv(_pad4(b"".join(struct.pack("<fff",*p) for p in positions)), ARRAY_BUFFER)
                bv_ind  = add_bv(_pad4(b"".join(struct.pack("<I",  i)  for i in indices)),   ELEMENT_ARRAY_BUFFER)
                acc_pos = add_acc(bv_pos, FLOAT,        vc,           "VEC3", mn=pos_min, mx=pos_max)
                acc_ind = add_acc(bv_ind, UNSIGNED_INT, len(indices), "SCALAR")
                attribs = {"POSITION": acc_pos}

                if export_norms:
                    bv_n = add_bv(_pad4(b"".join(struct.pack("<fff",*n) for n in normals)), ARRAY_BUFFER)
                    attribs["NORMAL"] = add_acc(bv_n, FLOAT, vc, "VEC3")

                if export_uvs:
                    for ui, uv_set in enumerate(uv_sets):
                        uv_list = uvs_dict.get(uv_set, [])
                        if uv_list:
                            bv_u = add_bv(_pad4(b"".join(struct.pack("<ff", *u) for u in uv_list)), ARRAY_BUFFER)
                            attribs[f"TEXCOORD_{ui}"] = add_acc(bv_u, FLOAT, vc, "VEC2")

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
                                    min(max(c.r,0.0),1.0), min(max(c.g,0.0),1.0),
                                    min(max(c.b,0.0),1.0), min(max(c.a,0.0),1.0),))
                            vc_bytes = _pad4(b"".join(struct.pack("<ffff",*c) for c in vc_data))
                            attribs["COLOR_0"] = add_acc(add_bv(vc_bytes, ARRAY_BUFFER), FLOAT, vc, "VEC4")
                    except Exception as vc_err:
                        print(f"[GLB] Vertex colour export failed: {vc_err}")

                prim_morphs = []
                morph_weights = []
                if morph_targets:
                    for mt in morph_targets:
                        deltas = mt['deltas']
                        tri_deltas = []
                        for vi in vert_ids:
                            d = deltas[vi] if vi < len(deltas) else (0.0, 0.0, 0.0)
                            if yup:
                                d = (d[0], d[2], -d[1])
                            tri_deltas.append(d)
                        mt_bv  = add_bv(_pad4(b"".join(struct.pack("<fff",*d) for d in tri_deltas)), ARRAY_BUFFER)
                        mt_acc = add_acc(mt_bv, FLOAT, vc, "VEC3")
                        prim_morphs.append({"POSITION": mt_acc})
                        morph_weights.append(0.0)

                # ------------------------------------------------------------------
                # Material / texture export
                # ------------------------------------------------------------------
                mat_idx = None

                if export_mats:
                    md = get_shader_data_for_sg(sg)

                    # ── Full PBR material — always written ───────────────────────
                    # make_orm:      pack O+R+M → single ORM texture
                    # keep_separate: RM packed into metallicRoughnessTexture (G=rough, B=metal)
                    #                AO embedded separately into occlusionTexture
                    color_tex = embed_file(md['color_path'])  if export_imgs else None
                    norm_tex  = embed_file(md['normal_path']) if export_imgs else None
                    emi_tex   = embed_file(md['emissive_path']) if (export_imgs and export_emissive) else None
                    orm_tex   = None   # make_orm: single packed ORM
                    rm_tex    = None   # keep_separate: packed RM (no AO channel)
                    ao_tex    = None   # keep_separate: AO as separate occlusionTexture

                    if export_imgs:
                        if orm_mode == 'make_orm':
                            # Pack O+R+M → single ORM texture (R=occlusion, G=roughness, B=metallic)
                            # Both metallicRoughnessTexture and occlusionTexture point to it.
                            o = md['occlusion_path']
                            r = md['roughness_path']
                            m = md['metallic_path']
                            if any([o, r, m]):
                                orm_tex = embed_pil(
                                    pack_orm_textures(o, r, m),
                                    cache_key=f"ORM::{o}::{r}::{m}")
                        else:
                            # keep_separate:
                            #   metallicRoughnessTexture → RM pack (G=roughness, B=metallic, R=255)
                            #     maps to bHasMetallicRoughnessTexture in UE MI_Default
                            #   occlusionTexture         → AO embedded as its own texture
                            #     maps to bHasOcclusionTexture in UE MI_Default
                            r_path = md['roughness_path']
                            m_path = md['metallic_path']
                            o_path = md['occlusion_path']
                            if r_path or m_path:
                                rm_tex = embed_pil(
                                    pack_orm_textures(None, r_path, m_path),
                                    cache_key=f"RM::{r_path}::{m_path}")
                            ao_tex = embed_file(o_path)

                    pbr = {
                        "baseColorFactor": md['factor'],
                        "metallicFactor":  md['metallic_val'] if (orm_tex is None and rm_tex is None) else 1.0,
                        "roughnessFactor": md['roughness_val'] if (orm_tex is None and rm_tex is None) else 1.0,
                    }
                    if color_tex is not None: pbr["baseColorTexture"]         = {"index": color_tex}
                    if orm_tex   is not None: pbr["metallicRoughnessTexture"] = {"index": orm_tex}
                    elif rm_tex  is not None: pbr["metallicRoughnessTexture"] = {"index": rm_tex}

                    mat = {
                        "name":                 f"M_{mesh_transform}_{sg}",
                        "doubleSided":          double_sided,
                        "pbrMetallicRoughness": pbr,
                        "alphaMode":            alpha_mode,
                    }
                    if alpha_mode == "MASK": mat["alphaCutoff"] = alpha_cutoff
                    if norm_tex  is not None: mat["normalTexture"]    = {"index": norm_tex}
                    if ao_tex    is not None: mat["occlusionTexture"] = {"index": ao_tex}
                    elif orm_tex is not None: mat["occlusionTexture"] = {"index": orm_tex}
                    if emi_tex is not None and export_emissive:
                        mat["emissiveTexture"] = {"index": emi_tex}
                        mat["emissiveFactor"]  = md.get('emissive_factor', [1.0, 1.0, 1.0])
                    elif any(v > 0 for v in md.get('emissive_factor', [0, 0, 0])) and export_emissive:
                        mat["emissiveFactor"] = md['emissive_factor']
                    if unlit:
                        mat.setdefault("extensions", {})
                        mat["extensions"]["KHR_materials_unlit"] = {}
                        gltf.setdefault("extensionsUsed", [])
                        if "KHR_materials_unlit" not in gltf["extensionsUsed"]:
                            gltf["extensionsUsed"].append("KHR_materials_unlit")

                    gltf["materials"].append(mat)
                    mat_idx = len(gltf["materials"]) - 1


                prim = {"attributes": attribs, "indices": acc_ind}
                if mat_idx is not None: prim["material"] = mat_idx


                prim["extras"] = {"lod_level": lod_level}
                is_col, col_type, _ = _is_collision_mesh(lod_transform)
                if is_col and tag_collision:
                    prim["extras"]["collision_type"] = col_type

                if prim_morphs:
                    prim["targets"]  = prim_morphs
                    prim["extras"]["targetNames"] = [mt['name'] for mt in morph_targets]

                gltf["meshes"][cur_mesh_idx]["primitives"].append(prim)

                if prim_morphs and "weights" not in gltf["meshes"][cur_mesh_idx]:
                    gltf["meshes"][cur_mesh_idx]["weights"] = morph_weights
                    gltf["meshes"][cur_mesh_idx]["extras"]["targetNames"] = [
                        mt['name'] for mt in morph_targets]

                prim_mesh_sg.append((mesh_transform, vert_ids, None))
                all_vert_ids.append((mesh_transform, vert_ids))

            lod_prim_end = len(gltf["meshes"][cur_mesh_idx]["primitives"])
            lod_prim_ranges[lod_level] = list(range(lod_prim_start, lod_prim_end))

        if len(lod_prim_ranges) > 1:
            gltf["meshes"][cur_mesh_idx]["extras"]["lod_prim_ranges"] = lod_prim_ranges

    for t in temps:
        try: cmds.delete(t)
        except: pass

    if not any(len(m["primitives"]) > 0 for m in gltf["meshes"]):
        raise RuntimeError("No primitives generated — check mesh selection.")

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
        if root_idxs: gltf["scenes"][0]["nodes"] += root_idxs

        ibms    = get_inverse_bind_matrices(joint_list, unit_scale, yup)
        ibm_bv  = add_bv(_pad4(b"".join(struct.pack("<16f", *m) for m in ibms)))
        ibm_acc = add_acc(ibm_bv, FLOAT, len(joint_list), "MAT4")
        j_node_indices = [joint_node_start + i for i in range(len(joint_list))]
        gltf.setdefault("skins", []).append({
            "name": "Armature", "joints": j_node_indices, "inverseBindMatrices": ibm_acc})
        gltf["nodes"][0]["skin"] = 0

        for prim_i, (mesh_transform, vert_ids) in enumerate(all_vert_ids):
            sc = get_skin_cluster(mesh_transform)
            if not sc: continue
            all_j_table, all_w_table = extract_skin_weights(sc, mesh_transform, joint_list)
            prim_j = [all_j_table[vi] for vi in vert_ids]
            prim_w = [all_w_table[vi] for vi in vert_ids]
            jb = _pad4(b"".join(struct.pack("<HHHH", *row) for row in prim_j))
            wb = _pad4(b"".join(struct.pack("<ffff", *row) for row in prim_w))
            vc = len(prim_j)
            j_acc = add_acc(add_bv(jb, ARRAY_BUFFER), UNSIGNED_SHORT, vc, "VEC4")
            w_acc = add_acc(add_bv(wb, ARRAY_BUFFER), FLOAT,          vc, "VEC4")
            flat_prims = [p for m in gltf["meshes"] for p in m["primitives"]]
            if prim_i < len(flat_prims):
                flat_prims[prim_i]["attributes"]["JOINTS_0"]  = j_acc
                flat_prims[prim_i]["attributes"]["WEIGHTS_0"] = w_acc

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
                    frames  = anim_data[j][key]
                    data_bv = add_bv(_pad4(b"".join(struct.pack(fmt, *f) for f in frames)))
                    out_acc = add_acc(data_bv, FLOAT, len(frames), atype)
                    samplers.append({"input": t_acc, "output": out_acc, "interpolation": anim_interp})
                    channels.append({"sampler": si, "target": {"node": node_idx, "path": path}})
                    si += 1
            gltf["animations"] = [{"name":"Take001","samplers":samplers,"channels":channels}]
            print(f"[GLB] Animation: {len(times)} frames, {si} channels")

    gltf["buffers"][0]["byteLength"] = len(bin_blob)
    for key in ["textures", "images", "materials", "skins", "animations"]:
        if not gltf.get(key): gltf.pop(key, None)

    return pack_glb(gltf, bin_blob)


# ---------------------------------------------------------------------------
# UI  v2.1
# ---------------------------------------------------------------------------
class UE_Blender_Final_Exporter:
    def __init__(self):
        self.win = "UE_Final_Exporter_Win"
        if cmds.window(self.win, exists=True): cmds.deleteUI(self.win)
        cmds.window(self.win, title="Ultimate GLB Exporter  v2.1",
                    w=520, sizeable=True, topLeftCorner=[100,100], toolbox=True)
        cmds.scrollLayout("GLB_Scroll", cr=True, hst=0, vst=14, childResizable=True)
        root = cmds.columnLayout("GLB_RootCol", adj=True, rs=0)

        cmds.frameLayout(l="", bv=False, mh=6, mw=0, p=root)
        cmds.text(l="  ULTIMATE GLB EXPORTER  v2.1", fn="boldLabelFont",
                  al="center", h=28, bgc=(0.10, 0.10, 0.16))
        cmds.text(l="  Blender / UE  ·  LOD  ·  Morphs  ·  Multi-UV  ·  Emissive  ·  Collision  ·  Presets",
                  fn="smallPlainLabelFont", al="center", h=18, bgc=(0.10, 0.10, 0.16))
        cmds.setParent('..'); cmds.setParent(root)

        CW = [200, 50]

        def _sub(label, collapsed=False):
            fl = cmds.frameLayout(l=f"  {label}", cll=True, cl=collapsed,
                                  mh=6, mw=14, bv=True,
                                  cc=self._refresh_win, ec=self._refresh_win)
            cmds.columnLayout(adj=True, rs=5)
            return fl

        def _end_sub():
            cmds.setParent('..')
            cmds.setParent('..')

        sf = cmds.frameLayout(l="  ▸  Settings & Presets", cll=True, cl=False,
                              mh=6, mw=6, p=root,
                              cc=self._refresh_win, ec=self._refresh_win)
        cmds.columnLayout(adj=True, rs=4)

        cmds.rowLayout(nc=4, cw4=[140,100,100,100])
        cmds.text(l="  Preset:", fn="smallPlainLabelFont")
        self.preset_menu = cmds.optionMenu(w=140, cc=self._on_preset_select)
        cmds.menuItem(l="-- select --")
        self._rebuild_preset_menu()
        cmds.button(l="Save Preset",   w=96,  c=self._save_preset_dialog)
        cmds.button(l="Delete Preset", w=96,  c=self._delete_preset)
        cmds.setParent('..')

        _sub("Transform")
        self.yup        = cmds.checkBoxGrp(l="+Y Up (Z→Y axis swap):", v1=False, cw2=CW,
                                            ann="Rotate mesh so Maya Z-up becomes Y-up in GLB.")
        self.unit_scale = cmds.floatFieldGrp(l="Scale multiplier:", nf=1, v1=1.0, cw2=[170,80],
                                              pre=6,
                                              ann="Multiply all vertex positions by this value.\nExamples: 1.0 = no change, 0.01 = cm to metres, 100 = metres to cm.")
        cmds.rowLayout(nc=2, cw2=[200,220])
        cmds.text(l="")
        cmds.button(l="Check Selection Scale", c=self._check_scale)
        cmds.setParent('..')
        _end_sub()

        _sub("Mesh")
        self.export_uvs   = cmds.checkBoxGrp(l="Export UVs:",             v1=True,  cw2=CW)
        self.uv_set_count = cmds.intSliderGrp(l="UV set count (1-4):", f=True, min=1, max=4, v=1,
                                               cw3=[170,40,100],
                                               ann="How many UV sets to export as TEXCOORD_0..3.")
        self.export_norms = cmds.checkBoxGrp(l="Export normals:",          v1=True,  cw2=CW)
        self.flip_norms   = cmds.checkBoxGrp(l="Flip normals:",            v1=False, cw2=CW)
        self.export_vcs   = cmds.checkBoxGrp(l="Export vertex colours:",   v1=False, cw2=CW)
        self.double_sided = cmds.checkBoxGrp(l="Double sided:",            v1=True,  cw2=CW)
        self.fix_nm       = cmds.checkBoxGrp(l="Check non-manifold geo:",  v1=True,  cw2=CW)
        self.apply_trs    = cmds.checkBoxGrp(l="Apply transform (freeze):",v1=False, cw2=CW)
        self.merge_verts  = cmds.checkBoxGrp(l="Merge vertices:",          v1=False, cw2=CW)
        self.merge_thresh = cmds.floatFieldGrp(l="  Merge threshold:", nf=1, v1=0.001, cw2=[170,80])
        _end_sub()

        _sub("LOD Export", collapsed=True)
        self.export_lod  = cmds.checkBoxGrp(l="Export LODs:", v1=False, cw2=CW,
                                             cc=self._on_lod_change,
                                             ann="Auto-detect LOD siblings by name or use manual assignment.")
        self.lod_mode    = cmds.radioButtonGrp(
            l="LOD source:", labelArray2=["Auto-detect by name", "Manual selection"],
            numberOfRadioButtons=2, sl=1, cw3=[170, 160, 130])
        cmds.separator(h=4, style='in')
        cmds.text(l="  Manual LOD assignment (leave blank to skip):", fn="smallPlainLabelFont", al="left")
        self.lod_fields = []
        for i in range(1, 5):
            f = cmds.textFieldButtonGrp(l=f"  LOD{i}:", bl="Pick", cw=[1,60],
                                         bc=self._make_lod_picker(i-1))
            self.lod_fields.append(f)
        _end_sub()

        _sub("Morph Targets / Blend Shapes", collapsed=True)
        self.export_morphs = cmds.checkBoxGrp(
            l="Export blend shapes as morphs:", v1=False, cw2=CW)
        cmds.text(l="  Blend shape weights baked to LOD0 only.",
                  fn="smallPlainLabelFont", al="left")
        _end_sub()

        _sub("Collision Mesh Tagging", collapsed=True)
        self.tag_collision = cmds.checkBoxGrp(
            l="Tag collision meshes (UCX_ UBX_ USP_):", v1=True, cw2=CW)
        cmds.text(l="  Collision geometry is exported + tagged in glTF extras.",
                  fn="smallPlainLabelFont", al="left")
        cmds.text(l="  UCX_ = Convex   UBX_ = Box   USP_ = Sphere   UCP_ = Capsule",
                  fn="smallPlainLabelFont", al="left")
        _end_sub()

        _sub("Animation", collapsed=True)
        self.export_skel = cmds.checkBoxGrp(l="Export skeleton:", v1=False, cw2=CW,
                                             cc=self._on_skel_change)
        self.export_anim = cmds.checkBoxGrp(l="Export animation:", v1=False, cw2=CW, en=False)
        self.anim_interp = cmds.optionMenuGrp(l="Interpolation:", cw2=[170,100])
        for m in ["LINEAR","STEP","CUBICSPLINE"]: cmds.menuItem(l=m)
        cmds.rowLayout(nc=2, cw2=[200,240])
        cmds.text(l="  Playback range:", fn="smallPlainLabelFont")
        self.range_text = cmds.text(l="", fn="smallPlainLabelFont")
        cmds.setParent('..')
        self._update_range_text()
        _end_sub()

        _sub("Texture", collapsed=True)
        self.export_imgs = cmds.checkBoxGrp(l="Embed textures:",          v1=True,  cw2=CW)
        self.tex_jpeg    = cmds.checkBoxGrp(l="Convert to JPEG:",         v1=False, cw2=CW)
        self.tex_res     = cmds.optionMenuGrp(l="Max texture size:", cw2=[170,100])
        for r in ["No limit","256","512","1024","2048","4096"]: cmds.menuItem(l=r)
        self.tex_srgb    = cmds.checkBoxGrp(l="Force sRGB colour space:", v1=True,  cw2=CW)
        self.export_thumb= cmds.checkBoxGrp(l="Embed viewport thumbnail:",v1=False, cw2=CW)
        _end_sub()

        _sub("Material", collapsed=True)
        self.export_mats    = cmds.checkBoxGrp(l="Export materials:",     v1=True,  cw2=CW)
        self.unlit           = cmds.checkBoxGrp(l="Unlit (shadeless):",   v1=False, cw2=CW)
        self.export_emissive = cmds.checkBoxGrp(l="Export emissive:",     v1=False, cw2=CW,
                                                 ann="Read emission/incandescence from the shader and export\nas glTF emissiveTexture + emissiveFactor.")
        self.alpha_mode      = cmds.optionMenuGrp(l="Alpha mode:", cw2=[170,100])
        for m in ["OPAQUE","MASK","BLEND"]: cmds.menuItem(l=m)
        self.alpha_cutoff    = cmds.floatFieldGrp(l="Alpha cutoff (MASK):", nf=1, v1=0.5, cw2=[170,80])
        cmds.text(l="  ORM:", fn="smallPlainLabelFont", al="left")
        self.orm_make = cmds.radioCollection()
        self.orm_rb1  = cmds.radioButton(l="ORM  (pack Occlusion + Roughness + Metallic → 1 texture)",
                                          sl=True, cc=self._on_orm_mode_change)
        self.orm_rb2  = cmds.radioButton(l="Separate AO  (RM packed texture + AO as separate occlusionTexture)",
                                          sl=False, cc=self._on_orm_mode_change)
        self.orm_sep_info = cmds.frameLayout(l="  Separate AO — note", bv=True, mh=4, mw=10)
        cmds.columnLayout(adj=True, rs=3)
        cmds.text(l="  metallicRoughnessTexture: G=Roughness, B=Metallic  (bHasMetallicRoughnessTexture)", fn="smallPlainLabelFont", al="left")
        cmds.text(l="  occlusionTexture: AO as its own texture  (bHasOcclusionTexture)", fn="smallPlainLabelFont", al="left")
        cmds.setParent('..'); cmds.setParent('..')
        cmds.frameLayout(self.orm_sep_info, e=True, vis=False)
        _end_sub()

        cmds.setParent('..'); cmds.setParent('..')  # end settings outer frame

        cmds.frameLayout(l="", bv=False, mh=8, mw=10, p=root)
        self.mode = cmds.radioButtonGrp(
            l="Mode: ", labelArray2=["Single file (merge all)", "Batch (one per obj)"],
            numberOfRadioButtons=2, sl=1, cc=self.update_ui_path, cw3=[60,175,175])
        self.path_field = cmds.textFieldButtonGrp(
            l="Output path:", bl="Browse", bc=self.browse_path,
            text=os.path.join(DEFAULT_EXPORT_DIR, "MyAsset.glb"),
            cw=[1,90], adj=2)
        cmds.setParent('..'); cmds.setParent(root)

        cmds.frameLayout(l="", bv=False, mh=4, mw=10, p=root)
        self.status_text = cmds.text(l="  Ready.", al="left",
                                     fn="smallPlainLabelFont", h=20, bgc=(0.18,0.18,0.18))
        cmds.setParent('..'); cmds.setParent(root)

        cmds.frameLayout(l="", bv=False, mh=8, mw=10, p=root)
        self.export_btn = cmds.button(l="EXPORT GLB", h=52,
                                      bgc=(0.18,0.42,0.78), c=self.run_export)
        cmds.setParent('..'); cmds.setParent(root)

        self.credits_frame = cmds.frameLayout(
            l="  ▸  Credits", cll=True, cl=True, mh=8, mw=10, p=root,
            cc=self._refresh_win, ec=self._refresh_win)
        cmds.columnLayout(adj=True, rs=5)
        cmds.text(l="  Ultimate GLB Exporter  v2.1",         fn="boldLabelFont",       al="left")
        cmds.text(l="  Maya 2026  ·  Python 3  ·  glTF 2.0", fn="smallPlainLabelFont", al="left")
        cmds.separator(h=8, style='in')
        cmds.text(l="  CREDITS",                              fn="boldLabelFont",       al="left")
        cmds.separator(h=6, style='in')
        cmds.text(l="  Connor Henry          -  Main Developer",                   al="left", fn="smallPlainLabelFont")
        cmds.text(l="  Claude / Anthropic    -  Debugging / Code Assistance",      al="left", fn="smallPlainLabelFont")
        cmds.text(l="  Jack Clewer           -  Being a Good Teacher",             al="left", fn="smallPlainLabelFont")
        cmds.text(l="  Maya                  -  Being annoying by not having GLB export.", al="left", fn="smallPlainLabelFont")
        cmds.text(l="  ", al="left", fn="smallPlainLabelFont")
        cmds.setParent('..'); cmds.setParent('..')

        self._apply_settings(load_settings())
        cmds.showWindow(self.win)
        cmds.evalDeferred(self._fit_window_height)

    def _refresh_win(self, *args):
        try: cmds.evalDeferred(lambda *a: cmds.evalDeferred(self._fit_window_height))
        except: pass

    def _fit_window_height(self, *args):
        try:
            content_h = cmds.columnLayout("GLB_RootCol", q=True, h=True)
            capped    = max(200, min(content_h + 4, 950))
            cmds.window(self.win, e=True, h=capped, w=520)
        except: pass

    def _set_status(self, msg, colour=(0.18,0.18,0.18)):
        try: cmds.text(self.status_text, e=True, l=f"  {msg}", bgc=colour)
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

    def _on_lod_change(self, *args):
        self._refresh_win()

    def _make_lod_picker(self, idx):
        def _pick(*a):
            sel = cmds.ls(sl=True, type='transform')
            if sel and cmds.listRelatives(sel[0], shapes=True, type='mesh'):
                cmds.textFieldButtonGrp(self.lod_fields[idx], e=True, text=sel[0])
            else:
                _show_error_popup("LOD Pick", "Select a mesh transform first.")
        return _pick

    def _check_scale(self, *args):
        sel    = cmds.ls(sl=True, type='transform')
        meshes = [o for o in sel if cmds.listRelatives(o, shapes=True, type='mesh')]
        if not meshes:
            _show_error_popup("Scale Check", "Select a mesh first."); return
        all_bb = [cmds.exactWorldBoundingBox(m) for m in meshes]
        w = max(bb[3] for bb in all_bb) - min(bb[0] for bb in all_bb)
        h = max(bb[4] for bb in all_bb) - min(bb[1] for bb in all_bb)
        d = max(bb[5] for bb in all_bb) - min(bb[2] for bb in all_bb)
        sc = cmds.floatFieldGrp(self.unit_scale, q=True, v1=True)
        wo,ho,do = w*sc, h*sc, d*sc
        lbl = "m" if abs(sc-0.01)<0.0001 else "units"
        if ho < 0.05:  verdict,col = "WARNING: Very small — check scale.", (0.55,0.2,0.2)
        elif ho > 500: verdict,col = "WARNING: Very large — check scale.",  (0.55,0.2,0.2)
        else:          verdict,col = "Scale looks reasonable.",              (0.14,0.38,0.18)
        self._set_status(f"{wo:.2f} x {ho:.2f} x {do:.2f} {lbl}  —  {verdict}", col)

    def update_ui_path(self, *args):
        is_batch = cmds.radioButtonGrp(self.mode, q=True, sl=True) == 2
        if is_batch:
            cmds.textFieldButtonGrp(self.path_field, e=True, text=DEFAULT_EXPORT_DIR)
        else:
            sel  = cmds.ls(sl=True, type='transform')
            name = _make_file_friendly(sel[0]) if sel else "Asset"
            cmds.textFieldButtonGrp(self.path_field, e=True,
                                    text=os.path.join(DEFAULT_EXPORT_DIR, f"{name}.glb"))

    def browse_path(self):
        is_batch = cmds.radioButtonGrp(self.mode, q=True, sl=True) == 2
        f = cmds.fileDialog2(fm=3 if is_batch else 0, dir=DEFAULT_EXPORT_DIR)
        if f: cmds.textFieldButtonGrp(self.path_field, e=True, text=f[0])

    def _rebuild_preset_menu(self):
        try:
            items = cmds.optionMenu(self.preset_menu, q=True, itemListLong=True) or []
            for it in items: cmds.deleteUI(it)
            cmds.menuItem(l="-- select --", parent=self.preset_menu)
            for name in sorted(load_presets().keys()):
                cmds.menuItem(l=name, parent=self.preset_menu)
        except Exception as e:
            print(f"[GLB] _rebuild_preset_menu: {e}")

    def _on_preset_select(self, val):
        if val == "-- select --": return
        presets = load_presets()
        if val in presets:
            self._apply_settings(presets[val])
            self._set_status(f"Preset loaded: {val}", (0.14,0.38,0.18))

    def _save_preset_dialog(self, *args):
        win = "GLB_PresetSave"
        if cmds.window(win, exists=True): cmds.deleteUI(win)
        cmds.window(win, title="Save Preset", w=300, sizeable=False, toolbox=True)
        cmds.columnLayout(adj=True, rs=8, co=["both",14])
        cmds.text(l="")
        cmds.text(l="Preset name:", fn="smallPlainLabelFont", al="left")
        field = cmds.textField(w=260, text="My Preset")
        warn  = cmds.text(l="", fn="smallPlainLabelFont", al="left")
        def _do(*a):
            name = cmds.textField(field, q=True, text=True).strip()
            if not name: cmds.text(warn, e=True, l="  Enter a name."); return
            save_preset(name, self._collect_settings())
            self._rebuild_preset_menu()
            cmds.deleteUI(win)
            self._set_status(f"Preset saved: {name}", (0.14,0.38,0.18))
        cmds.button(l="Save", h=28, bgc=(0.18,0.42,0.78), c=_do)
        cmds.text(l="")
        cmds.setParent('..')
        cmds.showWindow(win)

    def _delete_preset(self, *args):
        val = cmds.optionMenu(self.preset_menu, q=True, v=True)
        if val == "-- select --": return
        delete_preset(val)
        self._rebuild_preset_menu()
        self._set_status(f"Preset deleted: {val}", (0.5,0.35,0.1))

    def _collect_settings(self):
        # Force changes when any input is put in scale, this fixes issue 1: github.com/CodeByCon/MayaGLB/issues/1
        try: cmds.setFocus("GLB_RootCol")
        except Exception: pass
        is_sep    = cmds.radioButton(self.orm_rb2, q=True, sl=True)
        lod_names = [cmds.textFieldButtonGrp(f, q=True, text=True) for f in self.lod_fields]
        return {
            "yup":           cmds.checkBoxGrp(self.yup,          q=True, v1=True),
            "unit_scale":    cmds.floatFieldGrp(self.unit_scale,  q=True, v1=True),
            "export_uvs":    cmds.checkBoxGrp(self.export_uvs,   q=True, v1=True),
            "uv_set_count":  cmds.intSliderGrp(self.uv_set_count, q=True, v=True),
            "export_norms":  cmds.checkBoxGrp(self.export_norms, q=True, v1=True),
            "flip_norms":    cmds.checkBoxGrp(self.flip_norms,   q=True, v1=True),
            "export_vcs":    cmds.checkBoxGrp(self.export_vcs,   q=True, v1=True),
            "double_sided":  cmds.checkBoxGrp(self.double_sided, q=True, v1=True),
            "fix_nm":        cmds.checkBoxGrp(self.fix_nm,       q=True, v1=True),
            "apply_trs":     cmds.checkBoxGrp(self.apply_trs,    q=True, v1=True),
            "merge_verts":   cmds.checkBoxGrp(self.merge_verts,  q=True, v1=True),
            "merge_thresh":  cmds.floatFieldGrp(self.merge_thresh,q=True, v1=True),
            "export_morphs": cmds.checkBoxGrp(self.export_morphs,q=True, v1=True),
            "export_lod":    cmds.checkBoxGrp(self.export_lod,   q=True, v1=True),
            "lod_mode":      "name" if cmds.radioButtonGrp(self.lod_mode, q=True, sl=True)==1 else "manual",
            "lod_manual":    lod_names,
            "tag_collision": cmds.checkBoxGrp(self.tag_collision, q=True, v1=True),
            "export_skel":   cmds.checkBoxGrp(self.export_skel,  q=True, v1=True),
            "export_anim":   cmds.checkBoxGrp(self.export_anim,  q=True, v1=True),
            "anim_interp":   cmds.optionMenuGrp(self.anim_interp, q=True, v=True),
            "export_imgs":   cmds.checkBoxGrp(self.export_imgs,  q=True, v1=True),
            "tex_jpeg":      cmds.checkBoxGrp(self.tex_jpeg,     q=True, v1=True),
            "tex_res":       cmds.optionMenuGrp(self.tex_res,    q=True, v=True),
            "tex_srgb":      cmds.checkBoxGrp(self.tex_srgb,     q=True, v1=True),
            "export_thumb":  cmds.checkBoxGrp(self.export_thumb, q=True, v1=True),
            "export_mats":   cmds.checkBoxGrp(self.export_mats,  q=True, v1=True),
            "unlit":         cmds.checkBoxGrp(self.unlit,        q=True, v1=True),
            "export_emissive":cmds.checkBoxGrp(self.export_emissive, q=True, v1=True),
            "alpha_mode":    cmds.optionMenuGrp(self.alpha_mode, q=True, v=True),
            "alpha_cutoff":  cmds.floatFieldGrp(self.alpha_cutoff,q=True, v1=True),
            "orm_mode":      "keep_separate" if is_sep else "make_orm",
            "export_mode":   cmds.radioButtonGrp(self.mode,     q=True, sl=True),
            "export_path":   cmds.textFieldButtonGrp(self.path_field, q=True, text=True),
        }

    def _apply_settings(self, s):
        try:
            cmds.checkBoxGrp(self.yup,          e=True, v1=s.get('yup', False))
            cmds.floatFieldGrp(self.unit_scale,  e=True, v1=s.get('unit_scale', 1.0))
            cmds.checkBoxGrp(self.export_uvs,    e=True, v1=s.get('export_uvs', True))
            cmds.intSliderGrp(self.uv_set_count, e=True, v=s.get('uv_set_count', 1))
            cmds.checkBoxGrp(self.export_norms,  e=True, v1=s.get('export_norms', True))
            cmds.checkBoxGrp(self.flip_norms,    e=True, v1=s.get('flip_norms', False))
            cmds.checkBoxGrp(self.export_vcs,    e=True, v1=s.get('export_vcs', False))
            cmds.checkBoxGrp(self.double_sided,  e=True, v1=s.get('double_sided', True))  # FIX 4: was False
            cmds.checkBoxGrp(self.fix_nm,        e=True, v1=s.get('fix_nm', True))
            cmds.checkBoxGrp(self.apply_trs,     e=True, v1=s.get('apply_trs', False))
            cmds.checkBoxGrp(self.merge_verts,   e=True, v1=s.get('merge_verts', False))
            cmds.floatFieldGrp(self.merge_thresh,e=True, v1=s.get('merge_thresh', 0.001))
            cmds.checkBoxGrp(self.export_morphs, e=True, v1=s.get('export_morphs', False))
            cmds.checkBoxGrp(self.export_lod,    e=True, v1=s.get('export_lod', False))
            lod_mode_idx = 1 if s.get('lod_mode','name') == 'name' else 2
            cmds.radioButtonGrp(self.lod_mode,   e=True, sl=lod_mode_idx)
            manual_lods = s.get('lod_manual', ['','','',''])
            for i, f in enumerate(self.lod_fields):
                cmds.textFieldButtonGrp(f, e=True, text=manual_lods[i] if i < len(manual_lods) else '')
            cmds.checkBoxGrp(self.tag_collision, e=True, v1=s.get('tag_collision', True))
            has_skel = s.get('export_skel', False)
            cmds.checkBoxGrp(self.export_skel,   e=True, v1=has_skel)
            cmds.checkBoxGrp(self.export_anim,   e=True, en=has_skel,
                             v1=s.get('export_anim', False) if has_skel else False)
            interp_items = ["LINEAR","STEP","CUBICSPLINE"]
            interp_str   = s.get('anim_interp', 'LINEAR')
            interp_idx   = interp_items.index(interp_str)+1 if interp_str in interp_items else 1
            cmds.optionMenuGrp(self.anim_interp, e=True, sl=interp_idx)
            cmds.checkBoxGrp(self.export_imgs,   e=True, v1=s.get('export_imgs', True))
            cmds.checkBoxGrp(self.tex_jpeg,      e=True, v1=s.get('tex_jpeg', False))
            res_items = ["No limit","256","512","1024","2048","4096"]
            res_str   = s.get('tex_res', 'No limit')
            res_idx   = res_items.index(res_str)+1 if res_str in res_items else 1
            cmds.optionMenuGrp(self.tex_res,     e=True, sl=res_idx)
            cmds.checkBoxGrp(self.tex_srgb,      e=True, v1=s.get('tex_srgb', True))
            cmds.checkBoxGrp(self.export_thumb,  e=True, v1=s.get('export_thumb', False))
            cmds.checkBoxGrp(self.export_mats,   e=True, v1=s.get('export_mats', True))
            cmds.checkBoxGrp(self.unlit,         e=True, v1=s.get('unlit', False))
            cmds.checkBoxGrp(self.export_emissive,e=True,v1=s.get('export_emissive', False))
            alpha_items = ["OPAQUE","MASK","BLEND"]
            alpha_str   = s.get('alpha_mode','OPAQUE')
            alpha_idx   = alpha_items.index(alpha_str)+1 if alpha_str in alpha_items else 1
            cmds.optionMenuGrp(self.alpha_mode,  e=True, sl=alpha_idx)
            cmds.floatFieldGrp(self.alpha_cutoff,e=True, v1=s.get('alpha_cutoff', 0.5))
            is_sep = s.get('orm_mode','make_orm') == 'keep_separate'
            # In a Maya radioCollection only setting sl=True on the target button works;
            # sl=False on the other is a no-op so we just select the correct one directly.
            if is_sep:
                cmds.radioButton(self.orm_rb2, e=True, sl=True)
            else:
                cmds.radioButton(self.orm_rb1, e=True, sl=True)
            cmds.frameLayout(self.orm_sep_info, e=True, vis=is_sep)
            mode_val = s.get('export_mode', 1)
            cmds.radioButtonGrp(self.mode, e=True, sl=mode_val)
            if s.get('export_path',''):
                cmds.textFieldButtonGrp(self.path_field, e=True, text=s['export_path'])
        except Exception as e:
            import traceback
            print(f"[GLB] _apply_settings error: {e}"); traceback.print_exc()

    def run_export(self, *args):
        global Image, PILLOW_OK
        if not PILLOW_OK:
            try:
                PILLOW_OK = ensure_libraries(LIB_PATH)
                if PILLOW_OK:
                    from PIL import Image as _Image
                    Image = _Image
            except Exception as e:
                    print(f"[GLB] Pillow check failed: {e}")

        sel = cmds.ls(sl=True, type='transform')
        meshes = [o for o in sel if cmds.listRelatives(o, shapes=True, type='mesh')]
        if not meshes:
            _show_error_popup("No Mesh Selected",
                              "Please select one or more mesh transforms and try again."); return

        s         = self._collect_settings()
        is_batch  = s['export_mode'] == 2
        base_path = s['export_path'].strip()

        if not base_path:
            _show_error_popup("No Export Path", "Set an output path before exporting."); return

        tex_res_str = s.get('tex_res', 'No limit')
        max_tex     = int(tex_res_str) if tex_res_str.isdigit() else None

        opts = {
            'orm_mode':       s['orm_mode'],
            'yup':            s['yup'],
            'unit_scale':     s['unit_scale'],
            'export_uvs':     s['export_uvs'],
            'uv_set_count':   s['uv_set_count'],
            'export_norms':   s['export_norms'],
            'flip_norms':     s['flip_norms'],
            'export_vcs':     s['export_vcs'],
            'double_sided':   s['double_sided'],
            'apply_trs':      s['apply_trs'],
            'merge_verts':    s['merge_verts'],
            'merge_thresh':   s['merge_thresh'],
            'export_mats':    s['export_mats'],
            'export_imgs':    s['export_imgs'] and PILLOW_OK,
            'tex_jpeg':       s['tex_jpeg'],
            'max_tex_size':   max_tex,
            'unlit':          s['unlit'],
            'alpha_mode':     s['alpha_mode'],
            'alpha_cutoff':   s['alpha_cutoff'],
            'anim_interp':    s['anim_interp'],
            'export_skeleton':s['export_skel'],
            'export_anim':    s['export_anim'],
            'export_morphs':  s['export_morphs'],
            'export_lod':     s['export_lod'],
            'lod_mode':       s['lod_mode'],
            'lod_manual':     s.get('lod_manual', []),
            'export_emissive':s['export_emissive'],
            'tex_srgb':       s['tex_srgb'],
            'export_thumb':   s['export_thumb'] and PILLOW_OK,
            'tag_collision':  s['tag_collision'],
        }

        # Non-manifold check
        if s.get('fix_nm', True):
            for m in meshes:
                nm_e, nm_v = check_non_manifold(m)
                if nm_e or nm_v:
                    fix_non_manifold(m)

        save_settings(s)

        try:
            if is_batch:
                out_dir = base_path
                os.makedirs(out_dir, exist_ok=True)
                exported = []
                for mesh in meshes:
                    name      = _make_file_friendly(mesh)
                    out_path  = os.path.join(out_dir, f"{name}.glb")
                    opts['export_path'] = out_path
                    glb_data  = build_glb([mesh], opts)
                    with open(out_path, 'wb') as f: f.write(glb_data)
                    exported.append(out_path)
                    print(f"[GLB] Exported: {out_path}")
                self._set_status(f"Batch done — {len(exported)} file(s) → {out_dir}", (0.10, 0.38, 0.18))
                _show_success_popup(f"{len(exported)} meshes", out_dir)
            else:
                out_path = base_path
                if not out_path.lower().endswith('.glb'): out_path += '.glb'
                os.makedirs(os.path.dirname(out_path) or '.', exist_ok=True)
                opts['export_path'] = out_path
                glb_data = build_glb(meshes, opts)
                with open(out_path, 'wb') as f: f.write(glb_data)
                name = _make_file_friendly(meshes[0]) if len(meshes)==1 else f"{len(meshes)} meshes"
                self._set_status(f"Exported → {os.path.basename(out_path)}", (0.10, 0.38, 0.18))
                _show_success_popup(name, out_path)
                print(f"[GLB] Exported: {out_path}")
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            print(f"[GLB] Export FAILED:\n{tb}")
            _show_error_popup("Export Failed", str(e))
            self._set_status(f"Export failed: {e}", (0.5, 0.1, 0.1))


# ---------------------------------------------------------------------------
# Boot
# ---------------------------------------------------------------------------
def _boot():
    global Image, PILLOW_OK
    try:
        ok = ensure_libraries(LIB_PATH)
        if ok:
            from PIL import Image as _Image
            Image = _Image
            PILLOW_OK = True
    except Exception as e:
        print(f"[GLB] Pillow boot check: {e}")
    _install_shelf_button()
    UE_Blender_Final_Exporter()

if SETTINGS_FILE:   # only boot if drive was already found synchronously
    _boot()
