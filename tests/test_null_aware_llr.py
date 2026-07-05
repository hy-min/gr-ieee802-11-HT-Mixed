"""Phase 102: Verify IEEE80211_HTSIG_NULL_SCS env var parsing."""
import os
import subprocess
import sys


def test_null_scs_envvar_parsing():
    """Verify env var CSV -> d_htsig_null_sc_mask[52] with bits set at indices."""
    code = '''
import os
import sys
sys.path.insert(0, "/home/hy/gr-ieee802-11/python")
sys.path.insert(0, "/home/hy/gr-ieee802-11/build/python/bindings")
os.environ["IEEE80211_HTSIG_NULL_SCS"] = "0,5,10"
from ieee802_11 import frame_equalizer
from ieee802_11 import Equalizer
fe = frame_equalizer(Equalizer.LS, 0.0, 20.0, True, True)
mask = fe.d_htsig_null_sc_mask if hasattr(fe, "d_htsig_null_sc_mask") else None
if mask is None:
    print("FAIL: d_htsig_null_sc_mask member not found")
    sys.exit(1)
expected_set = {0, 5, 10}
actual_set = {i for i, v in enumerate(mask) if v}
if actual_set != expected_set:
    print(f"FAIL: expected mask bits {expected_set}, got {actual_set}")
    sys.exit(1)
print("PASS")
'''
    result = subprocess.run(
        ["/home/hy/conda/envs/gnuradio/bin/python", "-c", code],
        capture_output=True, text=True, env={**os.environ},
    )
    assert "PASS" in result.stdout, f"Failed: stdout={result.stdout!r} stderr={result.stderr!r}"