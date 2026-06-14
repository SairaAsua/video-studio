/t_in_s = f"{t_in:.3f}"\n        t_out_s = f"{t_out:.3f}"/{
n
s/.*/&\n\n        # Blend mode support (T4): read from asset dict, default "normal"/
N
s/\n        if i == 0:/\n        blend_mode = (item["asset"].get("blend_mode") or "normal")\n        if blend_mode != "normal" and blend_mode not in SUPPORTED_BLEND_MODES:\n            print(f"WARNING: blend_mode '"{blend_mode}"' not supported by ffmpeg -- using normal overlay", file=sys.stderr)\n        use_blend = blend_mode != "normal" and blend_mode in SUPPORTED_BLEND_MODES\n\n        if i == 0:/
}
