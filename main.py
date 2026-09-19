import sys
import os

sys.path = [
    p for p in sys.path
    if os.path.abspath(p) != os.path.abspath(os.path.dirname(__file__))
]

"""
LaserGRBL Cloud Bridge — Enhanced
==================================
Install:
    pip install flask pillow

Run:
    python main.py

Open:
    http://127.0.0.1:5000
"""

import shutil
import subprocess
import json
import threading
from datetime import datetime

from flask import (
    Flask, request, jsonify,
    send_file, render_template_string, Response
)
from werkzeug.utils import secure_filename
from PIL import Image, ImageFilter, ImageEnhance

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 64 * 1024 * 1024

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, 'uploads')
WATCH_FOLDER = os.path.join(os.path.expanduser('~'), 'Desktop', 'LaserGRBL_Jobs')
THUMB_DIR  = os.path.join(BASE_DIR, 'thumbs')

for d in (UPLOAD_DIR, WATCH_FOLDER, THUMB_DIR):
    os.makedirs(d, exist_ok=True)

IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.bmp', '.gif', '.tiff', '.webp'}
JOB_EXTS   = {'.nc', '.gcode'}

LASERGRBL_PATHS = [
    r"C:\Program Files\LaserGRBL\LaserGRBL.exe",
    r"C:\Program Files (x86)\LaserGRBL\LaserGRBL.exe",
    os.path.join(os.path.expanduser('~'), 'Desktop', 'LaserGRBL.exe'),
    os.path.join(os.path.expanduser('~'), 'Downloads', 'LaserGRBL.exe'),
]

# Live power override shared state
live_power = {'value': None, 'lock': threading.Lock()}


def find_lasergrbl():
    for p in LASERGRBL_PATHS:
        if os.path.exists(p):
            return p
    return None


def load_grayscale(filepath, contrast=1.0, brightness=1.0, invert=False):
    img = Image.open(filepath).convert('L')
    if brightness != 1.0:
        img = ImageEnhance.Brightness(img).enhance(brightness)
    if contrast != 1.0:
        img = ImageEnhance.Contrast(img).enhance(contrast)
    if invert:
        from PIL import ImageOps
        img = ImageOps.invert(img)
    return list(img.getdata()), img.size[0], img.size[1]


def image_to_gcode(filepath, config):
    speed      = int(config.get('speed', 1000))
    power_pct  = int(config.get('power', 80))
    width_mm   = float(config.get('width', 50))
    height_mm  = float(config.get('height', 50))
    line_gap   = float(config.get('gap', 0.1))
    max_s      = int(config.get('max_s', 1000))
    contrast   = float(config.get('contrast', 1.0))
    brightness = float(config.get('brightness', 1.0))
    invert     = config.get('invert', 'false').lower() == 'true'
    dither     = config.get('dither', 'none')
    scan_mode  = config.get('scan_mode', 'serpentine')

    fname     = os.path.basename(filepath)
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    s_max     = int((power_pct / 100) * max_s)

    try:
        pixels, img_w, img_h = load_grayscale(filepath, contrast, brightness, invert)
    except Exception as e:
        return f"; ERROR loading image: {e}"

    x_scale = width_mm / max(img_w, 1)
    y_scale = height_mm / max(img_h, 1)

    lines = [
        f'; LaserGRBL Cloud — {fname}',
        f'; Generated  : {timestamp}',
        f'; Image size : {img_w}x{img_h}px',
        f'; Burn area  : {width_mm}x{height_mm}mm',
        f'; Line gap   : {line_gap}mm',
        f'; Power      : {power_pct}%  (S0–{s_max} of {max_s})',
        f'; Speed      : {speed}mm/min',
        f'; Scan mode  : {scan_mode}',
        '',
        'G21        ; mm units',
        'G90        ; absolute positioning',
        'M5 S0      ; laser off',
        'G0 F3000   ; fast travel speed',
        'G0 X0 Y0   ; home',
        '',
    ]

    for row in range(img_h):
        y = row * y_scale

        if scan_mode == 'unidirectional':
            cols = range(img_w)
        else:
            cols = range(img_w) if row % 2 == 0 else range(img_w - 1, -1, -1)

        lines.append(f'; Row {row}')
        lines.append(f'G0 Y{y:.3f}')

        prev_pwr = -1
        for col in cols:
            idx = (img_h - 1 - row) * img_w + col
            gray = pixels[idx] if 0 <= idx < len(pixels) else 255
            pwr  = int((1 - gray / 255) * s_max)

            x = col * x_scale

            if pwr > 10:
                if pwr != prev_pwr:
                    lines.append(f'G1 X{x:.3f} S{pwr} M4 F{speed}')
                else:
                    lines.append(f'G1 X{x:.3f}')
            else:
                lines.append(f'G0 X{x:.3f} M5 S0')

            prev_pwr = pwr

        lines.append('M5 S0')

    lines += [
        '',
        '; === END ===',
        'M5 S0',
        'G0 X0 Y0',
        f'; Total rows: {img_h}',
        f'; EOF — {fname}',
    ]

    return '\n'.join(lines)


