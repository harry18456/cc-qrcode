"""Spec loading, layering (defaults < theme < profile < spec), and validation.

The spec is the only interface between the agent and the engine. Every value the
engine can decide on its own accepts "auto"; everything else is validated strictly so
that typos fail loudly instead of being silently ignored.
"""
import copy
import os

import yaml

SKILL_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
ASSETS = os.path.join(SKILL_ROOT, 'assets')

MODULE_SHAPES = ['square', 'rounded', 'dot', 'diamond', 'squircle', 'leaf', 'connected']
FINDER_OUTER = ['square', 'rounded', 'circle', 'leaf']
FINDER_INNER = ['square', 'rounded', 'circle', 'leaf', 'icon']
PLATES = ['auto', 'square', 'rounded', 'circle', 'hug', 'none']
DECO_MODES = ['clear', 'overlay']
ANCHORS = ['top-right', 'bottom-left', 'bottom-right', 'top', 'bottom', 'left', 'right']
CONTENT_TYPES = ['url', 'text', 'wifi', 'vcard', 'mecard', 'email', 'tel', 'sms', 'geo']
FORMATS = ['svg', 'png', 'pdf']

DEFAULTS = {
    'content': None,
    'ec': 'auto',
    'version': 'auto',
    'mask': 'auto',
    'quiet_zone': 4,
    'theme': None,
    'modules': {'shape': 'square', 'size': 'auto', 'radius': 0.5},
    'finder': {'outer': 'square', 'inner': 'square', 'icon': None, 'icon_recolor': True,
               'icon_scale': 1.0, 'color': None},
    'alignment': {'style': 'solid'},
    'colors': {'dark': '#000000', 'light': '#FFFFFF', 'auto_repair': True, 'allow_inverted': False},
    'center': {'image': None, 'size': 'auto', 'padding': 'auto', 'plate': 'auto', 'recolor': None},
    'decorations': [],
    'background': {'image': None, 'wash': 'auto'},
    'print': {'width_mm': None, 'distance_mm': None, 'extra_widths_mm': []},
    'output': {'formats': ['svg', 'png'], 'png_module_px': 16, 'transparent': False, 'basename': 'qr'},
}


class SpecError(ValueError):
    pass


