"""Phase 102: Verify IEEE80211_HTSIG_NULL_SCS env var parsing.

Edge-case coverage for the CSV -> d_htsig_null_sc_mask[52] parser in
lib/frame_equalizer_impl.cc (constructor-time, lines ~3625-3656).

Parser semantics (from C++ implementation):
- Digits (0-9) accumulate a value; ',' and ' ' both act as token separators.
- First token whose leading char is neither a digit nor a separator breaks
  the loop entirely (the C++ break fires on the first unknown char).
- Values >= 52 are silently skipped (range check).
- Duplicate indices are idempotent (set bit, no increment of a per-index flag).
- Out-of-range ('52', '-1', '99', '200') drop the token; negative sign is
  also a non-digit so it breaks the loop just like 'abc' would.
- Empty/unset env var leaves the mask all zeros.
"""
import os
import subprocess

import pytest

# Pick a python that can actually import gnuradio. Prefer $CONDA_PREFIX
# if it exists and its bin/python can import gnuradio.gr; otherwise fall
# back to the project's known gnuradio env. This means the test works
# inside any activated conda env that has gnuradio installed, while
# still defaulting to the project-canonical interpreter.
_CANDIDATE_BASES = [
    os.environ.get("CONDA_PREFIX"),
    "/home/hy/conda/envs/gnuradio",
]