def list_jobs():
    jobs = []
    for f in sorted(os.listdir(WATCH_FOLDER), reverse=True):
        ext = os.path.splitext(f)[1].lower()
        if ext in JOB_EXTS:
            p = os.path.join(WATCH_FOLDER, f)
            stat = os.stat(p)
            jobs.append({
                'name': f,
                'time': datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M'),
                'size': f"{stat.st_size / 1024:.1f} KB",
                'lines': sum(1 for _ in open(p, 'r', errors='ignore'))
            })
    return jobs


# ─────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────

@app.route('/')
def index():
    return render_template_string(HTML)


@app.route('/jobs')
def jobs():
    return jsonify({'jobs': list_jobs()})


@app.route('/download/<path:fname>')
def download(fname):
    p = os.path.join(WATCH_FOLDER, os.path.basename(fname))
    if os.path.exists(p):
        return send_file(p, as_attachment=True)
    return ('Not found', 404)


@app.route('/delete/<path:fname>', methods=['DELETE', 'GET'])
def delete(fname):
    p = os.path.join(WATCH_FOLDER, os.path.basename(fname))
    if os.path.exists(p):
        os.remove(p)
        return jsonify({'success': True})
    return jsonify({'success': False}), 404


@app.route('/rename', methods=['POST'])
def rename():
    data     = request.json or {}
    old_name = os.path.basename(data.get('old', ''))
    new_name = os.path.basename(data.get('new', ''))
    if not old_name or not new_name:
        return jsonify({'success': False, 'error': 'Missing name'})
    old_path = os.path.join(WATCH_FOLDER, old_name)
    new_path = os.path.join(WATCH_FOLDER, new_name)
    if not os.path.exists(old_path):
        return jsonify({'success': False, 'error': 'File not found'})
    os.rename(old_path, new_path)
    return jsonify({'success': True, 'new_name': new_name})


@app.route('/gcode/<path:fname>')
def gcode_view(fname):
    p = os.path.join(WATCH_FOLDER, os.path.basename(fname))
    if os.path.exists(p):
        with open(p, 'r', errors='ignore') as f:
            return jsonify({'content': f.read()})
    return jsonify({'error': 'Not found'}), 404


@app.route('/lasergrbl_path')
def lasergrbl_path():
    p = find_lasergrbl()
    return jsonify({'found': p is not None, 'path': p or ''})


