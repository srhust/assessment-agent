# -*- coding: utf-8 -*-
"""R14-A3：把本机 R13G 运行件脱敏归档进仓库（人工裁决点“runs 尚未全部归档”）。

- 逐题复制 json + 图像，绝对路径一律改写为 $ROOT 相对形式；
- 每个文件记录 sha256 与（png）内容哈希、墨迹统计、裁剪坐标；
- 原始 HTTP 请求/响应体管线未持久化 → 显式 artifact_unavailable；
- 归档前后做凭据扫描（api_key/sk-/Bearer），命中即中止。
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "evidence" / "R13G_RUN_ARCHIVE"
# 凭据扫描：**值级**判定。R13-G 首版误报教训：字段名 api_key_present 与网关域名
# 中的 "apigateway" 都会命中宽松词形。这里只做两件事：
#   1) 与本轮真实密钥（r61_dsh_env.load_creds）做字节级精确匹配；
#   2) 匹配 `api_key`/`Bearer` **后跟值**的模式，字段名本身不算命中。
SECRET = re.compile(r'("api_key"\s*:\s*"(?!\s*")([^"]{8,})")|(Bearer\s+[A-Za-z0-9._\-]{12,})|(\bsk-[A-Za-z0-9]{16,})')


def _live_key() -> str:
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import r61_dsh_env as _env  # noqa: PLC0415
        return str(_env.load_creds()[0] or "")
    except Exception:  # noqa: BLE001 - 取不到密钥就退回模式扫描，不阻断
        return ""


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()[:16]


def png_stats(p: Path):
    import cv2
    img = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return None
    ink = int((img < 128).sum())
    return {"shape": list(img.shape), "ink_pixels": ink,
            "ink_bbox": (np.argwhere(img < 128).min(axis=0).tolist()
                         if ink else None)}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    live_key = _live_key()
    index = {"stage": "R14-A3 sanitized archive of local R13G runs",
             "artifact_unavailable": [
                 "原始 HTTP 请求体/响应体（管线 save=True 不持久化 wire-level 载荷）",
                 "求解中间重建画布 stem_reconstructed.png 与 regions_pixel/*"
                 "（本次真实批在 layout=off 档执行，中间件未生成；离线重放可再生产生）"],
             "items": {}}
    for rd in sorted((ROOT / "runs").glob("r13g-*")):
        pid = rd.name.replace("r13g-", "")
        dst = OUT / pid
        if dst.exists():
            shutil.rmtree(dst)
        dst.mkdir(parents=True)
        files = {}
        for f in sorted(rd.rglob("*")):
            if not f.is_file():
                continue
            rel = str(f.relative_to(rd)).replace("\\", "/")
            data = f.read_bytes()
            text = data.decode("utf-8", "ignore") if f.suffix in (
                ".json", ".md", ".jsonl") else ""
            if live_key and live_key in text:
                raise SystemExit(f"真实密钥字节命中，中止: {rel}")
            if SECRET.search(text):
                raise SystemExit(f"凭据值模式命中，中止: {rel}")
            san = data
            if f.suffix in (".json", ".md", ".jsonl"):
                s = text.replace(str(ROOT), "$ROOT").replace(
                    str(ROOT).replace("\\", "/"), "$ROOT")
                # JSON 字符串里反斜杠是双写的（E:\\assessment-agent），一并归一
                s = s.replace(str(ROOT).replace("\\", "\\\\"), "$ROOT")
                san = s.encode("utf-8")
            (dst / rel).parent.mkdir(parents=True, exist_ok=True)
            (dst / rel).write_bytes(san)
            ent = {"sha256_16": sha(san), "bytes": len(san)}
            if f.suffix == ".png":
                st = png_stats(dst / rel)
                ent["png"] = st
                ent["png_content_sha16"] = sha(
                    np.ascontiguousarray(
                        __import__("cv2").imread(str(dst / rel), 0) < 128
                    ).tobytes())
            files[rel] = ent
        # 裁剪坐标与页面对应
        ex = json.loads((dst / "extraction.json").read_text(encoding="utf-8"))
        coords = {"stem_matrix_bbox": ex.get("stem_matrix_bbox"),
                  "stem_matrix_grid": ex.get("stem_matrix_grid"),
                  "option_regions": [
                      {"label": r.get("label"), "bbox_xywh": r.get("bbox"),
                       "has_figure": r.get("has_figure"),
                       "crop": f"regions/option_{r.get('label')}.png"}
                      for r in (ex.get("option_regions") or [])]}
        (dst / "crop_coordinates.json").write_text(
            json.dumps(coords, ensure_ascii=False, indent=1), encoding="utf-8")
        index["items"][pid] = {
            "files": files,
            "crop_coordinates": "crop_coordinates.json",
            "original_page_sha16": files.get("image/original.png", {}).get(
                "sha256_16"),
        }
    (OUT / "INDEX.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: len(v) if isinstance(v, dict) else v
                      for k, v in index["items"].items()} |
                     {"stage": index["stage"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
