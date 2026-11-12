# Rendering above 4x (experimental)

paraLLEl-GS renders at most 16 samples per PS2 pixel (4x4, the "4x" internal
resolution, 2560x1792): its samples are mapped to GPU subgroup lanes, so a
larger grid would need a rework of its shaders (NVIDIA's subgroups are 32
wide). A 4K display (2160 lines, 4.8x the PS2's 448) and high-resolution
headsets (the Bigscreen Beyond's 3200 lines, 7.1x) are above that.

## Jittered instances (`GA2_PGS_JITTER=1`)

With `GA2_PGS_SSAA=16` on the desktop, three more GS instances get the same
packets as the first, each with its geometry moved by half an internal pixel
(1/8 PS2 pixel) right, down, or both (paraLLEl-GS `Hacks::jitter_x/y`, patch
`parallel-gs/0009`). A primitive that samples a buffer the GS rendered (the
games compose 2D scenes off screen and copy them to the display; 3D frames
are copied to a 16-bit scan-out buffer) gets its texel coordinates moved by
the same amount, so the copy does not move the picture twice. The four 4x
frames are interleaved on the presentation's GPU into one 8x picture
(5120x3584): output pixel (2x + i, 2y + j) is instance i + 2j's pixel (x, y).
The rest of the presentation (text and prompts at the window's resolution,
debanding, area-downscaling to the window) works on that picture as on any
other.

Instances take each GIF packet in turn on the calling thread (a texture an
instance first samples is decoded for texture replacement from the CPU's copy
of GS memory, which only matches the packet at that moment); their vsync
(flush, scan-out, readback) runs on a worker thread each, side by side.

Measured on an RTX 4090 in the hidden-display test harness (whose OpenGL is
software rendered, so these are upper bounds for the GS bridge only):

| Scene | 4x | 8x (jittered) |
| --- | --- | --- |
| Zettai ship map (3D) | 54 frames/s, GS vsync 0.9 ms | 33 frames/s, GS vsync 8.9 ms, packets 4.9 ms |
| 2D story and menus | 54 frames/s | 50-53 frames/s, GS vsync ~10 ms |

At a 4K window the difference to 4x (scaled the last 1.2x, area-filtered
where smaller) is small: slightly cleaner 3D edges. It is therefore off by
default and not in the options menu. 2D art is upscaled per layer at the 4x
textures either way (ga2_layers), and the text and button prompts are drawn
at the window's resolution regardless.

Not done: jitter with VR stereo (it would need eight instances), and a GS
that renders more than 16 samples itself.