@app.route('/open_in_lasergrbl', methods=['POST'])
def open_in_lasergrbl():
    data     = request.json or {}
    job_name = os.path.basename(data.get('job_name', ''))
    job_path = os.path.join(WATCH_FOLDER, job_name)
    lgbl     = data.get('lasergrbl_exe', '') or find_lasergrbl()

    if not os.path.exists(job_path):
        return jsonify({'success': False, 'error': 'Job file not found'})
    if not lgbl or not os.path.exists(lgbl):
        return jsonify({'success': False, 'error': 'LaserGRBL.exe not found. Set path manually.'})
    try:
        subprocess.Popen([lgbl, job_path])
        return jsonify({'success': True, 'message': f'Opened {job_name} in LaserGRBL'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/set_live_power', methods=['POST'])
def set_live_power():
    """Store a live power override — client polls this and injects into running job."""
    data = request.json or {}
    pct  = data.get('power')
    with live_power['lock']:
        live_power['value'] = int(pct) if pct is not None else None
    return jsonify({'success': True, 'power': live_power['value']})


@app.route('/get_live_power')
def get_live_power():
    with live_power['lock']:
        return jsonify({'power': live_power['value']})


@app.route('/upload', methods=['POST'])
def upload():
    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify({'success': False, 'error': 'No file received'})

    fname    = secure_filename(f.filename)
    filepath = os.path.join(UPLOAD_DIR, fname)
    f.save(filepath)

    ext = os.path.splitext(fname)[1].lower()
    if ext not in IMAGE_EXTS | JOB_EXTS:
        return jsonify({'success': False, 'error': f'Unsupported type: {ext}'})

    config = {k: request.form.get(k, '') for k in (
        'speed', 'power', 'width', 'height', 'gap', 'max_s',
        'contrast', 'brightness', 'invert', 'dither', 'scan_mode'
    )}

    if ext in JOB_EXTS:
        job_name = fname
        shutil.copy(filepath, os.path.join(WATCH_FOLDER, job_name))
        with open(filepath, 'r', errors='ignore') as fh:
            gcode = fh.read()
    else:
        gcode    = image_to_gcode(filepath, config)
        job_name = (
            os.path.splitext(fname)[0]
            + '_'
            + datetime.now().strftime('%H%M%S')
            + '.nc'
        )
        with open(os.path.join(WATCH_FOLDER, job_name), 'w') as gf:
            gf.write(gcode)

    lines = gcode.count('\n') + 1
    return jsonify({
        'success'  : True,
        'job_name' : job_name,
        'gcode'    : gcode[:6000],
        'lines'    : lines,
        'truncated': len(gcode) > 6000
    })


# ─────────────────────────────────────────────
# HTML (full enhanced UI)
# ─────────────────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>LaserGRBL Cloud Bridge</title>
<style>
:root{
  --bg:#0d0d0f;--bg2:#141416;--bg3:#1c1c20;--bg4:#242428;
  --border:#2a2a30;--border2:#3a3a42;
  --accent:#ff5f1f;--accent2:#ff8c42;
  --text:#f0eeea;--muted:#888;--muted2:#555;
  --green:#22c55e;--red:#ef4444;--blue:#3b82f6;--amber:#f59e0b;
  --radius:8px;--radius-lg:12px;
  --font:'JetBrains Mono',monospace;
}
*{box-sizing:border-box;margin:0;padding:0}
body{
  background:var(--bg);color:var(--text);
  font-family:'Inter',sans-serif;font-size:14px;
  min-height:100vh;
}
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap');

/* Layout */
.shell{display:grid;grid-template-columns:260px 1fr;min-height:100vh}
.sidebar{
  background:var(--bg2);border-right:1px solid var(--border);
  padding:20px 16px;display:flex;flex-direction:column;gap:20px;
  overflow-y:auto;
}
.main{padding:24px;overflow-y:auto;display:flex;flex-direction:column;gap:20px}

/* Brand */
.brand{
  display:flex;align-items:center;gap:10px;
  padding-bottom:16px;border-bottom:1px solid var(--border);
}
.brand-icon{
  width:36px;height:36px;background:var(--accent);border-radius:8px;
  display:flex;align-items:center;justify-content:center;font-size:18px;
  flex-shrink:0;
}
.brand-name{font-size:15px;font-weight:600;line-height:1.2}
.brand-sub{font-size:11px;color:var(--muted)}

/* Cards */
.card{
  background:var(--bg3);border:1px solid var(--border);
  border-radius:var(--radius-lg);padding:16px;
}
.card-title{
  font-size:11px;font-weight:600;letter-spacing:.08em;
  text-transform:uppercase;color:var(--muted);
  margin-bottom:12px;display:flex;align-items:center;gap:6px;
}
.card-title .dot{
  width:6px;height:6px;border-radius:50%;background:var(--accent);
}

/* Upload zone */
.drop-zone{
  border:2px dashed var(--border2);border-radius:var(--radius-lg);
  padding:32px 20px;text-align:center;cursor:pointer;
  transition:all .2s;position:relative;
}
.drop-zone:hover,.drop-zone.drag-over{
  border-color:var(--accent);background:rgba(255,95,31,.05);
}
.drop-zone input[type=file]{
  position:absolute;inset:0;opacity:0;cursor:pointer;width:100%;height:100%;
}
.drop-icon{font-size:32px;margin-bottom:8px;opacity:.5}
.drop-label{color:var(--muted);font-size:13px;line-height:1.5}
.drop-label strong{color:var(--accent)}

/* Preview */
.preview-wrap{position:relative;display:none}
.preview-wrap img{
  width:100%;border-radius:var(--radius);
  border:1px solid var(--border);display:block;
  max-height:200px;object-fit:contain;background:#000;
}
.preview-info{
  position:absolute;bottom:6px;left:6px;right:6px;
  background:rgba(0,0,0,.75);border-radius:6px;
  padding:4px 8px;font-size:11px;color:var(--muted);
  display:flex;justify-content:space-between;
}

/* Controls */
.ctrl-row{display:flex;align-items:center;gap:8px;margin-bottom:8px}
.ctrl-label{font-size:12px;color:var(--muted);width:80px;flex-shrink:0}
.ctrl-value{
  font-size:12px;color:var(--accent);width:48px;
  text-align:right;font-family:var(--font);flex-shrink:0;
}
.ctrl-unit{font-size:11px;color:var(--muted2);width:28px;flex-shrink:0}
input[type=range]{
  flex:1;-webkit-appearance:none;appearance:none;
  height:4px;background:var(--border2);border-radius:2px;outline:none;
}
input[type=range]::-webkit-slider-thumb{
  -webkit-appearance:none;width:14px;height:14px;
  background:var(--accent);border-radius:50%;cursor:pointer;
}
input[type=number],input[type=text],select{
  background:var(--bg4);border:1px solid var(--border2);
  color:var(--text);border-radius:var(--radius);padding:6px 10px;
  font-size:13px;outline:none;width:100%;
  font-family:inherit;
}
input[type=number]:focus,input[type=text]:focus,select:focus{
  border-color:var(--accent);
}
.row2{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.form-group{margin-bottom:10px}
.form-group label{
  display:block;font-size:11px;color:var(--muted);
  margin-bottom:4px;text-transform:uppercase;letter-spacing:.05em;
}
.checkbox-row{
  display:flex;align-items:center;gap:8px;font-size:12px;color:var(--muted);
  cursor:pointer;
}
.checkbox-row input{accent-color:var(--accent);cursor:pointer}

/* Power live override */
.live-power-badge{
  display:inline-flex;align-items:center;gap:6px;
  background:rgba(255,95,31,.15);border:1px solid rgba(255,95,31,.3);
  color:var(--accent2);border-radius:20px;
  padding:3px 10px;font-size:11px;font-weight:600;
}
.live-dot{
  width:6px;height:6px;border-radius:50%;
  background:var(--accent);animation:pulse 1.2s infinite;
}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}

/* Buttons */
.btn{
  display:inline-flex;align-items:center;justify-content:center;gap:7px;
  padding:9px 16px;border-radius:var(--radius);border:none;
  cursor:pointer;font-size:13px;font-weight:500;
  transition:all .15s;white-space:nowrap;
}
.btn-primary{background:var(--accent);color:#fff}
.btn-primary:hover{background:var(--accent2)}
.btn-ghost{
  background:transparent;color:var(--muted);
  border:1px solid var(--border2);
}
.btn-ghost:hover{background:var(--bg4);color:var(--text);border-color:var(--border2)}
.btn-danger{background:transparent;color:var(--red);border:1px solid rgba(239,68,68,.3)}
.btn-danger:hover{background:rgba(239,68,68,.1)}
.btn-green{background:var(--green);color:#fff}
.btn-green:hover{filter:brightness(1.1)}
.btn-sm{padding:5px 10px;font-size:12px}
.btn:disabled{opacity:.4;cursor:not-allowed}
.btn-row{display:flex;gap:8px;flex-wrap:wrap}

/* GCode viewer */
.gcode-wrap{
  background:#000;border-radius:var(--radius);
  border:1px solid var(--border);
  font-family:var(--font);font-size:12px;
  height:320px;overflow-y:auto;padding:12px;
  white-space:pre;line-height:1.6;
}
.gc-comment{color:#4a9;}
.gc-g{color:#7af;}
.gc-m{color:#fa7;}
.gc-s{color:#f7a;}
.gc-coord{color:#adf;}
.gc-plain{color:#888;}

/* Job list */
.job-item{
  display:flex;align-items:center;gap:10px;
  padding:10px 12px;border-radius:var(--radius);
  border:1px solid var(--border);margin-bottom:6px;
  background:var(--bg4);transition:border-color .15s;
  cursor:pointer;
}
.job-item:hover{border-color:var(--border2)}
.job-item.selected{border-color:var(--accent)}
.job-name{flex:1;font-size:13px;font-family:var(--font);min-width:0}
.job-name span{
  display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
}
.job-meta{font-size:11px;color:var(--muted);margin-top:2px}
.job-actions{display:flex;gap:4px;flex-shrink:0}

/* Badge */
.badge{
  display:inline-flex;align-items:center;
  padding:2px 8px;border-radius:20px;font-size:11px;font-weight:500;
}
.badge-success{background:rgba(34,197,94,.15);color:var(--green)}
.badge-warn{background:rgba(245,158,11,.15);color:var(--amber)}
.badge-info{background:rgba(59,130,246,.15);color:var(--blue)}

/* Stats row */
.stats-row{
  display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:12px;
}
.stat{
  background:var(--bg4);border-radius:var(--radius);
  padding:10px;text-align:center;
}
.stat-val{font-size:18px;font-weight:600;color:var(--text)}
.stat-lab{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em}

/* Tabs */
.tabs{display:flex;gap:2px;margin-bottom:16px}
.tab{
  padding:6px 14px;border-radius:var(--radius);font-size:13px;
  cursor:pointer;color:var(--muted);transition:.15s;
  border:1px solid transparent;
}
.tab.active{
  background:var(--bg4);color:var(--text);border-color:var(--border2);
}
.tab-pane{display:none}
.tab-pane.active{display:block}

/* Notifications */
#notif{
  position:fixed;top:16px;right:16px;z-index:999;
  display:flex;flex-direction:column;gap:8px;
  pointer-events:none;
}
.notif-item{
  padding:10px 16px;border-radius:var(--radius);font-size:13px;
  border:1px solid;max-width:320px;
  animation:slide-in .2s ease;pointer-events:all;
}
@keyframes slide-in{from{opacity:0;transform:translateX(20px)}}
.notif-ok{background:rgba(34,197,94,.1);border-color:rgba(34,197,94,.3);color:var(--green)}
.notif-err{background:rgba(239,68,68,.1);border-color:rgba(239,68,68,.3);color:var(--red)}
.notif-info{background:rgba(59,130,246,.1);border-color:rgba(59,130,246,.3);color:var(--blue)}

/* Spinner */
.spinner{
  display:inline-block;width:14px;height:14px;
  border:2px solid rgba(255,255,255,.2);
  border-top-color:#fff;border-radius:50%;
  animation:spin .7s linear infinite;
}
@keyframes spin{to{transform:rotate(360deg)}}

/* Scrollbars */
::-webkit-scrollbar{width:5px;height:5px}
::-webkit-scrollbar-track{background:var(--bg2)}
::-webkit-scrollbar-thumb{background:var(--border2);border-radius:3px}

/* Responsive */
@media(max-width:700px){
  .shell{grid-template-columns:1fr}
  .sidebar{border-right:none;border-bottom:1px solid var(--border)}
}
</style>
</head>
<body>

<div id="notif"></div>

<div class="shell">

<!-- ═══ SIDEBAR ═══ -->
<aside class="sidebar">

  <div class="brand">
    <div class="brand-icon">🔥</div>
    <div>
      <div class="brand-name">LaserGRBL Cloud</div>
      <div class="brand-sub">Bridge v2.0</div>
    </div>
  </div>

  <!-- Upload -->
  <div class="card">
    <div class="card-title"><span class="dot"></span>Upload File</div>
    <div class="drop-zone" id="dropZone">
      <input type="file" id="fileInput" accept=".png,.jpg,.jpeg,.bmp,.gif,.tiff,.webp,.nc,.gcode">
      <div class="drop-icon">📂</div>
      <div class="drop-label">Drop image or <strong>click</strong><br><small>.png .jpg .bmp .nc .gcode</small></div>
    </div>
    <div class="preview-wrap" id="previewWrap" style="margin-top:10px">
      <img id="preview" alt="Preview">
      <div class="preview-info">
        <span id="previewName"></span>
        <span id="previewSize"></span>
      </div>
    </div>
  </div>

  <!-- Dimensions -->
  <div class="card">
    <div class="card-title"><span class="dot"></span>Dimensions</div>
    <div class="row2">
      <div class="form-group">
        <label>Width (mm)</label>
        <input type="number" id="width" value="50" min="1" max="400" step="1">
      </div>
      <div class="form-group">
        <label>Height (mm)</label>
        <input type="number" id="height" value="50" min="1" max="400" step="1">
      </div>
    </div>
    <div class="form-group">
      <label>Line Gap (mm)</label>
      <input type="number" id="gap" value="0.1" min="0.05" max="1" step="0.05">
    </div>
  </div>

  <!-- Laser params -->
  <div class="card">
    <div class="card-title"><span class="dot"></span>Laser Settings</div>

    <div class="ctrl-row">
      <span class="ctrl-label">Power</span>
      <input type="range" id="powerSlider" min="1" max="100" value="80">
      <span class="ctrl-value" id="powerVal">80</span>
      <span class="ctrl-unit">%</span>
    </div>
    <div class="ctrl-row">
      <span class="ctrl-label">Speed</span>
      <input type="range" id="speedSlider" min="100" max="5000" step="50" value="1000">
      <span class="ctrl-value" id="speedVal">1000</span>
      <span class="ctrl-unit">mm/m</span>
    </div>
    <div class="ctrl-row">
      <span class="ctrl-label">Max S</span>
      <input type="range" id="maxsSlider" min="100" max="1000" step="100" value="1000">
      <span class="ctrl-value" id="maxsVal">1000</span>
      <span class="ctrl-unit">S</span>
    </div>

    <div style="margin-top:10px">
      <div class="form-group">
        <label>Scan Mode</label>
        <select id="scanMode">
          <option value="serpentine">Serpentine (bidirectional)</option>
          <option value="unidirectional">Unidirectional</option>
        </select>
      </div>
    </div>
  </div>

  <!-- Image processing -->
  <div class="card">
    <div class="card-title"><span class="dot"></span>Image Processing</div>

    <div class="ctrl-row">
      <span class="ctrl-label">Contrast</span>
      <input type="range" id="contrastSlider" min="0.5" max="3" step="0.1" value="1">
      <span class="ctrl-value" id="contrastVal">1.0</span>
      <span class="ctrl-unit">×</span>
    </div>
    <div class="ctrl-row">
      <span class="ctrl-label">Brightness</span>
      <input type="range" id="brightnessSlider" min="0.2" max="2" step="0.1" value="1">
      <span class="ctrl-value" id="brightnessVal">1.0</span>
      <span class="ctrl-unit">×</span>
    </div>
    <label class="checkbox-row" style="margin-top:6px">
      <input type="checkbox" id="invertCheck"> Invert image
    </label>
  </div>

  <button class="btn btn-primary" id="convertBtn" style="width:100%;padding:12px" onclick="convertFile()">
    ⚡ Convert to GCODE
  </button>

</aside>

<!-- ═══ MAIN ═══ -->
<main class="main">

  <div style="display:flex;align-items:center;justify-content:space-between;gap:12px">
    <h2 style="font-size:18px;font-weight:600">Job Editor</h2>
    <div id="lgblStatus" style="font-size:12px;color:var(--muted)">Checking LaserGRBL...</div>
  </div>

  <!-- Live power override -->
  <div class="card" id="livePowerCard">
    <div class="card-title">
      <span class="dot"></span>
      Live Power Override
      <span class="live-power-badge" id="liveBadge" style="margin-left:auto;display:none">
        <span class="live-dot"></span>
        <span id="liveBadgeVal">80%</span>
      </span>
    </div>
    <p style="font-size:12px;color:var(--muted);margin-bottom:12px">
      Adjust laser power mid-job — change takes effect on next row burn.
    </p>
    <div class="ctrl-row">
      <span class="ctrl-label">Override</span>
      <input type="range" id="liveSlider" min="1" max="100" value="80">
      <span class="ctrl-value" id="liveVal">80</span>
      <span class="ctrl-unit">%</span>
    </div>
    <div class="btn-row" style="margin-top:10px">
      <button class="btn btn-primary btn-sm" onclick="applyLivePower()">Apply Override</button>
      <button class="btn btn-ghost btn-sm" onclick="clearLivePower()">Clear</button>
    </div>
  </div>

  <!-- Tabs -->
  <div class="tabs">
    <div class="tab active" onclick="switchTab('gcode')">GCODE Preview</div>
    <div class="tab" onclick="switchTab('jobs')">Job Queue</div>
  </div>

  <!-- GCODE tab -->
  <div class="tab-pane active" id="tab-gcode">
    <div class="card">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px;flex-wrap:wrap;gap:8px">
        <div class="card-title" style="margin-bottom:0">
          <span class="dot"></span>
          <span id="gcodeFileName">No file loaded</span>
        </div>
        <div id="gcodeStats" style="display:flex;gap:8px"></div>
      </div>
      <div class="gcode-wrap" id="gcodeView">
        <span style="color:var(--muted)">Convert an image to see GCODE here...</span>
      </div>
      <div class="btn-row" style="margin-top:12px">
        <button class="btn btn-green" id="launchBtn" onclick="launchLaserGRBL()" disabled>
          🚀 Open in LaserGRBL
        </button>
        <button class="btn btn-ghost" id="dlBtn" onclick="downloadJob()" disabled>
          ⬇ Download .nc
        </button>
        <div style="margin-left:auto">
          <input type="text" id="lgblExePath" placeholder="LaserGRBL.exe path (optional)"
            style="width:260px;font-size:12px">
        </div>
      </div>
    </div>
  </div>

  <!-- Jobs tab -->
  <div class="tab-pane" id="tab-jobs">
    <div class="card">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
        <div class="card-title" style="margin-bottom:0"><span class="dot"></span>Saved Jobs</div>
        <button class="btn btn-ghost btn-sm" onclick="loadJobs()">↻ Refresh</button>
      </div>
      <div id="jobList"><span style="color:var(--muted)">Loading...</span></div>
    </div>
  </div>

</main>
</div>

<script>
let currentJob = null;

// ─── Sliders ───
function bindSlider(sliderId, valId, decimals=0){
  const s = document.getElementById(sliderId);
  const v = document.getElementById(valId);
  s.addEventListener('input', () => {
    v.textContent = parseFloat(s.value).toFixed(decimals);
  });
}
bindSlider('powerSlider','powerVal');
bindSlider('speedSlider','speedVal');
bindSlider('maxsSlider','maxsVal');
bindSlider('contrastSlider','contrastVal',1);
bindSlider('brightnessSlider','brightnessVal',1);
bindSlider('liveSlider','liveVal');

// ─── Drag & Drop ───
const dz = document.getElementById('dropZone');
dz.addEventListener('dragover', e=>{ e.preventDefault(); dz.classList.add('drag-over'); });
dz.addEventListener('dragleave', ()=> dz.classList.remove('drag-over'));
dz.addEventListener('drop', e=>{
  e.preventDefault(); dz.classList.remove('drag-over');
  const f = e.dataTransfer.files[0];
  if(f) previewFile(f);
});
document.getElementById('fileInput').addEventListener('change', e=>{
  if(e.target.files[0]) previewFile(e.target.files[0]);
});

function previewFile(file){
  const wrap = document.getElementById('previewWrap');
  const img  = document.getElementById('preview');
  document.getElementById('previewName').textContent = file.name;
  document.getElementById('previewSize').textContent = (file.size/1024).toFixed(1)+' KB';
  if(file.type.startsWith('image/')){
    img.src = URL.createObjectURL(file);
    wrap.style.display = 'block';
  } else {
    wrap.style.display = 'none';
  }
}

// ─── Convert ───
async function convertFile(){
  const fi = document.getElementById('fileInput').files[0];
  if(!fi){ notify('Pick a file first','err'); return; }

  const btn = document.getElementById('convertBtn');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Converting...';

  const fd = new FormData();
  fd.append('file', fi);
  fd.append('width',  document.getElementById('width').value);
  fd.append('height', document.getElementById('height').value);
  fd.append('gap',    document.getElementById('gap').value);
  fd.append('power',  document.getElementById('powerSlider').value);
  fd.append('speed',  document.getElementById('speedSlider').value);
  fd.append('max_s',  document.getElementById('maxsSlider').value);
  fd.append('contrast',   document.getElementById('contrastSlider').value);
  fd.append('brightness', document.getElementById('brightnessSlider').value);
  fd.append('invert',     document.getElementById('invertCheck').checked ? 'true' : 'false');
  fd.append('scan_mode',  document.getElementById('scanMode').value);

  try{
    const r  = await fetch('/upload',{method:'POST',body:fd});
    const d  = await r.json();
    if(d.success){
      currentJob = d.job_name;
      renderGcode(d.gcode, d.job_name, d.lines, d.truncated);
      document.getElementById('launchBtn').disabled = false;
      document.getElementById('dlBtn').disabled = false;
      switchTab('gcode');
      notify(`Job saved: ${d.job_name}`,'ok');
      loadJobs();
    } else {
      notify(d.error,'err');
    }
  } catch(e){
    notify('Network error: '+e,'err');
  }
  btn.disabled = false;
  btn.innerHTML = '⚡ Convert to GCODE';
}

// ─── GCODE render ───
function renderGcode(code, name, lines, truncated){
  document.getElementById('gcodeFileName').textContent = name;
  document.getElementById('gcodeStats').innerHTML = `
    <span class="badge badge-info">${lines.toLocaleString()} lines</span>
    ${truncated ? '<span class="badge badge-warn">preview only</span>' : '<span class="badge badge-success">full</span>'}
  `;
  const lines_arr = code.split('\n');
  const html = lines_arr.map(l=>{
    if(l.startsWith(';'))  return `<span class="gc-comment">${esc(l)}</span>`;
    if(/^G[01] /.test(l))  return `<span class="gc-g">${esc(l)}</span>`;
    if(/^G0 /.test(l))     return `<span class="gc-coord">${esc(l)}</span>`;
    if(/^M[45]/.test(l))   return `<span class="gc-m">${esc(l)}</span>`;
    if(/S\d+/.test(l))     return `<span class="gc-s">${esc(l)}</span>`;
    return `<span class="gc-plain">${esc(l)}</span>`;
  }).join('\n');
  document.getElementById('gcodeView').innerHTML = html;
}

function esc(s){ return s.replace(/&/g,'&amp;').replace(/</g,'&lt;'); }

// ─── LaserGRBL ───
async function launchLaserGRBL(){
  if(!currentJob){ notify('No job loaded','err'); return; }
  const exe = document.getElementById('lgblExePath').value.trim();
  const r = await fetch('/open_in_lasergrbl',{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({job_name: currentJob, lasergrbl_exe: exe})
  });
  const d = await r.json();
  if(d.success) notify(d.message,'ok');
  else          notify(d.error,'err');
}

function downloadJob(){
  if(!currentJob) return;
  window.location = '/download/'+currentJob;
}

// ─── Live power ───
async function applyLivePower(){
  const pct = document.getElementById('liveSlider').value;
  await fetch('/set_live_power',{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({power: parseInt(pct)})
  });
  document.getElementById('liveBadge').style.display = 'inline-flex';
  document.getElementById('liveBadgeVal').textContent = pct+'%';
  notify(`Live power set to ${pct}%`,'info');
}

async function clearLivePower(){
  await fetch('/set_live_power',{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({power: null})
  });
  document.getElementById('liveBadge').style.display = 'none';
  notify('Live power override cleared','info');
}

// ─── Jobs ───
async function loadJobs(){
  const r = await fetch('/jobs');
  const d = await r.json();
  const el = document.getElementById('jobList');
  if(!d.jobs.length){
    el.innerHTML = '<span style="color:var(--muted)">No jobs yet.</span>';
    return;
  }
  el.innerHTML = d.jobs.map(j=>`
    <div class="job-item ${j.name===currentJob?'selected':''}" onclick="selectJob('${j.name}')">
      <div style="font-size:20px">📄</div>
      <div class="job-name">
        <span>${j.name}</span>
        <div class="job-meta">${j.time} · ${j.size} · ${j.lines.toLocaleString()} lines</div>
      </div>
      <div class="job-actions">
        <button class="btn btn-ghost btn-sm" onclick="event.stopPropagation();loadGcodeJob('${j.name}')" title="View">👁</button>
        <button class="btn btn-ghost btn-sm" onclick="event.stopPropagation();window.location='/download/${j.name}'" title="Download">⬇</button>
        <button class="btn btn-danger btn-sm" onclick="event.stopPropagation();deleteJob('${j.name}')" title="Delete">🗑</button>
      </div>
    </div>
  `).join('');
}

async function selectJob(name){
  currentJob = name;
  document.getElementById('launchBtn').disabled = false;
  document.getElementById('dlBtn').disabled = false;
  await loadGcodeJob(name);
  switchTab('gcode');
}

async function loadGcodeJob(name){
  const r = await fetch('/gcode/'+name);
  const d = await r.json();
  if(d.content){
    const lines = d.content.split('\n').length;
    renderGcode(d.content.slice(0,6000), name, lines, d.content.length>6000);
  }
}

async function deleteJob(name){
  if(!confirm('Delete '+name+'?')) return;
  const r = await fetch('/delete/'+name, {method:'DELETE'});
  const d = await r.json();
  if(d.success){
    notify('Deleted '+name,'ok');
    if(currentJob===name){ currentJob=null; }
    loadJobs();
  }
}

// ─── LaserGRBL detect ───
async function checkLgbl(){
  const r = await fetch('/lasergrbl_path');
  const d = await r.json();
  const el = document.getElementById('lgblStatus');
  if(d.found){
    el.innerHTML = `<span class="badge badge-success">LaserGRBL found</span>`;
  } else {
    el.innerHTML = `<span class="badge badge-warn">LaserGRBL not found — set path below</span>`;
  }
}

// ─── Tabs ───
function switchTab(name){
  document.querySelectorAll('.tab').forEach((t,i)=>{
    const n = ['gcode','jobs'][i];
    t.classList.toggle('active', n===name);
  });
  document.querySelectorAll('.tab-pane').forEach(p=>{
    p.classList.toggle('active', p.id==='tab-'+name);
  });
  if(name==='jobs') loadJobs();
}

// ─── Notifications ───
function notify(msg, type='ok'){
  const el = document.createElement('div');
  el.className = `notif-item notif-${type==='err'?'err':type==='info'?'info':'ok'}`;
  el.textContent = msg;
  document.getElementById('notif').appendChild(el);
  setTimeout(()=> el.remove(), 3500);
}

// ─── Init ───
checkLgbl();
loadJobs();
</script>
</body>
</html>
"""

if __name__ == '__main__':
    print(f'''
  ╔══════════════════════════════════════════╗
  ║   LaserGRBL Cloud Bridge v2.0 🔥        ║
  ╠══════════════════════════════════════════╣
  ║  Open  : http://127.0.0.1:5000          ║
  ║  Jobs  : {WATCH_FOLDER[:38]}            ║
  ╚══════════════════════════════════════════╝
''')
    app.run(debug=False, host='127.0.0.1', port=5000)