def load_yaml(path):
    with open(path, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise SpecError(f'{path}: top level must be a mapping')
    return data


def deep_merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load_thresholds(path=None):
    """Built-in thresholds, with a user's file (--thresholds) layered on top: a copy may list only the
    values it changes, and a key it leaves out keeps its built-in value."""
    th = load_yaml(os.path.join(ASSETS, 'thresholds.yaml'))
    return deep_merge(th, load_yaml(path)) if path else th


def load_calibration(path=None):
    path = path or os.path.join(ASSETS, 'calibration.yaml')
    if not os.path.exists(path):
        return {}
    return load_yaml(path)


def list_themes():
    d = os.path.join(ASSETS, 'themes')
    return sorted(f[:-5] for f in os.listdir(d) if f.endswith('.yaml'))


def _resolve_paths(spec, base_dir):
    """Make every image path absolute relative to the file that declared it."""
    def fix(p):
        if p and isinstance(p, str) and not os.path.isabs(p):
            return os.path.normpath(os.path.join(base_dir, p))
        return p
    if isinstance(spec.get('center'), dict):
        spec['center']['image'] = fix(spec['center'].get('image'))
    if isinstance(spec.get('finder'), dict):
        spec['finder']['icon'] = fix(spec['finder'].get('icon'))
    if isinstance(spec.get('background'), dict):
        spec['background']['image'] = fix(spec['background'].get('image'))
    for d in spec.get('decorations') or []:
        if isinstance(d, dict):
            d['image'] = fix(d.get('image'))
    return spec


def load_spec(spec_path, profile_path=None):
    """Return the merged spec plus the list of layers that contributed to it."""
    layers = ['defaults']
    spec = _resolve_paths(load_yaml(spec_path), os.path.dirname(os.path.abspath(spec_path)))
    profile = {}
    if profile_path:
        profile = _resolve_paths(load_yaml(profile_path), os.path.dirname(os.path.abspath(profile_path)))
    theme_name = spec.get('theme') or profile.get('theme')
    merged = copy.deepcopy(DEFAULTS)
    if theme_name:
        tpath = os.path.join(ASSETS, 'themes', f'{theme_name}.yaml')
        if not os.path.exists(tpath):
            raise SpecError(f"theme '{theme_name}' not found; available: {', '.join(list_themes())}")
        theme = load_yaml(tpath)
        theme.pop('description', None)
        merged = deep_merge(merged, theme)
        layers.append(f'theme:{theme_name}')
    if profile:
        merged = deep_merge(merged, profile)
        layers.append(f'profile:{os.path.basename(profile_path)}')
    merged = deep_merge(merged, spec)
    layers.append(f'spec:{os.path.basename(spec_path)}')
    merged['theme'] = theme_name
    validate(merged)
    return merged, layers


def _enum(val, allowed, where):
    if val not in allowed:
        raise SpecError(f'{where}: {val!r} not in {allowed}')


def _num(val, lo, hi, where, allow_auto=False, allow_none=False):
    if allow_auto and val == 'auto':
        return
    if allow_none and val is None:
        return
    if not isinstance(val, (int, float)) or isinstance(val, bool) or not (lo <= val <= hi):
        raise SpecError(f'{where}: expected number in [{lo}, {hi}]' + (" or 'auto'" if allow_auto else '')
                        + f', got {val!r}')


def _keys(d, allowed, where):
    extra = set(d) - set(allowed)
    if extra:
        raise SpecError(f'{where}: unknown key(s) {sorted(extra)}; allowed: {sorted(allowed)}')


def _color(val, where):
    if isinstance(val, str) and val.startswith('#') and len(val) in (4, 7):
        try:
            int(val[1:], 16)
            return
        except ValueError:
            pass
    raise SpecError(f'{where}: expected #RGB or #RRGGBB, got {val!r}')


def _file(path, where):
    if path is not None and not os.path.exists(path):
        raise SpecError(f'{where}: file not found: {path}')


def validate(s):
    _keys(s, DEFAULTS.keys(), 'spec')
    c = s.get('content')
    if not isinstance(c, dict) or len(c) != 1:
        raise SpecError(f'content: expected exactly one of {CONTENT_TYPES}, e.g. content: {{url: "https://..."}}')
    _enum(next(iter(c)), CONTENT_TYPES, 'content type')
    if s['ec'] != 'auto':
        _enum(s['ec'], ['L', 'M', 'Q', 'H'], 'ec')
    if s['version'] != 'auto':
        _num(s['version'], 1, 40, 'version')
    if s['mask'] != 'auto':
        _num(s['mask'], 0, 7, 'mask')
    _num(s['quiet_zone'], 4, 20, 'quiet_zone')

    m = s['modules']
    _keys(m, DEFAULTS['modules'].keys(), 'modules')
    _enum(m['shape'], MODULE_SHAPES, 'modules.shape')
    _num(m['size'], 0.5, 1.0, 'modules.size', allow_auto=True)
    _num(m['radius'], 0.0, 1.0, 'modules.radius')

    f = s['finder']
    _keys(f, DEFAULTS['finder'].keys(), 'finder')
    _enum(f['outer'], FINDER_OUTER, 'finder.outer')
    _enum(f['inner'], FINDER_INNER, 'finder.inner')
    if f['inner'] == 'icon':
        if not f.get('icon'):
            raise SpecError("finder.inner is 'icon' but finder.icon is empty")
        _file(f['icon'], 'finder.icon')
    _num(f['icon_scale'], 0.5, 1.0, 'finder.icon_scale')
    if f.get('color'):
        _color(f['color'], 'finder.color')

    _keys(s['alignment'], DEFAULTS['alignment'].keys(), 'alignment')
    _enum(s['alignment']['style'], ['solid', 'modules'], 'alignment.style')

    col = s['colors']
    _keys(col, DEFAULTS['colors'].keys(), 'colors')
    d = col['dark']
    if isinstance(d, dict):
        _keys(d, ['gradient', 'angle'], 'colors.dark')
        stops = d.get('gradient')
        if not isinstance(stops, list) or len(stops) < 2:
            raise SpecError('colors.dark.gradient: need a list of at least two colors')
        for i, st in enumerate(stops):
            _color(st, f'colors.dark.gradient[{i}]')
        _num(d.get('angle', 0), -360, 360, 'colors.dark.angle')
    elif d != 'auto_from_logo':
        _color(d, 'colors.dark')
    _color(col['light'], 'colors.light')

    ce = s['center']
    _keys(ce, DEFAULTS['center'].keys(), 'center')
    _file(ce.get('image'), 'center.image')
    _num(ce['size'], 0.05, 0.4, 'center.size', allow_auto=True)
    _num(ce['padding'], 0, 3, 'center.padding', allow_auto=True)
    _enum(ce['plate'], PLATES, 'center.plate')
    if ce.get('recolor'):
        _color(ce['recolor'], 'center.recolor')
    if col['dark'] == 'auto_from_logo' and not ce.get('image'):
        raise SpecError("colors.dark is 'auto_from_logo' but center.image is empty")

    if not isinstance(s['decorations'], list):
        raise SpecError('decorations: expected a list')
    for i, dec in enumerate(s['decorations']):
        w = f'decorations[{i}]'
        if not isinstance(dec, dict):
            raise SpecError(f'{w}: expected a mapping')
        _keys(dec, ['image', 'size', 'position', 'anchor', 'mode'], w)
        if not dec.get('image'):
            raise SpecError(f'{w}.image is required')
        _file(dec['image'], f'{w}.image')
        _num(dec.get('size', 5), 1.5, 15, f'{w}.size', allow_auto=True)
        pos = dec.get('position', 'auto')
        if pos != 'auto' and not (isinstance(pos, list) and len(pos) == 2):
            raise SpecError(f"{w}.position: expected 'auto' or [row, col] (module units, center of the image)")
        if dec.get('anchor') is not None:
            _enum(dec['anchor'], ANCHORS, f'{w}.anchor')
        _enum(dec.get('mode', 'clear'), DECO_MODES, f'{w}.mode')

    bg = s['background']
    _keys(bg, DEFAULTS['background'].keys(), 'background')
    _file(bg.get('image'), 'background.image')
    _num(bg['wash'], 0.0, 0.95, 'background.wash', allow_auto=True)

    p = s['print']
    _keys(p, DEFAULTS['print'].keys(), 'print')
    _num(p['width_mm'], 5, 5000, 'print.width_mm', allow_none=True)
    _num(p['distance_mm'], 20, 100000, 'print.distance_mm', allow_none=True)
    if not isinstance(p.get('extra_widths_mm') or [], list):
        raise SpecError('print.extra_widths_mm: expected a list of widths in mm')
    for i, w in enumerate(p.get('extra_widths_mm') or []):
        _num(w, 5, 5000, f'print.extra_widths_mm[{i}]')
    if p.get('extra_widths_mm') and not p['width_mm']:
        raise SpecError('print.extra_widths_mm needs print.width_mm (the main size)')

    o = s['output']
    _keys(o, DEFAULTS['output'].keys(), 'output')
    for fmt in o['formats']:
        _enum(fmt, FORMATS, 'output.formats')
    _num(o['png_module_px'], 4, 64, 'output.png_module_px')
    return s
