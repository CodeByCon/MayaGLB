# Ultimate GLB Exporter for Maya — v1.0

Free, open-source GLB exporter for Maya. No paid plugins, no admin rights needed — runs entirely from the Script Editor.

Made because Maya doesn't have a built-in GLB exporter, paid plugins exist but can't be installed on college/locked machines, and that's annoying.

---

## Install

1. Open Maya and go to the bottom middle of your screen where it says "MEL" and a text input box.
2. Copy the code below and paste it in.
3. Press enter.

```mel
python("import urllib.request,types; exec(compile(urllib.request.urlopen('https://raw.githubusercontent.com/CodeByCon/ultimate-glb-exporter/main/glb_exporter_v1.py').read(), '<glb>', 'exec'), types.ModuleType('glb').__dict__)");
```

Runs the latest version straight from GitHub every time — no files to manage. Save it as a shelf button for one-click access.

Pillow (the only dependency) is installed automatically on first run to `DRIVE:/MayaGLB/PythonPlugins`. No admin rights required.

---

<details>
<summary><b>Features</b></summary>

- GLB 2.0 — hand-written binary packer, no external library needed
- Multi-mesh — merge all into one GLB or batch export one file per object
- Multi-material — one glTF primitive per face-group
- PBR materials — reads Arnold / Standard Surface / Lambert shader networks
- ORM textures — pack O+R+M into one PNG, or keep separate with an auto-generated Blender node-wiring script
- Skeleton export — joint hierarchy, inverse bind matrices, skin weights
- Animation — bakes TRS keyframes over the playback range
- Scale presets — Maya / Blender / UE with a live bounding-box checker
- Non-manifold detection — warns and optionally auto-fixes before export
- Vertex colours — exports `COLOR_0` from the active colour set
- Persistent settings — saved to `DRIVE:/MayaGLB/Settings/exporter_settings.json`
- Auto drive detection — scans A–Z for an existing `MayaGLB` folder
- Tooltips — hover over any setting for a description

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

MIT — free to use, modify, and distribute. Please don't sell it as a paid plugin. That's literally why this exists.
