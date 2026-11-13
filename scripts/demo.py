"""One reproducible offline demo. Run from any directory after editable installation."""
from pathlib import Path
import gzip
import hashlib
import json
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    manifest = json.loads((ROOT/'data/source_manifest.json').read_text())
    expected = manifest['external_import']
    body = gzip.decompress((ROOT/'data/filing.html.gz').read_bytes())
    if len(body) != expected['byte_count'] or hashlib.sha256(body).hexdigest() != expected['sha256']:
        raise ValueError('bundled source does not match the source manifest')
    cache = ROOT/'data/cache/msft-20240630.htm'
    cache.parent.mkdir(exist_ok=True)
    if cache.exists() and cache.read_bytes() != body:
        raise ValueError('existing cache differs; preserve and inspect it before retrying')
    cache.write_bytes(body)
    commands = [(['extract'],0),(['evaluate'],0),
                (['ask','What was revenue in FY2024?'],0),
                (['ask','How did revenue change from FY2023 to FY2024?'],0),
                (['ask','What will revenue be in FY2027?'],1)]
    for args, expected_exit in commands:
        print('\n$ finreport '+' '.join(args),flush=True)
        result = subprocess.run([sys.executable,'-m','finreport','--root',str(ROOT),*args],check=False)
        if result.returncode != expected_exit:
            raise SystemExit(f'expected exit {expected_exit}, observed {result.returncode}')
    print('\nDemo passed: extraction, evaluation, lookup, change and unsupported question.')


if __name__ == '__main__': main()
