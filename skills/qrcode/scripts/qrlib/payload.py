"""Turn the spec's `content` block into the exact string that will be encoded."""
import segno.helpers as H

from .config import SpecError


def make_payload(content):
    kind, val = next(iter(content.items()))
    try:
        if kind in ('url', 'text'):
            if not isinstance(val, str) or not val:
                raise SpecError(f'content.{kind}: expected a non-empty string')
            return val
        if kind == 'tel':
            return val if str(val).lower().startswith('tel:') else f'tel:{val}'
        if kind == 'wifi':
            return H.make_wifi_data(ssid=val['ssid'], password=val.get('password'),
                                    security=val.get('security', 'WPA'), hidden=bool(val.get('hidden', False)))
        if kind == 'vcard':
            v = dict(val)
            v.setdefault('displayname', v.get('name'))
            return H.make_vcard_data(**v)
        if kind == 'mecard':
            return H.make_mecard_data(**val)
        if kind == 'email':
            return H.make_make_email_data(**val)
        if kind == 'sms':
            return f"SMSTO:{val['number']}:{val.get('body', '')}"
        if kind == 'geo':
            return H.make_geo_data(val['lat'], val['lng'])
    except (KeyError, TypeError) as e:
        raise SpecError(f'content.{kind}: {e}') from e
    raise SpecError(f'unsupported content type {kind}')


def describe(payload, limit=80):
    return payload if len(payload) <= limit else payload[:limit - 3] + '...'
