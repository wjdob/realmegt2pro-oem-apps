"""Keep optional-app platform contracts discrete and API36 templates immutable.

API37 uses the same reviewed transaction engine with exact generation guards.
Native/policy/provider bindings and real app qualification are independent gates.
"""
LEGACY = {'sdk': 36, 'lineage': '23.2'}
ANDROID17 = {'sdk': 37, 'lineage': '24.0'}


def explicit(value):
    if (not isinstance(value, dict) or set(value) != {'sdk', 'lineage'}
            or type(value['sdk']) is not int or not isinstance(value['lineage'], str)
            or value not in (LEGACY, ANDROID17)):
        raise ValueError('unreviewed optional-app platform pair')
    return dict(value)


def checked(record):
    return explicit(record['platform']) if 'platform' in record else dict(LEGACY)


def carry(result, record):
    value = checked(record)
    if value != LEGACY:
        result['platform'] = value
    return result


def template(source, record, kind):
    if kind not in ('installer', 'retention'):
        raise ValueError('unknown optional-app template role')
    value = checked(record)
    if value == LEGACY:
        return source  # Exact legacy bytes and archived-template identity remain valid.
    text = source.decode('utf-8')
    count = 1 if kind == 'installer' else 2
    replacements = [
        ('^ro.build.version.sdk=36$', '^ro.build.version.sdk=37$', count),
        (r'^ro.lineage.version=23\.2-', r'^ro.lineage.version=24\.0-', count),
    ]
    if kind == 'installer':
        replacements += [
            ('LineageOS23.2 required', 'LineageOS24.0 required', 1),
            ('Android API36 required', 'Android API37 required', 1),
            ('Lineage23.2 and project signing certificate',
             'Lineage24.0 and project signing certificate', 1),
        ]
    for before, after, expected in replacements:
        if text.count(before) != expected:
            raise ValueError('platform template guards changed; review required')
        text = text.replace(before, after)
    if 'ro.build.version.sdk=36' in text or r'ro.lineage.version=23\.2-' in text:
        raise ValueError('legacy generation guard remains in API37 template')
    return text.encode('utf-8')
