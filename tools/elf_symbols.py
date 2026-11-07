"""Read GNU/LLVM readelf dynamic-symbol rows, including multiword OS-specific types."""
import re

_ROW = re.compile(
    r'^\s*\d+:\s+[0-9a-fA-F]+\s+\S+\s+(?P<type>.+?)\s+'
    r'(?P<binding>LOCAL|GLOBAL|WEAK|UNIQUE)\s+'
    r'(?P<visibility>DEFAULT|HIDDEN|INTERNAL|PROTECTED)\s+'
    r'(?P<section>\S+)\s+(?P<name>\S+)(?:\s+.*)?$'
)


def dynamic_symbols(output):
    """Return named symbols; a blank/header row has no symbol to resolve."""
    result=[]
    for line in output.splitlines():
        match=_ROW.match(line)
        if match:
            result.append(match.groupdict())
    return result
