"""Safely unpack original substantive traces and check every file. No network."""
from pathlib import Path
import hashlib,json,tarfile
H=Path(__file__).resolve().parent
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
expected=json.loads((H/'primary-trace-file-sha256.json').read_text(encoding='utf-8'))
provenance=json.loads((H/'primary-archive-provenance.json').read_text(encoding='utf-8'))
assert sha(H/'primary-traces.tar.gz')==provenance['submission_trace_archive_sha256']
with tarfile.open(H/'primary-traces.tar.gz','r:gz') as archive:
    for member in archive:
        target=(H/member.name).resolve()
        assert target.is_relative_to(H.resolve()),member.name
        if member.isdir():target.mkdir(parents=True,exist_ok=True);continue
        assert member.isfile(),member.name
        data=archive.extractfile(member).read()
        assert hashlib.sha256(data).hexdigest()==expected[member.name]
        if target.exists():assert sha(target)==expected[member.name],member.name
        else:
            target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
            if member.name.endswith('/bin/orion-frozen'):target.chmod(0o755)
print('PASS: primary traces unpacked and every substantive file SHA verified; zero model calls')