def _pick_python_bin():
    for base in _CANDIDATE_BASES:
        if not base:
            continue
        candidate = os.path.join(base, "bin", "python")
        if not os.path.isfile(candidate):
            continue
        # Probe that candidate actually has gnuradio importable. We do
        # this by trying a tiny import inside the candidate itself.
        try:
            r = subprocess.run(
                [candidate, "-c", "import gnuradio.gr"],
                capture_output=True, text=True, timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if r.returncode == 0:
            return candidate
    # Last resort: fall back to CONDA_PREFIX/python if nothing else worked.
    # If even that fails, the test will surface a useful ImportError.
    last = _CANDIDATE_BASES[0] or "/home/hy/conda/envs/gnuradio"
    return os.path.join(last, "bin", "python")


PYTHON_BIN = _pick_python_bin()


def _run_parse_subprocess(env_value, env_unset=False):
    """Spawn a subprocess that constructs a frame_equalizer with the given
    env-var state and prints the resulting mask bit-set on stdout.

    Returns (returncode, stdout, stderr).
    """
    env_setup = ""
    if not env_unset:
        # Escape any embedded double-quotes (none expected, but be safe) and
        # backslashes so the inner Python sees the literal value.
        escaped = env_value.replace("\\", "\\\\").replace('"', '\\"')
        env_setup = (
            f'os.environ["IEEE80211_HTSIG_NULL_SCS"] = "{escaped}"\n'
        )

    inner = f'''
import os
import sys
sys.path.insert(0, "/home/hy/gr-ieee802-11/python")
sys.path.insert(0, "/home/hy/gr-ieee802-11/build/python/bindings")
{env_setup}
from ieee802_11 import frame_equalizer
from ieee802_11 import Equalizer
fe = frame_equalizer(Equalizer.LS, 0.0, 20.0, True, True)
mask = fe.d_htsig_null_sc_mask if hasattr(fe, "d_htsig_null_sc_mask") else None
if mask is None:
    print("FAIL: d_htsig_null_sc_mask member not found")
    sys.exit(1)
set_bits = sorted({{i for i, v in enumerate(mask) if v}})
print("BITS=" + ",".join(str(i) for i in set_bits))
'''

    # For "unset" we explicitly nuke the env var so the C++ getenv() returns
    # NULL; for everything else we inherit the parent env (matches existing
    # test behavior in test_null_aware_llr.py pre-refactor).
    if env_unset:
        env = {k: v for k, v in os.environ.items()
               if k != "IEEE80211_HTSIG_NULL_SCS"}
    else:
        env = {**os.environ}

    return subprocess.run(
        [PYTHON_BIN, "-c", inner],
        capture_output=True, text=True, env=env,
    )


def _bits_from_subprocess(env_value, env_unset=False):
    """Helper: run subprocess and parse the BITS=... line from stdout."""
    result = _run_parse_subprocess(env_value, env_unset=env_unset)
    assert result.returncode == 0, (
        f"subprocess failed (rc={result.returncode}): "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    line = None
    for out_line in result.stdout.splitlines():
        if out_line.startswith("BITS="):
            line = out_line
            break
    assert line is not None, (
        f"unexpected stdout: {result.stdout!r} stderr={result.stderr!r}"
    )
    payload = line[len("BITS="):]
    if not payload:
        return set()
    return {int(x) for x in payload.split(",") if x}


@pytest.mark.parametrize(
    ("env_value", "expected", "env_unset"),
    [
        # Empty / unset env var: parser never fires, all 52 bits stay 0.
        ("", set(), False),
        (None, set(), True),
        # Basic CSV
        ("0,5,10", {0, 5, 10}, False),
        # Boundary — 51 is the last valid index.
        ("0,5,10,51", {0, 5, 10, 51}, False),
        # Out-of-range values: 99/200/52/-1 dropped (52 fails the < 52 check,
        # -1 has a leading '-' which is non-digit/non-separator and breaks
        # the parser, so tokens AFTER -1 are also dropped — matching C++).
        ("5,99,200,10,52,-1", {5, 10}, False),
        # Consecutive separators: skipped silently, both valid tokens parsed.
        ("0,,5", {0, 5}, False),
        # Space separator: parser accepts both ',' and ' '.
        ("0 5 10", {0, 5, 10}, False),
        # Duplicates: idempotent — bit set once, but set is the same.
        ("5,5,5", {5}, False),
        # Junk prefix: 'abc' is non-digit/non-separator, parser breaks
        # immediately and never reaches ',5'. Whole mask is empty.
        ("abc,5", set(), False),
    ],
    ids=[
        "empty",
        "unset",
        "basic_csv",
        "boundary_51",
        "out_of_range",
        "consecutive_separators",
        "space_separator",
        "duplicates",
        "junk_prefix",
    ],
)
def test_htsig_null_scs_envvar_parsing(env_value, expected, env_unset):
    """CSV env var -> d_htsig_null_sc_mask[52] with bits set at indices.

    Each parametrized case spawns a fresh subprocess because the env var
    is read at frame_equalizer() construction time and the .so is already
    loaded into the pytest process.
    """
    actual = _bits_from_subprocess(env_value, env_unset=env_unset)
    assert actual == expected, (
        f"IEEE80211_HTSIG_NULL_SCS={env_value!r} "
        f"(unset={env_unset}): expected {sorted(expected)}, got {sorted(actual)}"
    )


def test_null_scs_zero_llr_smoke():
    """Verify mask is loaded and accessible; full LLR path tested in Task 3."""
    code = '''
import os
import sys
sys.path.insert(0, "/home/hy/gr-ieee802-11/python")
sys.path.insert(0, "/home/hy/gr-ieee802-11/build/python/bindings")
os.environ["IEEE80211_HTSIG_NULL_SCS"] = "3,7,15"
from ieee802_11 import frame_equalizer
from ieee802_11 import Equalizer
fe = frame_equalizer(Equalizer.LS, 0.0, 20.0, True, True)
mask = fe.d_htsig_null_sc_mask if hasattr(fe, "d_htsig_null_sc_mask") else None
if mask is None:
    print("FAIL: d_htsig_null_sc_mask member not found")
    sys.exit(1)
set_bits = sorted({i for i, v in enumerate(mask) if v})
if set_bits != [3, 7, 15]:
    print(f"FAIL: expected [3, 7, 15], got {set_bits}")
    sys.exit(1)
print("PASS")
'''
    result = subprocess.run(
        [PYTHON_BIN, "-c", code],
        capture_output=True, text=True,
    )
    assert "PASS" in result.stdout, f"Failed: stdout={result.stdout!r} stderr={result.stderr!r}"
