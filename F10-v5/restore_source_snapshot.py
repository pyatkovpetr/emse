"""Restore the exact source tar omitted from Git only because of file size."""
from pathlib import Path
import gzip,hashlib
root=Path(__file__).resolve().parent
target=root/'replication/sources/mmlu-data.tar'
expected='bec563ba4bac1d6aaf04141cd7d1605d7a5ca833e38f994051e818489592989b'
data=gzip.decompress((root/'replication/sources/mmlu-data.tar.gz').read_bytes())
assert hashlib.sha256(data).hexdigest()==expected
if target.exists():
    assert hashlib.sha256(target.read_bytes()).hexdigest()==expected, 'Refusing to overwrite a different snapshot'
else:
    target.write_bytes(data)
print('PASS: original MMLU source snapshot SHA verified; zero model calls')
