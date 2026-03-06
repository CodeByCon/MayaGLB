# Ultimate GLB Exporter for Maya — v1.0

A free, open-source GLB 2.0 exporter for Autodesk Maya, written entirely in Python.  
No paid plugins. No admin rights. No external installs required beyond Pillow, which the tool installs automatically on first run.

---

## Why does this exist?

Exporting GLB from Maya normally requires either a paid plugin or the **Game Exporter** — which isn't available on locked-down institutional machines (college labs, studio workstations with restricted installs, etc.).

This script runs entirely from Maya's built-in Script Editor. Drop in one `.py` file and you're done.

---

## Features

- **GLB 2.0** — hand-written binary packer, no external GLB library needed
- **Multi-mesh export** — select multiple objects, export as one merged GLB or batch into separate files
- **Multi-material** — one glTF primitive per face-group, full per-material texture support
- **PBR materials** — reads Maya shader networks (Arnold, Standard Surface, Lambert) and writes correct glTF `pbrMetallicRoughness` materials
- **ORM textures** — pack Occlusion + Roughness + Metallic into a single PNG, or keep them separate with an auto-generated Blender node-wiring script
- **Full skeleton export** — joint hierarchy, inverse bind matrices, skin weights
- **Keyframe animation** — bakes TRS animation over the playback range (LINEAR / STEP / CUBICSPLINE interpolation)
- **Scale presets** — Maya (1.0), Blender (0.01), Unreal Engine (0.01) with a live bounding-box scale checker
- **Non-manifold detection** — warns before export and optionally auto-fixes
- **Vertex colours** — exports `COLOR_0` from the active colour set
- **Persistent settings** — all UI settings saved to `DRIVE:/MayaGLB/Settings/exporter_settings.json` and restored on next open
- **Auto drive detection** — scans all drives A–Z for an existing `MayaGLB` folder; only shows a drive picker if none is found
- **Tooltips** — hover over any setting for a plain-English description of what it does

---

## Requirements

| Requirement | Version |
|---|---|
| Autodesk Maya | 2024 or later (tested on 2026) |
| Python | 3.x (bundled with Maya) |
| Pillow | Auto-installed on first run |

No admin rights needed. Pillow installs to a local folder on your chosen drive (`DRIVE:/MayaGLB/PythonPlugins`) using `mayapy pip`.

---

## Installation

1. Download `glb_exporter_v1.py`
2. Open Maya
3. Open the **Script Editor** (`Windows → General Editors → Script Editor`)
4. Set the tab type to **Python**
5. Drag and drop the `.py` file into the Script Editor, or paste the contents
6. Press **Ctrl+Enter** (or the Run All button) to execute

On first run the tool will:
- Scan your drives for an existing `MayaGLB` folder — if found, it uses that drive automatically
- If nothing is found, ask you which drive to use and create the folder structure there
- Install Pillow to `DRIVE:/MayaGLB/PythonPlugins` automatically

> **Tip:** Save the script as a shelf button so you can launch it with one click.  
> In the Script Editor, highlight all the code → middle-mouse drag to the shelf.

---

## Folder structure

Once set up, the tool creates and uses this layout on your chosen drive:

```
DRIVE:/
└── MayaGLB/
    ├── Exports/          ← default export destination
    ├── PythonPlugins/    ← Pillow installs here
    └── Settings/
        └── exporter_settings.json
```

---

## Usage

1. Select one or more mesh objects in the Maya viewport
2. Run the script (or click your shelf button) to open the exporter window
3. Adjust settings as needed — hover over any option for a tooltip
4. Set the output path or use the Browse button
5. Click **EXPORT GLB**

### Export modes

| Mode | Behaviour |
|---|---|
| Single file | All selected meshes merged into one GLB |
| Batch | Each selected mesh exported as its own separate GLB file |

---

## Settings overview

### Transform
| Setting | Description |
|---|---|
| +Y Up | Converts Maya's Z-up to Y-up (required for Blender and Unreal Engine) |
| Scale multiplier | Multiply vertex positions — use 0.01 to convert cm to metres |

### Mesh
| Setting | Description |
|---|---|
| Export UVs | Include `TEXCOORD_0` UV coordinates |
| Export normals | Include per-vertex normals |
| Flip normals | Invert normals — use if mesh appears inside-out |
| Export vertex colours | Export active colour set as `COLOR_0` |
| Double sided | Render both faces of every polygon |
| Check non-manifold geo | Warn and optionally fix non-manifold geometry before export |
| Apply transform (freeze) | Bake world transform into vertex positions |
| Merge vertices | Weld coincident vertices before export |

### Animation
| Setting | Description |
|---|---|
| Export skeleton | Include joint hierarchy and skin weights |
| Export animation | Bake TRS keyframes over the playback range |
| Interpolation | LINEAR / STEP / CUBICSPLINE between keyframes |

### Texture
| Setting | Description |
|---|---|
| Embed textures | Pack images directly into the GLB binary |
| Convert to JPEG | Re-encode textures as JPEG (smaller, loses alpha) |
| Max texture size | Downscale textures above this resolution |
| Force sRGB | Tag base colour textures as sRGB in the glTF JSON |

### Material
| Setting | Description |
|---|---|
| Export materials | Write glTF PBR materials from Maya shader networks |
| Unlit (shadeless) | Apply `KHR_materials_unlit` — no lighting or shadows |
| Alpha mode | OPAQUE / MASK (cutout) / BLEND (transparent) |
| ORM — Make ORM | Pack O+R+M into one PNG (standard glTF format) |
| ORM — Keep separate | Embed each channel separately + write a Blender node script |

---

## Blender node script (Keep Separate ORM)

When **Keep Separate** ORM mode is selected, a Python sidecar file is written next to the exported GLB:

```
MyAsset_blender_nodes.py
```

Run this in Blender's **Script Editor** after importing the GLB to automatically wire the separate ORM textures onto the material node tree.

---

## Tested with

- Maya 2026, Windows 11
- Blender 4.x (GLB import)
- Unreal Engine 5.x (GLB import)

---

## Credits

| Person | Role |
|---|---|
| **Connor Henry** | Main Developer |
| **Claude / Anthropic** | Debugging & Code Assistance |
| **Jack Clewer** | Being a Good Teacher |
| **Maya** | Being annoying by not having GLB export |

---

## License

MIT License — free to use, modify, and distribute.  
Credit appreciated but not required. Please don't sell it as your own paid plugin — that's literally why this exists.

```
Copyright (c) 2025 Connor Henry

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
```
