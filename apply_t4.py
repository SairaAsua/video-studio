#!/usr/bin/env python3
import sys

content = open("/home/nicolas/Projects/video-studio/lib/assemble.py").read()

start = content.find('t_in_s = f"{t_in:.3f}"')
if start < 0:
    print("FAIL: pattern start not found")
    sys.exit(1)

rest = content[start:]
end_marker = 'overlay_filters.append(ov)\n        current = out_label'
end = rest.find(end_marker)
if end < 0:
    print("FAIL: end marker not found")
    sys.exit(1)

blend_code = """
        # Blend mode support (T4): read from asset dict, default "normal"
        blend_mode = (item["asset"].get("blend_mode") or "normal")
        if blend_mode != "normal" and blend_mode not in SUPPORTED_BLEND_MODES:
            print(f"WARNING: blend_mode '{blend_mode}' not supported by ffmpeg -- using normal overlay", file=sys.stderr)
        use_blend = blend_mode != "normal" and blend_mode in SUPPORTED_BLEND_MODES

        if i == 0:
            # base (lowest layer after sort) onto black; no enable (persists)
            if use_blend:
                ov = f"{current}{l_label}blend=all_mode={blend_mode}{out_label}"
            else:
                ov = f"{current}{l_label}overlay=0:0{out_label}"
        else:
            # higher (later in sorted) with time window enable
            if use_blend:
                ov = f"{current}{l_label}blend=all_mode={blend_mode}:enable='between(t\\,{t_in_s}\\,{t_out_s})'{out_label}"
            else:
                ov = f"{current}{l_label}overlay=0:0:enable='between(t\\,{t_in_s}\\,{t_out_s})'{out_label}"
"""

new_content = content[:start] + blend_code.lstrip() + content[start+end:]
with open("/home/nicolas/Projects/video-studio/lib/assemble.py", "w") as f:
    f.write(new_content)
print("OK")
print("SUPPORTED_BLEND_MODES:", "SUPPORTED_BLEND_MODES" in new_content)
print("blend=all_mode:", "blend=all_mode" in new_content)
print("Lines:", new_content.count("\n"))
