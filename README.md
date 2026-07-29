# Ultimate GLB Exporter for Maya — v2.0

Free, open-source GLB exporter for Maya. No paid plugins, no admin rights needed — runs entirely from the Script Editor.

Made because Maya doesn't have a built-in GLB exporter, paid plugins exist but can't be installed on college/locked machines, and that's annoying.

---

## Install

1. Open Maya 2026 and go to the bottom middle of your screen where it says "MEL" and a text input box on the right.
2. Copy the code below and paste it in.
3. Press enter.

```mel
python("import urllib.request as r; exec(compile(r.urlopen('https://raw.githubusercontent.com/CodeByCon/MayaGLB/main/glb_exporter.py').read(),'<glb>','exec'))");
```

Runs the latest version straight from GitHub every time — no files to manage. Save it as a shelf button for one-click access.

Pillow (the only dependency) is installed automatically on first run to `DRIVE:/MayaGLB/PythonPlugins`. No admin rights required.

## Video Tutorial:
[![MayaGLB Tutorial (V1)](https://img.youtube.com/vi/VMBnNUz1HIQ/maxresdefault.jpg)](https://www.youtube.com/watch?v=VMBnNUz1HIQ)


> [!NOTE]
> If you have any feedback, please open an issue or discussion.

---

<details>
<summary><b>Features</b></summary>

- GLB 2.0 - hand-written binary packer, no external library needed
- Multi-mesh - merge all into one GLB or batch export one file per object
- Multi-material - one glTF primitive per face-group
- PBR materials - reads Arnold / Standard Surface / Lambert shader networks
- ORM textures - pack O+R+M into one PNG, or keep AO separate as its own occlusionTexture
- Skeleton export - joint hierarchy, inverse bind matrices, skin weights
- Animation - bakes TRS keyframes over the playback range
- Non-manifold detection - warns and optionally auto-fixes before export
- Vertex colours - exports COLOR_0 from the active colour set
- Persistent settings - saved to DRIVE:/MayaGLB/Settings/exporter_settings.json
- Auto drive detection - scans A-Z for an existing MayaGLB folder
- Tooltips - hover over any setting for a description
- LOD export - auto-detect siblings by name suffix (_LOD1, _LOD2 etc.) or assign manually
- Morph targets - exports Maya blendShape nodes as glTF morph targets
- Multi-UV - up to 4 UV sets exported as TEXCOORD_0 through TEXCOORD_3
- Emissive - reads emission/incandescence and exports emissiveTexture + emissiveFactor
- Collision mesh tagging - detects UCX_ / UBX_ / USP_ / UCP_ prefixes and tags them in glTF extras
- Export presets - save, load and delete named configurations
- Thumbnail - captures the active viewport and embeds a 256px PNG in the GLB metadata
- Auto shelf button - installs a dedicated GLB_Exporter shelf on first run, persists across restarts
- Scale check - reports bounding box dimensions at the current scale multiplier with a sanity warning
- Y-up - optional Z-to-Y axis swap for engines that use Y-up
- Scale multiplier - manual float for any unit conversion

</details>

---

## Credits

| | |
|---|---|
| **Connor Henry** | Main Developer |
| **Claude / Anthropic** | Debugging & Code Assistance |
| **Jack Clewer** | Being a Good Teacher |
| **Maya** | Being annoying by not having GLB export |

---

## License
This project, **MayaGLB**, is licensed under the **Apache License 2.0**.

You are free to use, modify, and distribute this software, but you **must**:
- Credit **Connor Henry** as the original author
- Include a link to this license
- State if changes were made

For full terms, see the [LICENSE](https://github.com/CodeByCon/MayaGLB/blob/main/LICENSE) file.

### Academic or Media Use
If you use MayaGLB in research, a publication, or media, please cite:
> Henry, C. (2026). *MayaGLB: A GLB Exporter for Autodesk Maya*.

> [!NOTE]
> This repository was re-uploaded, this means all previous issues and stars were removed. Please resubmit. Sorry for any inconveniences (29/07/26)
