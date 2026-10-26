# NotchLC interpolated-alpha sample

`notchlc_alpha_interp.mov` is a synthetic 64×32, 2-frame NotchLC file (1,889 bytes) made by
`make_sample.py` from the NotchLC frame layout alone. The picture is flat grey; the alpha mixes
transparent, opaque and interpolated 4×4 blocks so that each fault in FFmpeg's NotchLC alpha
decoding is visible: every interpolated block has its own endpoints, selectors vary per pixel,
and full and reversed 0–255 ramps are included. It contains no third-party picture content.

`make_sample.py` also writes the exact 12-bit alpha a correct decoder must produce.

```bash
python make_sample.py notchlc_alpha_interp.mov expected_alpha.npy   # needs numpy, lz4, av
```

It accompanies the FFmpeg patch "avcodec/notchlc: fix decoding of interpolated alpha blocks"
and its FATE test (`fate-notchlc-alpha`). MD5 `28a85c7887f6d759f2e0509441faf159`.
