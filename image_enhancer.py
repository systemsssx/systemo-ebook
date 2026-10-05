#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
封面超分 —— 本地 Real-ESRGAN (anime) ONNX 推理

取代原来的「腾讯云 COS + 数据万象 AISuperResolution」方案：
  * 完全本地：不需要网络、不需要密钥、不产生费用
  * 模型：models/realesrgan_anime_x4.onnx（约 4.9 MB，GAN / 感知派）
  * 依赖：onnxruntime（CPU，无需显卡）

为什么换掉 ESPCN/FSRCNN 那类方案：
    那些是 PSNR 派模型（训练目标是像素误差最小），输出天生偏软；
    Real-ESRGAN 是 GAN 派，能补出合理的高频结构，观感才接近云服务。

对外接口与旧版完全一致（main.js 不需要改调用方式）：
    stdin : {"image_path": "...", "magnify": 4}
    stdout: {"success": bool, "output_path": str, "message": str,
             "size_before", "size_after",
             "width_before", "height_before", "width_after", "height_after"}

注意：本脚本**不再覆盖原图**。超分结果写成同目录的 <原名>_sr.jpg，
      并通过 output_path 返回，避免破坏用户的原封面文件。
"""

import io
import json
import os
import sys

# ================================================================
# 配置
# ================================================================
MODEL_FILENAME = 'realesr_general_x4v3.onnx'

SUPPORTED_FORMATS = ('.jpg', '.jpeg', '.png', '.bmp', '.webp')

# 模型固定 4 倍；这些阈值决定「值不值得超分」
SCALE_BY_SHORT_SIDE = (
    (400, 4),      # 短边 < 400  → 4x
    (700, 2),      # 短边 < 700  → 4x 后降采样到 2x
)
MAX_OUTPUT_SHORT_SIDE = 1600     # 输出短边上限，避免封面把 EPUB 撑大
MAX_INPUT_PIXELS = 1_200_000     # 推理输入上限，防止大图把内存顶爆
JPEG_QUALITY = 90
MODEL_SCALE = 4                  # 模型自身的放大倍数
# ================================================================

if sys.platform == 'win32':
    try:
        sys.stdin = io.TextIOWrapper(sys.stdin.buffer, encoding='utf-8')
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')
    except Exception:
        pass


def log(msg):
    print(msg, file=sys.stderr)


# ================================================================
# 定位模型文件（开发 / 打包后都能找到）
# ================================================================
def find_model():
    candidates = []

    env_path = os.environ.get('EASYPUB_SR_MODEL')
    if env_path:
        candidates.append(env_path)

    here = os.path.dirname(os.path.abspath(__file__))
    roots = [here, os.path.dirname(here)]

    if getattr(sys, 'frozen', False):
        # <resources>/backend/easypub-backend.exe → <resources>/
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        roots += [exe_dir, os.path.dirname(exe_dir)]

    meipass = getattr(sys, '_MEIPASS', None)
    if meipass:
        roots += [meipass, os.path.dirname(meipass)]

    for root in roots:
        candidates.append(os.path.join(root, 'models', MODEL_FILENAME))
        candidates.append(os.path.join(root, MODEL_FILENAME))

    seen = set()
    for c in candidates:
        c = os.path.normpath(c)
        if c in seen:
            continue
        seen.add(c)
        if os.path.isfile(c):
            return c
    return None


# ================================================================
# 读图：修正 EXIF 方向 / 处理透明通道 / 保住 ICC
# ================================================================
def load_image_rgb(path):
    """返回 (PIL.Image RGB, icc_profile 或 None)。"""
    from PIL import Image, ImageOps

    img = Image.open(path)
    icc = img.info.get('icc_profile')

    # 网络封面常带 Orientation，不修正放大后会躺倒
    img = ImageOps.exif_transpose(img)

    has_alpha = img.mode in ('RGBA', 'LA') or (
        img.mode == 'P' and 'transparency' in img.info)

    if has_alpha:
        # 白底合成，避免 alpha 变黑底
        img = img.convert('RGBA')
        bg = Image.new('RGB', img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        img = bg
    else:
        img = img.convert('RGB')

    return img, icc


# ================================================================
# 决定放大目标
# ================================================================
def decide_target_scale(width, height, magnify_hint=None):
    """
    返回期望的最终放大倍数（1 表示不需要放大）。
    magnify_hint 只作为「上限」，不会突破按尺寸算出来的建议值。
    """
    short = min(width, height)

    target = 1
    for threshold, scale in SCALE_BY_SHORT_SIDE:
        if short < threshold:
            target = scale
            break

    if target > 1:
        # 输出短边封顶
        while target > 1 and short * target > MAX_OUTPUT_SHORT_SIDE:
            target -= 1
        if short * target > MAX_OUTPUT_SHORT_SIDE:
            target = 1

    if magnify_hint:
        try:
            hint = int(magnify_hint)
            if 2 <= hint < target:
                target = hint
        except (TypeError, ValueError):
            pass

    return target


# ================================================================
# 推理
# ================================================================
_SESSION = None


def get_session(model_path):
    global _SESSION
    if _SESSION is not None:
        return _SESSION

    import onnxruntime as ort

    so = ort.SessionOptions()
    cpu = os.cpu_count() or 4
    so.intra_op_num_threads = max(1, cpu - 1)   # 留一个核给界面
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

    _SESSION = ort.InferenceSession(
        model_path, sess_options=so, providers=['CPUExecutionProvider'])
    return _SESSION


def upsample(pil_img, model_path):
    """用模型做一次 4x，返回 PIL.Image（RGB）。"""
    import numpy as np

    sess = get_session(model_path)
    in_name = sess.get_inputs()[0].name
    out_name = sess.get_outputs()[0].name

    arr = np.asarray(pil_img, dtype=np.float32) / 255.0
    blob = np.ascontiguousarray(np.transpose(arr, (2, 0, 1))[None, ...])

    result = sess.run([out_name], {in_name: blob})[0]
    result = np.clip(result[0], 0.0, 1.0)
    rgb = (np.transpose(result, (1, 2, 0)) * 255.0 + 0.5).astype(np.uint8)

    from PIL import Image
    return Image.fromarray(rgb, 'RGB')


# ================================================================
# 主流程
# ================================================================
def enhance_image(image_path, magnify=4):
    from PIL import Image

    result = {
        'success': False,
        'output_path': image_path,
        'message': '',
        'size_before': 0,
        'size_after': 0,
        'width_before': 0,
        'height_before': 0,
        'width_after': 0,
        'height_after': 0,
    }

    if not image_path or not os.path.exists(image_path):
        result['message'] = '图片不存在'
        return result

    ext = os.path.splitext(image_path)[1].lower()
    if ext not in SUPPORTED_FORMATS:
        result['message'] = '不支持的格式: %s' % ext
        return result

    result['size_before'] = os.path.getsize(image_path)

    try:
        img, icc = load_image_rgb(image_path)
    except Exception as exc:                       # noqa: BLE001
        result['message'] = '读取图片失败: %s' % exc
        return result

    w0, h0 = img.size
    result['width_before'] = w0
    result['height_before'] = h0
    result['width_after'] = w0
    result['height_after'] = h0

    target = decide_target_scale(w0, h0, magnify)
    log('📷 封面超分: %s  %dx%d  目标 %dx'
        % (os.path.basename(image_path), w0, h0, target))

    if target <= 1:
        result['success'] = True
        result['message'] = '尺寸已足够（%dx%d），无需超分' % (w0, h0)
        return result

    model_path = find_model()
    if not model_path:
        result['message'] = '找不到超分模型 %s' % MODEL_FILENAME
        log('❌ %s' % result['message'])
        return result
    log('🧠 模型: %s' % model_path)

    try:
        work = img
        # 输入过大先缩一下，避免内存爆掉
        if w0 * h0 > MAX_INPUT_PIXELS:
            ratio = (MAX_INPUT_PIXELS / float(w0 * h0)) ** 0.5
            work = img.resize((max(1, int(w0 * ratio)), max(1, int(h0 * ratio))),
                              Image.Resampling.LANCZOS)
            log('⚠️ 输入过大，先缩到 %dx%d 再超分' % work.size)

        import time
        t0 = time.perf_counter()
        out = upsample(work, model_path)
        log('⏱️ 推理耗时 %.3f s' % (time.perf_counter() - t0))

        # 模型固定 4x；目标小于 4x 时降采样回去
        if target < MODEL_SCALE:
            out = out.resize((w0 * target, h0 * target), Image.Resampling.LANCZOS)

        # 输出短边封顶
        if min(out.size) > MAX_OUTPUT_SHORT_SIDE:
            ratio = MAX_OUTPUT_SHORT_SIDE / float(min(out.size))
            out = out.resize((int(out.width * ratio), int(out.height * ratio)),
                             Image.Resampling.LANCZOS)

    except Exception as exc:                       # noqa: BLE001
        import traceback
        traceback.print_exc(file=sys.stderr)
        result['message'] = '超分失败: %s' % exc
        return result

    # 一律存 JPEG：PNG 存 4x 图会让单张封面涨到 MB 级，EPUB 体积不可接受
    out_path = os.path.splitext(image_path)[0] + '_sr.jpg'
    tmp_path = out_path + '.tmp'
    try:
        out.save(tmp_path, 'JPEG', quality=JPEG_QUALITY, subsampling=0,
                 optimize=True, icc_profile=icc if icc else None)
        os.replace(tmp_path, out_path)
    except Exception as exc:                       # noqa: BLE001
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        result['message'] = '保存失败: %s' % exc
        return result

    result['success'] = True
    result['output_path'] = out_path
    result['size_after'] = os.path.getsize(out_path)
    result['width_after'] = out.width
    result['height_after'] = out.height
    result['message'] = ('✅ 超分完成 %dx%d → %dx%d'
                         % (w0, h0, out.width, out.height))
    log('✅ %s  原图 %.1fKB → 输出 %.1fKB'
        % (result['message'], result['size_before'] / 1024,
           result['size_after'] / 1024))
    return result


def main():
    try:
        raw = sys.stdin.read()
        if not raw:
            print(json.dumps({'success': False, 'error': '未收到输入数据'}))
            return

        # 容忍 UTF-8 BOM：某些 Windows 工具重定向进来的文件会带 BOM，
        # 直接 json.loads 会抛 "Unexpected UTF-8 BOM"
        raw = raw.lstrip('\ufeff').strip()

        params = json.loads(raw)
        image_path = params.get('image_path')
        magnify = params.get('magnify', 4)

        result = enhance_image(image_path, magnify)
        print(json.dumps(result, ensure_ascii=False))

    except Exception as exc:                       # noqa: BLE001
        import traceback
        traceback.print_exc(file=sys.stderr)
        print(json.dumps({'success': False, 'error': str(exc)}))


if __name__ == '__main__':
    main()
