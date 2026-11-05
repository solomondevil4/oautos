# -*- coding: utf-8 -*-
"""素材标定实验台：粗框 -> 坐标反查 -> 多峰检查 -> 跨帧/动态区 -> meta.json

用法（必须在仓库根目录，用 toolkit/python.exe 跑）：

    PYTHONPATH=. toolkit/python.exe dev_tools/roi_lab.py locate \
        --frame f1.png --tpl tpl.png --roi-back 0,0,1280,720

    PYTHONPATH=. toolkit/python.exe dev_tools/roi_lab.py peaks \
        --frame f1.png --tpl tpl.png --roi-back 611,134,40,43

    PYTHONPATH=. toolkit/python.exe dev_tools/roi_lab.py diff \
        --frames f1.png f2.png f3.png --roi-front 617,140,28,31

    PYTHONPATH=. toolkit/python.exe dev_tools/roi_lab.py run \
        --frame f1.png --tpl tpl.png --roi-back 611,134,40,43 \
        --frames f2.png f3.png --out-dir .workbuddy/tmp/roi_lab/demo \
        --name I_FRIEND_LIKE_OFF --port 16512 --screen "好友聊天-巫山圣尊"

设计要点（对应 .codebuddy/rules/04-image-ocr.md）：
1. 坐标一律由 detect_image_detail 反查，不用目测/网格图读数；它走的是运行时同一条
   匹配路径（含灰度/掩码），比裸 cv2.matchTemplate 可信。
2. 每次调用都新建一个 RuleImage —— 匹配成功后 detect_image_detail 会把落点回写进
   rule.roi_front，复用同一个对象会污染后续测试（全局单例污染）。
3. 搜索框内做多峰检测，防止「同款图标出现多次却只报最高峰」。
4. 三帧差分找动态元素，模板框与之相交就报警。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cv2
import numpy as np

from module.atom.image import RuleImage
from dev_tools.assets_test import detect_image_detail

DEFAULT_ROI_BACK = "0,0,1280,720"
DEFAULT_THRESHOLD = 0.8


# --------------------------------------------------------------------------- 基础

def imread(path: str | Path) -> np.ndarray:
    """用 imdecode + fromfile 读图（绕开 OpenCV 中文路径问题），统一返回 BGR 三通道。"""
    buf = np.fromfile(str(path), dtype=np.uint8)
    img = cv2.imdecode(buf, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise ValueError(f'读图失败: {path}')
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    if img.shape[2] == 4:
        img = img[:, :, :3]
    return img


def imwrite(path: str | Path, img: np.ndarray) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    ok, buf = cv2.imencode('.png', img)
    if not ok:
        raise ValueError(f'写图失败: {path}')
    buf.tofile(str(path))


def parse_roi(text: str) -> tuple[int, int, int, int]:
    parts = [p.strip() for p in text.split(',')]
    if len(parts) != 4:
        raise ValueError(f'ROI 必须是 x,y,w,h: {text!r}')
    x, y, w, h = (int(float(p)) for p in parts)
    if w <= 0 or h <= 0:
        raise ValueError(f'ROI 宽高必须大于 0: {text!r}')
    return x, y, w, h


def roi_text(roi) -> str:
    return ','.join(str(int(v)) for v in roi)


def make_rule(tpl_path: str, roi_back: tuple, threshold: float) -> RuleImage:
    """每次都是新实例，避免 roi_front 被上一次匹配回写污染。"""
    mat = imread(tpl_path)
    h, w = mat.shape[:2]
    return RuleImage(
        roi_front=(0, 0, w, h),
        roi_back=list(roi_back),
        method=RuleImage.METHOD_TEMPLATE_MATCH,
        threshold=threshold,
        file=str(Path(tpl_path).resolve()),
    )


# --------------------------------------------------------------------------- 1. 坐标反查

def locate(frame: str, tpl: str, roi_back: tuple, threshold: float = DEFAULT_THRESHOLD) -> dict:
    """用官方 detect_image_detail 反查 roiFront 落点。"""
    result = detect_image_detail(str(Path(frame).resolve()), make_rule(tpl, roi_back, threshold))
    return {
        'roiFront': result['roiFront'],
        'similarity': result['similarity'],
        'matched': result['matched'],
        'message': result['message'],
        'roiBack': roi_text(roi_back),
    }


# --------------------------------------------------------------------------- 2. 多峰检测

def find_peaks(frame: str, tpl: str, roi_back: tuple,
               threshold: float = DEFAULT_THRESHOLD,
               min_gap_ratio: float = 0.5, max_peaks: int = 20) -> list[dict]:
    """在 roiBack 内找出所有 >= 阈值的峰，用 NMS 抑制挨在一起重复计的同一个图标。"""
    img = imread(frame)
    mat = imread(tpl)
    x, y, w, h = roi_back
    source = img[y:y + h, x:x + w]
    th, tw = mat.shape[:2]
    if source.shape[0] < th or source.shape[1] < tw:
        raise ValueError(f'roiBack {roi_back} 比模板 {tw}x{th} 还小')

    res = cv2.matchTemplate(source, mat, cv2.TM_CCOEFF_NORMED)
    work = res.copy()
    gap = max(2, int(min_gap_ratio * min(th, tw)))
    peaks: list[dict] = []
    while len(peaks) < max_peaks:
        _, mx, _, loc = cv2.minMaxLoc(work)
        if mx < threshold:
            break
        peaks.append({
            'x': x + int(loc[0]),
            'y': y + int(loc[1]),
            'score': round(float(mx), 4),
        })
        r0, r1 = max(0, loc[1] - gap), min(work.shape[0], loc[1] + gap + 1)
        c0, c1 = max(0, loc[0] - gap), min(work.shape[1], loc[0] + gap + 1)
        work[r0:r1, c0:c1] = -1.0
    return peaks


# --------------------------------------------------------------------------- 3. 三帧差分

def dynamic_regions(frames: list[str], roi: tuple | None = None,
                    thr: int = 25, min_area: int = 40,
                    annotated_out: str | None = None,
                    roi_front: tuple | None = None) -> dict:
    """逐像素比较所有帧两两之差，取最大，标出会动的连通区域。"""
    imgs = [imread(f) for f in frames]
    shapes = {im.shape for im in imgs}
    if len(shapes) != 1:
        raise ValueError(f'帧尺寸不一致: {shapes}')
    ox, oy = (roi[0], roi[1]) if roi else (0, 0)
    crops = [im[oy:oy + (roi[3] if roi else im.shape[0]),
                ox:ox + (roi[2] if roi else im.shape[1])] for im in imgs]

    diff = np.zeros(crops[0].shape[:2], dtype=np.uint8)
    for i in range(len(crops)):
        for j in range(i + 1, len(crops)):
            d = np.abs(crops[i].astype(np.int16) - crops[j].astype(np.int16)).max(axis=2)
            diff = np.maximum(diff, d.astype(np.uint8))
    mask = (diff > thr).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    n, _, stats, _ = cv2.connectedComponentsWithStats(mask, 8)

    regions = []
    for i in range(1, n):
        bx, by, bw, bh, area = (int(v) for v in stats[i])
        if area < min_area:
            continue
        regions.append({
            'x': bx + ox, 'y': by + oy, 'w': bw, 'h': bh, 'area': area,
            'max_diff': int(diff[by:by + bh, bx:bx + bw].max()),
        })
    regions.sort(key=lambda r: -r['area'])

    overlap = []
    if roi_front:
        fx, fy, fw, fh = roi_front
        for r in regions:
            ix = max(fx, r['x']) < min(fx + fw, r['x'] + r['w'])
            iy = max(fy, r['y']) < min(fy + fh, r['y'] + r['h'])
            if ix and iy:
                overlap.append({'x': r['x'], 'y': r['y'], 'w': r['w'], 'h': r['h'], 'area': r['area']})

    if annotated_out:
        canvas = imgs[0].copy()
        for r in regions:
            cv2.rectangle(canvas, (r['x'], r['y']), (r['x'] + r['w'], r['y'] + r['h']), (0, 0, 255), 1)
        if roi_front:
            fx, fy, fw, fh = roi_front
            cv2.rectangle(canvas, (fx, fy), (fx + fw, fy + fh), (0, 255, 0), 2)
        imwrite(annotated_out, canvas)

    return {'frames': [str(f) for f in frames], 'regions': regions[:20],
            'region_count': len(regions), 'overlap_with_roi_front': overlap,
            'threshold': thr, 'annotated': annotated_out}


# --------------------------------------------------------------------------- 串联

def run(args) -> dict:
    roi_back = parse_roi(args.roi_back)
    tpl = args.tpl
    frame = args.frame
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []
    errors: list[str] = []

    # 1. 坐标反查（官方路径）
    loc = locate(frame, tpl, roi_back, args.threshold)
    roi_front = parse_roi(loc['roiFront'])
    if loc['similarity'] < args.threshold:
        warnings.append(f"同帧自洽失败：{loc['similarity']} < {args.threshold}，先检查模板/roiBack；"
                        '此次落点是低分位置，产出的裁块不可信')
        errors.append('同帧自洽失败')

    # 2. 多峰检测
    peaks = find_peaks(frame, tpl, roi_back, args.threshold)
    if len(peaks) > 1:
        warnings.append(f'搜索框内有 {len(peaks)} 个 >= {args.threshold} 的峰，素材不唯一，'
                        '应缩小 roiBack 或换更独特的标志物')
        errors.append('素材不唯一')

    # 3. 从原图裁出最终模板（保证 png 尺寸 == roiFront w/h）
    crop = imread(frame)[roi_front[1]:roi_front[1] + roi_front[3],
                         roi_front[0]:roi_front[0] + roi_front[2]]
    crop_path = out / f'{args.name}.png'
    imwrite(crop_path, crop)
    if crop.shape[1] != roi_front[2] or crop.shape[0] != roi_front[3]:
        warnings.append('裁块尺寸与 roiFront 的 w/h 不一致（通常因为贴近画面边缘）')

    # 4. 跨帧分数：一律用「输入模板」测，不能用刚裁出来的 crop 自证
    #    （负样本 / 落点异常时，crop 是从同一帧按错误落点裁的，拿它当模板必然 1.0，会掩盖问题）
    #    每帧都新建 RuleImage，避免 roi_front 回写污染。
    all_frames = [frame] + list(args.frames or [])
    cross = []
    for f in all_frames:
        r = detect_image_detail(str(Path(f).resolve()),
                                make_rule(tpl, roi_back, args.threshold))
        cross.append({'frame': str(f), 'similarity': r['similarity'],
                      'matched': r['matched'], 'roiFront': r['roiFront']})
    extras = cross[1:]
    if not extras:
        warnings.append('未给第二帧，跨帧未验证（不许拿同帧分数冒充）')
    else:
        low = [c for c in extras if c['similarity'] < args.threshold]
        if low:
            warnings.append('跨帧不稳：' + ', '.join(f"{Path(c['frame']).name}={c['similarity']}" for c in low))
            errors.append('跨帧不稳')

    # 5. 三帧差分找动态元素
    dynamic = None
    if len(all_frames) >= 3:
        d_roi = parse_roi(args.diff_roi) if args.diff_roi else None
        dynamic = dynamic_regions(all_frames[:3], d_roi, args.diff_thr,
                                  annotated_out=str(out / f'{args.name}_dynamic.png'),
                                  roi_front=roi_front)
        if dynamic['overlap_with_roi_front']:
            warnings.append(f"模板框内有 {len(dynamic['overlap_with_roi_front'])} 块会动的像素，"
                            '跨帧分数可能被它拖低，考虑避开或换标志物')
    elif len(all_frames) >= 2:
        warnings.append('只有 2 帧，未做三帧差分；建议补一帧以发现飘动元素')

    meta = {
        'name': args.name,
        'port': args.port,
        'captured_at': args.captured_at or datetime.now().strftime('%Y-%m-%d %H:%M'),
        'screen': args.screen,
        'resolution': '1280x720',
        'source_frame': str(frame),
        'roi_front': roi_text(roi_front),
        'roi_back': roi_text(roi_back),
        'template_png': str(crop_path),
        'template_size': f'{crop.shape[1]}x{crop.shape[0]}',
        'threshold': args.threshold,
        'method': 'Template matching',
        'locate': loc,
        'peaks': peaks,
        'peaks_unique': len(peaks) <= 1,
        'scores': {'same_frame': cross[0]['similarity'],
                   'cross_frames': [{'frame': c['frame'], 'score': c['similarity']} for c in extras],
                   'cross_frame_verified': bool(extras)},
        'dynamic': dynamic,
        'valid': not errors,
        'errors': errors,
        'warnings': warnings,
        'generator': 'dev_tools/roi_lab.py',
    }
    meta_path = out / f'{args.name}.meta.json'
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')

    print(f'roi_front   : {meta["roi_front"]}')
    print(f'roi_back    : {meta["roi_back"]}')
    print(f'同帧        : {loc["similarity"]}')
    print(f'峰数量      : {len(peaks)}  ' + ('唯一 ✓' if len(peaks) <= 1 else '不唯一 ✗'))
    for c in cross:
        print(f'分数        : {Path(c["frame"]).name} = {c["similarity"]}  @ {c["roiFront"]}')
    if dynamic:
        print(f'动态区      : {dynamic["region_count"]} 块，与模板相交 {len(dynamic["overlap_with_roi_front"])} 块')
    print(f'模板        : {crop_path}')
    print(f'元信息      : {meta_path}')
    print(f'结论        : ' + ('可用 ✓' if not errors else '不可用 ✗（' + ' / '.join(errors) + '）'))
    if warnings:
        print('\n⚠ 警告：')
        for w in warnings:
            print('  -', w)
    return meta


# --------------------------------------------------------------------------- CLI

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description='素材标定实验台（坐标反查 / 多峰 / 动态区 / meta.json）')
    sub = p.add_subparsers(dest='cmd', required=True)

    def common(sp, need_back=True):
        sp.add_argument('--frame', required=True, help='主帧 png（1280x720）')
        sp.add_argument('--tpl', required=True, help='模板 png')
        sp.add_argument('--roi-back', default=DEFAULT_ROI_BACK if not need_back else None,
                        required=need_back, help='搜索框 x,y,w,h')
        sp.add_argument('--threshold', type=float, default=DEFAULT_THRESHOLD)

    sp = sub.add_parser('locate', help='用官方 detect_image_detail 反查 roiFront 落点')
    common(sp, need_back=False)

    sp = sub.add_parser('peaks', help='检测 roiBack 内有几个峰（同款图标是否唯一）')
    common(sp)
    sp.add_argument('--min-gap', type=float, default=0.5, help='NMS 半径 = 模板短边 * 该值')

    sp = sub.add_parser('diff', help='三帧差分，标出会动的区域')
    sp.add_argument('--frames', nargs='+', required=True, help='2~3 帧 png')
    sp.add_argument('--roi', default=None, help='只看这块区域 x,y,w,h，默认全屏')
    sp.add_argument('--roi-front', default=None, help='模板框，用来检查是否压到动态区')
    sp.add_argument('--thr', type=int, default=25)
    sp.add_argument('--out', default=None, help='标注图输出路径')

    sp = sub.add_parser('run', help='串联：反查 -> 多峰 -> 裁剪 -> 跨帧 -> 动态区 -> meta.json')
    common(sp)
    sp.add_argument('--frames', nargs='*', default=[], help='额外帧（跨帧验证 / 差分）')
    sp.add_argument('--out-dir', required=True)
    sp.add_argument('--name', required=True, help='素材名，如 I_FRIEND_LIKE_OFF')
    sp.add_argument('--port', default='unknown')
    sp.add_argument('--screen', default='unknown')
    sp.add_argument('--captured-at', default=None)
    sp.add_argument('--diff-roi', default=None, help='差分限定区域，默认全屏')
    sp.add_argument('--diff-thr', type=int, default=25)
    return p


def main() -> int:
    args = build_parser().parse_args()
    roi_back = parse_roi(args.roi_back or DEFAULT_ROI_BACK) if args.cmd in ('locate', 'peaks', 'run') else None

    if args.cmd == 'locate':
        print(json.dumps(locate(args.frame, args.tpl, roi_back, args.threshold),
                         ensure_ascii=False, indent=2))
    elif args.cmd == 'peaks':
        peaks = find_peaks(args.frame, args.tpl, roi_back, args.threshold, args.min_gap)
        print(json.dumps({'roiBack': roi_text(roi_back), 'peak_count': len(peaks),
                          'unique': len(peaks) <= 1, 'peaks': peaks},
                         ensure_ascii=False, indent=2))
        if len(peaks) > 1:
            print('⚠ 搜索框内素材不唯一，缩小 roiBack 或换更独特的标志物')
    elif args.cmd == 'diff':
        roi = parse_roi(args.roi) if args.roi else None
        rf = parse_roi(args.roi_front) if args.roi_front else None
        print(json.dumps(dynamic_regions(args.frames, roi, args.thr,
                                         annotated_out=args.out, roi_front=rf),
                         ensure_ascii=False, indent=2))
    else:
        run(args)
    return 0


if __name__ == '__main__':
    sys.exit(main())
